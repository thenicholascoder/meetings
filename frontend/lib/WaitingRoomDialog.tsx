'use client';

import { useRoomContext } from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';
import React from 'react';
import {
  acceptWaiting,
  declineWaiting,
  listWaiting,
  type WaitingRequest,
} from '@/lib/meetingApi';
import { useMeetingRole, useMeetingSession } from '@/lib/MeetingSession';
import styles from '@/styles/MeetingRoom.module.css';

function waitingCount(metadata: string | undefined) {
  if (!metadata) {
    return 0;
  }
  try {
    const parsed = JSON.parse(metadata) as { waiting?: unknown };
    return typeof parsed.waiting === 'number' ? parsed.waiting : 0;
  } catch {
    return 0;
  }
}

/**
 * Host and co-host see one knock at a time. The joiner stays on the pre-join
 * screen until Accept. Decline sends them back to the Join meeting button.
 */
export function WaitingRoomDialog() {
  const session = useMeetingSession();
  const room = useRoomContext();
  const role = useMeetingRole();
  const canAdmit = role === 'host' || role === 'cohost';
  const [requests, setRequests] = React.useState<WaitingRequest[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const handled = React.useRef(new Set<string>());
  const seenCount = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (!session || !canAdmit) {
      return;
    }
    let cancelled = false;

    const load = async () => {
      try {
        const waiting = await listWaiting({
          roomName: session.roomName,
          hostKey: session.hostKey,
          participantKey: session.participantKey,
        });
        if (cancelled) {
          return;
        }
        setRequests(waiting.filter((item) => item.id && !handled.current.has(item.id)));
        setError('');
      } catch (err) {
        if (!cancelled) {
          console.error(err);
        }
      }
    };

    const onMetadata = (metadata?: string) => {
      const count = waitingCount(metadata ?? room.metadata);
      if (seenCount.current === count) {
        return;
      }
      seenCount.current = count;
      if (count === 0) {
        setRequests([]);
        return;
      }
      void load();
    };

    seenCount.current = null;
    void load();
    room.on(RoomEvent.RoomMetadataChanged, onMetadata);
    return () => {
      cancelled = true;
      room.off(RoomEvent.RoomMetadataChanged, onMetadata);
    };
  }, [canAdmit, room, session]);

  if (!session || !canAdmit) {
    return null;
  }

  const current = requests[0];
  if (!current) {
    return null;
  }

  const decide = async (accept: boolean) => {
    if (!current || busy) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const input = {
        roomName: session.roomName,
        requestId: current.id,
        hostKey: session.hostKey,
        participantKey: session.participantKey,
      };
      if (accept) {
        await acceptWaiting(input);
      } else {
        await declineWaiting(input);
      }
      handled.current.add(current.id);
      setRequests((prev) => prev.filter((item) => item.id !== current.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update that request');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.backdrop}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="waiting-room-title"
      >
        {current ? (
          <>
            <h2 id="waiting-room-title">{current.name} wants to join your meeting</h2>
            {requests.length > 1 ? (
              <p className={styles.queue}>
                {requests.length - 1} more {requests.length - 1 === 1 ? 'person is' : 'people are'} waiting
              </p>
            ) : null}
            {error ? <p className={styles.error}>{error}</p> : null}
            <div className={styles.actions}>
              <button
                className={`lk-button ${styles.secondary}`}
                type="button"
                disabled={busy}
                onClick={() => decide(false)}
              >
                Decline
              </button>
              <button className="lk-button" type="button" disabled={busy} autoFocus onClick={() => decide(true)}>
                Accept
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
