export type MeetingRole = 'host' | 'cohost' | 'participant';

export type AdmittedMeeting = {
  status: 'admitted';
  serverUrl: string;
  participantToken: string;
  role: MeetingRole;
  identity: string;
};

export type PendingJoin = {
  status: 'pending';
  requestId: string;
  participantKey: string;
};

export type WaitingRequest = {
  id: string;
  name: string;
};

type EndedJoin = { status: 'ended'; detail: string };
type MissingJoin = { status: 'missing'; detail: string };
type DeclinedJoin = { status: 'declined' };

export type JoinResult = AdmittedMeeting | PendingJoin | EndedJoin | MissingJoin;
export type AdmissionResult = AdmittedMeeting | { status: 'pending' } | DeclinedJoin | EndedJoin;

function hostKeyName(roomName: string) {
  return `meetings:host:${roomName}`;
}

function participantKeyName(roomName: string) {
  return `meetings:participant:${roomName}`;
}

export function readHostKey(roomName: string): string | null {
  try {
    // FOR PRODUCTION - users are unique on this browser, so every tab is the same person
    // return localStorage.getItem(hostKeyName(roomName));
    // FOR TESTING - each tab can join as its own person instead of replacing the host
    return sessionStorage.getItem(hostKeyName(roomName));
  } catch {
    return null;
  }
}

export function saveHostKey(roomName: string, hostKey: string) {
  // FOR PRODUCTION - users are unique on this browser, so every tab is the same person
  // localStorage.setItem(hostKeyName(roomName), hostKey);
  // FOR TESTING - each tab can join as its own person instead of replacing the host
  sessionStorage.setItem(hostKeyName(roomName), hostKey);
}

export function readParticipantKey(roomName: string): string | null {
  try {
    // FOR PRODUCTION - users are unique on this browser, so every tab is the same person
    // return localStorage.getItem(participantKeyName(roomName));
    // FOR TESTING - each tab can join as its own person instead of replacing the host
    return sessionStorage.getItem(participantKeyName(roomName));
  } catch {
    return null;
  }
}

export function saveParticipantKey(roomName: string, participantKey: string) {
  // FOR PRODUCTION - users are unique on this browser, so every tab is the same person
  // localStorage.setItem(participantKeyName(roomName), participantKey);
  // FOR TESTING - each tab can join as its own person instead of replacing the host
  sessionStorage.setItem(participantKeyName(roomName), participantKey);
}

function authHeaders(hostKey?: string | null, participantKey?: string | null): HeadersInit {
  const headers: Record<string, string> = {};
  if (hostKey) {
    headers['X-Host-Key'] = hostKey;
  }
  if (participantKey) {
    headers['X-Participant-Key'] = participantKey;
  }
  return headers;
}

async function readBody(response: Response): Promise<Record<string, unknown>> {
  try {
    const body = await response.json();
    return body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export class MeetingRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function detailOf(body: Record<string, unknown>, fallback: string) {
  return typeof body.detail === 'string' && body.detail ? body.detail : fallback;
}

function admittedFrom(body: Record<string, unknown>): AdmittedMeeting {
  return {
    status: 'admitted',
    serverUrl: String(body.server_url || ''),
    participantToken: String(body.participant_token || ''),
    role: body.role === 'host' || body.role === 'cohost' ? body.role : 'participant',
    identity: String(body.identity || ''),
  };
}

export async function createMeeting(roomName: string): Promise<{ roomName: string; hostKey: string }> {
  const response = await fetch('/api/meetings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ room_name: roomName }),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not start the meeting'), response.status);
  }
  return { roomName: String(body.room_name || roomName), hostKey: String(body.host_key || '') };
}

export async function joinMeeting(input: {
  roomName: string;
  participantName: string;
  hostKey?: string | null;
  participantKey?: string | null;
}): Promise<JoinResult> {
  const response = await fetch(`/api/meetings/${encodeURIComponent(input.roomName)}/join`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(input.hostKey, input.participantKey),
    },
    body: JSON.stringify({ participant_name: input.participantName }),
  });
  const body = await readBody(response);
  if (response.status === 404) {
    return { status: 'missing', detail: detailOf(body, "This meeting hasn't been started by a host.") };
  }
  if (response.status === 409 && body.status === 'ended') {
    return { status: 'ended', detail: detailOf(body, 'The host has ended the meeting.') };
  }
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not join the meeting'), response.status);
  }
  if (body.status === 'pending') {
    return {
      status: 'pending',
      requestId: String(body.request_id || ''),
      participantKey: String(body.participant_key || ''),
    };
  }
  return admittedFrom(body);
}

