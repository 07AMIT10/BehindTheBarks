// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import PairingModal from "./PairingModal";

vi.mock("qrcode", () => ({ default: { toDataURL: vi.fn(async () => "data:image/png;base64,qr") } }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const pairing = {
  code: "XYZ",
  expires_in: 300,
  qr: { v: 2 as const, url: "http://b/pair", pair_url: "http://b/pair/XYZ", code: "XYZ" },
};

const roots: Root[] = [];

function render(ui: React.ReactElement): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => root.render(ui));
  return container;
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  // Use the prototype setter so React's value tracker registers the change.
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
  act(() => {
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function baseProps() {
  return {
    isOpen: true,
    onClose: vi.fn(),
    phoneConnected: false,
    deviceName: null,
    pairing,
    onRegenerate: vi.fn(),
    onSwitchKind: vi.fn(),
    onCeilingChange: vi.fn(),
  };
}

afterEach(() => {
  while (roots.length) {
    act(() => roots.pop()!.unmount());
  }
  document.body.innerHTML = "";
});

describe("PairingModal viewer ceiling", () => {
  it("renders the shared ceiling choice in the select", () => {
    const container = render(<PairingModal {...baseProps()} kind="viewer" ceiling="privacy" />);
    const select = container.querySelector("select") as HTMLSelectElement;
    expect(select.value).toBe("privacy");
  });

  it("changing the select reports the new ceiling so the parent re-mints with it", () => {
    const onCeilingChange = vi.fn();
    const container = render(
      <PairingModal {...baseProps()} kind="viewer" ceiling="full" onCeilingChange={onCeilingChange} />
    );
    const select = container.querySelector("select") as HTMLSelectElement;
    setSelectValue(select, "privacy");
    expect(onCeilingChange).toHaveBeenCalledWith("privacy");
  });

  it("station pairing shows no ceiling select", () => {
    const container = render(<PairingModal {...baseProps()} kind="station" ceiling="full" />);
    expect(container.querySelector("select")).toBeNull();
  });
});
