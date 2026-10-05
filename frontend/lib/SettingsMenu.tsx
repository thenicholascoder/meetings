'use client';
import * as React from 'react';
import {
  useMaybeLayoutContext,
  MediaDeviceMenu,
  useRoomContext,
  useIsRecording,
} from '@livekit/components-react';
import styles from '../styles/SettingsMenu.module.css';
import { CameraSettings } from './CameraSettings';
import { MicrophoneSettings } from './MicrophoneSettings';
import { useMeetingSession } from './MeetingSession';
import { listRecordings, openRecording, type RoomRecording } from './meetingApi';
import { watchRecording } from '@/lib/RecordingPlayback';
/**
 * @alpha
 */
export interface SettingsMenuProps extends React.HTMLAttributes<HTMLDivElement> {}

/**
 * @alpha
 */
export function SettingsMenu(props: SettingsMenuProps) {
  const layoutContext = useMaybeLayoutContext();
  const room = useRoomContext();
  const session = useMeetingSession();
  const recordingEndpoint = process.env.NEXT_PUBLIC_LK_RECORD_ENDPOINT;

  const settings = React.useMemo(() => {
    return {
      media: { camera: true, microphone: true, label: 'Media Devices', speaker: true },
      recording: recordingEndpoint ? { label: 'Recording' } : undefined,
    };
  }, []);

  const tabs = React.useMemo(
    () => Object.keys(settings).filter((t) => t !== undefined) as Array<keyof typeof settings>,
    [settings],
  );
  const [activeTab, setActiveTab] = React.useState(tabs[0]);

  const isRecording = useIsRecording();
  const [recordingError, setRecordingError] = React.useState('');
  const [recordings, setRecordings] = React.useState<RoomRecording[]>([]);

  const refreshRecordings = React.useCallback(async () => {
    if (!session?.roomName) {
      return;
    }
    try {
      const rows = await listRecordings({
        roomName: session.roomName,
        hostKey: session.hostKey,
        participantKey: session.participantKey,
      });
      setRecordings(rows);
    } catch (error) {
      console.error(error);
    }
  }, [session?.hostKey, session?.participantKey, session?.roomName]);

  React.useEffect(() => {
    if (activeTab === 'recording') {
      void refreshRecordings();
    }
  }, [activeTab, refreshRecordings, isRecording]);

  return (
    <div className="settings-menu" style={{ width: '100%', position: 'relative' }} {...props}>
      <div className={styles.tabs}>
        {tabs.map(
          (tab) =>
            settings[tab] && (
              <button
                className={`${styles.tab} lk-button`}
                key={tab}
                onClick={() => setActiveTab(tab)}
                aria-pressed={tab === activeTab}
              >
                {
                  // @ts-ignore
                  settings[tab].label
                }
              </button>
            ),
        )}
      </div>
      <div className="tab-content">
        {activeTab === 'media' && (
          <>
            {settings.media && settings.media.camera && (
              <>
                <h3>Camera</h3>
                <section>
                  <CameraSettings />
                </section>
              </>
            )}
            {settings.media && settings.media.microphone && (
              <>
                <h3>Microphone</h3>
                <section>
                  <MicrophoneSettings />
                </section>
              </>
            )}
            {settings.media && settings.media.speaker && (
              <>
                <h3>Speaker & Headphones</h3>
                <section className="lk-button-group">
                  <span className="lk-button">Audio Output</span>
                  <div className="lk-button-group-menu">
                    <MediaDeviceMenu kind="audiooutput"></MediaDeviceMenu>
                  </div>
                </section>
              </>
            )}
          </>
        )}
        {activeTab === 'recording' && (
          <>
            <h3>Recordings</h3>
            <section>
              <p>The host starts and stops recording from the button at the top right.</p>
              {recordingError ? <p>{recordingError}</p> : null}
              {recordings.length > 0 && (
                <ul>
                  {recordings.map((item) => (
                    <li key={item.egressId}>
                      {item.status}
                      {item.startedAt ? ` · ${item.startedAt}` : ''}
                      {item.key && item.status === 'complete' ? (
                        <>
                          {' '}
                          <button type="button" onClick={() => watchRecording(item.key)}>
                            Watch
                          </button>
                          {' '}
                          <button
                            type="button"
                            onClick={() =>
                              void openRecording({
                                roomName: room.name,
                                key: item.key,
                                hostKey: session?.hostKey,
                                participantKey: session?.participantKey,
                              }).catch((error) => setRecordingError(String(error.message || error)))
                            }
                          >
                            Download
                          </button>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', width: '100%' }}>
        <button
          className={`lk-button`}
          onClick={() => layoutContext?.widget.dispatch?.({ msg: 'toggle_settings' })}
        >
          Close
        </button>
      </div>
    </div>
  );
}
