export type SlideFormat = 'pdf' | 'pptx';

export type Deck = {
  deckId: string;
  page: number;
  pageCount: number;
  name: string;
  format: SlideFormat;
  ownerIdentity: string;
  ownerName: string;
  hasNotes: boolean;
};

export function isPdfBytes(bytes: Uint8Array): boolean {
  return (
    bytes.length >= 5 &&
    bytes[0] === 0x25 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x44 &&
    bytes[3] === 0x46 &&
    bytes[4] === 0x2d
  );
}

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

export type SlideRoomState = {
  deck: Deck | null;
};

export async function fetchSlideState(roomName: string): Promise<SlideRoomState> {
  const response = await fetch(`/api/slides?roomName=${encodeURIComponent(roomName)}`, {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const body = (await response.json()) as { deck?: Deck | null };
  if (body.deck) {
    body.deck.hasNotes = body.deck.hasNotes === true;
    body.deck.format = body.deck.format === 'pptx' ? 'pptx' : 'pdf';
  }
  return { deck: body.deck ?? null };
}

export async function fetchCurrentDeck(roomName: string): Promise<Deck | null> {
  const state = await fetchSlideState(roomName);
  return state.deck;
}

export type UploadedDeck = { status: 'ready'; deck: Deck; notes: string[] };

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
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { deck: Deck; notes?: string[] };
  payload.deck.format = payload.deck.format === 'pptx' ? 'pptx' : 'pdf';
  return { status: 'ready', deck: payload.deck, notes: normalizeNotes(payload.notes) };
}

const RECOVER_WAIT_MS = 20_000;

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
