export type Deck = {
  deckId: string;
  page: number;
  pageCount: number;
  name: string;
  ownerIdentity: string;
  ownerName: string;
  hasNotes: boolean;
};

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) {
      return body.detail;
    }
  } catch {
    /* HTML error pages have no JSON body. */
  }
  return `Request failed (${response.status})`;
}

export type SlideConverting = {
  deckId: string;
  ownerIdentity: string;
  ownerName: string;
};

export type SlideShareError = {
  deckId: string;
  ownerIdentity: string;
  detail: string;
};

export type SlideRoomState = {
  deck: Deck | null;
  converting: SlideConverting | null;
  error: SlideShareError | null;
};

export async function fetchSlideState(roomName: string): Promise<SlideRoomState> {
  const response = await fetch(`/api/slides?roomName=${encodeURIComponent(roomName)}`, {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as {
    deck?: Deck | null;
    converting?: SlideConverting | null;
    error?: SlideShareError | null;
  };
  if (body.deck) {
    body.deck.hasNotes = body.deck.hasNotes === true;
  }
  return {
    deck: body.deck ?? null,
    converting: body.converting ?? null,
    error: body.error ?? null,
  };
}

export async function fetchCurrentDeck(roomName: string): Promise<Deck | null> {
  const state = await fetchSlideState(roomName);
  return state.deck;
}

export type UploadedDeck =
  | { status: 'ready'; deck: Deck; notes: string[] }
  | { status: 'converting'; deckId: string };

export async function uploadDeck(input: {
  file: File;
  roomName: string;
  ownerIdentity: string;
  ownerName: string;
}): Promise<UploadedDeck> {
  const body = new FormData();
  body.append('file', input.file);
  body.append('room_name', input.roomName);
  body.append('owner_identity', input.ownerIdentity);
  body.append('owner_name', input.ownerName);
  const response = await fetch('/api/slides', {
    method: 'POST',
    credentials: 'include',
    body,
  });
  if (response.status === 202) {
    const payload = (await response.json()) as { deckId?: string };
    if (!payload.deckId) {
      throw new Error('Could not convert that PowerPoint file');
    }
    return { status: 'converting', deckId: payload.deckId };
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { deck: Deck; notes?: string[] };
  return { status: 'ready', deck: payload.deck, notes: normalizeNotes(payload.notes) };
}

const CONVERT_WAIT_MS = 90_000;
const RECOVER_WAIT_MS = 20_000;

export async function waitForConvertedDeck(input: {
  roomName: string;
  deckId: string;
  ownerIdentity: string;
}): Promise<{ deck: Deck; notes: string[] }> {
  const deadline = Date.now() + CONVERT_WAIT_MS;
  while (Date.now() < deadline) {
    let state: SlideRoomState;
    try {
      state = await fetchSlideState(input.roomName);
    } catch {
      await delay(400);
      continue;
    }
    if (state.deck?.deckId === input.deckId) {
      return { deck: state.deck, notes: await notesFor(state.deck, input.ownerIdentity) };
    }
    if (state.error?.deckId === input.deckId) {
      throw new Error(state.error.detail || 'Could not convert that PowerPoint file');
    }
    await delay(400);
  }
  throw new Error('Converting that PowerPoint file took too long');
}

export async function recoverOwnedDeck(input: {
  roomName: string;
  ownerIdentity: string;
  previousDeckId: string | null;
}): Promise<{ deck: Deck; notes: string[] } | null> {
  const deadline = Date.now() + RECOVER_WAIT_MS;
  while (Date.now() < deadline) {
    try {
      const state = await fetchSlideState(input.roomName);
      const deck = state.deck;
      if (
        deck &&
        deck.ownerIdentity === input.ownerIdentity &&
        deck.deckId !== input.previousDeckId
      ) {
        return { deck, notes: await notesFor(deck, input.ownerIdentity) };
      }
      if (state.converting?.ownerIdentity === input.ownerIdentity) {
        return waitForConvertedDeck({
          roomName: input.roomName,
          deckId: state.converting.deckId,
          ownerIdentity: input.ownerIdentity,
        });
      }
    } catch {
      /* Keep asking. A timed-out upload often finishes on the server. */
    }
    await delay(400);
  }
  return null;
}

async function notesFor(deck: Deck, ownerIdentity: string): Promise<string[]> {
  if (!deck.hasNotes || deck.ownerIdentity !== ownerIdentity) {
    return [];
  }
  try {
    return await fetchDeckNotes(deck.deckId, ownerIdentity);
  } catch {
    return [];
  }
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function uploadFailureIsTemporary(error: unknown): boolean {
  const message = error instanceof Error ? error.message : '';
  return (
    message.startsWith('Request failed') ||
    message.includes('Failed to fetch') ||
    message.includes('NetworkError') ||
    message.includes('timed out') ||
    message.includes('Timeout')
  );
}

export async function updateDeckPage(deckId: string, page: number, ownerIdentity: string): Promise<void> {
  const response = await fetch(`/api/slides/${deckId}/page`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ page, owner_identity: ownerIdentity }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
}

export async function stopDeck(deckId: string, ownerIdentity: string): Promise<void> {
  const response = await fetch(`/api/slides/${deckId}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ owner_identity: ownerIdentity }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
}

export async function fetchDeckNotes(deckId: string, ownerIdentity: string): Promise<string[]> {
  const response = await fetch(
    `/api/slides/${deckId}/notes?owner_identity=${encodeURIComponent(ownerIdentity)}`,
    { credentials: 'include' },
  );
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { notes?: string[] };
  return normalizeNotes(body.notes);
}

export async function fetchDeckFile(deckId: string): Promise<Uint8Array> {
  const response = await fetch(`/api/slides/${deckId}/file`, {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return new Uint8Array(await response.arrayBuffer());
}

function normalizeNotes(notes: string[] | undefined): string[] {
  if (!Array.isArray(notes)) {
    return [];
  }
  return notes.map((note) => (typeof note === 'string' ? note : ''));
}
