'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { encodePassphrase, generateRoomId, randomString } from '@/lib/client-utils';
import { createMeeting, saveHostKey } from '@/lib/meetingApi';
import styles from '../styles/Home.module.css';

type HomeTab = 'host' | 'join' | 'schedule';

type ScheduledRoom = {
  id: number;
  title: string;
  room_name: string;
  starts_at: string;
  duration_minutes: number;
  max_participants: number;
  notes: string;
};

function Tabs(props: { tab: HomeTab; onTab: (tab: HomeTab) => void; children: React.ReactNode }) {
  const labels: HomeTab[] = ['host', 'join', 'schedule'];
  const childArray = React.Children.toArray(props.children);
  const index = labels.indexOf(props.tab);
  return (
    <div className={styles.tabContainer}>
      <div className={styles.tabSelect}>
        {labels.map((label) => (
          <button
            key={label}
            className="lk-button"
            onClick={() => props.onTab(label)}
            aria-pressed={props.tab === label}
            type="button"
          >
            {label.charAt(0).toUpperCase() + label.slice(1)}
          </button>
        ))}
      </div>
      {childArray[index]}
    </div>
  );
}

function E2eeFields(props: {
  e2ee: boolean;
  setE2ee: (value: boolean) => void;
  passphrase: string;
  setPassphrase: (value: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', flexDirection: 'row', gap: '1rem' }}>
        <input
          id="use-e2ee"
          type="checkbox"
          checked={props.e2ee}
          onChange={(ev) => props.setE2ee(ev.target.checked)}
        />
        <label htmlFor="use-e2ee">Enable end-to-end encryption</label>
      </div>
      {props.e2ee && (
        <div style={{ display: 'flex', flexDirection: 'row', gap: '1rem' }}>
          <label htmlFor="passphrase">Passphrase</label>
          <input
            id="passphrase"
            type="password"
            value={props.passphrase}
            onChange={(ev) => props.setPassphrase(ev.target.value)}
          />
        </div>
      )}
    </div>
  );
}

function HostPanel() {
  const router = useRouter();
  const [e2ee, setE2ee] = useState(false);
  const [sharedPassphrase, setSharedPassphrase] = useState(randomString(64));
  const [starting, setStarting] = useState(false);
  const startMeeting = async () => {
    if (starting) {
      return;
    }
    setStarting(true);
    const roomName = generateRoomId();
    try {
      const created = await createMeeting(roomName);
      saveHostKey(created.roomName, created.hostKey);
      const hash = e2ee ? `#${encodePassphrase(sharedPassphrase)}` : '';
      router.push(`/rooms/${created.roomName}${hash}`);
    } catch (error) {
      setStarting(false);
      alert(error instanceof Error ? error.message : 'Could not start the meeting');
    }
  };
  return (
    <div className={styles.tabContent}>
      <p style={{ margin: 0 }}>
        {/* FOR PRODUCTION - users are unique on this browser, so every tab is the same host
        Starting a meeting makes you the host on this browser. Share the room link for everyone else.
        They wait until you accept them. */}
        {/* FOR TESTING - open the room link in another tab to join as a guest on this laptop */}
        Starting a meeting makes you the host in this tab. Open the room link in another tab to join
        as someone else. They wait until you accept them.
      </p>
      <button
        style={{ marginTop: '1rem' }}
        className="lk-button"
        type="button"
        disabled={starting}
        onClick={startMeeting}
      >
        {starting ? 'Starting…' : 'Start Meeting'}
      </button>
      <E2eeFields
        e2ee={e2ee}
        setE2ee={setE2ee}
        passphrase={sharedPassphrase}
        setPassphrase={setSharedPassphrase}
      />
    </div>
  );
}

function JoinPanel() {
  const router = useRouter();
  const [roomName, setRoomName] = useState('');
  const [e2ee, setE2ee] = useState(false);
  const [sharedPassphrase, setSharedPassphrase] = useState(randomString(64));

  const onSubmit: React.FormEventHandler<HTMLFormElement> = (event) => {
    event.preventDefault();
    const slug = roomName.trim().toLowerCase();
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
      alert('Room name must be lowercase letters, numbers, and dashes (LiveKit room name).');
      return;
    }
    if (e2ee) {
      router.push(`/rooms/${encodeURIComponent(slug)}#${encodePassphrase(sharedPassphrase)}`);
    } else {
      router.push(`/rooms/${encodeURIComponent(slug)}`);
    }
  };

  return (
    <form className={styles.tabContent} onSubmit={onSubmit}>
      {/* <p style={{ margin: 0 }}>
        Tokens stay on the Django TokenSource endpoint.
      </p> */}
      <label htmlFor="join-room-name">Room name</label>
      <input
        id="join-room-name"
        name="roomName"
        type="text"
        autoComplete="off"
        placeholder="e.g. 4k2n-9abx"
        required
        value={roomName}
        onChange={(ev) => setRoomName(ev.target.value)}
      />
      <E2eeFields
        e2ee={e2ee}
        setE2ee={setE2ee}
        passphrase={sharedPassphrase}
        setPassphrase={setSharedPassphrase}
      />
      <button className="lk-button" type="submit" style={{ width: '100%' }}>
        Join
      </button>
    </form>
  );
}

