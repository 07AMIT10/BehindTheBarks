# Privacy Architecture, Threat Model & Anti-Lurking Controls

> **Context**: WagWatch (Behind The Barks) operates continuous multimodal in-home sensing (CameraX 15 FPS + 16 kHz AudioRecord) for dog affective perception. This document details the domestic privacy threat model, peer-reviewed human-computer interaction (HCI) research grounding, and the multi-layered defense architecture.

---

## 1. Domestic Threat Model: "The Co-Inhabitant Surveillance Dilemma"

In residential environments, traditional smart cameras that rely on a public URL and a static shared secret (such as a 4-digit PIN or household password) suffer from systemic privacy failures:

### The 4 Threat Vectors
1. **Silent Remote Lurking (Observer Invisibility)**:
   - A remote browser opens the live video stream (`/video`). The in-room station phone continues running with no visual change or sound.
   - Occupants in the room (homeowner, family members, roommates, domestic helpers, visitors) have **zero awareness** that they are being observed in real time.
2. **Persistent Credential Over-Privilege**:
   - A PIN shared with a friend, partner, or pet sitter for a single check-in remains valid indefinitely.
   - The credential holder can access the optical video feed at 2 AM or during intimate personal moments without the homeowner's knowledge or consent.
3. **Optical Overexposure (Excessive Modality Leakage)**:
   - To verify whether a dog is resting or eating, viewers receive a wide optical frame showing the entire private living room or bedroom, furniture, personal belongings, and passing family members.
4. **Cloud Broker Exposure**:
   - Cloud relays (Render, Koyeb, AWS) that process unencrypted video frames create an unnecessary third-party data vulnerability.

---

## 2. Peer-Reviewed Academic Grounding (OpenAlex / ACM)

