// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTalk, type TalkControl, type UseTalkOptions } from "./useTalk";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let trackStop = vi.fn();
let ctxClose = vi.fn();
let processorDisconnect = vi.fn();
let getUserMedia = vi.fn();

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

class FakeSource {
  connect = vi.fn();
  disconnect = vi.fn();
}

class FakeProcessor {
  onaudioprocess: ((e: unknown) => void) | null = null;
  connect = vi.fn();
  disconnect = () => processorDisconnect();
}

class FakeAudioContext {
  sampleRate = 48000;
  destination = {};
  createMediaStreamSource = () => new FakeSource();
  createScriptProcessor = () => new FakeProcessor();
  close = () => ctxClose();
}

function makeStream(): MediaStream {
  return { getTracks: () => [{ stop: trackStop }] } as unknown as MediaStream;
}

let latest: TalkControl;
const roots: Root[] = [];

function Harness(props: UseTalkOptions) {
  const control = useTalk(props);
  // Test-only snapshot of the control; done in an effect to keep render pure.
  useEffect(() => {
    latest = control;
  });
  const state = control.talking ? "talking" : control.starting ? "starting" : "idle";
  return <div data-state={state}>{state}</div>;
}

function renderHarness(opts: Partial<UseTalkOptions> = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() =>
    root.render(<Harness http={opts.http ?? "http://be"} token={opts.token ?? null} disabled={opts.disabled} />)
  );
  return container;
}

beforeEach(() => {
  trackStop = vi.fn();
  ctxClose = vi.fn();
  processorDisconnect = vi.fn();
  getUserMedia = vi.fn();
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  const win = window as unknown as { AudioContext: unknown };
  win.AudioContext = FakeAudioContext;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) })));
});

afterEach(() => {
  while (roots.length) {
    const root = roots.pop()!;
    act(() => root.unmount());
  }
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (window as unknown as { AudioContext?: unknown }).AudioContext;
});

describe("useTalk pending-release latch", () => {
  it("a release during the permission dialog stops the mic as soon as it is granted", async () => {
    const d = deferred<MediaStream>();
    getUserMedia.mockReturnValue(d.promise);
    renderHarness();

    await act(async () => {
      void latest.startTalk();
    });
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(latest.starting).toBe(true);
    expect(latest.talking).toBe(false);

    // User releases while the dialog is still up: latch, no-op teardown yet.
    await act(async () => {
      latest.stopTalk();
    });
    expect(trackStop).not.toHaveBeenCalled();
    expect(latest.talking).toBe(false);

    // Permission lands: the promised stop must happen immediately, not stream forever.
    await act(async () => {
      d.resolve(makeStream());
    });
    expect(trackStop).toHaveBeenCalledTimes(1);
    expect(processorDisconnect).toHaveBeenCalledTimes(1);
    expect(ctxClose).toHaveBeenCalledTimes(1);
    expect(latest.talking).toBe(false);
    expect(latest.starting).toBe(false);
  });

  it("a normal hold cycles talking and releases the mic", async () => {
    const d = deferred<MediaStream>();
    getUserMedia.mockReturnValue(d.promise);
    renderHarness();

    await act(async () => {
      void latest.startTalk();
    });
    await act(async () => {
      d.resolve(makeStream());
    });
    expect(latest.talking).toBe(true);
    expect(latest.starting).toBe(false);

    await act(async () => {
      latest.stopTalk();
    });
    expect(trackStop).toHaveBeenCalledTimes(1);
    expect(ctxClose).toHaveBeenCalledTimes(1);
    expect(latest.talking).toBe(false);
  });

  it("stop without a start latches nothing: the next hold still works", async () => {
    const d = deferred<MediaStream>();
    getUserMedia.mockReturnValue(d.promise);
    renderHarness();

    await act(async () => {
      latest.stopTalk();
    });
    expect(trackStop).not.toHaveBeenCalled();

    await act(async () => {
      void latest.startTalk();
    });
    await act(async () => {
      d.resolve(makeStream());
    });
    expect(latest.talking).toBe(true);
    await act(async () => {
      latest.stopTalk();
    });
    expect(trackStop).toHaveBeenCalledTimes(1);
  });
});
