'use client';

import { ConnectionState, RoomEvent, Track, type RemoteParticipant, type Room } from 'livekit-client';
import React from 'react';
import { useRoomContext } from '@livekit/components-react';
import { toast } from 'react-hot-toast';
import { useMeetingSession } from '@/lib/MeetingSession';
import {
  fetchCurrentDeck,
  fetchDeckFile,
  fetchDeckNotes,
  recoverOwnedDeck,
  stopDeck,
  updateDeckPage,
  uploadDeck,
  uploadFailureIsTemporary,
  waitForConvertedDeck,
  type Deck,
} from '@/lib/slides/api';

const SLIDES_TOPIC = 'slides';

function slidesActive(metadata: string) {
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
const MAX_PDF_BYTES = 25 * 1024 * 1024;

type SlidesMessage =
  | ({ type: 'start' } & Deck)
  | { type: 'page'; deckId: string; page: number; ownerIdentity: string }
  | { type: 'stop'; deckId: string; ownerIdentity: string }
  | { type: 'preparing'; ownerIdentity: string; ownerName: string }
  | { type: 'cancelled'; ownerIdentity: string };

type SlidePreparing = {
  ownerIdentity: string;
  ownerName: string;
};

export type SlidesContextValue = {
  deck: Deck | null;
  pdfBytes: Uint8Array | null;
  fileStatus: 'idle' | 'loading' | 'ready' | 'error';
  fileError: string | null;
  uploading: boolean;
  converting: boolean;
  preparing: SlidePreparing | null;
  presentationReleased: boolean;
  noteSlidesQuiet: () => void;
  isOwner: boolean;
  hasSpeakerNotes: boolean;
  showSpeakerNotes: boolean;
  speakerNotes: string[];
  notesLoaded: boolean;
  shareFile: (file: File) => Promise<void>;
  stopSharing: () => Promise<void>;
  setPage: (page: number) => void;
  setShowSpeakerNotes: (show: boolean) => void;
};

const SlidesContext = React.createContext<SlidesContextValue | null>(null);

export function useSlides(): SlidesContextValue {
  const value = React.useContext(SlidesContext);
  if (!value) {
    throw new Error('useSlides must be used inside the meeting room');
  }
  return value;
}

export function SlidesProvider({ children }: { children: React.ReactNode }) {
  const room = useRoomContext();
  const session = useMeetingSession();
  const [deck, setDeck] = React.useState<Deck | null>(null);
  const [pdfBytes, setPdfBytes] = React.useState<Uint8Array | null>(null);
  const [fileStatus, setFileStatus] = React.useState<SlidesContextValue['fileStatus']>('idle');
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [converting, setConverting] = React.useState(false);
  const [preparing, setPreparingState] = React.useState<SlidePreparing | null>(null);
  const [presentationReleased, setPresentationReleased] = React.useState(false);
  const [speakerNotes, setSpeakerNotes] = React.useState<string[]>([]);
  const [notesLoaded, setNotesLoaded] = React.useState(false);
  const [showSpeakerNotes, setShowSpeakerNotesState] = React.useState(false);

  const deckRef = React.useRef<Deck | null>(null);
  const pdfBytesRef = React.useRef<Uint8Array | null>(null);
  const notesLoadedRef = React.useRef(false);
  const loadToken = React.useRef(0);
  const chain = React.useRef(Promise.resolve());
  const lastSent = React.useRef({ deckId: '', page: 0 });
  const preparingRef = React.useRef<SlidePreparing | null>(null);
  const stoppedDeckIds = React.useRef(new Set<string>());

  const setPreparing = React.useCallback((next: SlidePreparing | null) => {
    preparingRef.current = next;
    setPreparingState(next);
    if (next) {
      setPresentationReleased(false);
    }
  }, []);

  const noteSlidesQuiet = React.useCallback(() => {
    setPresentationReleased(false);
  }, []);

  const clearPreparing = React.useCallback((ownerIdentity: string) => {
    if (preparingRef.current?.ownerIdentity === ownerIdentity) {
      preparingRef.current = null;
      setPreparingState(null);
    }
    hidePreparingToast(ownerIdentity);
  }, []);

  const applyDeck = React.useCallback((next: Deck | null) => {
    deckRef.current = next;
    setDeck(next);
    if (next) {
      setPresentationReleased(false);
    }
  }, []);

  const loadBytes = React.useCallback(
    async (deckId: string) => {
      const token = ++loadToken.current;
      setFileStatus('loading');
      setFileError(null);
      try {
        const bytes = await fetchDeckFile(deckId);
        if (token !== loadToken.current || deckRef.current?.deckId !== deckId) {
          return;
        }
        pdfBytesRef.current = bytes;
        setPdfBytes(bytes);
        setFileStatus('ready');
      } catch (error) {
        if (token !== loadToken.current) {
          return;
        }
        // The host already drew this deck from the local file. A second fetch
        // when the recorder joins must not replace that picture with an error.
        // Page controls would still work, which is what the host saw.
        if (deckRef.current?.deckId === deckId && pdfBytesRef.current) {
          setFileStatus('ready');
          setFileError(null);
          return;
        }
        pdfBytesRef.current = null;
        setPdfBytes(null);
        setFileStatus('error');
        setFileError(error instanceof Error ? error.message : 'Could not download slides');
      }
    },
    [],
  );

  const clearNotes = React.useCallback(() => {
    notesLoadedRef.current = false;
    setSpeakerNotes([]);
    setNotesLoaded(false);
    setShowSpeakerNotesState(false);
  }, []);

  const rememberNotes = React.useCallback((notes: string[]) => {
    notesLoadedRef.current = true;
    setSpeakerNotes(notes);
    setNotesLoaded(true);
  }, []);

  const clearDeck = React.useCallback(() => {
    loadToken.current += 1;
    applyDeck(null);
    pdfBytesRef.current = null;
    setPdfBytes(null);
    setFileStatus('idle');
    setFileError(null);
    clearNotes();
  }, [applyDeck, clearNotes]);

  const flushPage = React.useCallback(async () => {
    const snapshot = deckRef.current;
    if (!snapshot || snapshot.ownerIdentity !== room.localParticipant.identity) {
      return;
    }
    if (lastSent.current.deckId === snapshot.deckId && lastSent.current.page === snapshot.page) {
      return;
    }
    await updateDeckPage(snapshot.deckId, snapshot.page, snapshot.ownerIdentity);
    const after = deckRef.current;
    if (!after || after.deckId !== snapshot.deckId || after.page !== snapshot.page) {
      return;
    }
    await publish(room, {
      type: 'page',
      deckId: snapshot.deckId,
      page: snapshot.page,
      ownerIdentity: snapshot.ownerIdentity,
    });
    lastSent.current = { deckId: snapshot.deckId, page: snapshot.page };
  }, [room]);

  const enqueue = React.useCallback((task: () => Promise<void>) => {
    chain.current = chain.current.then(task, task);
    return chain.current;
  }, []);

  const setPage = React.useCallback(
    (page: number) => {
      const current = deckRef.current;
      if (!current || current.ownerIdentity !== room.localParticipant.identity) {
        return;
      }
      const next = Math.min(current.pageCount, Math.max(1, Math.trunc(page)));
      if (next === current.page) {
        return;
      }
      applyDeck({ ...current, page: next });
      void enqueue(async () => {
        try {
          await flushPage();
        } catch (error) {
          toast.error(error instanceof Error ? error.message : 'Could not change slide');
        }
      });
    },
    [applyDeck, enqueue, flushPage, room],
  );

  const shareFile = React.useCallback(
    async (file: File) => {
      if (file.size > MAX_PDF_BYTES) {
        toast.error('Slides must be 25 MB or smaller');
        return;
      }
      const lowerName = file.name.toLowerCase();
      const isPdf = file.type === 'application/pdf' || lowerName.endsWith('.pdf');
      const isPptx =
        file.type === 'application/vnd.openxmlformats-officedocument.presentationml.presentation' ||
        lowerName.endsWith('.pptx');
      if (!isPdf && !isPptx) {
        toast.error('Choose a PDF or PowerPoint file');
        return;
      }
      if (roomIsSharingScreen(room)) {
        toast.error('Stop screen sharing before sharing slides');
        return;
      }
      const alreadyShared = deckRef.current;
      const alreadyPreparing = preparingRef.current;
      const me = room.localParticipant.identity;
      if (
        (alreadyShared && alreadyShared.ownerIdentity !== me) ||
        (alreadyPreparing && alreadyPreparing.ownerIdentity !== me)
      ) {
        toast.error('Someone is already sharing slides');
        return;
      }
      const ownerIdentity = room.localParticipant.identity;
      const ownerName = room.localParticipant.name || '';
      const previousDeckId = deckRef.current?.deckId ?? null;
      setUploading(true);
      setConverting(isPptx);
      setPreparing({ ownerIdentity, ownerName });
      showProgressToast(ownerIdentity, isPptx ? 'Converting PowerPoint…' : 'Uploading slides…');
      // Tell the room before the upload returns. Conversion can take a while,
      // and this message does not need to finish before the file is sent.
      void publish(room, { type: 'preparing', ownerIdentity, ownerName }).catch((error) => {
        console.error(error);
      });
      try {
        // A PDF can be previewed from the local file while it uploads. PowerPoint
        // is accepted immediately and converted on the server.
        const [created, localBuffer] = await Promise.all([
          uploadDeck({
            file,
            roomName: room.name,
            ownerIdentity,
            ownerName,
          }).catch(async (error: unknown) => {
            if (!uploadFailureIsTemporary(error)) {
              throw error;
            }
            setUploading(false);
            setConverting(true);
            showProgressToast(ownerIdentity, 'Converting PowerPoint…');
            const recovered = await recoverOwnedDeck({
              roomName: room.name,
              ownerIdentity,
              previousDeckId,
            });
            if (!recovered) {
              throw error;
            }
            return { status: 'ready' as const, deck: recovered.deck, notes: recovered.notes };
          }),
          isPdf ? file.arrayBuffer() : Promise.resolve(null),
        ]);
        const ready =
          created.status === 'converting'
            ? await waitForConvertedDeck({
                roomName: room.name,
                deckId: created.deckId,
                ownerIdentity,
              })
            : created;
        if (stoppedDeckIds.current.has(ready.deck.deckId)) {
          clearPreparing(ownerIdentity);
          return;
        }
        loadToken.current += 1;
        applyDeck(ready.deck);
        rememberNotes(ready.notes);
        setShowSpeakerNotesState(false);
        lastSent.current = { deckId: ready.deck.deckId, page: ready.deck.page };
        clearPreparing(ownerIdentity);
        const started = publish(room, { type: 'start', ...ready.deck });
        if (localBuffer && created.status === 'ready') {
          pdfBytesRef.current = new Uint8Array(localBuffer);
          setPdfBytes(pdfBytesRef.current);
          setFileStatus('ready');
          setFileError(null);
          await started;
        } else {
          await Promise.all([started, loadBytes(ready.deck.deckId)]);
        }
      } catch (error) {
        clearPreparing(ownerIdentity);
        void publish(room, { type: 'cancelled', ownerIdentity }).catch((publishError) => {
          console.error(publishError);
        });
        toast.error(error instanceof Error ? error.message : 'Could not share slides');
      } finally {
        setUploading(false);
        setConverting(false);
      }
    },
    [applyDeck, clearPreparing, loadBytes, rememberNotes, room, setPreparing],
  );

  const stopSharing = React.useCallback(async () => {
    const current = deckRef.current;
    if (!current || current.ownerIdentity !== room.localParticipant.identity) {
      return;
    }
    stoppedDeckIds.current.add(current.deckId);
    setPresentationReleased(true);
    clearDeck();
    try {
      await chain.current.catch(() => undefined);
      await stopDeck(current.deckId, current.ownerIdentity);
      await publish(room, {
        type: 'stop',
        deckId: current.deckId,
        ownerIdentity: current.ownerIdentity,
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not stop slides');
    }
  }, [clearDeck, room]);

  React.useEffect(() => {
    return () => {
      const pending = preparingRef.current;
      if (pending) {
        hidePreparingToast(pending.ownerIdentity);
      }
    };
  }, [room]);

  React.useEffect(() => {
    let cancelled = false;

    const pull = async () => {
      if (room.state !== ConnectionState.Connected) {
        return;
      }
      const names = [...new Set([room.name, session?.roomName].filter((name): name is string => !!name))];
      if (names.length === 0) {
        return;
      }
      try {
        let current: Deck | null = null;
        for (const name of names) {
          current = await fetchCurrentDeck(name);
          if (current) {
            break;
          }
        }
        if (cancelled || !current || stoppedDeckIds.current.has(current.deckId)) {
          return;
        }
        const existing = deckRef.current;
        if (existing?.deckId === current.deckId) {
          if (
            existing.page !== current.page &&
            existing.ownerIdentity !== room.localParticipant.identity
          ) {
            applyDeck({ ...existing, page: current.page });
          }
          return;
        }
        if (existing) {
          return;
        }
        applyDeck(current);
        const ownedNotes =
          current.hasNotes && current.ownerIdentity === room.localParticipant.identity;
        if (!ownedNotes) {
          clearNotes();
          notesLoadedRef.current = true;
          setNotesLoaded(true);
        }
        await loadBytes(current.deckId);
        if (ownedNotes) {
          try {
            const notes = await fetchDeckNotes(current.deckId, room.localParticipant.identity);
            if (!cancelled && deckRef.current?.deckId === current.deckId) {
              rememberNotes(notes);
            }
          } catch (error) {
            console.error(error);
          }
        }
      } catch (error) {
        console.error(error);
      }
    };

    let slidesOn = slidesActive(room.metadata ?? '');
    const onMetadata = (metadata?: string) => {
      const active = slidesActive(metadata ?? '');
      const turnedOn = active && !slidesOn;
      slidesOn = active;
      if (turnedOn) {
        void pull();
      }
    };
    void pull();
    room.on(RoomEvent.Connected, pull);
    room.on(RoomEvent.RoomMetadataChanged, onMetadata);
    return () => {
      cancelled = true;
      room.off(RoomEvent.Connected, pull);
      room.off(RoomEvent.RoomMetadataChanged, onMetadata);
    };
  }, [applyDeck, clearNotes, loadBytes, rememberNotes, room, session?.roomName]);

  React.useEffect(() => {
    const onData = (payload: Uint8Array, participant?: { identity: string }, _kind?: unknown, topic?: string) => {
      if (topic !== SLIDES_TOPIC) {
        return;
      }
      const message = parseMessage(payload);
      if (!message) {
        return;
      }
      if (participant && participant.identity !== message.ownerIdentity) {
        return;
      }
      if (message.type === 'preparing') {
        if (message.ownerIdentity === room.localParticipant.identity) {
          return;
        }
        setPreparing({ ownerIdentity: message.ownerIdentity, ownerName: message.ownerName });
        showPreparingToast(message.ownerName, message.ownerIdentity);
        return;
      }
      if (message.type === 'cancelled') {
        clearPreparing(message.ownerIdentity);
        return;
      }
      if (message.type === 'stop') {
        stoppedDeckIds.current.add(message.deckId);
        clearPreparing(message.ownerIdentity);
        const current = deckRef.current;
        if (!current || current.deckId === message.deckId) {
          setPresentationReleased(true);
          if (current?.deckId === message.deckId) {
            clearDeck();
          }
        }
        return;
      }
      if (message.type === 'page') {
        const current = deckRef.current;
        if (!current || current.deckId !== message.deckId) {
          return;
        }
        if (current.ownerIdentity === room.localParticipant.identity) {
          return;
        }
        const page = Math.min(current.pageCount, Math.max(1, message.page));
        applyDeck({ ...current, page });
        return;
      }
      if (stoppedDeckIds.current.has(message.deckId)) {
        return;
      }
      clearPreparing(message.ownerIdentity);
      const alreadyShown =
        message.ownerIdentity === room.localParticipant.identity &&
        deckRef.current?.deckId === message.deckId &&
        pdfBytesRef.current;
      applyDeck(message);
      if (message.ownerIdentity !== room.localParticipant.identity) {
        clearNotes();
      }
      if (!alreadyShown) {
        void loadBytes(message.deckId);
      }
    };

    const onJoin = (participant: RemoteParticipant) => {
      const pending = preparingRef.current;
      if (pending && pending.ownerIdentity === room.localParticipant.identity) {
        void publish(room, { type: 'preparing', ...pending }, [participant.identity]);
      }
      const current = deckRef.current;
      if (!current || current.ownerIdentity !== room.localParticipant.identity) {
        return;
      }
      void publish(room, { type: 'start', ...current }, [participant.identity]);
    };

    const onLeave = (participant: RemoteParticipant) => {
      clearPreparing(participant.identity);
    };

    room.on(RoomEvent.DataReceived, onData);
    room.on(RoomEvent.ParticipantConnected, onJoin);
    room.on(RoomEvent.ParticipantDisconnected, onLeave);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
      room.off(RoomEvent.ParticipantConnected, onJoin);
      room.off(RoomEvent.ParticipantDisconnected, onLeave);
    };
  }, [applyDeck, clearDeck, clearNotes, clearPreparing, loadBytes, room, setPreparing]);

  const setShowSpeakerNotes = React.useCallback(
    (show: boolean) => {
      setShowSpeakerNotesState(show);
      if (!show || notesLoadedRef.current) {
        return;
      }
      const current = deckRef.current;
      if (!current?.hasNotes || current.ownerIdentity !== room.localParticipant.identity) {
        return;
      }
      void (async () => {
        try {
          const notes = await fetchDeckNotes(current.deckId, room.localParticipant.identity);
          if (deckRef.current?.deckId === current.deckId) {
            rememberNotes(notes);
          }
        } catch (error) {
          toast.error(error instanceof Error ? error.message : 'Could not load speaker notes');
        }
      })();
    },
    [rememberNotes, room],
  );

  const isOwner = !!deck && deck.ownerIdentity === room.localParticipant.identity;
  const hasSpeakerNotes =
    isOwner && (deck.hasNotes || speakerNotes.some((note) => note.trim() !== ''));

  const value = React.useMemo<SlidesContextValue>(
    () => ({
      deck,
      pdfBytes,
      fileStatus,
      fileError,
      uploading,
      converting,
      preparing,
      presentationReleased,
      noteSlidesQuiet,
      isOwner,
      hasSpeakerNotes,
      showSpeakerNotes,
      speakerNotes,
      notesLoaded,
      shareFile,
      stopSharing,
      setPage,
      setShowSpeakerNotes,
    }),
    [
      converting,
      deck,
      fileError,
      fileStatus,
      hasSpeakerNotes,
      isOwner,
      noteSlidesQuiet,
      notesLoaded,
      pdfBytes,
      preparing,
      presentationReleased,
      setPage,
      setShowSpeakerNotes,
      shareFile,
      showSpeakerNotes,
      speakerNotes,
      stopSharing,
      uploading,
    ],
  );

  return <SlidesContext.Provider value={value}>{children}</SlidesContext.Provider>;
}

async function publish(room: Room, message: SlidesMessage, destinationIdentities?: string[]) {
  const data = new TextEncoder().encode(JSON.stringify(message));
  await room.localParticipant.publishData(data, {
    reliable: true,
    topic: SLIDES_TOPIC,
    ...(destinationIdentities ? { destinationIdentities } : {}),
  });
}

function parseMessage(payload: Uint8Array): SlidesMessage | null {
  try {
    const message = JSON.parse(new TextDecoder().decode(payload)) as {
      type?: string;
      deckId?: unknown;
      ownerIdentity?: unknown;
      ownerName?: unknown;
      page?: unknown;
      pageCount?: unknown;
      name?: unknown;
      hasNotes?: unknown;
    };
    if (!message || typeof message.ownerIdentity !== 'string' || !message.ownerIdentity) {
      return null;
    }
    if (message.type === 'preparing') {
      return {
        type: 'preparing',
        ownerIdentity: message.ownerIdentity,
        ownerName: typeof message.ownerName === 'string' ? message.ownerName : '',
      };
    }
    if (message.type === 'cancelled') {
      return { type: 'cancelled', ownerIdentity: message.ownerIdentity };
    }
    if (typeof message.deckId !== 'string') {
      return null;
    }
    if (message.type === 'stop') {
      return { type: 'stop', deckId: message.deckId, ownerIdentity: message.ownerIdentity };
    }
    if (message.type === 'page' && typeof message.page === 'number') {
      return {
        type: 'page',
        deckId: message.deckId,
        page: message.page,
        ownerIdentity: message.ownerIdentity,
      };
    }
    if (
      message.type === 'start' &&
      typeof message.page === 'number' &&
      typeof message.pageCount === 'number' &&
      typeof message.name === 'string'
    ) {
      return {
        type: 'start',
        deckId: message.deckId,
        page: message.page,
        pageCount: message.pageCount,
        name: message.name,
        ownerIdentity: message.ownerIdentity,
        ownerName: typeof message.ownerName === 'string' ? message.ownerName : '',
        hasNotes: message.hasNotes === true,
      };
    }
  } catch {
    return null;
  }
  return null;
}

function preparingToastId(ownerIdentity: string) {
  return `slides-preparing-${ownerIdentity}`;
}

function sharerLabel(ownerName: string) {
  const name = ownerName.trim();
  return name || 'Someone';
}

function showProgressToast(ownerIdentity: string, message: string) {
  toast.loading(message, {
    id: preparingToastId(ownerIdentity),
    position: 'top-center',
    duration: Infinity,
  });
}

function showPreparingToast(ownerName: string, ownerIdentity: string) {
  toast.loading(`${sharerLabel(ownerName)} is sharing slides, please wait...`, {
    id: preparingToastId(ownerIdentity),
    position: 'top-center',
    duration: Infinity,
  });
}

function hidePreparingToast(ownerIdentity: string) {
  toast.dismiss(preparingToastId(ownerIdentity));
}

function roomIsSharingScreen(room: Room) {
  const participants = [room.localParticipant, ...room.remoteParticipants.values()];
  return participants.some((participant) =>
    [...participant.trackPublications.values()].some(
      (publication) => publication.source === Track.Source.ScreenShare && !publication.isMuted,
    ),
  );
}
