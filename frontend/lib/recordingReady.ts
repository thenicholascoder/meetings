/**
 * When the custom template may call EgressHelper.startRecording().
 *
 * Same checks as the built-in Room Composite template: wait until a published
 * video track has decoded a frame, and otherwise give a camera a brief chance
 * to appear. A room that never publishes video still starts, so a meeting
 * with every camera and microphone off still writes a file.
 *
 * https://github.com/livekit/egress/blob/main/template-default/src/Room.tsx
 */
export const FRAME_DECODE_TIMEOUT_MS = 5000;
export const NO_VIDEO_START_DELAY_MS = 500;
/** getStats can hang. Skip that sample instead of blocking the start signal. */
export const STATS_WAIT_MS = 1000;
/**
 * Egress aborts with "Start signal not received" if this page never prints
 * START_RECORDING. Send it even when the room socket is still opening.
 */
export const START_SIGNAL_DEADLINE_MS = 12000;

export function recordingShouldStart(input: {
  elapsedMs: number;
  hasVideoTracks: boolean;
  hasDecodedFrames: boolean;
}): boolean {
  if (input.hasDecodedFrames) {
    return true;
  }
  if (!input.hasVideoTracks && input.elapsedMs >= NO_VIDEO_START_DELAY_MS) {
    return true;
  }
  return input.elapsedMs >= FRAME_DECODE_TIMEOUT_MS;
}

export function recordingDeadlineReached(elapsedMs: number): boolean {
  return elapsedMs >= START_SIGNAL_DEADLINE_MS;
}
