import type { FrameEvent } from "./contracts";
import { SKELETON } from "./skeleton";

export type Fit = { scale: number; dx: number; dy: number };
export type Layers = { box: boolean; skeleton: boolean; face: boolean };

export function fitContain(srcW: number, srcH: number, boxW: number, boxH: number): Fit {
  if (srcW <= 0 || srcH <= 0 || boxW <= 0 || boxH <= 0) return { scale: 1, dx: 0, dy: 0 };
  const scale = Math.min(boxW / srcW, boxH / srcH);
  return { scale, dx: (boxW - srcW * scale) / 2, dy: (boxH - srcH * scale) / 2 };
}

const KP = "#7FE3E8";
const FACE = "#F5E6B8";

export function drawOverlay(ctx: CanvasRenderingContext2D, frame: FrameEvent | null, fit: Fit, layers: Layers, dpr: number): void {
  const { width, height } = ctx.canvas;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!frame || !frame.dog_detected) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const X = (x: number) => fit.dx + x * fit.scale;
  const Y = (y: number) => fit.dy + y * fit.scale;

  if (layers.box && frame.bbox) {
    const [x1, y1, x2, y2] = frame.bbox;
    const l = X(x1), t = Y(y1), r = X(x2), b = Y(y2);
    ctx.strokeStyle = KP;
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(l, t, r - l, b - t);
    ctx.globalAlpha = 1;
    ctx.lineWidth = 3;
    const c = Math.min(20, (r - l) / 4, (b - t) / 4);
    ctx.beginPath();
    ctx.moveTo(l, t + c); ctx.lineTo(l, t); ctx.lineTo(l + c, t);
    ctx.moveTo(r - c, t); ctx.lineTo(r, t); ctx.lineTo(r, t + c);
    ctx.moveTo(r, b - c); ctx.lineTo(r, b); ctx.lineTo(r - c, b);
    ctx.moveTo(l + c, b); ctx.lineTo(l, b); ctx.lineTo(l, b - c);
    ctx.stroke();
    const label = `dog ${(frame.bbox_conf ?? 0).toFixed(2)}`;
    // Canvas fonts can't use var(): resolve next/font's family name from the CSS variable instead.
    const mono = getComputedStyle(ctx.canvas).getPropertyValue("--font-plex-mono").trim();
    ctx.font = `700 11px ${mono ? `${mono}, ` : ""}ui-monospace, monospace`;
    const tw = ctx.measureText(label).width + 16;
    ctx.fillStyle = KP;
    ctx.fillRect(l, t - 24, tw, 22);
    ctx.fillStyle = "#0F1113";
    ctx.fillText(label, l + 8, t - 9);
  }

  if (layers.skeleton) {
    const kp = frame.body_keypoints;
    ctx.strokeStyle = KP;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (const [a, b] of SKELETON) {
      const p = kp[a], q = kp[b];
      if (!p || !q) continue;
      ctx.moveTo(X(p[0]), Y(p[1]));
      ctx.lineTo(X(q[0]), Y(q[1]));
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = KP;
    for (const p of Object.values(kp)) {
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(X(p[0]), Y(p[1]), 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (layers.face && frame.face_landmarks) {
    ctx.fillStyle = FACE;
    for (const [x, y] of frame.face_landmarks) {
      ctx.beginPath();
      ctx.arc(X(x), Y(y), 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}
