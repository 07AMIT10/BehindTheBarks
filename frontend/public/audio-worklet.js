// AudioWorklet for the dashboard Listen player (lib/audioPlayer.ts). Plays 16 kHz mono Float32 samples
// that the main thread pushes ({type:"push", samples}) and resamples them to the context rate with
// linear interpolation. The main thread owns the jitter buffer (lib/audioRing.ts) and only sends a
// block when this queue is short, so the queue here stays near 300 ms. Underrun is silence, uncounted
// here: the main thread counts it. Reports {type:"consumed", n} (cumulative 16 kHz samples used).
// `sampleRate` is a global in AudioWorkletGlobalScope.
const IN_RATE = 16000;
const CAPACITY = IN_RATE; // 1 s; overflow drops the oldest samples
const REPORT_EVERY = 800; // 50 ms of 16 kHz samples

class BtbAudioOut extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(CAPACITY);
    this.head = 0;
    this.len = 0;
    this.ratio = IN_RATE / sampleRate; // input samples per output sample
    this.frac = 0; // position between `a` (current) and the next queued sample
    this.a = 0;
    this.haveA = false;
    this.consumed = 0;
    this.reported = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === "push") this.push(e.data.samples);
    };
  }

  push(samples) {
    let input = samples;
    if (input.length > CAPACITY) input = input.subarray(input.length - CAPACITY);
    const overflow = this.len + input.length - CAPACITY;
    if (overflow > 0) {
      this.head = (this.head + overflow) % CAPACITY;
      this.len -= overflow;
    }
    let w = (this.head + this.len) % CAPACITY;
    for (let i = 0; i < input.length; i++) {
      this.buf[w] = input[i];
      w = w + 1 === CAPACITY ? 0 : w + 1;
    }
    this.len += input.length;
  }

  take() {
    const v = this.buf[this.head];
    this.head = this.head + 1 === CAPACITY ? 0 : this.head + 1;
    this.len -= 1;
    this.consumed += 1;
    return v;
  }

  process(_inputs, outputs) {
    const out = outputs[0] && outputs[0][0];
    if (!out) return true;
    for (let i = 0; i < out.length; i++) {
      if (!this.haveA) {
        if (this.len === 0) {
          out[i] = 0;
          continue;
        }
        this.a = this.take();
        this.haveA = true;
      }
      const b = this.len > 0 ? this.buf[this.head] : this.a;
      out[i] = this.a + (b - this.a) * this.frac;
      this.frac += this.ratio;
      while (this.frac >= 1 && this.len > 0) {
        this.frac -= 1;
        this.a = this.take();
      }
      if (this.frac >= 1) this.frac = 0.999999; // starved: hold the last sample until more arrives
    }
    if (this.consumed - this.reported >= REPORT_EVERY) {
      this.reported = this.consumed;
      this.port.postMessage({ type: "consumed", n: this.consumed });
    }
    return true;
  }
}

registerProcessor("btb-audio-out", BtbAudioOut);
