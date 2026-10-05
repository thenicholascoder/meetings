'use client';

import { LiveKitRoom } from '@livekit/components-react';
import EgressHelper from '@livekit/egress-sdk';
import {
  ConnectionState,
  ParticipantKind,
  Room,
  RoomEvent,
  Track,
  type Room as LiveKitRoomType,
} from 'livekit-client';
import React from 'react';
import { MeetingConference } from '@/lib/MeetingConference';
import {
  recordingDeadlineReached,
  recordingShouldStart,
  STATS_WAIT_MS,
} from '@/lib/recordingReady';

/**
 * Chrome inside Egress opens this page. Connection details, layout updates,
 * and the start/stop console lines go through EgressHelper.
 * https://docs.livekit.io/transport/media/ingress-egress/egress/custom-template/
 *
 * The meeting stage is what gets captured. Layout from the start request and
 * from UpdateLayout is applied on the frame; the tiles stay the meeting view
 * (people, screen share, and slides) with the control bar left out.
 *
 * LiveKitRoom must own the connection. connect={false} calls room.disconnect()
 * on mount, which closes Chrome before the start signal and aborts the file.
 */

/**
 * Participant audio is played by RoomAudioRenderer inside MeetingConference.
 * Egress records Chrome's audio output. With every microphone off, that
 * output never opens and the file is aborted. Silence keeps the device open
 * without adding a tone of its own.
 */
function holdSilentOutput() {
  const AudioContextCtor =
    window.AudioContext ??
    (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) {
    return () => undefined;
  }
  // A tone, even at gain 0, still runs through Chrome's mixer. Under load that
  // mixer underruns and the recording sounds choppy, then the file's timestamps
  // jump. A silent looping buffer only keeps the output device open.
  // 'interactive' flushes often. 'playback' waits, then dumps one buffer, and
  // the muxer turns the wait into a multi-second sample.
  let context: AudioContext;
  try {
    context = new AudioContextCtor({ sampleRate: 48000, latencyHint: 'interactive' });
  } catch {
    context = new AudioContextCtor();
  }
  const silence = context.createBuffer(1, context.sampleRate, context.sampleRate);
  const source = context.createBufferSource();
  source.buffer = silence;
  source.loop = true;
  source.connect(context.destination);
  source.start();
  const resume = () => {
    if (context.state !== 'running') {
      void context.resume().catch(() => undefined);
    }
  };
  resume();
  const keepAwake = window.setInterval(resume, 1000);
  return () => {
    window.clearInterval(keepAwake);
    try {
      source.stop();
    } catch {
      // The buffer is already stopped when the page is closing.
    }
    void context.close().catch(() => undefined);
  };
}

/** A brief socket drop must not finish the file. Egress treats END_RECORDING as the end of the movie. */
function keepRecordingAcrossBlips(room: LiveKitRoomType) {
  room.off(RoomEvent.Disconnected, EgressHelper.endRecording);
  let timer = 0;
  const onDisconnect = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (room.state === ConnectionState.Disconnected) {
        EgressHelper.endRecording();
      }
    }, 15000);
  };
  const onBack = () => {
    window.clearTimeout(timer);
  };
  room.on(RoomEvent.Disconnected, onDisconnect);
  room.on(RoomEvent.Reconnected, onBack);
  room.on(RoomEvent.Connected, onBack);
  return () => {
    window.clearTimeout(timer);
    room.off(RoomEvent.Disconnected, onDisconnect);
    room.off(RoomEvent.Reconnected, onBack);
    room.off(RoomEvent.Connected, onBack);
  };
}

async function publishedVideoState(room: LiveKitRoomType) {
  let hasVideoTracks = false;
  let hasDecodedFrames = false;
  for (const person of room.remoteParticipants.values()) {
    if (person.kind === ParticipantKind.EGRESS) {
      continue;
    }
    for (const publication of person.trackPublications.values()) {
      if (publication.kind !== Track.Kind.Video) {
        continue;
      }
      hasVideoTracks = true;
      const track = publication.videoTrack;
      if (!track) {
        continue;
      }
      const stats = await track.getRTCStatsReport();
      if (!stats) {
        continue;
      }
      for (const report of stats.values()) {
        if (
          report.type === 'inbound-rtp' &&
          'framesDecoded' in report &&
          typeof report.framesDecoded === 'number' &&
          report.framesDecoded > 0
        ) {
          hasDecodedFrames = true;
        }
      }
    }
  }
  return { hasVideoTracks, hasDecodedFrames };
}

