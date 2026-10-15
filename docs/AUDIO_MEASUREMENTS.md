# Live audio measurements (Full mode, Samsung SM-E075F, Android 16)

Measured 2026-10-11 with `scripts/audio_measure.py` (raw JSON from that run is not committed; rerun the script
to reproduce). Wire format: `docs/AUDIO_WIRE.md`. Protocol and close codes: `CLAUDE.md`.

## Method

- Phone: Samsung SM-E075F (Galaxy A07), Android 16, serial R9ZY80PKWTW, USB-powered (charging) throughout, screen
  awake. WagWatch debug build from this branch (`app-debug.apk` installed with `adb install -r`).
- Backend: pin mode, `WEB_PIPELINE=remote`, `uvicorn` on 127.0.0.1:8000 from this worktree, commit 670559e.
- Mode: Full (station cap full, household full). No `/video` viewer in any window.
- Windows: 60 s each, six in total, alternating ON / OFF / ON / OFF / ON / OFF.
  - ON: one `/audio` listener connected for the whole window.
  - OFF: no listener, no other viewer.
- App CPU: `utime + stime` ticks from `/proc/<pid>/stat` read at the start and end of each window; `getconf CLK_TCK`
  is 100. CPU % = ticks / 100 / wall seconds x 100 (100 % = one core). The figure is the whole app process, all
  threads, including the on-device perception that runs in both ON and OFF windows.
- Wire bytes: the listener's binary payloads (the forwarded frame without the `0x02` byte, 813 bytes per frame).
  WebSocket framing is not counted.
- Arrival intervals: time between consecutive frames at the viewer (client receive clock).
- Sequence gaps: from the frame `seq` field, checked with the reference codec in `backend/web/audio_codec.py`.
- Battery: `dumpsys battery` level, temperature and charging status at every window boundary.
  Battery level has 1 % resolution.
- Decoded level: RMS and peak of the first 10 s of decoded samples from the first ON window (int16 units).

## Results

| Metric | Value |
|---|---|
| Frames per second at the viewer (ON, 3 windows) | 9.99 to 10.0 (600 to 601 per 60 s) |
| Payload bytes per second at the viewer | 8118 to 8134 B/s (mean 8124 B/s) |
| Payload per listener per day (projection) | 8124 B/s x 86 400 s = 0.70 GB/day |
| Arrival interval p50 / p95 / max (per window) | 100.0-100.3 ms / 107.6-111.4 ms / 128.9-202.1 ms |
| Sequence gaps and rejected frames | 0 gap events, 0 missing seq, 0 rejected, in all 3 ON windows |
| Server counters in ON windows | `audio_frames` +598 to +599 per 60 s (matches viewer frames) |
| App CPU, ON windows (% of one core) | 80.74, 77.47, 75.02 (mean 77.7) |
| App CPU, OFF windows (% of one core) | 73.57, 73.14, 71.95 (mean 72.9) |
| CPU difference ON minus OFF | about +4.9 percentage points of one core (n = 3 pairs) |
| Decoded level, first 10 s of ON windows | RMS 390 to 400 (about -38 dBFS), peak 4842 to 6667 |
| Battery level | 60 % at start, 60 % at end (1 % resolution, no change visible) |
| Battery temperature | 37.1 C at start, 36.8 C at end; 36.8-36.9 C through the run |
| Charging state | USB powered, `status` 2 (charging), AC false, throughout |

## Reading the numbers

- The stream runs at the phone's frame rate: 10 frames/s, one 100 ms chunk per frame, with no sequence gaps
  in this run. The projected egress is about 0.7 GB per listener per day. That matches the ADPCM estimate
  (about 64 kbit/s plus framing) and is far below the 2.7 GB/day that raw PCM16 would cost.
- The CPU cost of listening is about 5 points of one core on this device in these windows. The app's baseline
  without listeners is already about 73 % of one core, because the on-device perception runs in both states.
  Audio is a small increment on top of that baseline, not the dominant cost.
- Battery did not move at 1 % resolution. The phone was charging over USB during the run, so this shows
  nothing about battery drain on battery power. Do not read it as a battery-life figure.
- The 10 s decoded level is room sound at a moderate level (RMS about -38 dBFS). It shows that the decoder
  produces real signal, not silence. It does not measure intelligibility or the audible path.

## Not measured (stated plainly)

- End-to-end latency (phone capture to dashboard playback). There is no shared clock between the phone's
  capture and the viewer's playback, so no absolute latency number is claimed.
- Audible playback through the dashboard player (the AudioWorklet path in a browser) and the 300 ms jitter
  buffer under real network conditions. Only the wire and the reference decoder were measured.
- Battery drain on battery power, over hours, and with the screen off. Charging hid the battery change.
- Behaviour on other phones, on Android 14, and over the public tunnel or a real network with loss.
- Long runs (more than 60 s windows), thermal throttling, and CPU with video and audio viewers together.
- TalkBack behaviour of the Listen control and the listening indicator.