export async function pollAdmission(input: {
  roomName: string;
  requestId: string;
  participantKey: string;
}): Promise<AdmissionResult> {
  const response = await fetch(
    `/api/meetings/${encodeURIComponent(input.roomName)}/admission/${encodeURIComponent(input.requestId)}`,
    { headers: authHeaders(null, input.participantKey) },
  );
  const body = await readBody(response);
  if (response.status === 409 && body.status === 'ended') {
    return { status: 'ended', detail: detailOf(body, 'The host has ended the meeting.') };
  }
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not check the waiting room'), response.status);
  }
  if (body.status === 'pending') {
    return { status: 'pending' };
  }
  if (body.status === 'declined') {
    return { status: 'declined' };
  }
  if (body.status === 'ended') {
    return { status: 'ended', detail: detailOf(body, 'The host has ended the meeting.') };
  }
  return admittedFrom(body);
}

export async function listWaiting(input: {
  roomName: string;
  hostKey?: string | null;
  participantKey?: string | null;
}): Promise<WaitingRequest[]> {
  const response = await fetch(`/api/meetings/${encodeURIComponent(input.roomName)}/waiting`, {
    headers: authHeaders(input.hostKey, input.participantKey),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not check who is waiting'), response.status);
  }
  const requests = Array.isArray(body.requests) ? body.requests : [];
  return requests.map((item) => {
    const row = item as Record<string, unknown>;
    return { id: String(row.id || ''), name: String(row.name || 'Someone') };
  });
}

async function postAction(
  path: string,
  hostKey: string | null,
  participantKey: string | null,
  payload?: Record<string, string>,
) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(hostKey, participantKey),
    },
    body: JSON.stringify(payload ?? {}),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'That action was refused'), response.status);
  }
}

export function acceptWaiting(input: {
  roomName: string;
  requestId: string;
  hostKey?: string | null;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/admission/${encodeURIComponent(input.requestId)}/accept`,
    input.hostKey ?? null,
    input.participantKey ?? null,
  );
}

export function declineWaiting(input: {
  roomName: string;
  requestId: string;
  hostKey?: string | null;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/admission/${encodeURIComponent(input.requestId)}/decline`,
    input.hostKey ?? null,
    input.participantKey ?? null,
  );
}

export type CohostInvite = {
  id: string;
  hostName: string;
};

