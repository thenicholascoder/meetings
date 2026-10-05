'use client';

import { useLocalParticipant, useParticipants, useRoomContext } from '@livekit/components-react';
import type { Participant } from 'livekit-client';
import React from 'react';
import {
  endMeeting,
  muteParticipant,
  removeParticipant,
  setParticipantRole,
  type MeetingRole,
} from '@/lib/meetingApi';
import { roleFromAttributes, useMeetingRole, useMeetingSession } from '@/lib/MeetingSession';
import styles from '@/styles/MeetingRoom.module.css';

const ROLE_RANK: Record<MeetingRole, number> = { host: 0, cohost: 1, participant: 2 };
const ROLE_LABEL: Record<MeetingRole, string> = {
  host: 'Host',
  cohost: 'Co-host',
  participant: '',
};

export function PeopleMenu({ showText }: { showText: boolean }) {
  const session = useMeetingSession();
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const participants = useParticipants();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState('');
  const [busyId, setBusyId] = React.useState('');
  const myRole = useMeetingRole();

  if (!session) {
    return null;
  }

  const ordered = [...participants].sort((a, b) => {
    const roleA = roleFromAttributes(a.attributes, 'participant');
    const roleB = roleFromAttributes(b.attributes, 'participant');
    return ROLE_RANK[roleA] - ROLE_RANK[roleB];
  });

  const run = async (identity: string, action: () => Promise<void>) => {
    setBusyId(identity);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update that person');
    } finally {
      setBusyId('');
    }
  };

  const hint =
    myRole === 'host'
      ? 'You started this meeting, so you are the host. You can admit people, assign co-hosts, remove or mute anyone, and end the meeting.'
      : myRole === 'cohost'
        ? 'You are a co-host. You can admit, mute, and remove participants. Only the host can assign co-hosts or end the meeting.'
        : 'The person who started the meeting is the host.';

  return (
    <>
      <button className="lk-button" type="button" aria-pressed={open} onClick={() => setOpen(true)}>
        <PeopleIcon />
        {showText && 'People'}
      </button>
      {open && (
        <div className={styles.backdrop} onClick={() => setOpen(false)}>
          <div
            className={`${styles.dialog} ${styles.people}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="people-title"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="people-title">People</h2>
            <p className={styles.hint}>{hint}</p>
            <ul className={styles.list}>
              {ordered.map((person) => (
                <PersonRow
                  key={person.identity}
                  person={person}
                  myRole={myRole}
                  isMe={person.identity === localParticipant.identity}
                  busy={busyId === person.identity}
                  invited={Boolean(person.attributes?.cohost_invite)}
                  onMakeCohost={() =>
                    run(person.identity, () =>
                      setParticipantRole({
                        roomName: session.roomName,
                        identity: person.identity,
                        role: 'cohost',
                        hostKey: session.hostKey,
                      }),
                    )
                  }
                  onRemoveCohost={() =>
                    run(person.identity, () =>
                      setParticipantRole({
                        roomName: session.roomName,
                        identity: person.identity,
                        role: 'participant',
                        hostKey: session.hostKey,
                      }),
                    )
                  }
                  onMute={() =>
                    run(person.identity, () =>
                      muteParticipant({
                        roomName: session.roomName,
                        identity: person.identity,
                        hostKey: session.hostKey,
                        participantKey: session.participantKey,
                      }),
                    )
                  }
                  onRemove={() =>
                    run(person.identity, () =>
                      removeParticipant({
                        roomName: session.roomName,
                        identity: person.identity,
                        hostKey: session.hostKey,
                        participantKey: session.participantKey,
                      }),
                    )
                  }
                />
              ))}
            </ul>
            {error ? <p className={styles.error}>{error}</p> : null}
            <div className={styles.actions}>
              {myRole === 'host' && (
                <button
                  className={styles.endButton}
                  type="button"
                  onClick={() => {
                    if (!window.confirm('End the meeting for everyone?')) {
                      return;
                    }
                    run('end', async () => {
                      await endMeeting({ roomName: session.roomName, hostKey: session.hostKey });
                      room.disconnect();
                    });
                  }}
                >
                  End meeting for everyone
                </button>
              )}
              <button className={`lk-button ${styles.secondary}`} type="button" onClick={() => setOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function PersonRow(props: {
  person: Participant;
  myRole: MeetingRole;
  isMe: boolean;
  busy: boolean;
  invited: boolean;
  onMakeCohost: () => void;
  onRemoveCohost: () => void;
  onMute: () => void;
  onRemove: () => void;
}) {
  const role = props.isMe ? props.myRole : roleFromAttributes(props.person.attributes, 'participant');
  const label = ROLE_LABEL[role];
  const canManage = props.myRole === 'host' || props.myRole === 'cohost';
  const canRemove =
    canManage &&
    !props.isMe &&
    role !== 'host' &&
    !(props.myRole === 'cohost' && role === 'cohost');

  return (
    <li className={styles.person}>
      <div className={styles.personName}>
        <span>{props.person.name || props.person.identity}</span>
        {props.isMe ? <span className={styles.badge}>You</span> : null}
        {label ? <span className={styles.badge}>{label}</span> : null}
      </div>
      {canManage && !props.isMe ? (
        <div className={styles.rowActions}>
          {props.myRole === 'host' && role === 'participant' && !props.invited ? (
            <button className="lk-button" type="button" disabled={props.busy} onClick={props.onMakeCohost}>
              Make co-host
            </button>
          ) : null}
          {props.myRole === 'host' && role === 'participant' && props.invited ? (
            <button className="lk-button" type="button" disabled title="Waiting for them to accept">
              Invited
            </button>
          ) : null}
          {props.myRole === 'host' && role === 'cohost' ? (
            <button className="lk-button" type="button" disabled={props.busy} onClick={props.onRemoveCohost}>
              Remove co-host
            </button>
          ) : null}
          <button className="lk-button" type="button" disabled={props.busy} onClick={props.onMute}>
            Mute
          </button>
          {canRemove ? (
            <button className="lk-button" type="button" disabled={props.busy} onClick={props.onRemove}>
              Remove
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function PeopleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M9 11a3.5 3.5 0 1 0-3.5-3.5A3.5 3.5 0 0 0 9 11zm6.5 1a3 3 0 1 0-3-3 3 3 0 0 0 3 3zM9 13c-3.3 0-6 1.6-6 3.5V19h8.2a6.4 6.4 0 0 1-.2-1.5A5.4 5.4 0 0 1 12.6 13zm6.5 1c-.5 0-1 .1-1.5.2 1.5.9 2.5 2.2 2.5 3.3V19H22v-1.5c0-1.9-2.7-3.5-6.5-3.5z" />
    </svg>
  );
}
