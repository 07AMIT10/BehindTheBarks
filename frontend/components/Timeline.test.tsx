// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import Timeline from "./Timeline";
import type { AudioMark, Span } from "@/lib/types";

const nowSec = 1_800_000;

const span = {
  emotion: "happy",
  start: nowSec - 60,
  end: nowSec,
  confidence: 0.9,
  reason: "tail wagging",
  source: "rules",
} as unknown as Span;

const mark = (overrides: Partial<AudioMark> = {}): AudioMark =>
  ({ ts: nowSec - 30, label: "bark", score: 0.8, snippet_id: "s1", duration_s: 5, ...overrides }) as AudioMark;

const roots: Root[] = [];

function renderTimeline(props: { audio: AudioMark[] }): { container: HTMLElement; unmount: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  const bound = (
    <Timeline
      spans={[span]}
      audio={props.audio}
      treats={[]}
      notifications={[]}
      nowSec={nowSec}
      http="http://be"
      token="tok"
      selected={null}
      onSelect={() => {}}
    />
  );
  act(() => root.render(bound));
  return {
    container,
    unmount: () => act(() => root.unmount()),
  };
}

afterEach(() => {
  while (roots.length) {
    act(() => roots.pop()!.unmount());
  }
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("Timeline snippet player", () => {
  it("pauses the snippet audio on unmount", () => {
    const pauseSpy = vi.spyOn(HTMLMediaElement.prototype, "pause");
    const playSpy = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockReturnValue(Promise.resolve() as unknown as Promise<void>);

    const t = renderTimeline({ audio: [mark()] });
    const chip = t.container.querySelector<HTMLButtonElement>('button[title^="Play audio snippet"]');
    expect(chip).toBeTruthy();
    act(() => chip!.click());
    expect(playSpy).toHaveBeenCalled();

    t.unmount();
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("renders the real snippet duration and never the fake 0:05 label", () => {
    const t = renderTimeline({ audio: [mark({ duration_s: 5 })] });
    const html = t.container.innerHTML;
    expect(html).toContain("5 s");
    expect(html).not.toContain("0:05");
  });

  it("drops the duration label when the server did not report one", () => {
    const t = renderTimeline({ audio: [mark({ duration_s: null })] });
    const chip = t.container.querySelector<HTMLButtonElement>('button[title^="Play audio snippet"]');
    expect(chip?.textContent).not.toContain("0:05");
  });
});
