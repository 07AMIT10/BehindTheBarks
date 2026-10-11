import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { type PlayerEnd, type PlayerContextLike, type SocketLike, type WorkletNodeLike, AudioPlayer, audioSocketUrl, WORKLET_URL } from "./audioPlayer";
import { CHUNK_SAMPLES, decodeFrameSamples, parseViewerFrame, toFloat32 } from "./adpcm";

interface Vector {
  name: string;
  block_hex: string;
  decoded: number[];
  frame_hex: string;
}
const doc: { vectors: Vector[] } = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../tests/vectors/audio_v1.json"), "utf8"),
);
const hex = (s: string) => Uint8Array.from(s.match(/../g) ?? [], (b) => parseInt(b, 16));

/** A viewer frame (no 0x02) built from a vector block, as GET /audio sends it. */
function viewerFrame(block: Uint8Array, seq: number, tMs = 0): ArrayBuffer {
  const out = new Uint8Array(9 + block.byteLength);
  const view = new DataView(out.buffer);
  out[0] = 1;
  view.setUint32(1, seq, true);
  view.setUint32(5, tMs, true);
  out.set(block, 9);
  return out.buffer;
}

class FakeSocket implements SocketLike {
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed: number | null = null;
  constructor(public url: string) {}
  close(code?: number) {
    this.closed = code ?? 1000;
  }
  receive(data: unknown) {
    this.onmessage?.({ data });
  }
  drop(code: number) {
    this.onclose?.({ code });
  }
}

class FakeNode implements WorkletNodeLike {
  posted: { type: string; samples: Float32Array }[] = [];
  connected: unknown = null;
  disconnected = false;
  port = {
    postMessage: (m: unknown) => {
      this.posted.push(m as { type: string; samples: Float32Array });
    },
    onmessage: null as ((e: { data: unknown }) => void) | null,
  };
  connect(n: unknown) {
    this.connected = n;
  }
  disconnect() {
    this.disconnected = true;
  }
}

function fakeContext() {
  const gain = { gain: { value: 1 }, connect: () => undefined, disconnect: () => undefined };
  const ctx = {
    sampleRate: 48000,
    state: "running",
    destination: "dest",
    closed: false,
    audioWorklet: { addModule: async (url: string) => void (ctx.modules = [...ctx.modules, url]) },
    modules: [] as string[],
    resume: async () => undefined,
    close: async () => {
      ctx.closed = true;
    },
    createGain: () => gain,
    gain,
  };
  return ctx as typeof ctx & PlayerContextLike;
}

const tick = () => new Promise((r) => setTimeout(r, 0));

