// Pure jitter buffer for the dashboard audio player (docs/AUDIO_WIRE.md, Part A4 of the plan).
// Mono Float32 samples at the wire rate (16 kHz). The player pushes decoded 100 ms chunks in and
// pulls fixed-size blocks out for the AudioWorklet. No DOM, no timers: fully unit-testable.
//
// - Priming: playback waits until `targetMs` (300 ms) is buffered. After an underrun the ring primes
//   again, so a stall costs one re-buffer instead of a stutter per chunk.
// - Underrun: a read that finds too few samples fills the rest with silence and counts it.
// - Overrun: a push beyond `maxMs` (1 s) drops the OLDEST samples so latency never grows unbounded.

export const AUDIO_RATE = 16000;
export const TARGET_MS = 300;
export const MAX_MS = 1000;

export interface AudioRingOptions {
  sampleRate?: number;
  targetMs?: number;
  maxMs?: number;
}

export class AudioRing {
  readonly sampleRate: number;
  readonly targetSamples: number;
  readonly capacity: number;

  private readonly buf: Float32Array;
  private head = 0; // index of the oldest buffered sample
  private len = 0; // samples currently buffered
  private primed = false;

  /** Read calls that ran out of samples while playing (each one re-primes the ring). */
  underruns = 0;
  /** Silence samples written by underruns. */
  silentSamples = 0;
  /** Oldest samples dropped because the ring was full. */
  overrunSamples = 0;

  constructor(opts: AudioRingOptions = {}) {
    this.sampleRate = opts.sampleRate ?? AUDIO_RATE;
    const targetMs = opts.targetMs ?? TARGET_MS;
    const maxMs = opts.maxMs ?? MAX_MS;
    this.targetSamples = Math.round((this.sampleRate * targetMs) / 1000);
    this.capacity = Math.max(this.targetSamples, Math.round((this.sampleRate * maxMs) / 1000));
    this.buf = new Float32Array(this.capacity);
  }

  /** Samples waiting to be played. */
  get buffered(): number {
    return this.len;
  }

  /** True while the ring is (re)filling to the target and read() returns silence without counting it. */
  get priming(): boolean {
    return !this.primed;
  }

  /** Append samples. Returns how many of the oldest samples were dropped to make room. */
  push(samples: Float32Array): number {
    let input = samples;
    let dropped = 0;
    if (input.length > this.capacity) {
      // Only the newest `capacity` samples can fit at all.
      dropped += input.length - this.capacity;
      input = input.subarray(input.length - this.capacity);
    }
    const overflow = this.len + input.length - this.capacity;
    if (overflow > 0) {
      this.head = (this.head + overflow) % this.capacity;
      this.len -= overflow;
      dropped += overflow;
    }
    let write = (this.head + this.len) % this.capacity;
    for (let i = 0; i < input.length; i++) {
      this.buf[write] = input[i];
      write = write + 1 === this.capacity ? 0 : write + 1;
    }
    this.len += input.length;
    this.overrunSamples += dropped;
    if (!this.primed && this.len >= this.targetSamples) this.primed = true;
    return dropped;
  }

  /**
   * Fill `out` with the next samples. While priming the output is silence and nothing is counted.
   * While playing, a shortfall is filled with silence, counted as one underrun, and priming restarts.
   * Returns the number of silence samples written by an underrun (0 when the read was fully served).
   */
  read(out: Float32Array): number {
    if (!this.primed) {
      out.fill(0);
      return 0;
    }
    const n = Math.min(out.length, this.len);
    for (let i = 0; i < n; i++) {
      out[i] = this.buf[this.head];
      this.head = this.head + 1 === this.capacity ? 0 : this.head + 1;
    }
    this.len -= n;
    const missing = out.length - n;
    if (missing > 0) {
      out.fill(0, n);
      this.underruns += 1;
      this.silentSamples += missing;
      this.primed = false;
    }
    return missing;
  }

  /** Drop everything and start priming again (used when the stream stops or changes codec). */
  clear(): void {
    this.head = 0;
    this.len = 0;
    this.primed = false;
  }
}

/** Root mean square of a block (0 for an empty block). */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/**
 * Meter position 0..100 on a dB scale: -60 dBFS (or quieter) is 0, full scale is 100.
 * A linear meter would show almost nothing for speech; this one moves with ordinary sounds.
 */
export function levelPercent(rmsValue: number): number {
  if (!(rmsValue > 0)) return 0;
  const db = 20 * Math.log10(rmsValue);
  const pct = ((db + 60) / 60) * 100;
  return Math.round(Math.min(100, Math.max(0, pct)));
}
