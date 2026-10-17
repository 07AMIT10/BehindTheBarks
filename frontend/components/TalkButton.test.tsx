// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { TalkControl } from "@/lib/useTalk";
import { TalkButton } from "./TalkButton";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function renderControl(control: Partial<TalkControl>): { container: HTMLElement; text: string } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() =>
    root.render(
      <TalkButton
        control={control as TalkControl}
        variant="sheet"
        disabled={false}
      />
    )
  );
  return { container, text: container.querySelector("button span:last-child")?.textContent ?? "" };
}

afterEach(() => {
  while (roots.length) {
    act(() => roots.pop()!.unmount());
  }
  document.body.innerHTML = "";
});

describe("TalkButton pending period", () => {
  it("idle hold invites a press", () => {
    const idle = renderControl({ talking: false, starting: false });
    expect(idle.text).toContain("Hold to Talk");
  });

  it("shows Starting… while the mic graph is pending", () => {
    const pending = renderControl({ talking: false, starting: true });
    expect(pending.text).toContain("Starting…");
  });

  it("shows Speaking… once the graph is live", () => {
    const live = renderControl({ talking: true, starting: false });
    expect(live.text).toContain("Speaking…");
  });
});