function harness() {
  const sockets: FakeSocket[] = [];
  const nodes: FakeNode[] = [];
  const ctxs: ReturnType<typeof fakeContext>[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const ends: PlayerEnd[] = [];
  let hellos = 0;
  const player = new AudioPlayer({
    url: "ws://x/audio?station=a",
    factories: {
      createContext: () => {
        const c = fakeContext();
        ctxs.push(c);
        return c;
      },
      createWorkletNode: () => {
        const n = new FakeNode();
        nodes.push(n);
        return n;
      },
      createSocket: (url) => {
        const s = new FakeSocket(url);
        sockets.push(s);
        return s;
      },
      schedule: (fn, ms) => {
        timers.push({ fn, ms });
        return timers.length;
      },
      cancel: () => undefined,
    },
    onEnd: (e) => ends.push(e),
    onHello: () => hellos++,
  });
  return { player, sockets, nodes, ctxs, timers, ends, hellos: () => hellos };
}

const hello = JSON.stringify({ type: "audio_hello", codec: 1, rate: 16000, chunk_ms: 100, station: "a" });

describe("audioSocketUrl", () => {
  it("builds the same-origin /audio URL with station and pin token", () => {
    expect(audioSocketUrl({ ws: "wss://h.example" }, { station: "s 1", token: "t+/=" })).toBe(
      "wss://h.example/audio?station=s+1&token=t%2B%2F%3D",
    );
    expect(audioSocketUrl({ ws: "ws://localhost:8000" })).toBe("ws://localhost:8000/audio");
  });
});

describe("AudioPlayer frame path", () => {
  it("loads the worklet, sends muted audio only after the 300 ms target, and posts decoded blocks", async () => {
    const h = harness();
    h.player.start();
    await tick();
    expect(h.ctxs[0].modules).toEqual([WORKLET_URL]);
    expect(h.sockets).toHaveLength(1);
    expect(h.sockets[0].binaryType).toBe("arraybuffer");
    h.sockets[0].receive(hello);
    expect(h.hellos()).toBe(1);
    expect(h.player.snapshot().muted).toBe(true);

    const v = doc.vectors.find((x) => x.name === "synthetic_mixed")!;
    const block = hex(v.block_hex);
    h.sockets[0].receive(viewerFrame(block, 1));
    h.sockets[0].receive(viewerFrame(block, 2));
    expect(h.nodes[0].posted).toHaveLength(0); // 200 ms: still priming
    h.sockets[0].receive(viewerFrame(block, 3));
    // Primed at 300 ms: the worklet gets 100 ms blocks until it holds the target.
    expect(h.nodes[0].posted.length).toBeGreaterThanOrEqual(3);
    const first = h.nodes[0].posted[0];
    expect(first.type).toBe("push");
    expect(first.samples).toHaveLength(CHUNK_SAMPLES);
    const want = toFloat32(decodeFrameSamples(parseViewerFrame(new Uint8Array(viewerFrame(block, 1)))));
    expect(Array.from(first.samples.subarray(0, 4))).toEqual(Array.from(want.subarray(0, 4)));
    expect(h.player.snapshot()).toMatchObject({ frames: 3, connected: true, codec: 1, station: "a", seqGaps: 0 });
    h.player.stop();
  });

  it("counts a sequence gap, drops malformed frames and keeps going", async () => {
    const h = harness();
    h.player.start();
    await tick();
    h.sockets[0].receive(hello);
    const block = hex(doc.vectors[0].block_hex);
    h.sockets[0].receive(viewerFrame(block, 5));
    h.sockets[0].receive(viewerFrame(block, 8)); // 6 and 7 lost
    h.sockets[0].receive(new Uint8Array([1, 2, 3]).buffer); // too short
    h.sockets[0].receive("not json{");
    const s = h.player.snapshot();
    expect(s.seqGaps).toBe(2);
    expect(s.frames).toBe(2);
    expect(s.droppedFrames).toBe(2);
    h.player.stop();
  });

  it("setMuted toggles the gain without touching the socket", async () => {
    const h = harness();
    h.player.start();
    await tick();
    h.player.setMuted(false);
    expect(h.ctxs[0].gain.gain.value).toBe(1);
    expect(h.player.snapshot().muted).toBe(false);
    h.player.setMuted(true);
    expect(h.ctxs[0].gain.gain.value).toBe(0);
    h.player.stop();
  });
});

describe("AudioPlayer reconnect and refusals", () => {
  it("retries a network drop up to three times with backoff, then ends", async () => {
    const h = harness();
    h.player.start();
    await tick();
    h.sockets[0].receive(hello);
    h.sockets[0].drop(1006);
    expect(h.timers.map((t) => t.ms)).toEqual([500]);
    h.timers[0].fn();
    expect(h.sockets).toHaveLength(2);
    h.sockets[1].drop(1006);
    h.timers[1].fn();
    h.sockets[2].drop(1006);
    h.timers[2].fn();
    h.sockets[3].drop(1006);
    expect(h.timers.map((t) => t.ms)).toEqual([500, 1000, 2000]); // no fourth retry
    expect(h.ends).toEqual([{ reason: "network", code: 1006 }]);
    expect(h.player.running).toBe(false);
  });

  it.each([
    [4401, "unauthorized"],
    [4403, "forbidden"],
    [4411, "kicked"],
    [4412, "lowered"],
  ] as const)("never retries close code %i", async (code, reason) => {
    const h = harness();
    h.player.start();
    await tick();
    h.sockets[0].receive(hello);
    h.sockets[0].drop(code);
    expect(h.timers).toHaveLength(0);
    expect(h.sockets).toHaveLength(1);
    expect(h.ends).toEqual([{ reason, code }]);
    expect(h.player.running).toBe(false);
  });

  it("stop closes the socket and the context, detaches the worklet and cancels a pending retry", async () => {
    const h = harness();
    h.player.start();
    await tick();
    h.sockets[0].receive(hello);
    h.sockets[0].drop(1006); // schedules a retry
    h.player.stop();
    expect(h.ctxs[0].closed).toBe(true);
    expect(h.nodes[0].disconnected).toBe(true);
    expect(h.nodes[0].port.onmessage).toBeNull();
    h.timers[0].fn(); // a late timer must not reopen the socket
    expect(h.sockets).toHaveLength(1);
    h.player.stop(); // idempotent
    expect(h.ctxs).toHaveLength(1);
  });

  it("reports an unsupported browser when the worklet cannot load", async () => {
    const ends: PlayerEnd[] = [];
    const ctx = fakeContext();
    ctx.audioWorklet.addModule = async () => {
      throw new Error("no worklet");
    };
    const p = new AudioPlayer({
      url: "ws://x/audio",
      factories: {
        createContext: () => ctx,
        createWorkletNode: () => new FakeNode(),
        createSocket: (u) => new FakeSocket(u),
      },
      onEnd: (e) => ends.push(e),
    });
    p.start();
    await tick();
    expect(ends).toEqual([{ reason: "unsupported", code: 0 }]);
    expect(ctx.closed).toBe(true);
  });
});
