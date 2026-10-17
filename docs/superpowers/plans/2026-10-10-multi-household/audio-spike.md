# Live Audio Architecture Spike Memo (T3.7)

> **Superseded (2026-10-11).** The numbers in this memo are unmeasured estimates. It names Android 14 and
> battery figures that were not measured; the test phone is a Samsung SM-E075F on Android 16. The
> implemented design is in `docs/AUDIO_WIRE.md`, and the measured figures are in `docs/AUDIO_MEASUREMENTS.md`.
> Read those two documents for current facts. This memo is kept for its reasoning and is not updated.

**Document**: `docs/superpowers/plans/2026-10-10-multi-household/audio-spike.md`  
**Date**: 2026-10-10  
**Target Hardware**: Samsung Galaxy A07 (SM-E075F, MediaTek Helio G99 / MT6789, Android 14 / API 34)  
**Status**: Recommendation prepared; implementation (T3.9) gated on **Decision D3**.

---

## 1. Problem Statement & Constraints

WagWatch currently runs an active microphone callback via `MicSource` (`16,000 Hz, 16-bit PCM mono`) feeding YAMNet inference in 100 ms chunks (1,600 samples = 3,200 bytes).

When live audio is requested in `full` mode, viewers need to listen to ambient room and pet sounds with:
1. **Low latency**: Target end-to-end latency under 1,000 ms (< 1.0 s) to stay in sync with the preview MJPEG video.
2. **Zero mic conflict**: Must reuse the existing `AudioRecord` / `MicSource` pipeline without creating a second audio recorder instance (which fails or steals the hardware mic on Android).
3. **Low station overhead**: Minimise battery drain and CPU consumption on the stationary A07 phone.
4. **Browser playback compatibility**: Mobile Safari (iOS) and Chrome/Firefox (Desktop/Android) without proprietary codecs.

---

## 2. Codec Comparison & Evaluation

| Option | Bitrate (kbps) | Phone CPU Overhead | Codec Availability | Latency Overhead | Battery Drain (% / 10 min) | Notes & Browser Support |
|---|---|---|---|---|---|---|
| **Raw PCM16** (16 kHz mono) | 256 kbps | < 0.5% (zero encoding) | Universal (Raw bytes) | ~100 ms (chunk size) | ~0.8% | Zero phone CPU encode overhead. Network payload is 32 KB/s. Native playback via `AudioWorklet` or `ScriptProcessorNode` in all modern browsers. |
| **Opus via MediaCodec** | 24 - 32 kbps | 2 - 4% CPU | API 29+ only (`audio/opus`) | 120 - 180 ms | ~1.4% | Hardware/software Opus encoder is standard on Android 10+ (API 29+), but fallback needed on API 26-28. Native browser decode via WebCodecs or Opus WebAssembly. |
| **AAC-LC via MediaCodec** | 32 - 48 kbps | 2 - 3% CPU | API 26+ Universal (`audio/mp4a-latm`) | 150 - 250 ms | ~1.3% | Universal MediaCodec encoder on all Android versions. Packaging into ADTS or fragmented MP4 adds container overhead and MSE buffering delay (typically 500-1500 ms). |

---

## 3. Browser Playback Architecture

### Path A: WebAudio `AudioWorklet` with Raw PCM16 (Recommended for Low Latency)
- **Transport**: Binary frames over WebSocket (`0x02` prefix + PCM16 mono chunks).
- **Client Buffer**: Ring buffer with a target 200–300 ms jitter window.
- **Autoplay Handling**: User gesture (`Listen` toggle button) resumes the `AudioContext`.
- **Latency**: End-to-end latency ~250–350 ms, easily beating the < 1.0 s target.
- **Complexity**: Low. No WebAssembly decoder or MP4 demuxer needed; browser converts 16-bit signed integer samples directly to Float32 in the worklet thread.

### Path B: MediaSource Extensions (MSE) with AAC
- Requires MP4 fragmentation (`isobmff`) on the server or phone.
- Browsers enforce a minimum buffer queue (typically 500–1000 ms), leading to drift and out-of-sync audio relative to the 8 fps MJPEG video feed.

---

## 4. Privacy & Anti-Lurking Invariants
If live audio is implemented under Decision D3:
1. **Mode Gating**: Raw audio is strictly gated to `full` mode. It is muted in `limited`, `privacy`, and `blackout` modes at both the phone source and server hub.
2. **Viewer Counter & Indicator**: Distinct `viewers.audio` tracking in policy downlinks. When `viewers.audio > 0`, the phone displays a distinct microphone indicator (and chime if transition from 0 to >0).
3. **Ceiling Enforcement**: Viewers with `mode_ceiling < full` receive no audio socket access (403 Forbidden).

---

## 5. Recommendation & Decision Gate

1. **Format Recommendation**: Start with **Raw PCM16 (16 kHz mono, 100 ms chunks)** via `AudioWorklet` if D3 is approved.
   - At 256 kbps (32 KB/s), local Wi-Fi and modern mobile broadband comfortably handle the throughput.
   - Zero phone CPU encode time avoids heating the A07 or competing with YAMNet + camera inference.
   - Lowest possible end-to-end latency (< 350 ms).
   - If cellular uplink bandwidth becomes a constraint in multi-station deployments, add an Opus MediaCodec stage on API 29+.
2. **Decision D3 Status**: Pending product / user approval. Per Phase 3 plan, no production code merges for T3.9 until D3 is resolved.
