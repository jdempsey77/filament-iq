# Labels — source of truth

## Current status
Live and printing. Enhanced label path active for all 44 filaments (100% high confidence).

## Hardware
- Brother QL-810W, DK-1218 24mm round labels (d24) and 29x90mm continuous (portrait)
- Printer at tcp://192.168.4.156:9100

## Label types

### Enhanced label (high/medium confidence match)
- QR code encoding 3dfilamentprofiles.com profile URL
- Spool ID badge (bottom right, prominent)
- Vendor, material, type
- Nozzle + bed temps (when available in dataset — currently none)
- Left color stripe from filament color_hex

### Standard label (low/no match fallback)
- Same layout, QR encodes plain #spool_id text
- Spool ID badge always present

## Profile dataset
- Source: 3dfilamentprofiles.com (scraped via jklewa/filament-profiles-data parser)
- Location on HA: `/config/filament_profiles/filaments.json`
- Size: 25,945 profiles, 897 brands
- NOT committed to repo (scraped data, gray area)
- To refresh: re-run scraper from filament-profiles-data repo with fresh browser cookies
- apps.yaml key: `filament_profiles_path: "/config/filament_profiles/filaments.json"`

## Matching
- Brand + material + type keyword extraction via _TYPE_KEYWORDS
- High confidence (≥0.9): enhanced label + brand/material/type QR URL
- Medium confidence (≥0.7): enhanced label + brand/material QR URL
- Low/none: standard label + plain spool ID QR

## Inventory coverage
44/44 filaments at high confidence as of 2026-05-14.
Known gap: print settings (temps/flow) not available in current dataset.

## Naming convention for new spools
Follow pattern: `{MATERIAL} {TYPE} {COLOR}`
Examples: "PLA Basic Red", "PETG Transparent Clear", "TPU 68D Gray", "PLA+ High-Speed Gray"

## Next steps
- Print settings data source TBD (Bambu Studio profiles is a candidate)
