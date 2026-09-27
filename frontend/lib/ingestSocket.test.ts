import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IngestSocket, type IngestState, type SocketLike } from "./ingestSocket";

class FakeSocket implements SocketLike {
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "blob";
  sent: (string | Uint8Array)[] = [];
  closedWith: [number | undefined, string | undefined] | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) {}
  send(data: string | Uint8Array) { this.sent.push(data); }
  close(code?: number, reason?: string) { this.closedWith = [code, reason]; this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.({}); }
  drop(code = 1006, reason = "") { this.readyState = 3; this.onclose?.({ code, reason }); }
}

function setup() {
  const sockets: FakeSocket[] = [];
  const states: IngestState[] = [];
  let helloCount = 0;
  const client = new IngestSocket({
    url: "wss://b.example/ingest",
    hello: () => `hello-${++helloCount}`,
    onState: (s) => states.push(s),
    makeSocket: (url) => { const s = new FakeSocket(url); sockets.push(s); return s; },
  });
  return { client, sockets, states, last: () => states[states.length - 1] };
}

describe("IngestSocket", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000_000); });
  afterEach(() => { vi.useRealTimers(); });

  it("sends the hello on open, then binary messages", () => {
    const { client, sockets, last } = setup();
    client.start();
    expect(sockets[0].url).toBe("wss://b.example/ingest");
    expect(sockets[0].binaryType).toBe("arraybuffer");
    expect(client.send(new Uint8Array([1]))).toBe(false); // not open yet
    sockets[0].open();
    expect(last().status).toBe("open");
    expect(client.send(new Uint8Array([1, 2]))).toBe(true);
    expect(sockets[0].sent).toEqual(["hello-1", new Uint8Array([1, 2])]);
  });

  it("reconnects with backoff and resends the hello", () => {
    const { client, sockets, last } = setup();
    client.start();
    sockets[0].open();
    sockets[0].drop(1006);
    expect(last()).toMatchObject({ status: "retrying", attempt: 1, reconnects: [1_000_000], message: null });
    vi.advanceTimersByTime(499);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    sockets[1].drop(1006); // failed attempt: 1 s next
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    sockets[2].open();
    expect(sockets[2].sent).toEqual(["hello-2"]);
    expect(last()).toMatchObject({ status: "open", attempt: 0 });
  });

  it("busy (4409) keeps retrying and explains why", () => {
    const { client, sockets, last } = setup();
    client.start();
    sockets[0].open();
    sockets[0].drop(4409, "Another phone is already streaming");
    expect(last().status).toBe("retrying");
    expect(last().message).toMatch(/Another phone is already streaming/);
    vi.advanceTimersByTime(500);
    expect(sockets).toHaveLength(2);
  });

  it("bad hello (4400) and replaced (4408) stop for good", () => {
    for (const code of [4400, 4408]) {
      const { client, sockets, last } = setup();
      client.start();
      sockets[0].open();
      sockets[0].drop(code);
      expect(last().status).toBe("stopped");
      expect(last().message).not.toBeNull();
      vi.advanceTimersByTime(60_000);
      expect(sockets).toHaveLength(1);
    }
  });

  it("stop closes with 1000 and never reconnects", () => {
    const { client, sockets, last } = setup();
    client.start();
    sockets[0].open();
    sockets[0].bufferedAmount = 1234;
    expect(client.bufferedAmount).toBe(1234);
    client.stop();
    expect(sockets[0].closedWith).toEqual([1000, "Stopped"]);
    expect(last().status).toBe("stopped");
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
    expect(client.send(new Uint8Array([1]))).toBe(false);
  });
});
