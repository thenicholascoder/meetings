'use client';

import { RoomEvent, Track, type Participant } from 'livekit-client';
import * as React from 'react';
import {
  ChatToggle,
  DisconnectButton,
  MediaDeviceMenu,
  StartMediaButton,
  TrackToggle,
  useLocalParticipantPermissions,
  useMaybeLayoutContext,
  usePersistentUserChoices,
  useParticipants,
  useRoomContext,
  useTrackToggle,
} from '@livekit/components-react';
import { meetingScreenShareCapture } from '@/lib/conferenceMedia';
import { PeopleMenu } from '@/lib/PeopleMenu';
import { useMeetingRole } from '@/lib/MeetingSession';
import { ShareSlidesButton } from '@/lib/slides/ShareSlidesButton';
import { useSlides } from '@/lib/slides/SlidesContext';

const trackSourceToProtocol = (source: Track.Source) => {
  switch (source) {
    case Track.Source.Camera:
      return 1;
    case Track.Source.Microphone:
      return 2;
    case Track.Source.ScreenShare:
      return 3;
    default:
      return 0;
  }
};

function browserSupportsScreenSharing() {
  return (
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getDisplayMedia === 'function'
  );
}

function useMatchMedia(query: string) {
  const [matches, setMatches] = React.useState(false);
  React.useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}

function participantIsSharingScreen(person: Participant) {
  return [...person.trackPublications.values()].some(
    (publication) => publication.source === Track.Source.ScreenShare && !publication.isMuted,
  );
}

function slidesFlag(metadata: string) {
  if (!metadata) {
    return false;
  }
  try {
    const parsed = JSON.parse(metadata) as { slides?: unknown };
    return parsed.slides === true;
  } catch {
    return false;
  }
}

function useRoomSlidesFlag() {
  const room = useRoomContext();
  const [active, setActive] = React.useState(() => slidesFlag(room.metadata ?? ''));
  React.useEffect(() => {
    const apply = (metadata?: string) => setActive(slidesFlag(metadata ?? ''));
    const applyCurrent = () => apply(room.metadata);
    applyCurrent();
    room.on(RoomEvent.RoomMetadataChanged, apply);
    room.on(RoomEvent.Connected, applyCurrent);
    return () => {
      room.off(RoomEvent.RoomMetadataChanged, apply);
      room.off(RoomEvent.Connected, applyCurrent);
    };
  }, [room]);
  return active;
}

function usePublishedScreenShare(ignored: ReadonlySet<string>) {
  const room = useRoomContext();
  const read = React.useCallback(() => {
    const people = [room.localParticipant, ...room.remoteParticipants.values()];
    return people.some(
      (person) => !ignored.has(person.identity) && participantIsSharingScreen(person),
    );
  }, [ignored, room]);
  const [active, setActive] = React.useState(false);
  React.useEffect(() => {
    const scan = () => setActive(read());
    scan();
    const events = [
      RoomEvent.Connected,
      RoomEvent.ParticipantConnected,
      RoomEvent.ParticipantDisconnected,
      RoomEvent.TrackPublished,
      RoomEvent.TrackUnpublished,
      RoomEvent.TrackMuted,
      RoomEvent.TrackUnmuted,
      RoomEvent.LocalTrackPublished,
      RoomEvent.LocalTrackUnpublished,
    ] as const;
    events.forEach((event) => room.on(event, scan));
    return () => {
      events.forEach((event) => room.off(event, scan));
    };
  }, [read, room]);
  return active;
}

/**
 * LiveKit's control bar, with Share slides placed directly after Share screen.
 */
