'use client';

import type { MessageDecoder, MessageEncoder, TrackReferenceOrPlaceholder, WidgetState } from '@livekit/components-core';
import { isEqualTrackRef, isTrackReference, isWeb } from '@livekit/components-core';
import { ParticipantKind, RoomEvent, Track, type Participant } from 'livekit-client';
import * as React from 'react';
import type { MessageFormatter } from '@livekit/components-react';
import {
  CarouselLayout,
  Chat,
  ConnectionStateToast,
  FocusLayout,
  FocusLayoutContainer,
  GridLayout,
  LayoutContextProvider,
  ParticipantTile,
  RoomAudioRenderer,
  useCreateLayoutContext,
  useParticipants,
  usePinnedTracks,
  useTracks,
} from '@livekit/components-react';
import { RecordingPlaybackHost } from '@/lib/RecordingPlayback';
import { MeetingControlBar } from '@/lib/MeetingControlBar';
import { RecordButton } from '@/lib/RecordButton';
import { CohostInviteDialog } from '@/lib/CohostInviteDialog';
import { WaitingRoomDialog } from '@/lib/WaitingRoomDialog';
import { SlideCarousel } from '@/lib/slides/SlideCarousel';
import { SlideStage } from '@/lib/slides/SlideStage';
import { SlidesProvider, useSlides } from '@/lib/slides/SlidesContext';
import styles from '@/styles/Slides.module.css';

export interface MeetingConferenceProps extends React.HTMLAttributes<HTMLDivElement> {
  chatMessageFormatter?: MessageFormatter;
  chatMessageEncoder?: MessageEncoder;
  chatMessageDecoder?: MessageDecoder;
  SettingsComponent?: React.ComponentType;
  /** Egress capture: the same stage the room shows, without the control bar. */
  recording?: boolean;
}

export function MeetingConference(props: MeetingConferenceProps) {
  return (
    <SlidesProvider>
      <ConferenceLayout {...props} />
    </SlidesProvider>
  );
}

