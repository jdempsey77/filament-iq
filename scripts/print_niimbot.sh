#!/bin/bash
# print_niimbot.sh <spool_id>
# Fetches spool from Spoolman, renders label with PIL, prints on D11_H.

set -euo pipefail

SPOOL_ID="${1:-}"
if [ -z "${SPOOL_ID}" ] || [ "${SPOOL_ID}" = "0" ]; then
    echo "Error: spool_id is missing or 0" >&2
    exit 1
fi

CONFIG_ENV="${HOME}/.config/filament_iq/monitor-config.env"
if [ -f "${CONFIG_ENV}" ]; then
    # shellcheck disable=SC1090
    source "${CONFIG_ENV}"
fi

if [ -z "${SPOOLMAN_URL:-}" ]; then
    echo "Error: SPOOLMAN_URL not set in ${CONFIG_ENV}" >&2
    exit 1
fi

SPOOL_JSON_FILE=$(mktemp /tmp/niimbot_spool_XXXXXX.json)
trap 'rm -f "${SPOOL_JSON_FILE}"' EXIT

if ! curl -s --fail "${SPOOLMAN_URL}/api/v1/spool/${SPOOL_ID}" > "${SPOOL_JSON_FILE}"; then
    echo "Error: failed to fetch spool ${SPOOL_ID} from ${SPOOLMAN_URL}" >&2
    exit 1
fi

if [ ! -s "${SPOOL_JSON_FILE}" ]; then
    echo "Error: empty response for spool ${SPOOL_ID}" >&2
    exit 1
fi

~/niimprint-311-env/bin/python3 - "${SPOOL_JSON_FILE}" <<'PYEOF'
import json
import sys
from PIL import Image, ImageDraw, ImageFont

with open(sys.argv[1]) as f:
    spool = json.load(f)

filament  = spool.get("filament") or {}
vendor    = str((filament.get("vendor") or {}).get("name") or "Unknown")
material  = str(filament.get("material") or "?").upper()
name      = str(filament.get("name") or "")
color_hex = str(filament.get("color_hex") or "").strip().lstrip("#")
spool_id  = str(spool.get("id", "?"))

try:
    cr = int(color_hex[0:2], 16)
    cg = int(color_hex[2:4], 16)
    cb = int(color_hex[4:6], 16)
except Exception:
    cr, cg, cb = 128, 128, 128

# Landscape canvas 306×141 → rotate -90° → portrait 141×306
TW, TH = 306, 141
tmp  = Image.new("RGB", (TW, TH), (255, 255, 255))
draw = ImageDraw.Draw(tmp)

BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

try:
    f_vendor = ImageFont.truetype(BOLD, 28)
    f_mat    = ImageFont.truetype(REG,  20)
    f_name   = ImageFont.truetype(REG,  18)
    f_badge  = ImageFont.truetype(MONO, 16)
except Exception:
    f_vendor = f_mat = f_name = f_badge = ImageFont.load_default()


def tw(text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def th(text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# Left color stripe (4px)
STRIPE_W = 4
draw.rectangle([0, 0, STRIPE_W - 1, TH - 1], fill=(cr, cg, cb))

TX     = STRIPE_W + 8
TX_END = TW - 8
TY     = 8

# Line 1: vendor (bold, auto-shrink to fit)
vf = f_vendor
vendor_max_w = TX_END - TX
for sz in [28, 24, 20, 16]:
    try:
        vf = ImageFont.truetype(BOLD, sz)
    except Exception:
        vf = f_vendor
    if tw(vendor, vf) <= vendor_max_w:
        break
draw.text((TX, TY), vendor, font=vf, fill=(17, 17, 17))
y = TY + th(vendor, vf) + 4

# Line 2: material
draw.text((TX, y), material, font=f_mat, fill=(51, 51, 51))
y += th(material, f_mat) + 3

# Line 3: filament name (muted — this is the key fix vs. pre-baked PNGs)
if name:
    draw.text((TX, y), name, font=f_name, fill=(136, 136, 136))
    y += th(name, f_name) + 5

# Divider
draw.line([(TX, y), (TX_END, y)], fill=(218, 218, 218), width=1)
y += 5

# Line 4: spool ID badge
badge = f"#{spool_id}"
draw.text((TX, y), badge, font=f_badge, fill=(85, 85, 85))

# Rotate -90° CW → portrait 141×306
out = tmp.rotate(-90, expand=True)
out.convert("1").save("/tmp/niimbot_label.png")
print(f"Label rendered: /tmp/niimbot_label.png  spool={spool_id} {vendor} {material} {name!r}")
PYEOF

~/niimprint-311-env/bin/python3 ~/test_d11h_print.py /tmp/niimbot_label.png
