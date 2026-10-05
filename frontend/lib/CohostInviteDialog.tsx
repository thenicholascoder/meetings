'use client';

import { useLocalParticipant } from '@livekit/components-react';
import React from 'react';
import {
  acceptCohostInvite,
  declineCohostInvite,
  myCohostInvite,
  type CohostInvite,
} from '@/lib/meetingApi';
import { useMeetingRole, useMeetingSession } from '@/lib/MeetingSession';
import styles from '@/styles/MeetingRoom.module.css';

/**
 * The host's "Make co-host" only sends an invitation. This person stays a
 * participant until they accept. Decline leaves the title off.
 */
export function CohostInviteDialog() {
  const session = useMeetingSession();
  const { localParticipant } = useLocalParticipant();
  const role = useMeetingRole();
  const canAnswer = role === 'participant';
  const attributeId = localParticipant.attributes?.cohost_invite || '';
  const [invite, setInvite] = React.useState<CohostInvite | null>(null);
  const [heardFromServer, setHeardFromServer] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const answered = React.useRef(new Set<string>());

  React.useEffect(() => {
    if (!session || !canAnswer || !attributeId) {
      setInvite(null);
      return;
    }
    let cancelled = false;
    setHeardFromServer(false);
    const load = async () => {
      try {
        const pending = await myCohostInvite({
          roomName: session.roomName,
          participantKey: session.participantKey,
        });
        if (cancelled) {
          return;
        }
        setHeardFromServer(true);
        if (pending && answered.current.has(pending.id)) {
          setInvite(null);
        } else {
          setInvite(pending);
        }
        setError('');
      } catch (err) {
        if (!cancelled) {
          console.error(err);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [attributeId, canAnswer, session]);

  const pendingInvite =
    invite ??
    (!heardFromServer && attributeId && !answered.current.has(attributeId)
      ? { id: attributeId, hostName: 'The host' }
      : null);

  if (!session || !canAnswer || !pendingInvite) {
    return null;
  }

  const decide = async (accept: boolean) => {
    if (!pendingInvite || busy) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const input = {
        roomName: session.roomName,
        inviteId: pendingInvite.id,
        participantKey: session.participantKey,
      };
      if (accept) {
        await acceptCohostInvite(input);
        session.setRole?.('cohost');
      } else {
        await declineCohostInvite(input);
      }
      answered.current.add(pendingInvite.id);
      setInvite(null);
      setHeardFromServer(true);
    } catch (err) {
      const finished = err instanceof Error && 'status' in err && (err as { status: number }).status === 409;
      if (finished) {
        answered.current.add(pendingInvite.id);
        setInvite(null);
        setHeardFromServer(true);
      } else {
        setError(err instanceof Error ? err.message : 'Could not answer that invitation');
      }
    } finally {
      setBusy(false);
    }
  };

  const hostName = pendingInvite.hostName || 'The host';

  return (
    <div className={styles.backdrop}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="cohost-invite-title">
        <h2 id="cohost-invite-title">{hostName} wants you to be a co-host</h2>
        <p className={styles.hint}>
          If you accept, you can admit people, share your screen, and share slides. If you decline, you stay a
          participant.
        </p>
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
      </div>
    </div>
  );
}
