# WagWatch flow test: runbook for the tester

You test with the Galaxy A07 (SM-E075F) on your desk, connected by USB, and the dashboard in Chrome on this computer.
Work through the steps in order. Tick each box as you go. If something you see does not match "You should see",
write it in the results table at the end and do the "If not" line.

Nothing here needs a cloud service. Everything runs on this computer.

Key words used below:
- **Dashboard**: the web page at http://localhost:8000/ in Chrome.
- **Phone**: the Android app "WagWatch" (package com.btb.ondevice).
- **Backend**: the server on this computer. Its log is `out/flowtest/backend.log` (relative to the repo root).
- **Logs on the phone**: `adb logcat -s EventUploader MonitorService Pairing Scanner CameraSource PreviewEncoder`
  (run it in a terminal, and copy what it prints when a step fails).

---

## Step 0: prerequisites and the check script

- [ ] The phone is connected by USB cable, the screen is on, and it is unlocked by you (the tests never unlock it).
- [ ] USB debugging is on and the computer is authorised on the phone (the prompt was accepted once).
- [ ] The app WagWatch is installed (the latest debug build).
- [ ] Chrome is open. You will use one window for the owner and a second profile or window for step 5 and step 11.
- [ ] In a terminal, from the repo root, run:

  ```
  scripts/flow_test_check.sh
  ```

  **You should see:** every line says PASS, and the last line says `RESULT: all checks PASS`.
  The "port 8001" line is INFO only: that is another process on this computer. Do not stop it.

  **If not:** note the FAIL line. A locked phone: unlock it by hand. A missing app: tell the owner, it must be built and installed.

---

## Step 1: start the backend

- [ ] In the terminal, run:

  ```
  scripts/flow_test_up.sh
  ```

  **You should see:** `Flow-test backend is up`, the dashboard address `http://localhost:8000/`, and the phone line
  saying the adb reverse is set. The numbered first steps are printed at the end.

  **If not:** the script prints the last lines of `out/flowtest/backend.log`. Send them back. If the port is already in use, the other process is not ours; do not kill it.

---

## Step 2: open the dashboard and create a household

- [ ] In Chrome, open **http://localhost:8000/**. Type exactly `localhost`, not `127.0.0.1` and not the LAN address. The pairing QR is built from the address you open, so only `localhost` gives the phone a URL it can reach through the USB link.
- [ ] Create a household. Enter the dog's name (for example `Rex`). Save the **recovery key** that appears: copy it somewhere safe. You need it for step 14.

  **You should see:** the dashboard shows the dog name you entered. A first-run card says **Pair a phone**. There is no "Reconnecting" text anywhere.

  **If not:** check `out/flowtest/backend.log` for errors on `POST /households`. If you see "Reconnecting" on the first run, note it: that is a bug.

---

## Step 3: pair the phone (QR)

- [ ] On the dashboard, click **Pair a phone**. A modal shows a QR code and a short code.
- [ ] On the phone, open WagWatch and tap **Scan QR**. Do **not** tap Start Monitoring yet.
- [ ] Point the phone's camera at the QR on the screen. Hold it about 20 cm away until the app reacts.

  **You should see:** on the phone, the household name (the dog name) appears on the main screen, and the app is still not monitoring. On the dashboard, the Pair modal closes or shows the station as paired.

  **If not:** check the phone log for tag `Pairing` and `Scanner`, and `out/flowtest/backend.log` for `POST /pair` (a 400 means the code expired after 5 minutes: mint a new one by closing and reopening the Pair modal).

---

## Step 4: start monitoring

- [ ] On the phone, tap **Start Monitoring**. Grant the camera and microphone permissions if asked.
- [ ] Watch the dashboard.

  **You should see:** on the dashboard, live video, the current emotion, and a battery pill showing the phone's charge. The phone screen shows **Stop Monitoring** and the status line shows **Live**.

  **If not:** check the phone log for tag `MonitorService` and `EventUploader`. Check the backend log for `/ingest-events`. Send both if the video does not appear within 30 seconds.

---

## Step 5: viewers (second tab, chime, Disconnect)

- [ ] Open a second Chrome window in **a different profile** (so it has no owner session) and go to **http://localhost:8000/** in it. Wait for the live video. (Step 11 covers the invite link for a limited viewer.)
- [ ] Listen.

  **You should see:** the phone plays a short chime and the phone shows **1 viewer watching** (the badge). The owner dashboard also shows the viewer count.
- [ ] On the phone, tap **Disconnect** (or the viewers control). The second window loses its video.

  **You should see:** the second window shows that the viewer was disconnected, and the phone's count goes back to 0 viewers.

  **If not:** check the phone log for `EventUploader` (look for close code 4411 = viewer kicked).

---

## Step 6: modes (Full, Limited, Privacy, Blackout) and approval

- [ ] On the dashboard, open the mode control and choose each mode in turn: **Full**, **Limited**, **Privacy**, **Blackout**.

  **You should see:**
  - Full: live video.
  - Limited: video is blurred.
  - Privacy: no video, only events (emotion and battery).
  - Blackout: a paused card that says the camera is paused. It must **never** say "Reconnecting".
- [ ] On the phone, lower the mode to **Privacy** (use the in-app mode control). Then on the dashboard, raise the mode back to **Full**.

  **You should see:** a prompt on the phone with **Allow** and **Keep private**. Tap **Allow**: the dashboard shows the mode as approved. Repeat once, tap **Keep private**: the dashboard shows declined. Repeat once more and do not tap anything for 60 seconds: the dashboard shows "no answer".