function SchedulePanel() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [roomName, setRoomName] = useState('');
  const [startsAt, setStartsAt] = useState('');
  const [durationMinutes, setDurationMinutes] = useState(30);
  const [maxParticipants, setMaxParticipants] = useState(0);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [items, setItems] = useState<ScheduledRoom[]>([]);

  const load = useCallback(async () => {
    const response = await fetch('/api/schedules');
    if (!response.ok) {
      setError(await response.text());
      return;
    }
    setItems(await response.json());
  }, []);

  useEffect(() => {
    load().catch((err) => setError(String(err)));
  }, [load]);

  const onSubmit: React.FormEventHandler<HTMLFormElement> = async (event) => {
    event.preventDefault();
    setError('');
    const slug = (roomName.trim() || generateRoomId()).toLowerCase();
    const response = await fetch('/api/schedules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title.trim(),
        room_name: slug,
        starts_at: new Date(startsAt).toISOString(),
        duration_minutes: durationMinutes,
        max_participants: maxParticipants,
        notes: notes.trim(),
      }),
    });
    if (!response.ok) {
      setError(await response.text());
      return;
    }
    const created = await response.json();
    if (typeof created.host_key === 'string' && created.host_key && typeof created.room_name === 'string') {
      saveHostKey(created.room_name, created.host_key);
    }
    setTitle('');
    setRoomName('');
    setNotes('');
    await load();
  };

  return (
    <div className={styles.tabContent}>
      <p style={{ margin: 0 }}>
        {/* FOR PRODUCTION - users are unique on this browser, so every tab is the same host
        Saving a schedule makes you the host on this browser. Everyone else who opens the
        room waits until you or a co-host accepts them. */}
        {/* FOR TESTING - open the room in another tab to join as a guest on this laptop */}
        Saving a schedule makes you the host in this tab. Everyone else who opens the
        room in another tab waits until you or a co-host accepts them.
      </p>
      <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <label htmlFor="sched-title">Title</label>
        <input
          id="sched-title"
          required
          value={title}
          onChange={(ev) => setTitle(ev.target.value)}
          placeholder="Weekly standup"
        />
        <label htmlFor="sched-room">Room name (optional, generated if empty)</label>
        <input
          id="sched-room"
          value={roomName}
          onChange={(ev) => setRoomName(ev.target.value)}
          placeholder="standup-thursday"
        />
        <label htmlFor="sched-start">Starts at</label>
        <input
          id="sched-start"
          type="datetime-local"
          required
          value={startsAt}
          onChange={(ev) => setStartsAt(ev.target.value)}
        />
        <label htmlFor="sched-duration">Duration (minutes)</label>
        <input
          id="sched-duration"
          type="number"
          min={5}
          required
          value={durationMinutes}
          onChange={(ev) => setDurationMinutes(Number(ev.target.value))}
        />
        <label htmlFor="sched-max">Max participants (0 = unlimited)</label>
        <input
          id="sched-max"
          type="number"
          min={0}
          value={maxParticipants}
          onChange={(ev) => setMaxParticipants(Number(ev.target.value))}
        />
        <label htmlFor="sched-notes">Notes</label>
        <textarea
          id="sched-notes"
          rows={3}
          value={notes}
          onChange={(ev) => setNotes(ev.target.value)}
        />
        <button className="lk-button" type="submit" style={{ width: '100%' }}>
          Save schedule
        </button>
      </form>
      {error && <p style={{ color: 'var(--lk-danger, #ff6352)', margin: 0 }}>{error}</p>}
      <hr style={{ width: '100%', borderColor: 'rgba(255, 255, 255, 0.15)' }} />
      <p style={{ margin: 0 }}>Upcoming</p>
      {items.length === 0 ? (
        <p style={{ margin: 0, opacity: 0.7 }}>No scheduled rooms yet.</p>
      ) : (
        items.map((item) => (
          <div key={item.id} style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
            <strong>{item.title}</strong>
            <span>
              {item.room_name} · {new Date(item.starts_at).toLocaleString()} · {item.duration_minutes}m
            </span>
            <button
              className="lk-button"
              type="button"
              onClick={() => router.push(`/rooms/${encodeURIComponent(item.room_name)}`)}
            >
              Join (PreJoin)
            </button>
          </div>
        ))
      )}
    </div>
  );
}

function HomeTabs() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const raw = searchParams?.get('tab');
  const tab: HomeTab = raw === 'join' || raw === 'schedule' ? raw : 'host';

  function onTab(next: HomeTab) {
    router.push(`/?tab=${next}`);
  }

  return (
    <Tabs tab={tab} onTab={onTab}>
      <HostPanel />
      <JoinPanel />
      <SchedulePanel />
    </Tabs>
  );
}

export default function Page() {
  return (
    <>
      <main className={styles.main} data-lk-theme="default">
        <div className="header">
          {/* <img src="/images/livekit-meet-home.svg" alt="LiveKit Meet" width="360" height="45" /> */}
          <h2>
            MEETINGS
          </h2>
        </div>
        <Suspense fallback="Loading">
          <HomeTabs />
        </Suspense>
      </main>
      <footer data-lk-theme="default">
        Self-hosted with Docker.
      </footer>
    </>
  );
}
