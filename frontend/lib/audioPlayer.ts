// Dashboard audio player for GET /audio (docs/AUDIO_WIRE.md, Part A4). The browser specifics (AudioContext,
// AudioWorkletNode, WebSocket) are injected, so the frame path is tested with fakes. Creating the context
// must happen inside the Listen tap: browsers allow audio only after a user gesture.
import { type AudioFrame, decodeFrameSamples, parseViewerFrame, toFloat32 } from "./adpcm";
import { AudioRing, levelPercent, rms } from "./audioRing";
import { type ListenErrorReason, closeReason, reconnectDelayMs, shouldReconnect } from "./listenState";
import type { BackendBase } from "./config";

export const WORKLET_URL = "/audio-worklet.js";
export const WORKLET_NAME = "btb-audio-out";
const CHUNK = 1600; // 100 ms at 16 kHz
const STATUS_EVERY_MS = 200;

/** The WebSocket URL for GET /audio: same origin as /ws, the token in pin mode (like useBackend.ts). */
export function audioSocketUrl(base: Pick<BackendBase, "ws">, opts: { station?: string; token?: string | null } = {}): string {
  const q = new URLSearchParams();
  if (opts.station) q.set("station", opts.station);
  if (opts.token) q.set("token", opts.token);
  const qs = q.toString();
  return `${base.ws}/audio${qs ? `?${qs}` : ""}`;
}

// ---- injected browser surface -------------------------------------------------------------------

export interface GainLike {
  gain: { value: number };
  connect(node: unknown): void;
  disconnect(): void;
}
export interface PlayerContextLike {
  readonly sampleRate: number;
  readonly state?: string;
  readonly destination: unknown;
  readonly audioWorklet: { addModule(url: string): Promise<void> };
  resume(): Promise<void>;
  close(): Promise<void>;
  createGain(): GainLike;
}
export interface WorkletNodeLike {
  readonly port: {
    postMessage(message: unknown, transfer?: Transferable[]): void;
    onmessage: ((e: { data: unknown }) => void) | null;
  };
  connect(node: unknown): void;
  disconnect(): void;
}
export interface SocketLike {
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((e: { data: unknown }) => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: (() => void) | null;
  close(code?: number, reason?: string): void;
}

export interface AudioPlayerFactories {
  createContext: () => PlayerContextLike;
  createWorkletNode: (ctx: PlayerContextLike) => WorkletNodeLike;
  createSocket: (url: string) => SocketLike;
  /** setTimeout/clearTimeout, injectable for the reconnect test. */
  schedule?: (fn: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}

export function browserFactories(): AudioPlayerFactories {
  return {
    createContext: () => new AudioContext() as unknown as PlayerContextLike,
    createWorkletNode: (ctx) =>
      new AudioWorkletNode(ctx as unknown as AudioContext, WORKLET_NAME, {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      }) as unknown as WorkletNodeLike,
    createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
  };
}

// ---- public state -------------------------------------------------------------------------------

export interface PlayerStats {
  connected: boolean;
  codec: number | null;
  station: string | null;
  frames: number;
  droppedFrames: number;
  seqGaps: number;
  underruns: number;
  level: number; // 0..100 meter position
  muted: boolean;
}

export type PlayerEnd = { reason: ListenErrorReason | null; code: number };

export interface AudioPlayerOptions {
  url: string;
  station?: string | null;
  factories: AudioPlayerFactories;
  onStatus?: (s: PlayerStats) => void;
  /** Called once when the player stops for good: a refusal, a close it will not retry, or unsupported. */
  onEnd?: (end: PlayerEnd) => void;
  /** Called when the audio_hello arrives (listening). */
  onHello?: () => void;
  /** Called on each retry attempt (connecting). */
  onRetry?: () => void;
}

interface HelloMessage {
  type: "audio_hello";
  codec: number;
  rate: number;
  chunk_ms: number;
  station: string;
}

function isHello(v: unknown): v is HelloMessage {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return o.type === "audio_hello" && typeof o.codec === "number" && o.rate === 16000;
}

export class AudioPlayer {
  private readonly opts: AudioPlayerOptions;
  private ctx: PlayerContextLike | null = null;
  private node: WorkletNodeLike | null = null;
  private gain: GainLike | null = null;
  private socket: SocketLike | null = null;
  private retryHandle: unknown = null;
  private stopped = false;
  private attempts = 0;
  private connected = false;
  private codec: number | null = null;
  private station: string | null = null;
  private lastSeq: number | null = null;
  private muted = true;
  private level = 0;
  private frames = 0;
  private droppedFrames = 0;
  private seqGaps = 0;
  private sent = 0; // 16 kHz samples posted to the worklet
  private consumed = 0; // 16 kHz samples the worklet has played
  private lastEmit = 0;
  readonly ring = new AudioRing();

  constructor(opts: AudioPlayerOptions) {
    this.opts = opts;
  }

