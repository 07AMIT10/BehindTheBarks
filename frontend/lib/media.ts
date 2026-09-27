// Browser media plumbing for the /camera page: getUserMedia, JPEG frame capture, AudioWorklet PCM
// capture and the screen wake lock. DOM-only and deliberately thin: every decision it needs (sizes,
// skip rule, framing) comes from the pure, tested lib/ingest.ts.
import { type Facing, JPEG_QUALITY, fitLongSide, frameMessage, shouldSkipFrame } from "./ingest";

export async function openMedia(facing: Facing, withVideo: boolean): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new DOMException("getUserMedia needs a secure context (HTTPS or localhost)", "SecurityError");
  }
  return navigator.mediaDevices.getUserMedia({
    video: withVideo
      ? { facingMode: { ideal: facing === "back" ? "environment" : "user" }, width: { ideal: 1280 }, height: { ideal: 720 } }
      : false,
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
}

export function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

/** Create (and resume) the AudioContext. Call it synchronously inside a tap handler: iOS Safari only
 * lets audio start from a user gesture. Returns null if Web Audio is unavailable. */
export function createAudioContext(): AudioContext | null {
  try {
    const ctx = new AudioContext();
    void ctx.resume().catch(() => undefined);
    return ctx;
  } catch {
    return null;
  }
}

/** Mono Float32 chunks of ~100 ms at ctx.sampleRate from the stream's audio track. Returns a stop function. */
export async function startAudioCapture(
  ctx: AudioContext,
  stream: MediaStream,
  onChunk: (samples: Float32Array) => void,
): Promise<() => void> {
  await ctx.audioWorklet.addModule("/pcm-worklet.js");
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture", {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    channelCount: 1,
    channelCountMode: "explicit",
  });
  const mute = ctx.createGain();
  mute.gain.value = 0; // keeps the graph pulling samples without playing the mic back
  node.port.onmessage = (e: MessageEvent<Float32Array>) => onChunk(e.data);
  source.connect(node);
  node.connect(mute);
  mute.connect(ctx.destination);
  if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
  return () => {
    node.port.onmessage = null;
    source.disconnect();
    node.disconnect();
    mute.disconnect();
  };
}

export type FrameCaptureOptions = {
  video: () => HTMLVideoElement | null;
  fps: number;
  isOpen: () => boolean;
  bufferedAmount: () => number;
  send: (msg: Uint8Array) => boolean;
  onSent: () => void;
  onSkip: () => void;
};

/** Every 1/fps s: draw the video onto a canvas (long side <= 640 px), encode JPEG q0.7, send 0x01+JPEG.
 * Skips a tick while the previous frame is still encoding or the socket is backed up. Returns stop. */
export function startFrameCapture(o: FrameCaptureOptions): () => void {
  const canvas = document.createElement("canvas");
  const g = canvas.getContext("2d");
  let encoding = false;
  let stopped = false;

  const tick = () => {
    const v = o.video();
    if (!g || !v || v.readyState < 2 || v.videoWidth === 0 || !o.isOpen()) return;
    if (shouldSkipFrame(o.bufferedAmount(), encoding)) {
      o.onSkip();
      return;
    }
    const { width, height } = fitLongSide(v.videoWidth, v.videoHeight);
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    g.drawImage(v, 0, 0, width, height);
    encoding = true;
    canvas.toBlob(
      (blob) => {
        if (!blob || stopped) {
          encoding = false;
          return;
        }
        blob
          .arrayBuffer()
          .then((buf) => {
            encoding = false;
            if (!stopped && o.send(frameMessage(new Uint8Array(buf)))) o.onSent();
          })
          .catch(() => {
            encoding = false;
          });
      },
      "image/jpeg",
      JPEG_QUALITY,
    );
  };

  const id = setInterval(tick, 1000 / o.fps);
  return () => {
    stopped = true;
    clearInterval(id);
  };
}

/** Screen Wake Lock where supported; re-acquired whenever the page becomes visible again. Returns release. */
export function keepScreenOn(): () => void {
  let sentinel: WakeLockSentinel | null = null;
  let stopped = false;

  const acquire = async () => {
    if (stopped || document.visibilityState !== "visible" || !("wakeLock" in navigator)) return;
    try {
      const s = await navigator.wakeLock.request("screen");
      if (stopped) void s.release().catch(() => undefined);
      else sentinel = s;
    } catch {
      sentinel = null; // denied (battery saver, no gesture): the page still works, the screen may sleep
    }
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") void acquire();
  };

  document.addEventListener("visibilitychange", onVisible);
  void acquire();
  return () => {
    stopped = true;
    document.removeEventListener("visibilitychange", onVisible);
    void sentinel?.release().catch(() => undefined);
    sentinel = null;
  };
}
