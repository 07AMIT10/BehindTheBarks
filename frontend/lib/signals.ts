import type { Features } from "./contracts";

export type SignalRow = { key: keyof Features; name: string; value: string; unit: string; points: string; live: boolean };

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

export function sparkPoints(values: (number | null)[]): string {
  const pts: string[] = [];
  const n = values.length;
  values.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) return;
    const x = n > 1 ? (i / (n - 1)) * 100 : 0;
    pts.push(`${x.toFixed(1)},${(22 - clamp01(v) * 20).toFixed(1)}`);
  });
  return pts.length ? pts.join(" ") : "";
}

function tailLabel(v: number): string {
  if (v <= -0.6) return "Tucked";
  if (v < -0.3) return "Low";
  if (v > 0.3) return "High";
  return "Mid";
}

const EAR_NUM: Record<string, number> = { up: 1, neutral: 0.5, back: 0, unknown: 0.5 };

type Spec = {
  key: keyof Features;
  name: string;
  norm: (v: Features[keyof Features]) => number | null;
  show: (v: NonNullable<Features[keyof Features]>) => [string, string];
};

const SPECS: Spec[] = [
  { key: "tail_height", name: "Tail height", norm: (v) => (v === null ? null : ((v as number) + 1) / 2), show: (v) => [tailLabel(v as number), (v as number).toFixed(2)] },
  { key: "tail_wag_hz", name: "Tail wag", norm: (v) => (v === null ? null : (v as number) / 6), show: (v) => [(v as number).toFixed(1), "Hz"] },
  { key: "ear_position", name: "Ear position", norm: (v) => (v === null ? null : EAR_NUM[v as string] ?? 0.5), show: (v) => [String(v).charAt(0).toUpperCase() + String(v).slice(1), ""] },
  { key: "mouth_open", name: "Mouth open", norm: (v) => (v === null ? null : (v as number)), show: (v) => [String(Math.round((v as number) * 100)), "%"] },
  { key: "body_lowering", name: "Body lowering", norm: (v) => (v === null ? null : (v as number)), show: (v) => [String(Math.round((v as number) * 100)), "%"] },
  { key: "motion_energy", name: "Motion energy", norm: (v) => (v === null ? null : (v as number)), show: (v) => [(v as number).toFixed(2), ""] },
];

export function signalRows(history: Features[]): SignalRow[] {
  const latest = history[history.length - 1];
  return SPECS.map((spec) => {
    const raw = latest ? latest[spec.key] : null;
    const live = raw !== null && raw !== undefined;
    const [value, unit] = live ? spec.show(raw as NonNullable<Features[keyof Features]>) : ["—", ""];
    return { key: spec.key, name: spec.name, value, unit, live, points: sparkPoints(history.map((f) => spec.norm(f[spec.key]))) };
  });
}