- [ ] Repeat the raise with the phone app in the **background** (press Home on the phone first).

  **You should see:** a notification on the phone with the same choices (Allow, Keep private). The result shows on the dashboard as before.

  **If not:** check the phone log for the `mode_result` messages on `EventUploader`, and the backend log for `mode_request`.

---

## Step 7: stop monitoring on the phone

- [ ] On the phone, tap **Stop Monitoring**.

  **You should see:** the dashboard shows **Monitoring stopped**. It must **never** show "Reconnecting".

  **If not:** note what the dashboard shows and check the backend log for the `bye` message.

---

## Step 8: a true drop (force-stop or airplane mode)

- [ ] Start monitoring again (step 4).
- [ ] Either force-stop the app on the phone from Android Settings, **or** turn on airplane mode on the phone for about 20 seconds, then turn it off.

  **You should see:** the dashboard shows **Reconnecting to camera**. When the phone is back and monitoring again, the dashboard recovers to live video without a page reload. (After a force-stop you must start monitoring again yourself.)

  **If not:** check the phone log for `EventUploader` (reconnect attempts) and the backend log for the disconnect.

---

## Step 9: treat button, torch, flip

- [ ] On the dashboard, press the **treat** button.

  **You should see:** the phone registers a treat. On the phone's log, look for a `treat` line from `EventUploader`.
- [ ] On the dashboard, turn the **torch** on, then off. Then press **flip** (switch camera).

  **You should see:** the phone's torch turns on and off, and the camera switches between front and back. The dashboard video follows.

  **If not:** note which control did nothing, and check `MonitorService` in the phone log.

---

## Step 10: second station (grid demo)

Use a second, simulated station on this computer (not the phone).

- [ ] Start monitoring is **not** needed for this step. In the dashboard, open the Pair modal (or the Members list) and mint a **new** pairing code. Write the code down.
- [ ] In a terminal, from the repo root, run (replace `CODE` with the code you wrote down):

  ```
  scripts/flow_test_sim_station.sh CODE
  ```

  The script runs for 30 minutes unless you press Ctrl-C.
- [ ] Look at the dashboard.

  **You should see:** a grid with **two tiles** (the phone and the simulated station). Tap one tile to **focus** it: it becomes the main video. Each tile shows its own battery.

  **If not:** check the terminal output of the script and `out/flowtest/backend.log`.

---

## Step 11: viewer invite (limited access)

- [ ] On the dashboard, open **Members** and create a **viewer invite** with the ceiling set to **Limited**.
- [ ] Open the invite link in a **second browser profile** (for example a new Chrome profile, not the owner's window).

  **You should see:** the second profile sees the dog's video **blurred** (Limited). It must **not** see the full video. The mode control in that profile cannot raise above Limited.

  **If not:** note what the viewer can see, and check the backend log.

---

## Step 12: revoke the station

- [ ] On the dashboard, open **Members** and **revoke** the phone's station.

  **You should see:** on the phone, the message **Removed from household · pair again** (with "Pair by QR" to follow). The app must stop sending video. The dashboard shows the station as removed, not as "Reconnecting".

  **If not:** check the phone log for close code 4410 (`EventUploader`).

---

## Step 13: layout on different sizes

- [ ] On the phone, turn it sideways (rotate). Look at the screen. Rotate back.
- [ ] In Chrome, resize the window to about **360 px**, then **768 px**, then **1280 px** wide (use the DevTools device toolbar or drag the window edge).

  **You should see:** no text cut off, no buttons that cannot be reached (scroll if needed), no overlapping controls. Monitoring keeps going through the phone rotation.

  **If not:** take a screenshot, write the width and what is clipped, and send it.

---

## Step 14: delete the household (back to first run)

- [ ] On the dashboard, open the household settings and choose **Delete household**. Enter the recovery key from step 2.

  **You should see:** the dashboard returns to first-run (the **Create household** card). The phone's pairing is gone: the phone shows that it must pair again.

  **If not:** the recovery key must be correct. If the wrong key is rejected, that is expected. If it is accepted with the right key and nothing changes, note it.

---

## Stopping the test

- [ ] On the phone, tap Stop Monitoring if it is still running.
- [ ] In the terminal, run `scripts/flow_test_down.sh`. It stops the backend, removes the USB link, and force-stops the app. It keeps your data in `out/flowtest/`.

---

## Results table

Fill in one row per step. Use PASS, FAIL, or NOTE (passed with a remark).

| Step | Result (PASS / FAIL / NOTE) | What you saw, or the line from "If not" | Screenshot file (if any) |
|---|---|---|---|
| 0 check script | | | |
| 1 start | | | |
| 2 household, first run | | | |
| 3 pair by QR | | | |
| 4 start monitoring, live | | | |
| 5 viewers, chime, Disconnect | | | |
| 6 modes and approval (foreground) | | | |
| 6 approval (in background, notification) | | | |
| 7 stop monitoring | | | |
| 8 true drop and recovery | | | |
| 9 treat, torch, flip | | | |
| 10 second station grid | | | |
| 11 viewer invite, Limited | | | |
| 12 revoke station | | | |
| 13 layout at 360, 768, 1280 px and rotation | | | |
| 14 delete household | | | |

**What to send back:** the filled table; the file `out/flowtest/backend.log`; the phone log, if you captured one
(`adb logcat -d -s EventUploader MonitorService Pairing Scanner CameraSource PreviewEncoder > phone-log.txt`); and any
screenshots for the rows marked FAIL. Do not send the recovery key.
