// The /ingest WebSocket with auto-reconnect. Sends the hello on every (re)connect. No React here, and
// the socket constructor is injectable so ingestSocket.test.ts can drive it with a fake.
import { backoffMs, closeMessage, shouldReconnect } from "./ingest";

export type SocketLike = {
  readyState: number;
  bufferedAmount: number;
  binaryType: string;
  send(data: string | Uint8Array): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
};

export type IngestStatus = "connecting" | "open" | "retrying" | "stopped";

export type IngestState = {
  status: IngestStatus;
  attempt: number; // consecutive failed attempts since the last successful open
  reconnects: number[]; // Date.now() of every unplanned close
  message: string | null; // closeMessage() of the last close, if any
};

export type IngestSocketOptions = {
  url: string;
  hello: () => string;
  onState: (s: IngestState) => void;
  makeSocket?: (url: string) => SocketLike;
};

const OPEN = 1;
const NORMAL_CLOSURE = 1000;

export class IngestSocket {
  private ws: SocketLike | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  private state: IngestState = { status: "connecting", attempt: 0, reconnects: [], message: null };
  private readonly make: (url: string) => SocketLike;

  constructor(private readonly opts: IngestSocketOptions) {
    this.make = opts.makeSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike);
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  /** Close with 1000 (the backend then forgets this phone instead of showing "dropped"). */
  stop(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onopen = null;
      ws.onerror = null;
      ws.close(NORMAL_CLOSURE, "Stopped");
    }
    this.set({ status: "stopped" });
  }

  get isOpen(): boolean {
    return this.ws !== null && this.ws.readyState === OPEN;
  }

  get bufferedAmount(): number {
    return this.ws?.bufferedAmount ?? 0;
  }

  /** Send one binary message; false (and nothing sent) when the socket isn't open. */
  send(data: Uint8Array): boolean {
    if (!this.isOpen || !this.ws) return false;
    this.ws.send(data);
    return true;
  }

  private set(patch: Partial<IngestState>): void {
    this.state = { ...this.state, ...patch };
    this.opts.onState(this.state);
  }

  private connect(): void {
    if (this.stopped) return;
    this.set({ status: this.state.attempt === 0 ? "connecting" : "retrying" });
    let ws: SocketLike;
    try {
      ws = this.make(this.opts.url);
    } catch {
      this.scheduleRetry(1006);
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      ws.send(this.opts.hello());
      this.set({ status: "open", attempt: 0, message: null });
    };
    ws.onerror = () => undefined; // a close event always follows
    ws.onclose = (ev) => {
      if (this.ws === ws) this.ws = null;
      if (this.stopped) return;
      this.set({ reconnects: [...this.state.reconnects, Date.now()].slice(-20), message: closeMessage(ev.code) });
      if (!shouldReconnect(ev.code)) {
        this.stopped = true;
        this.set({ status: "stopped" });
        return;
      }
      this.scheduleRetry(ev.code);
    };
  }

  private scheduleRetry(code: number): void {
    const attempt = this.state.attempt + 1;
    this.set({ status: "retrying", attempt, message: closeMessage(code) });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), backoffMs(attempt));
  }
}