  /** Must run synchronously inside the user's tap. Muted until setMuted(false). */
  start(): void {
    if (this.ctx) return;
    this.stopped = false;
    let ctx: PlayerContextLike;
    try {
      ctx = this.opts.factories.createContext();
    } catch {
      this.end({ reason: "unsupported", code: 0 });
      return;
    }
    this.ctx = ctx;
    void ctx.resume().catch(() => undefined);
    this.gain = ctx.createGain();
    this.gain.gain.value = 0;
    this.gain.connect(ctx.destination);
    void ctx.audioWorklet
      .addModule(WORKLET_URL)
      .then(() => {
        if (this.stopped) return;
        const node = this.opts.factories.createWorkletNode(ctx);
        this.node = node;
        node.port.onmessage = (e) => this.onWorkletMessage(e.data);
        node.connect(this.gain);
        this.connect();
      })
      .catch(() => {
        if (!this.stopped) this.end({ reason: "unsupported", code: 0 });
      });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.gain) this.gain.gain.value = muted ? 0 : 1;
    this.emit(true);
  }

  /** Close the socket and the context and drop all state. Safe to call twice. */
  stop(): void {
    if (this.stopped && !this.ctx && !this.socket) return;
    this.stopped = true;
    if (this.retryHandle !== null) (this.opts.factories.cancel ?? clearTimeout)(this.retryHandle as never);
    this.retryHandle = null;
    const ws = this.socket;
    this.socket = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close(1000, "listen stopped");
      } catch {
        /* already closed */
      }
    }
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
    }
    this.gain?.disconnect();
    const ctx = this.ctx;
    this.ctx = null;
    this.node = null;
    this.gain = null;
    this.connected = false;
    this.ring.clear();
    this.sent = 0;
    this.consumed = 0;
    if (ctx) void ctx.close().catch(() => undefined);
    this.emit(true);
  }

  get running(): boolean {
    return this.ctx !== null && !this.stopped;
  }

  snapshot(): PlayerStats {
    return {
      connected: this.connected,
      codec: this.codec,
      station: this.station,
      frames: this.frames,
      droppedFrames: this.droppedFrames,
      seqGaps: this.seqGaps,
      underruns: this.ring.underruns,
      level: this.level,
      muted: this.muted,
    };
  }

  // ---- socket -----------------------------------------------------------------------------------

  private connect(): void {
    if (this.stopped) return;
    const ws = this.opts.factories.createSocket(this.opts.url);
    ws.binaryType = "arraybuffer";
    this.socket = ws;
    ws.onmessage = (e) => this.onSocketMessage(e.data);
    ws.onerror = () => undefined; // onclose carries the decision
    ws.onclose = (e) => this.onSocketClose(e.code);
  }

  private onSocketClose(code: number): void {
    this.socket = null;
    this.connected = false;
    if (this.stopped) return;
    if (shouldReconnect(code, this.attempts)) {
      this.attempts += 1;
      this.opts.onRetry?.();
      this.emit(true);
      const delay = reconnectDelayMs(this.attempts);
      const sched = this.opts.factories.schedule ?? setTimeout;
      this.retryHandle = sched(() => {
        this.retryHandle = null;
        this.connect();
      }, delay);
      return;
    }
    this.end({ reason: closeReason(code) ?? "ended", code });
  }

  private end(end: PlayerEnd): void {
    this.opts.onEnd?.(end);
    this.stop();
  }

  private onSocketMessage(data: unknown): void {
    if (typeof data === "string") {
      let msg: unknown;
      try {
        msg = JSON.parse(data);
      } catch {
        this.droppedFrames += 1;
        return;
      }
      if (!isHello(msg) || (msg.codec !== 0 && msg.codec !== 1)) {
        this.droppedFrames += 1;
        return;
      }
      if (this.codec !== null && this.codec !== msg.codec) this.ring.clear();
      this.codec = msg.codec;
      this.station = msg.station;
      this.connected = true;
      this.attempts = 0;
      this.opts.onHello?.();
      this.emit(true);
      return;
    }
    if (!(data instanceof ArrayBuffer)) return;
    let frame: AudioFrame;
    let samples: Float32Array;
    try {
      frame = parseViewerFrame(new Uint8Array(data));
      samples = toFloat32(decodeFrameSamples(frame));
    } catch {
      this.droppedFrames += 1;
      return;
    }
    this.frames += 1;
    if (this.lastSeq !== null) {
      const gap = (frame.seq - this.lastSeq - 1) >>> 0;
      if (gap > 0 && gap < 0x10000) this.seqGaps += gap;
    }
    this.lastSeq = frame.seq;
    this.level = levelPercent(rms(samples));
    this.ring.push(samples);
    this.pump();
    this.emit(false);
  }

  // ---- worklet ----------------------------------------------------------------------------------

  private onWorkletMessage(data: unknown): void {
    const m = data as { type?: string; n?: number } | null;
    if (m && m.type === "consumed" && typeof m.n === "number") {
      this.consumed = m.n;
      this.pump();
    }
  }

  /** Post 100 ms blocks from the jitter buffer while the worklet holds less than the target. */
  private pump(): void {
    const node = this.node;
    if (!node || this.stopped) return;
    while (!this.ring.priming && this.sent - this.consumed < this.ring.targetSamples) {
      const block = new Float32Array(CHUNK);
      this.ring.read(block);
      node.port.postMessage({ type: "push", samples: block }, [block.buffer]);
      this.sent += CHUNK;
    }
  }

  private emit(force: boolean): void {
    const now = Date.now();
    if (!force && now - this.lastEmit < STATUS_EVERY_MS) return;
    this.lastEmit = now;
    this.opts.onStatus?.(this.snapshot());
  }
}