export function MeetingControlBar({
  saveUserChoices = true,
  controls,
}: {
  saveUserChoices?: boolean;
  controls?: { chat?: boolean; settings?: boolean };
}) {
  // The bar stays full width under the stage and the chat column.
  const isTooLittleSpace = useMatchMedia('(max-width: 760px)');
  const showText = !isTooLittleSpace;
  const [canScreenShare, setCanScreenShare] = React.useState(false);
  React.useEffect(() => {
    setCanScreenShare(browserSupportsScreenSharing());
  }, []);

  const visibleControls = {
    leave: true,
    microphone: true as boolean,
    camera: true as boolean,
    screenShare: true as boolean,
    chat: controls?.chat,
    settings: controls?.settings,
  };

  const {
    deck,
    preparing,
    uploading,
    converting,
    isOwner,
    presentationReleased,
    noteSlidesQuiet,
  } = useSlides();
  const room = useRoomContext();
  const roomSlides = useRoomSlidesFlag();
  const participants = useParticipants();
  // Set on everyone in the room when a deck is shared, so a joiner sees it
  // with the participant list and does not have to wait for the slide file.
  const slidesAnnounced = participants.some((person) => person.attributes?.slides === '1');
  const staleSlideHints = roomSlides || slidesAnnounced;
  // After a stop, room metadata, the slides attribute, and the deck poll stay
  // true until LiveKit and the next request catch up. Ignore them so both
  // share buttons enable on the click.
  const slidesActive =
    Boolean(deck) ||
    Boolean(preparing) ||
    uploading ||
    converting ||
    (!presentationReleased && staleSlideHints);
  React.useEffect(() => {
    if (presentationReleased && !deck && !preparing && !uploading && !converting && !staleSlideHints) {
      noteSlidesQuiet();
    }
  }, [converting, deck, noteSlidesQuiet, preparing, presentationReleased, staleSlideHints, uploading]);
  const [localScreenStopped, setLocalScreenStopped] = React.useState(false);
  const [ignoredSharers, setIgnoredSharers] = React.useState<ReadonlySet<string>>(() => new Set());
  const screenShareCapture = React.useMemo(() => meetingScreenShareCapture(), []);
  const screenShare = useTrackToggle({
    source: Track.Source.ScreenShare,
    captureOptions: screenShareCapture,
  });
  const publishedScreenShare = usePublishedScreenShare(ignoredSharers);
  // A joiner receives the host's screen-share publication before its video
  // track. The publication alone is enough to know someone is presenting.
  // Stopping locally drops that publication immediately instead of waiting
  // for LiveKit to unpublish the track.
  const startingScreen = screenShare.pending && !screenShare.enabled;
  const screenShareActive =
    startingScreen || (screenShare.enabled && !localScreenStopped) || publishedScreenShare;
  const someonePresenting = slidesActive || screenShareActive;
  const iAmSharingSlides = isOwner && Boolean(deck);
  const iAmSharingScreen = screenShare.enabled && !localScreenStopped;
  const myRole = useMeetingRole();
  const canPresent = myRole === 'host' || myRole === 'cohost';
  const localPermissions = useLocalParticipantPermissions();
  const publishSources = localPermissions?.canPublishSources ?? [];
  const canPublishSource = (source: Track.Source) => {
    return (
      !!localPermissions?.canPublish &&
      (publishSources.length === 0 || publishSources.includes(trackSourceToProtocol(source)))
    );
  };
  // While slides are up, the host and co-hosts keep Share screen visible and
  // disabled. A participant never receives screen share in their token.
  const screenShareRevoked =
    !!localPermissions?.canPublish &&
    publishSources.length > 0 &&
    !publishSources.includes(trackSourceToProtocol(Track.Source.ScreenShare));
  // Anyone who is not the current presenter gets both buttons disabled.
  // A normal participant never sees them, and an in-progress share stops.
  const blockScreenShare =
    !canPresent || (someonePresenting && !iAmSharingScreen) || (screenShareRevoked && slidesActive);
  const blockSlides = someonePresenting && !iAmSharingSlides;
  if (!localPermissions) {
    visibleControls.camera = false;
    visibleControls.chat = false;
    visibleControls.microphone = false;
    visibleControls.screenShare = false;
  } else {
    visibleControls.microphone = canPublishSource(Track.Source.Microphone);
    visibleControls.camera = canPublishSource(Track.Source.Camera);
    visibleControls.screenShare =
      canPresent && (canPublishSource(Track.Source.ScreenShare) || blockScreenShare);
    visibleControls.chat = localPermissions.canPublishData && controls?.chat;
  }

  React.useEffect(() => {
    if (blockScreenShare && screenShare.enabled && !screenShare.pending && !localScreenStopped) {
      void screenShare.toggle(false);
    }
  }, [blockScreenShare, localScreenStopped, screenShare.enabled, screenShare.pending, screenShare.toggle]);

  const beginScreenStop = React.useCallback(() => {
    const identity = room.localParticipant.identity;
    setLocalScreenStopped(true);
    setIgnoredSharers((current) => {
      if (current.has(identity)) {
        return current;
      }
      const next = new Set(current);
      next.add(identity);
      return next;
    });
    const data = new TextEncoder().encode(JSON.stringify({ type: 'screen-stop', identity }));
    void room.localParticipant
      .publishData(data, { reliable: true, topic: 'presentation' })
      .catch((error) => {
        console.error(error);
      });
  }, [room]);

  React.useEffect(() => {
    const onData = (
      payload: Uint8Array,
      participant?: { identity: string },
      _kind?: unknown,
      topic?: string,
    ) => {
      if (topic !== 'presentation') {
        return;
      }
      try {
        const message = JSON.parse(new TextDecoder().decode(payload)) as { type?: string; identity?: string };
        if (message.type !== 'screen-stop' || !message.identity) {
          return;
        }
        if (participant && participant.identity !== message.identity) {
          return;
        }
        const identity = message.identity;
        setIgnoredSharers((current) => {
          if (current.has(identity)) {
            return current;
          }
          const next = new Set(current);
          next.add(identity);
          return next;
        });
      } catch {
        /* Ignore a payload that is not a presentation message. */
      }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  React.useEffect(() => {
    setIgnoredSharers((current) => {
      if (current.size === 0) {
        return current;
      }
      let changed = false;
      const next = new Set<string>();
      current.forEach((identity) => {
        if (localScreenStopped && identity === room.localParticipant.identity) {
          next.add(identity);
          return;
        }
        const person = participants.find((item) => item.identity === identity);
        if (person && participantIsSharingScreen(person)) {
          next.add(identity);
        } else {
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [localScreenStopped, participants, room]);

  React.useEffect(() => {
    if (!localScreenStopped || screenShare.enabled || screenShare.pending) {
      return;
    }
    const local = participants.find((person) => person.isLocal);
    if (local && participantIsSharingScreen(local)) {
      return;
    }
    setLocalScreenStopped(false);
  }, [localScreenStopped, participants, screenShare.enabled, screenShare.pending]);

  const {
    saveAudioInputEnabled,
    saveVideoInputEnabled,
    saveAudioInputDeviceId,
    saveVideoInputDeviceId,
  } = usePersistentUserChoices({ preventSave: !saveUserChoices });

  const microphoneOnChange = React.useCallback(
    (enabled: boolean, isUserInitiated: boolean) =>
      isUserInitiated ? saveAudioInputEnabled(enabled) : null,
    [saveAudioInputEnabled],
  );
  const cameraOnChange = React.useCallback(
    (enabled: boolean, isUserInitiated: boolean) =>
      isUserInitiated ? saveVideoInputEnabled(enabled) : null,
    [saveVideoInputEnabled],
  );

  return (
    <div className="lk-control-bar">
      {visibleControls.microphone && (
        <div className="lk-button-group">
          <TrackToggle
            source={Track.Source.Microphone}
            showIcon
            onChange={microphoneOnChange}
          >
            {showText && 'Microphone'}
          </TrackToggle>
          <div className="lk-button-group-menu">
            <MediaDeviceMenu
              kind="audioinput"
              onActiveDeviceChange={(_kind, deviceId) => saveAudioInputDeviceId(deviceId ?? 'default')}
            />
          </div>
        </div>
      )}
      {visibleControls.camera && (
        <div className="lk-button-group">
          <TrackToggle source={Track.Source.Camera} showIcon onChange={cameraOnChange}>
            {showText && 'Camera'}
          </TrackToggle>
          <div className="lk-button-group-menu">
            <MediaDeviceMenu
              kind="videoinput"
              onActiveDeviceChange={(_kind, deviceId) => saveVideoInputDeviceId(deviceId ?? 'default')}
            />
          </div>
        </div>
      )}
      {visibleControls.screenShare && canScreenShare && (
        <ScreenShareControl
          showText={showText}
          locked={blockScreenShare}
          stopping={localScreenStopped}
          onStop={beginScreenStop}
          toggle={screenShare}
        />
      )}
      {canPresent && localPermissions?.canPublishData && (
        <ShareSlidesButton showText={showText} locked={blockSlides} />
      )}
      <PeopleMenu showText={showText} />
      {visibleControls.chat && (
        <ChatToggle>
          <ChatIcon />
          {showText && 'Chat'}
        </ChatToggle>
      )}
      {visibleControls.settings && (
        <SettingsButton showText={showText} />
      )}
      {visibleControls.leave && (
        <DisconnectButton>
          <LeaveIcon />
          {showText && 'Leave'}
        </DisconnectButton>
      )}
      <StartMediaButton />
    </div>
  );
}

function ScreenShareControl({
  showText,
  locked,
  stopping,
  onStop,
  toggle,
}: {
  showText: boolean;
  locked: boolean;
  stopping: boolean;
  onStop: () => void;
  toggle: {
    enabled: boolean;
    pending: boolean;
    buttonProps: React.ButtonHTMLAttributes<HTMLButtonElement>;
  };
}) {
  const shownOn = toggle.enabled && !stopping;
  const blocked = locked && !shownOn;
  const label = shownOn ? 'Stop screen share' : 'Share screen';
  return (
    <button
      {...toggle.buttonProps}
      type="button"
      aria-pressed={shownOn}
      disabled={blocked || (toggle.pending && !stopping)}
      aria-disabled={blocked || undefined}
      style={blocked ? { pointerEvents: 'none' } : undefined}
      aria-label={blocked ? 'Share screen unavailable while slides are shared' : label}
      title={blocked ? 'Unavailable while someone is presenting' : undefined}
      onPointerDown={(event) => {
        if (blocked) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onClick={(event) => {
        if (blocked || (stopping && toggle.pending)) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        if (shownOn) {
          onStop();
        }
        toggle.buttonProps.onClick?.(event);
      }}
    >
      {shownOn ? <ScreenShareStopIcon /> : <ScreenShareIcon />}
      {showText && label}
    </button>
  );
}

function ScreenShareIcon() {
  return (
    <svg width="20" height="16" viewBox="0 0 20 16" fill="none" aria-hidden="true">
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M0 2.75A2.75 2.75 0 0 1 2.75 0h14.5A2.75 2.75 0 0 1 20 2.75v10.5A2.75 2.75 0 0 1 17.25 16H2.75A2.75 2.75 0 0 1 0 13.25zM2.75 1.5c-.69 0-1.25.56-1.25 1.25v10.5c0 .69.56 1.25 1.25 1.25h14.5c.69 0 1.25-.56 1.25-1.25V2.75c0-.69-.56-1.25-1.25-1.25z"
        clipRule="evenodd"
      />
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M9.47 4.22a.75.75 0 0 1 1.06 0l2.25 2.25a.75.75 0 0 1-1.06 1.06l-.97-.97v4.69a.75.75 0 0 1-1.5 0V6.56l-.97.97a.75.75 0 0 1-1.06-1.06z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function ScreenShareStopIcon() {
  return (
    <svg width="20" height="16" viewBox="0 0 20 16" fill="none" aria-hidden="true">
      <path
        fill="currentColor"
        d="M7.28 4.22a.75.75 0 0 0-1.06 1.06L8.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L10 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L11.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L10 6.94z"
      />
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M2.75 0A2.75 2.75 0 0 0 0 2.75v10.5A2.75 2.75 0 0 0 2.75 16h14.5A2.75 2.75 0 0 0 20 13.25V2.75A2.75 2.75 0 0 0 17.25 0zM1.5 2.75c0-.69.56-1.25 1.25-1.25h14.5c.69 0 1.25.56 1.25 1.25v10.5c0 .69-.56 1.25-1.25 1.25H2.75c-.69 0-1.25-.56-1.25-1.25z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function SettingsButton({ showText }: { showText: boolean }) {
  const layoutContext = useMaybeLayoutContext();
  const settingsOpen = layoutContext?.widget.state?.showSettings ?? false;
  return (
    <button
      type="button"
      className="lk-button"
      aria-pressed={settingsOpen}
      onClick={() => layoutContext?.widget.dispatch?.({ msg: 'toggle_settings' })}
    >
      <GearIcon />
      {showText && 'Settings'}
    </button>
  );
}

function ChatIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M19.14 12.94a7.49 7.49 0 0 0 .05-.94 7.49 7.49 0 0 0-.05-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.28 7.28 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.59.22-1.14.54-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.71 8.84a.5.5 0 0 0 .12.64l2.03 1.58a7.5 7.5 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .6.22l2.39-.96c.49.4 1.04.72 1.63.94l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54c.59-.22 1.14-.54 1.63-.94l2.39.96a.5.5 0 0 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z" />
    </svg>
  );
}

function LeaveIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5v-2H5V5h5V3zm4.3 4.3 1.4 1.4L13.4 11H21v2h-7.6l2.3 2.3-1.4 1.4L9.6 12l4.7-4.7z" />
    </svg>
  );
}
