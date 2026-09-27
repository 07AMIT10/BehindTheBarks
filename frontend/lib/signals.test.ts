import { describe, expect, it } from "vitest";
import type { Features } from "./contracts";
import { signalRows, sparkPoints } from "./signals";

const F = (o: Partial<Features>): Features => ({
  tail_height: null, tail_wag_hz: null, ear_position: null, mouth_open: null, body_lowering: null,
  motion_energy: null, in_feeding_zone: null, ...o,
});

describe("signals", () => {
  it("six rows in design order with formatted latest values", () => {
    const rows = signalRows([F({ tail_height: 0.1 }), F({ tail_height: 0.82, tail_wag_hz: 4.23, ear_position: "up", mouth_open: 0.64, body_lowering: 0.08, motion_energy: 0.712 })]);
    expect(rows.map((r) => r.name)).toEqual(["Tail height", "Tail wag", "Ear position", "Mouth open", "Body lowering", "Motion energy"]);
    expect(rows.map((r) => [r.value, r.unit])).toEqual([["High", "0.82"], ["4.2", "Hz"], ["Up", ""], ["64", "%"], ["8", "%"], ["0.71", ""]]);
    expect(rows.every((r) => r.live)).toBe(true);
  });
  it("null latest value shows a dash and is not live", () => {
    const rows = signalRows([F({ tail_height: -0.7 })]);
    expect(rows[0]).toMatchObject({ value: "Tucked", live: true });
    expect(rows[1]).toMatchObject({ value: "—", unit: "", live: false });
  });
  it("tail height labels", () => {
    const label = (v: number) => signalRows([F({ tail_height: v })])[0].value;
    expect([label(0.5), label(0), label(-0.4), label(-0.8)]).toEqual(["High", "Mid", "Low", "Tucked"]);
  });
  it("sparkPoints maps 0..1 to y 22..2 across x 0..100 and skips nulls", () => {
    expect(sparkPoints([0, 1])).toBe("0.0,22.0 100.0,2.0");
    expect(sparkPoints([0.5, null, 0.5])).toBe("0.0,12.0 100.0,12.0");
    expect(sparkPoints([null])).toBe("");
  });
});