export async function listCohostInvites(input: {
  roomName: string;
  hostKey?: string | null;
}): Promise<{ id: string; identity: string; name: string }[]> {
  const response = await fetch(`/api/meetings/${encodeURIComponent(input.roomName)}/cohost-invites`, {
    headers: authHeaders(input.hostKey, null),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not check co-host invitations'), response.status);
  }
  const invites = Array.isArray(body.invites) ? body.invites : [];
  return invites.map((item) => {
    const row = item as Record<string, unknown>;
    return {
      id: String(row.id || ''),
      identity: String(row.identity || ''),
      name: String(row.name || 'Someone'),
    };
  });
}

export async function myCohostInvite(input: {
  roomName: string;
  participantKey?: string | null;
}): Promise<CohostInvite | null> {
  const response = await fetch(`/api/meetings/${encodeURIComponent(input.roomName)}/cohost-invite`, {
    headers: authHeaders(null, input.participantKey),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not check the co-host invitation'), response.status);
  }
  const invite = body.invite as Record<string, unknown> | null;
  if (!invite || !invite.id) {
    return null;
  }
  return { id: String(invite.id), hostName: String(invite.host_name || 'The host') };
}

export function acceptCohostInvite(input: {
  roomName: string;
  inviteId: string;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/cohost-invites/${encodeURIComponent(input.inviteId)}/accept`,
    null,
    input.participantKey ?? null,
  );
}

export function declineCohostInvite(input: {
  roomName: string;
  inviteId: string;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/cohost-invites/${encodeURIComponent(input.inviteId)}/decline`,
    null,
    input.participantKey ?? null,
  );
}

export function setParticipantRole(input: {
  roomName: string;
  identity: string;
  role: 'cohost' | 'participant';
  hostKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/participants/${encodeURIComponent(input.identity)}/role`,
    input.hostKey ?? null,
    null,
    { role: input.role },
  );
}

export function removeParticipant(input: {
  roomName: string;
  identity: string;
  hostKey?: string | null;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/participants/${encodeURIComponent(input.identity)}/remove`,
    input.hostKey ?? null,
    input.participantKey ?? null,
  );
}

export function muteParticipant(input: {
  roomName: string;
  identity: string;
  hostKey?: string | null;
  participantKey?: string | null;
}) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/participants/${encodeURIComponent(input.identity)}/mute`,
    input.hostKey ?? null,
    input.participantKey ?? null,
  );
}

export function endMeeting(input: { roomName: string; hostKey?: string | null }) {
  return postAction(
    `/api/meetings/${encodeURIComponent(input.roomName)}/end`,
    input.hostKey ?? null,
    null,
  );
}

export type RoomRecording = {
  egressId: string;
  status: string;
  key: string;
  sizeBytes: number | null;
  error: string;
  startedAt: string | null;
  endedAt: string | null;
};

export async function listRecordings(input: {
  roomName: string;
  hostKey?: string | null;
  participantKey?: string | null;
}): Promise<RoomRecording[]> {
  const response = await fetch(`/api/recordings?roomName=${encodeURIComponent(input.roomName)}`, {
    headers: authHeaders(input.hostKey, input.participantKey),
  });
  const body = await readBody(response);
  if (!response.ok) {
    throw new MeetingRequestError(detailOf(body, 'Could not load recordings'), response.status);
  }
  const rows = Array.isArray(body.recordings) ? body.recordings : [];
  return rows.map((row) => {
    const item = row as Record<string, unknown>;
    return {
      egressId: String(item.egressId || ''),
      status: String(item.status || ''),
      key: String(item.key || ''),
      sizeBytes: typeof item.sizeBytes === 'number' ? item.sizeBytes : null,
      error: String(item.error || ''),
      startedAt: typeof item.startedAt === 'string' ? item.startedAt : null,
      endedAt: typeof item.endedAt === 'string' ? item.endedAt : null,
    };
  });
}

export async function openRecording(input: {
  roomName: string;
  key: string;
  hostKey?: string | null;
  participantKey?: string | null;
}) {
  const params = new URLSearchParams({
    roomName: input.roomName,
    key: input.key,
    as: 'url',
  });
  const response = await fetch(`/api/recordings/file?${params}`, {
    headers: authHeaders(input.hostKey, input.participantKey),
  });
  if (!response.ok) {
    const body = await readBody(response);
    throw new MeetingRequestError(detailOf(body, 'Could not open that recording'), response.status);
  }
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    const body = await readBody(response);
    const url = typeof body.url === 'string' ? body.url : '';
    if (!url) {
      throw new MeetingRequestError('Could not open that recording', response.status);
    }
    window.open(url, '_blank', 'noopener');
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener');
}

/** URL the in-call player can show. A presigned link plays directly; otherwise the file is kept in memory. */
export async function loadRecordingSrc(input: {
  roomName: string;
  key: string;
  hostKey?: string | null;
  participantKey?: string | null;
}): Promise<{ src: string; revoke?: () => void }> {
  const params = new URLSearchParams({
    roomName: input.roomName,
    key: input.key,
  });
  const response = await fetch(`/api/recordings/file?${params}`, {
    headers: authHeaders(input.hostKey, input.participantKey),
  });
  if (!response.ok) {
    const body = await readBody(response);
    throw new MeetingRequestError(detailOf(body, 'Could not open that recording'), response.status);
  }
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    const body = await readBody(response);
    const url = typeof body.url === 'string' ? body.url : '';
    if (!url) {
      throw new MeetingRequestError('Could not open that recording', response.status);
    }
    return { src: url };
  }
  const blob = await response.blob();
  const playable = blob.type.startsWith('video/') ? blob : new Blob([blob], { type: 'video/mp4' });
  const src = URL.createObjectURL(playable);
  return { src, revoke: () => URL.revokeObjectURL(src) };
}
