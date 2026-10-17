// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { ModeSheet } from "./ModeSheet";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function renderModeSheet(ui: React.ReactElement): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => root.render(ui));
  return container;
}

function baseProps() {
  return {
    isOpen: true,
    onClose: () => {},
    effectiveMode: "full" as const,
    stationCap: "full" as const,
    householdMode: "full" as const,
    modeCeiling: "full" as const,
    isOwner: true,
    onSetMode: async () => ({ status: "pending" as const, mode: "privacy" as const }),
    onPeek: undefined,
  };
}

function clickPrivacyMode(container: HTMLElement) {
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Privacy"));
  expect(button).toBeTruthy();
  button!.click();
}

afterEach(() => {
  while (roots.length) {
    act(() => roots.pop()!.unmount());
  }
  document.body.innerHTML = "";
});

describe("ModeSheet request result", () => {
  it("shows the station's answer next to the pending notice", async () => {
    const container = renderModeSheet(
      <ModeSheet
        {...baseProps()}
        requestResult={{ mode: "privacy", result: "denied", ts: 1 }}
      />
    );
    await act(async () => clickPrivacyMode(container));
    expect(container.textContent).toContain("Mode request sent to station phone");
    expect(container.textContent).toContain("Station: rejected");
  });

  it("shows 'accepted' wording when the phone allows the raise", async () => {
    const container = renderModeSheet(
      <ModeSheet {...baseProps()} requestResult={{ mode: "privacy", result: "allowed", ts: 1 }} />
    );
    await act(async () => clickPrivacyMode(container));
    expect(container.textContent).toContain("Station: accepted");
  });

  it("without a station answer the notice stays alone", async () => {
    const container = renderModeSheet(<ModeSheet {...baseProps()} />);
    await act(async () => clickPrivacyMode(container));
    expect(container.textContent).toContain("Mode request sent to station phone");
    expect(container.textContent).not.toContain("Station:");
  });
});
