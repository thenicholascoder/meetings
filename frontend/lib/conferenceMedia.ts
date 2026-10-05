import {
  AudioPresets,
  ScreenSharePresets,
  VideoPresets,
  type AudioCaptureOptions,
  type RoomOptions,
  type ScreenShareCaptureOptions,
  type TrackPublishDefaults,
  type TrackPublishOptions,
  type VideoCodec,
} from 'livekit-client';

/**
 * Media defaults for a meeting that should stay in the same latency range as
 * Zoom (about 100–200ms), using the LiveKit client controls for that:
 * https://docs.livekit.io/transport/media/subscribe/
 * https://docs.livekit.io/transport/media/advanced/
 *
 * VP8 simulcast is the low-delay path. VP9 and AV1 stay available with
 * ?codec=, and they use SVC instead of simulcast. A 4K capture is not used:
 * encoding it is what makes a call feel slower than a normal meeting.
 */

const svcCodecs = new Set<VideoCodec>(['vp9', 'av1']);

export function meetingVideoCodec(
  requested: VideoCodec | undefined,
  e2eeEnabled: boolean,
): VideoCodec {
  const codec = requested ?? 'vp8';
  // Insertable streams used for E2EE do not support these codecs.
  if (e2eeEnabled && svcCodecs.has(codec)) {
    return 'vp8';
  }
  return codec;
}

export function meetingPublishDefaults(options: {
  codec: VideoCodec | undefined;
  hq: boolean;
  e2eeEnabled: boolean;
}): TrackPublishDefaults {
  const videoCodec = meetingVideoCodec(options.codec, options.e2eeEnabled);
  const svc = svcCodecs.has(videoCodec);
  const capture = options.hq ? VideoPresets.h1080 : VideoPresets.h720;
  return {
    videoCodec,
    // SVC already carries three spatial layers. Simulcast on top of it
    // encodes the camera twice.
    simulcast: !svc,
    scalabilityMode: svc ? 'L3T3_KEY' : undefined,
    videoEncoding: capture.encoding,
    // Lowest to highest. These are the layers below the capture. Thumbnails
    // subscribe to h180/h360 instead of a second almost-720p encode.
    // https://docs.livekit.io/transport/media/advanced/
    videoSimulcastLayers: svc
      ? undefined
      : options.hq
        ? [VideoPresets.h360, VideoPresets.h720]
        : [VideoPresets.h180, VideoPresets.h360],
    // Publish VP8 as well only when a subscriber cannot decode VP9/AV1.
    // Regression stops the primary encode instead of sending both at once.
    backupCodec: svc ? { codec: 'vp8', encoding: VideoPresets.h720.encoding } : true,
    // 15fps keeps text readable without a 30fps screen encode fighting the camera.
    screenShareEncoding: ScreenSharePresets.h1080fps15.encoding,
    screenShareSimulcastLayers: [ScreenSharePresets.h360fps3, ScreenSharePresets.h720fps5],
    // DTX stops sending silent frames. RED repeats audio so a lost packet
    // does not have to be retransmitted. Both are mono-track features.
    dtx: true,
    red: !options.e2eeEnabled,
    forceStereo: false,
  };
}

export function meetingAudioCapture(deviceId?: string): AudioCaptureOptions {
  return {
    deviceId,
    // Browser echo and noise control. Leave these on when Krisp is not used.
    // https://docs.livekit.io/transport/media/noise-cancellation/
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  };
}

/** Speech bitrate, and a higher priority than video so congestion cuts picture first. */
export function meetingMicrophonePublishOptions(e2eeEnabled: boolean): TrackPublishOptions {
  return {
    audioPreset: { maxBitrate: AudioPresets.speech.maxBitrate, priority: 'high' },
    dtx: true,
    red: !e2eeEnabled,
    forceStereo: false,
  };
}

function isSafariBrowser() {
  if (typeof navigator === 'undefined') {
    return false;
  }
  const ua = navigator.userAgent;
  return /Safari/i.test(ua) && !/Chrome|Chromium|Edg|Android|CriOS|FxiOS/i.test(ua);
}

export function meetingScreenShareCapture(): ScreenShareCaptureOptions {
  return {
    audio: true,
    selfBrowserSurface: 'include',
    // Slides and windows: keep edges sharp and let the frame rate stay low.
    contentHint: 'detail',
    // Safari 17 captures a tiny frame if any resolution is requested.
    // https://bugs.webkit.org/show_bug.cgi?id=263015
    resolution: isSafariBrowser() ? undefined : ScreenSharePresets.h1080fps15.resolution,
  };
}

export function meetingRoomOptions(options: {
  codec: VideoCodec | undefined;
  hq: boolean;
  e2eeEnabled: boolean;
  videoDeviceId?: string;
  audioDeviceId?: string;
  singlePeerConnection?: boolean;
  e2ee?: RoomOptions['e2ee'];
}): RoomOptions {
  const capture = options.hq ? VideoPresets.h1080 : VideoPresets.h720;
  return {
    videoCaptureDefaults: {
      deviceId: options.videoDeviceId,
      resolution: capture.resolution,
    },
    audioCaptureDefaults: meetingAudioCapture(options.audioDeviceId),
    publishDefaults: meetingPublishDefaults(options),
    // Match the physical pixels of each tile, and pause a video that is not
    // on screen. Dynacast then stops encoding layers nobody is watching.
    adaptiveStream: { pixelDensity: 'screen', pauseVideoInBackground: true },
    dynacast: true,
    singlePeerConnection: options.singlePeerConnection,
    e2ee: options.e2ee,
    // Mixing in Web Audio adds a playback buffer on top of the jitter buffer.
    webAudioMix: false,
  };
}
