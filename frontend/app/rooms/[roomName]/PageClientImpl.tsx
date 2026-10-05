'use client';

import React from 'react';
import { decodePassphrase } from '@/lib/client-utils';
import { DebugMode } from '@/lib/Debug';
import { KeyboardShortcuts } from '@/lib/KeyboardShortcuts';
import { RecordingIndicator } from '@/lib/RecordingIndicator';
import { SettingsMenu } from '@/lib/SettingsMenu';
import { MeetingSessionProvider, type MeetingSession } from '@/lib/MeetingSession';
import {
  joinMeeting,
  pollAdmission,
  readHostKey,
  readParticipantKey,
  saveParticipantKey,
  type AdmittedMeeting,
  type MeetingRole,
} from '@/lib/meetingApi';
import { ConnectionDetails } from '@/lib/types';
import lobbyStyles from '@/styles/MeetingRoom.module.css';
import {
  formatChatMessageLinks,
  LocalUserChoices,
  PreJoin,
  RoomContext,
} from '@livekit/components-react';
import {
  ExternalE2EEKeyProvider,
  RoomOptions,
  VideoCodec,
  Room,
  DeviceUnsupportedError,
  RoomConnectOptions,
  RoomEvent,
} from 'livekit-client';
import { meetingMicrophonePublishOptions, meetingRoomOptions } from '@/lib/conferenceMedia';
import { useRouter } from 'next/navigation';
import { useSetupE2EE } from '@/lib/useSetupE2EE';
import { useLowCPUOptimizer } from '@/lib/usePerfomanceOptimiser';
import { MeetingConference } from '@/lib/MeetingConference';

const SHOW_SETTINGS_MENU = process.env.NEXT_PUBLIC_SHOW_SETTINGS_MENU == 'true';

