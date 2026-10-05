import { TokenSource } from 'livekit-client';

const TOKEN_ENDPOINT = process.env.NEXT_PUBLIC_LK_TOKEN_ENDPOINT ?? '/api/getToken';

/**
 * Direct TokenSource fetch. The meeting UI does not use this.
 * Join goes through meetingApi so the host can admit people first.
 * POST /api/getToken is refused for that reason.
 */
export async function fetchLiveKitToken(input: {
  roomName: string;
  participantName: string;
  participantIdentity?: string;
  participantMetadata?: string;
}): Promise<{ serverUrl: string; participantToken: string }> {
  const url = new URL(TOKEN_ENDPOINT, window.location.origin).toString();
  const tokenSource = TokenSource.endpoint(url);
  const creds = await tokenSource.fetch({
    roomName: input.roomName,
    participantName: input.participantName,
    participantIdentity: input.participantIdentity,
    participantMetadata: input.participantMetadata,
  });
  return {
    serverUrl: creds.serverUrl,
    participantToken: creds.participantToken,
  };
}
