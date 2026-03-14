# Bambu Lab RFID Tag Internals

**Source:** https://github.com/Bambu-Research-Group/RFID-Tag-Guide
**Block layout reference:** https://github.com/Bambu-Research-Group/RFID-Tag-Guide/blob/main/BambuLabRfid.md
**Date fetched:** 2026-03-14
**Confidence:** HIGH (community reverse-engineering group, actively maintained)

---

## Tag Hardware

- **Type:** MiFare Classic 13.56 MHz RFID (1K, 16 sectors x 4 blocks)
- **UID:** 4-byte hardware identifier, unencrypted, read-only on standard MIFARE chips
- **Storage:** 16 sectors (0-15), each containing 4 blocks of 16 bytes
  - Sectors 0-4: filament metadata (encrypted)
  - Sectors 5-9: empty (keys only)
  - Sectors 10-15: RSA-2048 signature
- **Each block uses a different encryption key** (KeyA in block 3 of each sector)
- **Keys are unique per tag** — a key from one tag cannot unlock another tag

## Block Layout

| Sector | Block | Content |
|--------|-------|---------|
| 0 | 0 | Tag UID (4 bytes) + manufacturer data |
| 0 | 1 | Material variant ID + material ID |
| 0 | 2 | Filament type string (16 bytes) |
| 1 | 4 | Detailed filament type (16 bytes) |
| 1 | 5 | RGBA color (4 bytes) + spool weight in grams (2 bytes LE) + filament diameter (8 bytes float LE) |
| 1 | 6 | Drying temp, drying hours, bed temp type, bed temp, max/min hotend temp (2 bytes each) |
| 2 | 8 | X Cam info (12 bytes) + nozzle diameter (4 bytes float LE) |
| 2 | 9 | Tray UID (16 bytes) — the factory spool serial |
| 2 | 10 | Spool width in mm x 100 (2 bytes LE at offset 4) |
| 3 | 12 | Production date/time as ASCII (`YYYY_MM_DD_HH_MM`) |
| 3 | 13 | Short production date/time |
| 3 | 14 | Filament length in meters (2 bytes LE at offset 4) |
| 4 | 16 | Format ID (2 bytes) + color count (2 bytes) + secondary color ABGR (4 bytes) |
| 4 | 17 | Unknown (2 bytes) |
| 5-9 | — | Empty (encryption keys only) |
| 10-15 | — | RSA-2048 signature blocks |

Each sector's block 3 contains MIFARE keys: KeyA (6 bytes) + permission bits (`87 87 87 69`) + KeyB (6 bytes, zeros).

## Key Derivation (as of Nov 2024)

- Keys can now be **derived from the UID alone** using a known KDF
- Script: `deriveKeys.py` in the repo — `python3 deriveKeys.py [UID] > ./keys.dic`
- Previously required physical sniffing with a Proxmark3 positioned between the AMS and tag
- Proxmark3 (Iceman fork v4.18994+) `fm11rf08s_recovery` script automates full dump (~15-20 min)
- Flipper Zero can also scan tags

## RSA Signature

- **One block range (sectors 10-15) contains a 2048-bit RSA signature**
- Signature covers ALL tag data — changing any single byte invalidates it
- Bambu printer validates signature on every read
- **Only Bambu Lab holds the private key** — custom/forged tags are not possible without custom AMS firmware
- This means: you can READ all tag data, but you cannot WRITE modified data that passes validation

## Cloning

- Tags **CAN be cloned** if UID + all data blocks + signature are copied identically
- **Magic Gen 2 tags** work for cloning (UID is writable)
- **Gen 1 tags do NOT work** — AMS tests for the unlockable command `0x40` and rejects Gen 1
- **FUID (write-once UID) tags** work — UID written once, then locked permanently
- Gen 3-4 tags: untested

## UID / Spool Serial Relationship

- The **UID is the first 8 characters of the spool's serial number** as displayed on the printer touchscreen, Bambu Studio, and Bambu Handy
- The full serial comes from Block 9 (Tray UID, 16 bytes)

---

## Implications for Filament IQ

### Identity model
- **`tray_uuid`** in ha-bambulab is derived from the tag's Tray UID (Block 9) — this is the orientation-independent factory serial we use as primary identity in `lot_nr`
- **`tag_uid`** is the hardware chip UID (Block 0, 4 bytes) — orientation-dependent on some readers, retired from our identity model
- Cannot write custom data to tags (RSA signature prevents it) — **Filament IQ identity must live in Spoolman (`lot_nr`), not on the tag itself**

### Sensor data origin
- The `remain%` and `tray_weight` attributes from ha-bambulab are **read from the tag's encrypted blocks**, not computed by the printer
- `tray_weight` comes from Block 5 (spool weight in grams) — this is the factory-programmed initial weight
- `remain%` is likely computed by the AMS from the difference between factory weight and current measured weight (weigh cell in AMS)
- Color hex comes from Block 5 RGBA field (first 3 bytes = RGB)
- Material type comes from Blocks 1-2 / 4

### Third-party filament tags
- Non-Bambu third-party filament may use the same RFID hardware with different data layout
- Some may pass the RSA signature check (if Bambu-manufactured tags are reused), some may not
- Tags that fail signature validation appear as "unknown" filament in the AMS

### What we cannot do
- Write custom metadata to RFID tags (RSA signature)
- Create new tags from scratch (no private key)
- Use Gen 1 magic tags (AMS detects and rejects)
- Read tags without knowing the encryption keys (but keys are now derivable from UID)
