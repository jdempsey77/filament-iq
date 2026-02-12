# Tapo C111 (external P1S) – stable setup

Camera entity: **camera.tapo_c111_live_view** (Tapo integration). **go2rtc** serves the RTSP stream for WebRTC; config is in **go2rtc.yaml** and deployed with `./scripts/manage_ha.sh --go2rtc`. **secrets.yaml** holds the RTSP URL for the generic camera fallback (if used).

---

## Before bed (one-time deploy)

From the repo root, run:

```bash
./scripts/manage_ha.sh --config --restart
```

That deploys **configuration.yaml** and **secrets.yaml** to HA and restarts Home Assistant. Wait ~1–2 minutes for HA to come back, then:

```bash
./scripts/manage_ha.sh --stage
```

Stage dashboard is then using the new camera entity.

---

## Once in Home Assistant (do after first deploy)

1. **Remove the old camera**  
   **Settings** → **Devices & Services** → find the old Generic Camera (was `camera.192_168_4_166`) → **⋮** → **Delete**.

2. **Restart WebRTC add-on**  
   **Settings** → **Add-ons** → **WebRTC** → **Restart**.

---

## go2rtc (streams for WebRTC)

**go2rtc.yaml** in the repo defines streams keyed by camera entity_id. For the Tapo C111 we use **camera.tapo_c111_live_view** so the WebRTC card gets the RTSP feed from go2rtc (port 1984).

To push updates:

```bash
./scripts/manage_ha.sh --go2rtc
```

Then restart the go2rtc add-on (Settings → Add-ons → go2rtc → Restart). Optional: set **REMOTE_GO2RTC_PATH** in **deploy.env** if the add-on uses a different path (e.g. `/config/go2rtc.yml`).

---

---

## Still black? Quick checks

1. **VLC test (same Wi‑Fi as camera)**  
   Open VLC → Media → Open Network Stream → paste:
   ```text
   rtsp://camera:PrinterCam2026@192.168.4.166:554/stream1
   ```
   - If **VLC is black or fails**: camera, credentials, or network (fix in Tapo app / network first).
   - If **VLC shows picture**: problem is in HA or WebRTC (see below).

2. **HA logs**  
   Settings → System → Logs. Open the camera card (get the black screen), then check the logs for lines with `rtsp`, `ffmpeg`, `camera`, or `192.168.4.166`. Note any errors and fix or search for them.

3. **Fallback: add camera in UI**  
   If the YAML camera never shows picture:
   - In **configuration.yaml**, comment out or remove the Tapo camera block (the `camera:` entry with `stream_source: !secret tapo_c111_stream_url`), then deploy and restart.
   - In HA: **Settings** → **Devices & Services** → **Add Integration** → search **Generic Camera** → add.
   - **Stream URL** (paste exactly):  
     `rtsp://camera:PrinterCam2026@192.168.4.166:554/stream1?rtsp_transport=tcp`
   - Create the integration, then in the dashboard card set the entity to the new camera (e.g. `camera.tapo_c111_live_view` or whatever name HA gives it).

After that, the feed uses **stream1** and **rtsp_transport=tcp**.
