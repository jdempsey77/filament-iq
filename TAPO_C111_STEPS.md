# Tapo C111 camera – step-by-step fix

**If the Tapo integration says "Camera stream authentication failed"**, stop using the Tapo integration and use **only** the Generic Camera (RTSP URL that works in VLC). Do the steps below in order.

---

## Step 1: Remove the Tapo C111 integration

1. In Home Assistant go to **Settings** → **Devices & Services**.
2. Find **Tapo** (or the card for **Tapo C111 C111**).
3. Click it to open the integration/device.
4. Click the **⋮** (three dots) at the top right.
5. Click **Delete** (or **Remove**).
6. Confirm. That removes the Tapo integration for this camera so HA stops trying to use its auth.

---

## Step 2: Make sure the Generic Camera has the stream URL

Your repo already has:

- **configuration.yaml** – camera block with `stream_source: !secret tapo_c111_stream_url`
- **secrets.yaml** – `tapo_c111_stream_url: "rtsp://camera:PrinterCam2026@192.168.4.166:554/stream1?rtsp_transport=tcp"`

So that **stream URL is correct** (same as VLC).

Deploy config and secrets, then restart HA so the Generic Camera loads:

```bash
cd /Users/jdempsey/code/home_assistant
./scripts/manage_ha.sh --config --restart
```

Wait 1–2 minutes for HA to come back.

---

## Step 3: Find the Generic Camera entity

1. In HA go to **Developer Tools** → **States**.
2. In the filter box type **camera** or **tapo** or **generic**.
3. Find the entity for the external/Tapo camera (e.g. **Tapo C111 P1S external** or **Generic**).
4. Note its **entity_id** (e.g. `camera.tapo_c111_p1s_external` or `camera.tapo_c111_live_view`).

---

## Step 4: Point the dashboard at that entity

1. In your repo open **dashboards/dashboard.stage.yaml**.
2. Search for the line that has **entity: camera.** for the external camera (the one that was black).
3. Set it to the entity_id from Step 3, for example:
   ```yaml
   entity: camera.tapo_c111_p1s_external
   ```
   (Use the exact id you saw in States.)
4. Save the file.
5. Deploy the stage dashboard:
   ```bash
   ./scripts/manage_ha.sh --stage
   ```
6. In the browser open the **Stage** dashboard and open the external camera card. You should see the stream (same as VLC).

---

## Step 5: Restart WebRTC add-on (if the card uses WebRTC)

1. **Settings** → **Add-ons** → find **WebRTC** (or “WebRTC Camera”) → **Restart**.

---

## If the camera is still black

- In **Settings** → **System** → **Logs** look for errors when you open the camera (e.g. containing `rtsp`, `generic`, `camera`, `secret`). If you see “secret not found” or similar, **secrets.yaml** on the server may be wrong or missing the key **tapo_c111_stream_url**. Re-run the deploy so **secrets.yaml** is copied again, then restart HA.
- Confirm on the **HA host** that the file exists and has the URL, e.g.:
  - SSH to the host and run: `cat /config/secrets.yaml` (or your `REMOTE_CONFIG_PATH`). You should see a line like `tapo_c111_stream_url: "rtsp://camera:PrinterCam2026@192.168.4.166:554/stream1?rtsp_transport=tcp"`.

---

## Summary

| Step | Action |
|------|--------|
| 1 | Remove Tapo C111 integration (Settings → Devices & Services → Tapo → ⋮ → Delete). |
| 2 | Run `./scripts/manage_ha.sh --config --restart`. |
| 3 | Developer Tools → States → find Generic/Tapo camera → note **entity_id**. |
| 4 | In **dashboard.stage.yaml** set the external camera card to that **entity_id** → save → `./scripts/manage_ha.sh --stage`. |
| 5 | Restart WebRTC add-on. |

After this, the camera uses **only** the Generic Camera with the RTSP URL (no Tapo integration auth), so “Camera stream authentication failed” from the integration no longer applies.