export function PageClientImpl(props: {
  roomName: string;
  region?: string;
  hq: boolean;
  codec: VideoCodec;
  singlePeerConnection: boolean;
}) {
  const [preJoinChoices, setPreJoinChoices] = React.useState<LocalUserChoices | undefined>(
    undefined,
  );
  const preJoinDefaults = React.useMemo(() => {
    return {
      username: '',
      videoEnabled: true,
      audioEnabled: true,
    };
  }, []);
  const [connectionDetails, setConnectionDetails] = React.useState<ConnectionDetails | undefined>(
    undefined,
  );
  const [session, setSession] = React.useState<MeetingSession | null>(null);
  const [phase, setPhase] = React.useState<'idle' | 'waiting' | 'declined'>('idle');
  const [notice, setNotice] = React.useState('');
  const [pending, setPending] = React.useState<{
    requestId: string;
    participantKey: string;
    choices: LocalUserChoices;
  } | null>(null);
  const submitLock = React.useRef(false);
  const setRole = React.useCallback((role: MeetingRole) => {
    setSession((current) =>
      current ? { ...current, role, roleEpoch: (current.roleEpoch ?? 0) + 1 } : current,
    );
  }, []);

  const enterRoom = React.useCallback(
    (choices: LocalUserChoices, admitted: AdmittedMeeting, participantKey: string | null) => {
      if (participantKey) {
        saveParticipantKey(props.roomName, participantKey);
      }
      setPreJoinChoices(choices);
      setConnectionDetails({
        serverUrl: admitted.serverUrl,
        participantToken: admitted.participantToken,
        roomName: props.roomName,
        participantName: choices.username,
      });
      setSession({
        roomName: props.roomName,
        hostKey: readHostKey(props.roomName),
        participantKey: participantKey || readParticipantKey(props.roomName),
        role: admitted.role,
      });
    },
    [props.roomName],
  );

  const handlePreJoinSubmit = React.useCallback(
    async (values: LocalUserChoices) => {
      if (submitLock.current) {
        return;
      }
      submitLock.current = true;
      setNotice('');
      const hostKey = readHostKey(props.roomName);
      const participantKey = readParticipantKey(props.roomName);
      if (!hostKey) {
        setPhase('waiting');
      }
      try {
        const result = await joinMeeting({
          roomName: props.roomName,
          participantName: values.username.trim(),
          hostKey,
          participantKey,
        });
        if (result.status === 'pending' && result.participantKey) {
          saveParticipantKey(props.roomName, result.participantKey);
        }
        if (result.status === 'admitted') {
          setPhase('idle');
          setPending(null);
          enterRoom(values, result, participantKey);
          return;
        }
        if (result.status === 'pending') {
          setPending({
            requestId: result.requestId,
            participantKey: result.participantKey,
            choices: values,
          });
          setPhase('waiting');
          return;
        }
        submitLock.current = false;
        setPending(null);
        setPhase('idle');
        setNotice(result.detail);
      } catch (error) {
        submitLock.current = false;
        setPending(null);
        setPhase('idle');
        setNotice(error instanceof Error ? error.message : 'Could not join the meeting');
      }
    },
    [enterRoom, props.roomName],
  );

  React.useEffect(() => {
    if (phase !== 'waiting' || !pending) {
      return;
    }
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      try {
        const result = await pollAdmission({
          roomName: props.roomName,
          requestId: pending.requestId,
          participantKey: pending.participantKey,
        });
        if (cancelled) {
          return;
        }
        if (result.status === 'admitted') {
          setPhase('idle');
          setPending(null);
          enterRoom(pending.choices, result, pending.participantKey);
          return;
        }
        if (result.status === 'declined') {
          submitLock.current = false;
          setNotice('The host has declined your request');
          setPhase('declined');
          setPending(null);
          return;
        }
        if (result.status === 'ended') {
          submitLock.current = false;
          setNotice(result.detail);
          setPhase('idle');
          setPending(null);
          return;
        }
      } catch (error) {
        if (!cancelled) {
          console.error(error);
        }
      }
      if (!cancelled) {
        timer = window.setTimeout(tick, 1500);
      }
    };
    timer = window.setTimeout(tick, 1500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [enterRoom, pending, phase, props.roomName]);

  const onValidate = React.useCallback(
    (values: LocalUserChoices) => phase !== 'waiting' && values.username.trim().length > 0,
    [phase],
  );
  const handlePreJoinError = React.useCallback((e: Error) => console.error(e), []);
  const joinLabel = phase === 'waiting' ? 'Waiting for the host to accept you' : 'Join meeting';
  const hostOnThisBrowser = readHostKey(props.roomName);
  // FOR PRODUCTION - users are unique on this browser, so a second tab is still the host
  // const hostLobbyHint =
  //   "You're the host on this browser, so you'll go straight in. Open this link in a private window to join as a guest.";
  // FOR TESTING - open the room link in another tab to join as a guest on this laptop
  const hostLobbyHint =
    "You're the host in this tab, so you'll go straight in. Open this link in another tab to join as a guest.";
  const lobbyHint = hostOnThisBrowser
    ? hostLobbyHint
    : "You'll stay here until the host accepts you.";

  return (
    <main data-lk-theme="default" style={{ height: '100%' }}>
      {connectionDetails === undefined || preJoinChoices === undefined || session === null ? (
        <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
          <div className={lobbyStyles.lobby}>
            {notice ? (
              <p className={lobbyStyles.notice} role="status">
                {notice}
              </p>
            ) : phase !== 'waiting' ? (
              <p className={lobbyStyles.notice}>{lobbyHint}</p>
            ) : null}
            <PreJoin
              defaults={preJoinDefaults}
              joinLabel={joinLabel}
              onValidate={onValidate}
              onSubmit={handlePreJoinSubmit}
              onError={handlePreJoinError}
            />
          </div>
        </div>
      ) : (
        <MeetingSessionProvider value={session} setRole={setRole}>
          <VideoConferenceComponent
            connectionDetails={connectionDetails}
            userChoices={preJoinChoices}
            options={{
              codec: props.codec,
              hq: props.hq,
              singlePeerConnection: props.singlePeerConnection,
            }}
          />
        </MeetingSessionProvider>
      )}
    </main>
  );
}

function VideoConferenceComponent(props: {
  userChoices: LocalUserChoices;
  connectionDetails: ConnectionDetails;
  options: {
    hq: boolean;
    codec: VideoCodec;
    singlePeerConnection: boolean;
  };
}) {
  const keyProvider = React.useMemo(() => new ExternalE2EEKeyProvider(), []);
  const { worker, e2eePassphrase } = useSetupE2EE();
  const e2eeEnabled = !!(e2eePassphrase && worker);

  const [e2eeSetupComplete, setE2eeSetupComplete] = React.useState(false);

  const roomOptions = React.useMemo((): RoomOptions => {
    return meetingRoomOptions({
      codec: props.options.codec,
      hq: props.options.hq,
      e2eeEnabled,
      videoDeviceId: props.userChoices.videoDeviceId ?? undefined,
      audioDeviceId: props.userChoices.audioDeviceId ?? undefined,
      singlePeerConnection: props.options.singlePeerConnection,
      e2ee: keyProvider && worker && e2eeEnabled ? { keyProvider, worker } : undefined,
    });
  }, [e2eeEnabled, keyProvider, props.options.codec, props.options.hq, props.options.singlePeerConnection, props.userChoices, worker]);

  const room = React.useMemo(() => new Room(roomOptions), []);

  React.useEffect(() => {
    if (e2eeEnabled) {
      keyProvider
        .setKey(decodePassphrase(e2eePassphrase))
        .then(() => {
          room.setE2EEEnabled(true).catch((e) => {
            if (e instanceof DeviceUnsupportedError) {
              alert(
                `You're trying to join an encrypted meeting, but your browser does not support it. Please update it to the latest version and try again.`,
              );
              console.error(e);
            } else {
              throw e;
            }
          });
        })
        .then(() => setE2eeSetupComplete(true));
    } else {
      setE2eeSetupComplete(true);
    }
  }, [e2eeEnabled, room, e2eePassphrase]);

  const connectOptions = React.useMemo((): RoomConnectOptions => {
    return {
      autoSubscribe: true,
    };
  }, []);

  React.useEffect(() => {
    room.on(RoomEvent.Disconnected, handleOnLeave);
    room.on(RoomEvent.EncryptionError, handleEncryptionError);
    room.on(RoomEvent.MediaDevicesError, handleError);

    if (e2eeSetupComplete) {
      room
        .connect(
          props.connectionDetails.serverUrl,
          props.connectionDetails.participantToken,
          connectOptions,
        )
        .catch((error) => {
          handleError(error);
        });
      if (props.userChoices.videoEnabled) {
        room.localParticipant.setCameraEnabled(true).catch((error) => {
          handleError(error);
        });
      }
      if (props.userChoices.audioEnabled) {
        room.localParticipant
          .setMicrophoneEnabled(true, undefined, meetingMicrophonePublishOptions(e2eeEnabled))
          .catch((error) => {
            handleError(error);
          });
      }
    }
    return () => {
      room.off(RoomEvent.Disconnected, handleOnLeave);
      room.off(RoomEvent.EncryptionError, handleEncryptionError);
      room.off(RoomEvent.MediaDevicesError, handleError);
    };
  }, [e2eeSetupComplete, room, props.connectionDetails, props.userChoices]);

  const lowPowerMode = useLowCPUOptimizer(room);

  const router = useRouter();
  const handleOnLeave = React.useCallback(() => router.push('/'), [router]);
  const handleError = React.useCallback((error: Error) => {
    console.error(error);
    alert(`Encountered an unexpected error, check the console logs for details: ${error.message}`);
  }, []);
  const handleEncryptionError = React.useCallback((error: Error) => {
    console.error(error);
    alert(
      `Encountered an unexpected encryption error, check the console logs for details: ${error.message}`,
    );
  }, []);

  React.useEffect(() => {
    if (lowPowerMode) {
      console.warn('Low power mode enabled');
    }
  }, [lowPowerMode]);

  return (
    <div className="lk-room-container">
      <RoomContext.Provider value={room}>
        <KeyboardShortcuts />
        <MeetingConference
          chatMessageFormatter={formatChatMessageLinks}
          SettingsComponent={SHOW_SETTINGS_MENU ? SettingsMenu : undefined}
        />
        <DebugMode />
        <RecordingIndicator />
      </RoomContext.Provider>
    </div>
  );
}