1. **Everyday Uses and Surveillance in Pet & Home Cameras**:
   * **Citation**: Geeng, E., & Roesner, F. (2022). *Monitoring Pets, Deterring Intruders, and Casually Spying on Neighbors: Everyday Uses of Smart Home Cameras*. In *Proceedings of the 2022 CHI Conference on Human Factors in Computing Systems* (CHI '22).  
   * **DOI**: [10.1145/3491102.3517617](https://doi.org/10.1145/3491102.3517617)  
   * **Key Finding**: Pet cameras are the most frequent vector of accidental domestic surveillance. Non-primary users (bystanders and co-inhabitants) experience acute discomfort when observation occurs without mutual awareness.
2. **Failure of Shared Credentials for Domestic Bystanders**:
   * **Citation**: Yao, Y., Basdeo, J. R., Kaushik, S., & Wang, Y. (2019). *Privacy Perceptions and Designs of Bystanders in Smart Homes*. *Proceedings of the ACM on Interactive, Mobile, Wearable and Ubiquitous Technologies* (IMWUT/UbiComp), 3(2), 1-24.  
   * **DOI**: [10.1145/3359161](https://doi.org/10.1145/3359161)  
   * **Key Finding**: Passwords fail in domestic settings due to social delegation. True privacy requires **physical mutual awareness (audible/visual cues)** and **in-situ device sovereignty** (the physical station can cut feeds).
3. **Decoupling Functionality from Optical Surveillance**:
   * **Citation**: Geeng, E., Cranor, J. R., & Roesner, F. (2022). *Addressing Adjacent Actor Privacy: Designing for Bystanders, Co-Users, and Surveilled Subjects of Smart Home Cameras*. *ACM Designing Interactive Systems* (DIS '22).  
   * **DOI**: [10.1145/3532106.3535195](https://doi.org/10.1145/3532106.3535195)  
   * **Key Finding**: Domestic sensing must decouple functional utility (pet behavioral metrics) from raw optical video.
4. **Kinematic Abstraction Over Raw Video**:
   * **Citation**: Aloufi et al. (2024). *Skeleton-based Privacy-Preserving Smart Activity Sensor for Senior Care and Patient Monitoring*. *Preprints*.  
   * **DOI**: [10.20944/preprints202401.0108.v1](https://doi.org/10.20944/preprints202401.0108.v1)  
   * **Key Finding**: Discarding raw video on edge and transmitting only 17 skeletal joint coordinates eliminates optical snooping while maintaining >98% behavioral classification accuracy.

---

## 3. The 5 Architectural Defenses for WagWatch

```mermaid
flowchart TD
    subgraph Station["On-Device Android Station (In Living Room)"]
        Sensors["CameraX + AudioRecord"]
        Chime["Audible 2-Tone Intercom Chime"]
        ScreenBadge["Screen HUD: '👁️ 1 Active Viewer'"]
        PhysicalMute["Physical 'Family Home' Shutter"]
    end

    subgraph Backend["Cloud Broker (FastAPI / Render)"]
        ViewerTracker["Active Viewer Tracker (Count & Sessions)"]
        Downlink["WebSocket Downlink Dispatcher"]
    end

    subgraph Client["Web Dashboard (Remote Browser)"]
        AuthModal["Device Approval / PIN Gate"]
        KinematicMode["Kinematic Avatar Mode (Dog Skeleton Only)"]
        VideoView["Optical Stream (3-Min Auto-Timeout)"]
    end

    Client -- "Connects to /video" --> ViewerTracker
    ViewerTracker -- "Downlink alert" --> Downlink
    Downlink --> Chime
    Downlink --> ScreenBadge
    PhysicalMute -- "Tap Mute" --> Sensors
```

### Defense 1: Mutual Presence Awareness & Intercom Chime
* **Immediate Audible Notice**: The millisecond a remote browser initiates a `/video` connection or WebSocket stream, the station phone in the room plays a gentle 2-tone intercom chime (`Beep-Boop`).
  - *Guarantee*: Nobody can ever watch secretly. Anyone in the room immediately hears when viewing begins.
* **On-Screen Active Viewer Badge**: The phone's Station Display shows:
  `👁️ 1 Active Viewer: Chrome (Windows)` with an active indicator dot.
* **One-Touch Station Kick**: The homeowner can tap **`🛑 Disconnect Viewers`** on the phone screen to cut the stream immediately.

### Defense 2: Two-Factor Station Device Approval (No Static Secrets)
* Similar to WhatsApp Web or Apple HomeKit:
* Entering the household PIN on a laptop does **not** grant immediate camera access.
* The station phone in the room displays an authorization prompt:
  > *"Allow remote viewer 'Chrome on Mac' to view camera? [Allow 15 min] [Allow Always] [Deny]"*
* Access requires physical in-situ confirmation by someone with physical access to the station phone.

### Defense 3: Kinematic-First Mode (Zero Optical Video)
* **Default Tier**: Viewers see the dog's emotion timeline (*"Happy / Relaxed / Sleeping"*), wag frequency, meal logs, and an animated wireframe skeleton of the dog on an abstract dark canvas.
* **Zero Optical Video**: No JPEG frames or raw audio leave the phone. The room, bed, clothes, and passing family members are 100% invisible.
* Optical camera feed requires an explicit *"Request 3-Minute Video Peek"* button.

### Defense 4: 3-Minute Stream Inactivity Timeout
* Active video streams automatically terminate after **180 seconds** (3 minutes) of continuous viewing with a prompt:
  > *"Stream paused to protect household privacy. [Tap to Resume]"*
* Prevents open browser tabs from streaming living room video indefinitely.

### Defense 5: Semantic Room Redaction (Dog-Only ROI Masking)
* Using the on-device YOLO detector, the phone station can black out or heavily blur 100% of the pixel area outside the dog's bounding box.
* The viewer sees only the dog; the surrounding domestic space is completely redacted.

---

## 4. Implementation Matrix

| Component | Responsibility | Status |
| :--- | :--- | :--- |
| `backend/main.py` | Track active `/video` subscribers; kick cooldown enforcement; dispatch downlinks. | Implemented (T3.4) |
| `backend/web/tracker.py` | Maintain active viewers count, kick cooldown, and fire debounced downlinks. | Implemented (T3.4) |
| `android/.../EventUploader.kt` | Parse `policy` downlinks and send `kick_viewers` uplinks. | Implemented (T3.4) |
| `android/.../MonitorService.kt` | Play two-tone chime on 0->>0 viewers, notification action & kick handling. | Implemented (T3.4) |
| `android/.../ui/MainActivity.kt` | Active viewer count indicator & "Disconnect" action button. | Implemented (T3.4) |
| `frontend/components/VideoPanel.tsx` | Implement 3-minute inactivity watchdog overlay and tab visibility pause. | Implemented (T3.4) |
| `frontend/lib/watchdog.ts` | Pure timer watchdog engine with user activity reset. | Implemented (T3.4) |

---

## 5. Multi-Household Data Retention (T2.9, T2.10)

Each household is a separate data boundary. Nothing below is shared between households, and the
per-household isolation gate (`tests/web/test_isolation.py`) enforces it.

### What is stored, where, and for how long

| Data | Where | Default lifetime | Deleted by |
| :--- | :--- | :--- | :--- |
| Household, station, viewer rows | `BTB_DATABASE_URL` (`out/btb.db`; Postgres in prod) | Until the owner deletes the household | `DELETE /households/me` |
| Recovery key, session tokens, pairing codes | **Hashed only** (SHA-256) in the DB | Recovery/session: until delete or expiry; pairing codes: 1 day | `registry.sweep()` |
| Household ID, role, dog name, location | DB row + that household's runtime memory | Until delete | `DELETE /households/me` |
| Station public key (ECDSA P-256) | DB row | Until the station is revoked or the household is deleted | Revoke / delete |
| Session logs (`session-*.jsonl`) | `<log_dir>/<household_id>/` | **Off by default.** When enabled, **7 days** (`web.log_retention_days`) | `registry.sweep()` |
| Live video frames, features, audio | In memory and over the LAN socket only; never written unless session logs are on | Ephemeral | Never persisted by default |

### The opt-in rules

* **No AI interpretation by default.** A new household runs with a `DisabledInterpreter`: frames are
  not sent to the operator's LLM provider and cost no quota. The owner turns it on in
  **Members & access → Privacy → "Use AI interpretation"**.
* **No external notifications by default.** Non-legacy households get a `DashboardOnlyNotifier`; they
  can never send a Telegram/WhatsApp message, so the operator's tokens are not exposed to testers.
* **No session logs by default.** A household writes `session-*.jsonl` under its own directory only
  after the owner enables **"Save session logs"**, and those files are pruned after 7 days.

### The retention sweep

`HouseholdRegistry.sweep()` runs on a timer (`web.registry.sweep_every_s`, default hourly) and in
households mode only. It:

1. Deletes `session-*.jsonl` older than `web.log_retention_days` in **household directories only** —
   it never touches any other file in the log root, and never crosses a household boundary.
2. Expires spent/expired pairing codes.
3. Revokes viewers whose `expires_at` has passed (soft-revoked with `revoked_at` for the audit trail).
4. Evicts idle household runtimes (no station, no viewer) after `web.registry.idle_evict_s`; a
   household with an active station or viewer is never evicted.

A failed sweep never takes the server down: the background task logs and continues.

### Operational notes

* Render's free disk is ephemeral: on redeploy, `out/` and the SQLite database are lost, so stations
  must be re-paired. This is expected, not a data-retention failure.
* A tester who wants a household removed entirely uses **Settings → Delete household**, which drops
  the runtime, closes its sockets, and removes the DB rows in one action.