function videoSnapshot(room: LiveKitRoomType) {
  return new Promise<{ hasVideoTracks: boolean; hasDecodedFrames: boolean } | null>((resolve) => {
    const timer = window.setTimeout(() => resolve(null), STATS_WAIT_MS);
    publishedVideoState(room).then(
      (snapshot) => {
        window.clearTimeout(timer);
        resolve(snapshot);
      },
      () => {
        window.clearTimeout(timer);
        resolve(null);
      },
    );
  });
}

function watchUntilReady(room: LiveKitRoomType) {
  let cancelled = false;
  let timer = 0;
  let deadlineTimer = 0;
  let started = false;
  const pageStarted = performance.now();

  const start = () => {
    if (started || cancelled) {
      return;
    }
    started = true;
    window.clearInterval(timer);
    window.clearTimeout(deadlineTimer);
    // Start after the stage has painted, not on the timer alone.
    // Do not require Connected here. A slow room socket must not skip the
    // signal, or Egress aborts with "Start signal not received".
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (!cancelled) {
          EgressHelper.startRecording();
        }
      });
    });
  };

  const arm = () => {
    const connectedAt = performance.now();
    let checking = false;
    timer = window.setInterval(() => {
      if (started || cancelled || checking || room.state !== ConnectionState.Connected) {
        return;
      }
      if (recordingDeadlineReached(performance.now() - pageStarted)) {
        start();
        return;
      }
      checking = true;
      void videoSnapshot(room).then((snapshot) => {
        checking = false;
        if (started || cancelled || !snapshot) {
          return;
        }
        if (
          !recordingShouldStart({
            elapsedMs: performance.now() - connectedAt,
            ...snapshot,
          })
        ) {
          return;
        }
        start();
      });
    }, 100);
  };

  deadlineTimer = window.setInterval(() => {
    if (recordingDeadlineReached(performance.now() - pageStarted)) {
      start();
    }
  }, 200);

  if (room.state === ConnectionState.Connected) {
    arm();
  } else {
    room.once(RoomEvent.Connected, arm);
  }

  return () => {
    cancelled = true;
    window.clearInterval(timer);
    window.clearInterval(deadlineTimer);
    room.off(RoomEvent.Connected, arm);
  };
}

export function EgressRoom() {
  const [layout, setLayout] = React.useState('');
  const [connection, setConnection] = React.useState<{ url: string; token: string } | null>(null);
  const [missingParams, setMissingParams] = React.useState(false);
  const room = React.useMemo(
    () =>
      new Room({
        // A hidden recorder must not pause tracks that are off its own screen.
        adaptiveStream: false,
        dynacast: false,
      }),
    [],
  );

  React.useEffect(() => {
    let url = '';
    let token = '';
    try {
      url = EgressHelper.getLiveKitURL();
      token = EgressHelper.getAccessToken();
    } catch (error) {
      console.error(error);
      setMissingParams(true);
      return;
    }
    // Register before setRoom. The helper reads layout from participant
    // metadata immediately, and calls this listener when UpdateLayout runs.
    EgressHelper.onLayoutChanged(setLayout);
    setLayout(EgressHelper.getLayout());
    EgressHelper.setRoom(room);
    const releaseBlips = keepRecordingAcrossBlips(room);
    setConnection({ url, token });
    // Arm the start signal before the silent output. AudioContext can throw
    // in this Chrome, and that must not skip START_RECORDING.
    const stopWatching = watchUntilReady(room);
    let releaseSilence: () => void = () => undefined;
    try {
      releaseSilence = holdSilentOutput();
    } catch (error) {
      console.error(error);
    }
    return () => {
      stopWatching();
      releaseBlips();
      releaseSilence();
    };
  }, [room]);

  if (missingParams) {
    return <div>missing required params url and token</div>;
  }

  return (
    <div className="egress-frame" data-layout={layout}>
      {connection && (
        <LiveKitRoom
          room={room}
          serverUrl={connection.url}
          token={connection.token}
          video={false}
          audio={false}
          connectOptions={{ autoSubscribe: true }}
          onError={(error) => {
            console.error(error);
          }}
        >
          <MeetingConference recording />
        </LiveKitRoom>
      )}
    </div>
  );
}