function ConferenceLayout({
  chatMessageFormatter,
  chatMessageDecoder,
  chatMessageEncoder,
  SettingsComponent,
  recording = false,
  className,
  ...props
}: MeetingConferenceProps) {
  const { deck } = useSlides();
  const [widgetState, setWidgetState] = React.useState<WidgetState>({
    showChat: false,
    unreadMessages: 0,
    showSettings: false,
  });
  const lastAutoFocusedScreenShareTrack = React.useRef<TrackReferenceOrPlaceholder | null>(null);

  const tracks = useTracks(
    [
      { source: Track.Source.Camera, withPlaceholder: true },
      { source: Track.Source.ScreenShare, withPlaceholder: false },
    ],
    // Speaker changes reorder the grid. Publish, mute, and subscribe events
    // are included so a camera or screen share appears without waiting for
    // the next person to talk. The recorder uses the same events.
    {
      onlySubscribed: false,
      updateOnlyOn: [
        RoomEvent.ActiveSpeakersChanged,
        RoomEvent.ParticipantConnected,
        RoomEvent.ParticipantDisconnected,
        RoomEvent.TrackPublished,
        RoomEvent.TrackUnpublished,
        RoomEvent.TrackMuted,
        RoomEvent.TrackUnmuted,
        RoomEvent.TrackSubscribed,
        RoomEvent.TrackUnsubscribed,
        RoomEvent.LocalTrackPublished,
        RoomEvent.LocalTrackUnpublished,
      ],
    },
  );
  // The recorder is a hidden local participant. Leave its empty tile out of the file.
  const stageTracks = recording ? tracks.filter((track) => shownInRecording(track.participant)) : tracks;

  const layoutContext = useCreateLayoutContext();
  const screenShareTracks = stageTracks
    .filter(isTrackReference)
    .filter((track) => track.publication.source === Track.Source.ScreenShare);
  const focusTrack = usePinnedTracks(layoutContext)?.[0];
  const carouselTracks = stageTracks.filter((track) => !isEqualTrackRef(track, focusTrack));

  // Drop whatever was focused when a deck first appears so the slides take the
  // stage. Later expand clicks must keep their pin; clearing it again is why
  // the button did nothing during a slide share.
  const slidesOpenedForDeck = React.useRef<string | null>(null);

  // Page changes share the same deck id and should not reset a screen-share pin.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => {
    if (deck) {
      if (slidesOpenedForDeck.current !== deck.deckId) {
        slidesOpenedForDeck.current = deck.deckId;
        lastAutoFocusedScreenShareTrack.current = null;
        if (focusTrack) {
          layoutContext.pin.dispatch?.({ msg: 'clear_pin' });
        }
        return;
      }
      if (focusTrack && !isTrackReference(focusTrack)) {
        const updatedFocusTrack = tracks.find(
          (tr) =>
            tr.participant.identity === focusTrack.participant.identity && tr.source === focusTrack.source,
        );
        if (updatedFocusTrack !== focusTrack && isTrackReference(updatedFocusTrack)) {
          layoutContext.pin.dispatch?.({ msg: 'set_pin', trackReference: updatedFocusTrack });
        }
      }
      return;
    }

    slidesOpenedForDeck.current = null;

    if (
      screenShareTracks.some((track) => track.publication.isSubscribed) &&
      lastAutoFocusedScreenShareTrack.current === null
    ) {
      layoutContext.pin.dispatch?.({ msg: 'set_pin', trackReference: screenShareTracks[0] });
      lastAutoFocusedScreenShareTrack.current = screenShareTracks[0];
    } else if (
      lastAutoFocusedScreenShareTrack.current &&
      !screenShareTracks.some(
        (track) =>
          track.publication.trackSid === lastAutoFocusedScreenShareTrack.current?.publication?.trackSid,
      )
    ) {
      layoutContext.pin.dispatch?.({ msg: 'clear_pin' });
      lastAutoFocusedScreenShareTrack.current = null;
    }

    if (focusTrack && !isTrackReference(focusTrack)) {
      const updatedFocusTrack = tracks.find(
        (tr) =>
          tr.participant.identity === focusTrack.participant.identity && tr.source === focusTrack.source,
      );
      if (updatedFocusTrack !== focusTrack && isTrackReference(updatedFocusTrack)) {
        layoutContext.pin.dispatch?.({ msg: 'set_pin', trackReference: updatedFocusTrack });
      }
    }
  }, [
    deck?.deckId,
    screenShareTracks.map((ref) => `${ref.publication.trackSid}_${ref.publication.isSubscribed}`).join(),
    focusTrack?.publication?.trackSid,
  ]);

  return (
    <div className={['lk-video-conference', className].filter(Boolean).join(' ')} {...props}>
      {isWeb() && (
        <LayoutContextProvider value={layoutContext} onWidgetChange={setWidgetState}>
          <div
            className={[
              'meeting-layout',
              !recording && widgetState.showChat ? 'lk-chat-open' : '',
              recording ? 'meeting-recording' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
            {!recording && <RecordButton />}
            <div className="lk-video-conference-inner">
              {deck || focusTrack ? (
                <div className="lk-focus-layout-wrapper">
                  <FocusLayoutContainer className={deck ? styles.focus : undefined}>
                    {deck && focusTrack ? (
                      <SlideCarousel tracks={carouselTracks} capture={recording} />
                    ) : (
                      <CarouselLayout tracks={focusTrack ? carouselTracks : stageTracks}>
                        <ParticipantTile />
                      </CarouselLayout>
                    )}
                    {deck && !focusTrack ? (
                      <SlideStage capture={recording} />
                    ) : (
                      focusTrack && <FocusLayout trackRef={focusTrack} />
                    )}
                  </FocusLayoutContainer>
                </div>
              ) : (
                <div className="lk-grid-layout-wrapper">
                  {recording && stageTracks.length === 0 ? (
                    <CameraOffStage />
                  ) : (
                    <GridLayout tracks={stageTracks}>
                      <ParticipantTile />
                    </GridLayout>
                  )}
                </div>
              )}
              {!recording && (
                <MeetingControlBar controls={{ chat: true, settings: !!SettingsComponent }} />
              )}
            </div>
            {!recording && (
            <Chat
              style={{ display: widgetState.showChat ? 'grid' : 'none' }}
              messageFormatter={chatMessageFormatter}
              messageEncoder={chatMessageEncoder}
              messageDecoder={chatMessageDecoder}
            />
            )}
            {!recording && SettingsComponent && (
              <div
                className="lk-settings-menu-modal"
                style={{ display: widgetState.showSettings ? 'block' : 'none' }}
              >
                <SettingsComponent />
              </div>
            )}
          </div>
        </LayoutContextProvider>
      )}
      {!recording && <RecordingPlaybackHost />}
      {!recording && <WaitingRoomDialog />}
      {!recording && <CohostInviteDialog />}
      {/* Room audio for the people in the call. Egress captures this output. */}
      <RoomAudioRenderer />
      {!recording && <ConnectionStateToast />}
    </div>
  );
}

function shownInRecording(participant: Participant) {
  return !participant.isLocal && participant.kind !== ParticipantKind.EGRESS;
}

function CameraOffStage() {
  const people = useParticipants().filter(shownInRecording);
  if (people.length === 0) {
    return <div className="recording-empty">Waiting for the room</div>;
  }
  return (
    <div className="recording-placeholders">
      {people.map((person) => {
        const name = person.name || person.identity;
        return (
          <div key={person.identity} className="recording-placeholder">
            <span>{initials(name)}</span>
            <p>{name}</p>
          </div>
        );
      })}
    </div>
  );
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? '');
  return letters.join('') || '?';
}
