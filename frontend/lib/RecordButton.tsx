'use client';

import { useIsRecording, useRoomContext } from '@livekit/components-react';
import React from 'react';
import toast from 'react-hot-toast';
import { useMeetingRole, useMeetingSession } from '@/lib/MeetingSession';
import { dismissRecordingBorder, followLiveRecording } from '@/lib/RecordingIndicator';
import { watchRecording } from '@/lib/RecordingPlayback';
import { listRecordings } from '@/lib/meetingApi';
import { recorderPageIsWarm, warmRecorderPage } from '@/lib/warmRecorderPage';
import styles from '@/styles/RecordButton.module.css';

const RECORDING_TOAST = 'room-recording';

export function RecordButton() {
  const room = useRoomContext();
  const session = useMeetingSession();
  const role = useMeetingRole();
  const liveRecording = useIsRecording();
  const canRecord = role === 'host' || role === 'cohost';
  const endpoint = process.env.NEXT_PUBLIC_LK_RECORD_ENDPOINT;
  const [pending, setPending] = React.useState(false);
  const [recording, setRecording] = React.useState(liveRecording);

  React.useEffect(() => {
    if (liveRecording) {
      setRecording(true);
    }
  }, [liveRecording]);

  React.useEffect(() => {
    if (!canRecord || !endpoint) {
      return;
    }
    void warmRecorderPage();
  }, [canRecord, endpoint]);

  if (!endpoint || !session || !canRecord) {
    return null;
  }

  const toggle = async () => {
    if (pending) {
      return;
    }
    if (room.isE2EEEnabled) {
      toast.error('Recording of encrypted meetings is currently not supported');
      return;
    }
    setPending(true);
    const action = recording ? 'stop' : 'start';
    if (action === 'stop') {
      toast('Saving recording…', { id: RECORDING_TOAST });
    }
    try {
      if (action === 'start' && !recorderPageIsWarm()) {
        toast('Preparing the recorder…', { id: RECORDING_TOAST });
        await warmRecorderPage();
      }
      const response = await fetch(`${endpoint}/${action}?roomName=${encodeURIComponent(room.name)}`, {
        method: 'POST',
        headers: {
          ...(session.hostKey ? { 'X-Host-Key': session.hostKey } : {}),
          ...(session.participantKey ? { 'X-Participant-Key': session.participantKey } : {}),
        },
      });
      const body = await readBody(response);
      const message = body.detail || '';
      if (response.ok && action === 'start') {
        setRecording(true);
        followLiveRecording();
        toast('This room is being recorded', { id: RECORDING_TOAST, duration: 4000 });
        return;
      }
      if (response.status === 409 && action === 'start') {
        setRecording(true);
        followLiveRecording();
        toast('This room is being recorded', { id: RECORDING_TOAST, duration: 4000 });
        return;
      }
      if (response.ok && action === 'stop') {
        setRecording(false);
        dismissRecordingBorder();
        const rows = Array.isArray(body.recordings) ? body.recordings : [];
        const failed = rows.find((row) => row.status === 'failed' || row.status === 'aborted');
        const saved = rows.find((row) => row.status === 'complete' && row.key);
        if (failed && !saved) {
          toast.error(failed.error || 'Recording failed', { id: RECORDING_TOAST });
          return;
        }
        const opened = await openStoppedRecording({
          roomName: room.name,
          hostKey: session.hostKey,
          participantKey: session.participantKey,
          key: saved?.key,
          egressId: saved?.egressId || rows[0]?.egressId,
        });
        if (opened) {
          toast.dismiss(RECORDING_TOAST);
          return;
        }
        toast.error('Recording was saved but could not be opened', { id: RECORDING_TOAST });
        return;
      }
      if (action === 'stop' && (response.status === 404 || /cannot be stopped/i.test(message))) {
        setRecording(false);
        toast.error('Recording failed');
        return;
      }
      toast.error(message || 'Could not update recording');
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error && error.message ? error.message : 'Could not update recording', {
        id: RECORDING_TOAST,
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <button
      type="button"
      className={`${styles.button} record-button`}
      onClick={() => void toggle()}
      disabled={pending}
      aria-pressed={recording}
    >
      <span className={recording ? styles.dotLive : styles.dot} aria-hidden="true" />
      {recording ? 'Stop recording' : 'Start record'}
    </button>
  );
}

async function openStoppedRecording(input: {
  roomName: string;
  hostKey?: string | null;
  participantKey?: string | null;
  key?: string;
  egressId?: string;
}) {
  let key = input.key || '';
  if (!key) {
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      const rows = await listRecordings({
        roomName: input.roomName,
        hostKey: input.hostKey,
        participantKey: input.participantKey,
      });
      const match = rows.find((row) => row.egressId === input.egressId) ?? rows[0];
      if (!match) {
        continue;
      }
      if (match.status === 'failed' || match.status === 'aborted') {
        throw new Error(match.error || 'Recording failed');
      }
      if (match.key && match.status === 'complete') {
        key = match.key;
        break;
      }
    }
  }
  if (!key) {
    return false;
  }
  watchRecording(key);
  return true;
}

async function readBody(response: Response): Promise<{
  detail?: string;
  recordings?: Array<{ status?: string; error?: string; key?: string; egressId?: string }>;
}> {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as {
      detail?: string;
      recordings?: Array<{ status?: string; error?: string; key?: string; egressId?: string }>;
    };
    if (parsed && typeof parsed === 'object') {
      return parsed;
    }
  } catch {
    return { detail: text };
  }
  return { detail: text };
}
