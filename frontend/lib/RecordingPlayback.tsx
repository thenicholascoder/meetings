'use client';

import { useRoomContext } from '@livekit/components-react';
import React from 'react';
import { createPortal } from 'react-dom';
import { useMeetingSession } from '@/lib/MeetingSession';
import { loadRecordingSrc } from '@/lib/meetingApi';
import styles from '@/styles/RecordingPlayback.module.css';

type WatchHandler = (key: string) => void;

const watchers = new Set<WatchHandler>();

/** Open the recording over the meeting, the same picture the recorder captured. */
export function watchRecording(key: string) {
  watchers.forEach((handler) => handler(key));
}

function subscribeWatchRecording(handler: WatchHandler) {
  watchers.add(handler);
  return () => {
    watchers.delete(handler);
  };
}

export function RecordingPlaybackHost() {
  const room = useRoomContext();
  const session = useMeetingSession();
  const [fileKey, setFileKey] = React.useState<string | null>(null);

  React.useEffect(() => subscribeWatchRecording(setFileKey), []);

  if (!fileKey || typeof document === 'undefined') {
    return null;
  }

  return createPortal(
    <RecordingPlayback
      roomName={room.name}
      fileKey={fileKey}
      hostKey={session?.hostKey}
      participantKey={session?.participantKey}
      onClose={() => setFileKey(null)}
    />,
    document.body,
  );
}

function RecordingPlayback({
  roomName,
  fileKey,
  hostKey,
  participantKey,
  onClose,
}: {
  roomName: string;
  fileKey: string;
  hostKey?: string | null;
  participantKey?: string | null;
  onClose: () => void;
}) {
  const videoRef = React.useRef<HTMLVideoElement>(null);
  const [src, setSrc] = React.useState('');
  const [error, setError] = React.useState('');
  const [paused, setPaused] = React.useState(false);

  React.useEffect(() => {
    let revoke: () => void = () => undefined;
    let cancelled = false;
    void loadRecordingSrc({
      roomName,
      key: fileKey,
      hostKey,
      participantKey,
    })
      .then((opened) => {
        if (cancelled) {
          opened.revoke?.();
          return;
        }
        revoke = opened.revoke ?? revoke;
        setSrc(opened.src);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Could not open that recording');
        }
      });
    return () => {
      cancelled = true;
      revoke();
    };
  }, [fileKey, hostKey, participantKey, roomName]);

  const toggle = () => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    if (video.paused) {
      void video.play();
      return;
    }
    video.pause();
  };

  return (
    <div className={styles.stage} role="dialog" aria-label="Recording">
      <button type="button" className={styles.close} onClick={onClose}>
        Close
      </button>
      {error ? <p className={styles.error}>{error}</p> : null}
      {src ? (
        <video
          ref={videoRef}
          className={styles.video}
          src={src}
          autoPlay
          playsInline
          onClick={toggle}
          onPlay={() => setPaused(false)}
          onPause={() => setPaused(true)}
        />
      ) : error ? null : (
        <p className={styles.error}>Opening recording…</p>
      )}
      {src && paused ? (
        <button type="button" className={styles.play} onClick={toggle}>
          Play
        </button>
      ) : null}
    </div>
  );
}
