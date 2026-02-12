# 3MF Retrieval for Multi-Color Filament Tracking — Feasibility Investigation

**Goal:** Determine whether the Bambu printer or Bambu Cloud exposes a way to retrieve the sliced 3MF (or equivalent job package) for a completed print, so we can parse per-filament usage and subtract exact grams from Spoolman.

**Constraint:** No workaround design (e.g. watched folder) until this decision is made.

---

## Phase 1 — Feasibility Investigation

### 1. Printer-local APIs

#### Does the Bambu printer store the uploaded 3MF locally?

**Yes.** When a print is sent (Bambu Studio “Print Plate” / “Send”, or via app/cloud), the 3MF (or `.gcode.3mf`) is stored on the printer. Evidence:

- Bambu Lab Wiki (SD card): “When you use ‘Print Plate’ or ‘Print All’ in Bambu Studio … the 3MF and G-code files are automatically saved to the SD card” and “stored in a cache location on the printer’s SD card during print jobs.”  
  [FAQ for Printing from micro SD Card | Bambu Lab Wiki](https://wiki.bambulab.com/en/general/micro-sd-faq)
- MQTT command captured in ha-bambulab discussion #628 shows the print start URL: `"url":"file:///sdcard/cache/Wirespool_A.3mf"`. So the file lives under `/sdcard/cache/` with a name derived from the project/plate.  
  [greghesp/ha-bambulab Discussion #628](https://github.com/greghesp/ha-bambulab/discussions/628)
- A user in that same discussion listed 3MF files on the printer via FTPS at root `/`, e.g. `File: /Wirespool_A.gcode.3mf`, `File: /Wirespool_B.gcode.3mf` (with sizes and dates). So files are present and listable.  
  [RHolmesLboro comment, Oct 29 2024](https://github.com/greghesp/ha-bambulab/discussions/628#discussioncomment-11091260)

#### Is there any REST, MQTT, FTP, SMB, or undocumented endpoint that allows listing or downloading print job files?

**Yes — FTPS.** P1 and A1 series support FTPS (implicit TLS, port 990):

- **Credentials:** Username `bblp`, password = printer access code (from LCD WiFi settings). No LAN-only restriction for FTPS.  
  [We have now FTP(S) Support for Bambu Lab P1 and A1](https://extrawitz.com/bambu-lab-ftp-access-on-a1-and-p-series/), [Forum post #6464](https://forum.bambulab.com/t/we-can-now-connect-to-ftp-on-the-p1-and-a1-series/6464)
- **Listing:** The Python library **mattcar15/bambu-connect** implements `FileClient.get_files(directory="/", extension=".3mf")` using `curl` over FTPS. It returns filenames (e.g. `.gcode.3mf` at root).  
  [bambu_connect/FileClient.py](https://github.com/mattcar15/bambu-connect/blob/main/bambu_connect/FileClient.py)
- **Download:** Same library has `download_file(remote_path, local_path)` — again via `curl` over FTPS — and is used e.g. for timelapse download. So **listing and downloading 3MF from the printer is implemented in the wild.**  
  [FileClient.py](https://github.com/mattcar15/bambu-connect/blob/main/bambu_connect/FileClient.py), [download_timelapse.py](https://github.com/mattcar15/bambu-connect/blob/0.2.0/examples/download_timelapse.py)

No REST or MQTT endpoint for “download this file” was found; file access is via FTPS, not REST/MQTT.

#### Are job IDs, file IDs, or task IDs exposed that map to stored artifacts?

- **Task / job name:** Home Assistant (and ha-bambulab) expose the current or last task name (e.g. `sensor.p1s_..._task_name` or similar). The MQTT print command uses a **filename** (e.g. `Wirespool_A.3mf`) and path `file:///sdcard/cache/...`. So the **task name and the 3MF filename are related** (plate/project name in slicer → filename on printer).
- There is no separate “job ID” or “file ID” in the public MQTT/reports that we need for retrieval; matching is by **task name ↔ filename** (normalize: strip extension, handle `.gcode.3mf` vs `.3mf`, spaces/underscores).

**Conclusion (printer-local):** The printer **does** store the 3MF, and we **can** list and download it via FTPS (P1/A1). Existing code (bambu-connect) proves both list and download. Matching to the completed job is by task name ↔ filename.

---

### 2. Bambu Cloud APIs

#### Does the cloud retain the sliced 3MF (or a job package) for completed prints?

- Public documentation and forum posts do **not** describe Bambu Cloud retaining the actual 3MF file for download. The forum thread “Access to download files from Print History” indicates users want to download from history and that there is **no straightforward method** to do so.  
  [Access to download files from Print History?](https://forum.bambulab.com/t/access-to-download-files-from-print-history/13536)
- Print history in Handy/Bambu Studio is time-limited (e.g. last 90 days) and is about **metadata** (job name, time, status), not “download the 3MF for this job.”  
  [How To See All Of My Print History](https://forum.bambulab.com/t/how-to-see-all-of-my-print-history/149856)

#### Are there any known REST / GraphQL / websocket APIs (official or reverse-engineered) to fetch job metadata, job files, or print artifacts?

- **BambuTools/bambulabs_api** uses MQTT to the printer (and optionally cloud for setup). It does **not** document or implement “download 3MF from cloud” or “get job file by job ID.”  
  [BambuTools/bambulabs_api](https://github.com/BambuTools/bambulabs_api)
- The “Get Job History” endpoint found in search (`apidocs.cloud.mbanq.com`) belongs to **Mbanq** (different product), not Bambu Lab’s consumer cloud.
- No reverse‑engineered Bambu Cloud API for **downloading** a job’s 3MF was found in the repos or discussions checked.

**Conclusion (Bambu Cloud):** **No evidence** that Bambu Cloud exposes an API to retrieve the sliced 3MF (or equivalent) for a completed print. Cloud is **not** a viable first-class source for automatic 3MF retrieval with current public knowledge.

---

### 3. Existing projects

| Project | Finding |
|--------|---------|
| **ha-bambulab** (greghesp) | Exposes print control and status; `print_project_file` starts a print from a path (e.g. on SD). Does **not** implement “list printer files” or “download 3MF from printer” in the integration. Discussion #628 shows MQTT path `file:///sdcard/cache/<name>.3mf` and community interest in listing/starting from SD. |
| **mattcar15/bambu-connect** | Implements **get_files()** and **download_file()** over FTPS. Used for listing 3MF and downloading (e.g. timelapse). Proves **printer-local 3MF list + download is feasible.** |
| **BambuTools/bambulabs_api** | MQTT-focused; no file list/download in the main API. |
| **OctoPrint-BambuPrinter** (jneilliii) | References listing 3MF files (e.g. `list_3mf_files.py` output in discussion #628); path is root `/` with names like `Wirespool_A.gcode.3mf`. |
| **darkorb/bambu-ftp-and-print** | Uses FTP for upload/print; does not change the conclusion that FTPS list/download is supported. |

None of the projects implement “download 3MF from Bambu Cloud.” At least one (bambu-connect) successfully implements **printer-local** list and download via FTPS.

---

### 4. Post-print file retention

Forum: “Auto delete downloaded file and cache after print” — answer from Bambu was “Not to my knowledge.” So by default, **files are not auto-deleted** after the print; the 3MF remains on the printer when the job finishes.  
[Auto delete downloaded file and cache after print](https://forum.bambulab.com/t/auto-delete-downloaded-file-and-cache-after-print/58551)

This makes “on print finished → fetch 3MF from printer” viable as long as the user has not enabled any future auto-delete option.

---

## Phase 2 — Decision Gate

### 3MF retrieval from printer/cloud: feasible or not?

- **Bambu Cloud:**  
  **Not currently feasible.** The cloud does not expose a documented or reverse‑engineered API to download the sliced 3MF (or job package) for a completed print. Evidence: forum posts (no way to download from print history), no such API in BambuTools or ha-bambulab, and no public Bambu Cloud file-download docs.

- **Printer (P1/A1) via FTPS:**  
  **Feasible.**  
  - The printer stores the 3MF (e.g. under `/sdcard/cache/`, often listed at FTPS root as `*.gcode.3mf`).  
  - FTPS is supported (bblp + access code); listing and downloading are implemented (e.g. mattcar15/bambu-connect).  
  - Files remain after the print (no default auto-delete).  
  - The completed job can be matched to a file by **task name** (from HA) and **filename** (from FTPS list) after normalizing (extension, spaces/underscores).

So overall:

- **Printer-local (FTPS):** **Yes — 3MF retrieval from the printer is feasible** for P1/A1.
- **Bambu Cloud:** **No — 3MF retrieval from the cloud is not currently feasible** with public APIs or known reverse‑engineering.

---

## Phase 3 — Conditional Next Steps

### If feasible (printer path) — automated retrieval flow

1. **On print finished** (existing trigger): read `task_name` (and optionally print start time).
2. **Connect to printer via FTPS** (same credentials as MQTT: IP, access code; user bblp). Use a small script or integration that:
   - Lists files (e.g. `get_files("/", ".3mf")` or `get_files("/cache/", ".3mf")` depending on actual FTPS layout).
   - Normalizes `task_name` (strip extension, collapse spaces/underscores, optionally ignore `.gcode` in name).
   - Picks the best match (e.g. filename that normalizes to task name; if several, use modification time closest to print start).
3. **Download** the matched 3MF to a temp path (e.g. `/config/tts/` or a dedicated folder).
4. **Parse** the 3MF (ZIP) → `Metadata/slice_info.config` (or equivalent) → per-filament `used_g` (or `used_m` + density).
5. **Map** filaments to AMS trays (color + type) and call `spoolman.use_spool_filament` per spool; then refresh Spoolman.
6. **If no 3MF is found or match fails:** do not subtract; notify “Multi-color print finished – no 3MF matched; subtract manually or check printer storage.”

**Identifiers:** Task name from HA; filename (and optionally mtime) from FTPS. No job_id/file_id required.

**Constraints:**  
- Only for printers that support FTPS (P1, A1 confirmed; X1 may differ).  
- User must not enable “auto delete after print” (or we must run before that runs).  
- If the job was started from **cloud** and the file was streamed without being stored as a named file, behavior may differ; community evidence suggests sent jobs do land in cache with a name.

### If not feasible (cloud only)

Then **do not** rely on cloud for 3MF. Use **printer FTPS** as above, or fall back to **watched folder** (user places 3MF in a known path) and same parse/map/subtract logic.

---

## Summary

| Source | Feasible? | Evidence |
|--------|-----------|----------|
| **Printer (FTPS)** | Yes (P1/A1) | FTPS supported; bambu-connect lists and downloads 3MF; file stored in cache and not auto-deleted; task name ↔ filename matching is viable. |
| **Bambu Cloud** | No | No public API or reverse‑engineered way to download a job’s 3MF; forum confirms no download from print history. |

**Outcome:** Proceed with **printer-local FTPS** as the first-class automatic 3MF retrieval path for P1/A1. Treat Bambu Cloud as not feasible until an API or method is documented. If a specific printer (e.g. X1) does not support FTPS or stores files differently, that model should be checked separately; the decision gate for “printer or cloud?” is answered as above.
