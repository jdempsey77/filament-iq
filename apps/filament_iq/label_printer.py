"""
Label Printer — generates and prints spool labels on a Brother QL-810W.

Listens for HA event `filament_iq_print_label` with payload { spool_id: int }.
Fetches spool + filament data from Spoolman, generates a 306x991 portrait
label image (DK-1201 29x90mm) with Pillow, and sends to the printer via
brother_ql.

Optionally moves spool location from "New" to "Shelf" after printing.
Fires HA event `filament_iq_label_result` with { spool_id, success, error }.

Config keys: spoolman_url, printer_url, printer_model, label_size, dry_run.
"""

import json
import logging
import urllib.error
import urllib.request

try:
    import qrcode as _qrcode_lib
    from qrcode.constants import ERROR_CORRECT_M as _QR_ECM
except ImportError:
    _qrcode_lib = None
    _QR_ECM = None

from .base import FilamentIQBase
from .filament_profiles import FilamentProfilesClient

logger = logging.getLogger(__name__)

LABEL_DIMENSIONS = {
    "29x90": (306, 991),
    "62x100": (696, 1109),
    "62x29": (696, 306),
    "d24": (236, 236),
}


class LabelPrinter(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        self.printer_url = str(self.args.get("printer_url", "")).strip()
        self.printer_model = str(self.args.get("printer_model", "QL-810W")).strip()
        self.label_size = str(self.args.get("label_size", "29x90")).strip()
        self.dry_run = bool(self.args.get("dry_run", True))

        profiles_path = self.args.get("filament_profiles_path")
        self.profiles_client = FilamentProfilesClient(str(profiles_path)) if profiles_path else None

        self.listen_event(self._on_print_label_event, "filament_iq_print_label")
        self.listen_event(self._on_font_test_event, "filament_iq_print_font_test")
        self.log(
            f"LabelPrinter initialized spoolman={self.spoolman_url} "
            f"printer={self.printer_url} dry_run={self.dry_run}",
            level="INFO",
        )

    def _on_print_label_event(self, event_name, data, kwargs):
        """Handle filament_iq_print_label event."""
        payload = data or {}
        spool_id = int(payload.get("spool_id", 0))
        if spool_id <= 0:
            self.log(f"LABEL_SKIP invalid spool_id={spool_id}", level="WARNING")
            return

        try:
            spool_data = self.fetch_spool(spool_id)
            if spool_data is None:
                self.fire_result_event(spool_id, False, "Spool not found in Spoolman")
                return

            filament_id = spool_data.get("filament", {}).get("id")
            if filament_id:
                filament_data = self.fetch_filament(filament_id)
            else:
                filament_data = spool_data.get("filament", {})

            image = self.generate_label_image(spool_data, filament_data or {})
            self.send_to_printer(image, spool_id)
            self.update_spool_location(spool_id, spool_data)
            self.fire_result_event(spool_id, True)

        except Exception as e:
            self.log(f"LABEL_ERROR spool_id={spool_id}: {e}", level="ERROR")
            self.fire_result_event(spool_id, False, str(e))

    def fetch_spool(self, spool_id):
        """GET spool from Spoolman. Returns dict or None."""
        url = f"{self.spoolman_url}/api/v1/spool/{spool_id}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.log(f"LABEL_FETCH_SPOOL_FAILED spool_id={spool_id}: {e}", level="ERROR")
            return None

    def fetch_filament(self, filament_id):
        """GET filament from Spoolman. Returns dict or None."""
        url = f"{self.spoolman_url}/api/v1/filament/{filament_id}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.log(f"LABEL_FETCH_FILAMENT_FAILED filament_id={filament_id}: {e}", level="WARNING")
            return None

    def _get_profile(self, filament_data):
        """Return a FilamentProfile if client is available, else None."""
        client = getattr(self, "profiles_client", None)
        if not (client and client.available):
            return None
        return client.lookup(
            vendor=str(filament_data.get("vendor", {}).get("name") or ""),
            material=str(filament_data.get("material") or ""),
            filament_name=str(filament_data.get("name") or ""),
        )

    def generate_label_image(self, spool_data, filament_data):
        """Route to enhanced or standard label; falls back to standard on any error."""
        spool_id = spool_data.get("id", "?")
        profile = None
        try:
            profile = self._get_profile(filament_data)
            if profile:
                self.log(
                    f"LABEL_PROFILE spool_id={spool_id} matched={profile.matched} "
                    f"confidence={profile.confidence} source={profile.source} "
                    f"temp={profile.temp_min}-{profile.temp_max} "
                    f"bed={profile.bed_temp_min} flow={profile.flow_ratio}",
                    level="INFO",
                )
            else:
                self.log(
                    f"LABEL_PROFILE spool_id={spool_id} profile=None "
                    f"(client={'available' if getattr(getattr(self, 'profiles_client', None), 'available', False) else 'unavailable'})",
                    level="INFO",
                )
            if profile and profile.confidence in ("high", "medium"):
                self.log(
                    f"LABEL_PATH spool_id={spool_id} path=enhanced "
                    f"reason=confidence:{profile.confidence}",
                    level="INFO",
                )
                return self._generate_enhanced_label(spool_data, filament_data, profile)
        except Exception as e:
            self.log(
                f"LABEL: Enhanced label failed, falling back to standard: {e}",
                level="WARNING",
            )
        self.log(
            f"LABEL_PATH spool_id={spool_id} path=standard "
            f"reason={'no_profile' if not profile else 'low_confidence:' + profile.confidence}",
            level="INFO",
        )
        return self._generate_standard_label(spool_data, filament_data)

    # ── QR code ───────────────────────────────────────────────────────

    def _make_qr_image(self, spool_id):
        """Generate a QR code PIL image for the Spoolman spool URL.

        Returns a PIL Image, or None if qrcode library is not installed.
        Install via AppDaemon addon options: python_packages: [qrcode[pil]]
        """
        if _qrcode_lib is None:
            return None
        from PIL import Image
        url = f"{self.spoolman_url}/spool/{spool_id}"
        qr = _qrcode_lib.QRCode(
            version=None, error_correction=_QR_ECM, box_size=8, border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # ── Portrait label (29x90 and other non-round sizes) ─────────────

    def _generate_standard_label(self, spool_data, filament_data):
        """Standard label: QR code + text, no temp line."""
        return self._generate_label_portrait(spool_data, filament_data, profile=None)

    def _generate_enhanced_label(self, spool_data, filament_data, profile):
        """Route to d24 round layout or portrait layout based on label_size."""
        if self.label_size == "d24":
            return self._generate_enhanced_d24(spool_data, filament_data, profile)
        return self._generate_enhanced_portrait(spool_data, filament_data, profile)

    def _generate_enhanced_portrait(self, spool_data, filament_data, profile):
        """Enhanced portrait label: QR code + text + temps (when available)."""
        return self._generate_label_portrait(spool_data, filament_data, profile=profile)

    def _generate_label_portrait(self, spool_data, filament_data, profile=None):
        """
        Shared landscape→portrait label layout.

        Left zone:  6px color stripe  |  QR code (or placeholder box)
        Right zone: vendor (bold) + #ID badge | material [type] | color name | divider | temps
        Temps line only drawn when profile has non-None temp values.
        """
        from PIL import Image, ImageDraw, ImageFont

        W, H = LABEL_DIMENSIONS.get(self.label_size, (306, 991))
        TW, TH = H, W  # landscape canvas; rotated -90 CW -> portrait W x H

        spool_id   = spool_data.get("id", "?")
        vendor     = str(filament_data.get("vendor", {}).get("name") or "Unknown")
        material   = str(filament_data.get("material") or "?").upper()
        color_name = str(filament_data.get("name") or "")
        color_hex  = str(filament_data.get("color_hex") or "").strip().lstrip("#")

        try:
            cr = int(color_hex[0:2], 16)
            cg = int(color_hex[2:4], 16)
            cb = int(color_hex[4:6], 16)
        except Exception:
            cr, cg, cb = 128, 128, 128

        mat_type = ""
        if profile and profile.material_type:
            mat_type = profile.material_type.title()

        tmp  = Image.new("RGB", (TW, TH), (255, 255, 255))
        draw = ImageDraw.Draw(tmp)

        BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
        try:
            f_vendor = ImageFont.truetype(BOLD, 40)
            f_mat    = ImageFont.truetype(REG,  28)
            f_color  = ImageFont.truetype(REG,  28)
            f_temp   = ImageFont.truetype(REG,  26)
            f_badge  = ImageFont.truetype(MONO, 28)
        except Exception:
            f_vendor = f_mat = f_color = f_temp = f_badge = ImageFont.load_default()

        def _tw(text, font):
            bb = draw.textbbox((0, 0), text, font=font)
            return bb[2] - bb[0]

        def _th(text, font):
            bb = draw.textbbox((0, 0), text, font=font)
            return bb[3] - bb[1]

        # ── Left zone: color stripe + QR ─────────────────────────────
        STRIPE_W  = 6
        QR_MARGIN = 10
        QR_SIZE   = TH - QR_MARGIN * 2   # fits full label height with small margin

        draw.rectangle([0, 0, STRIPE_W - 1, TH - 1], fill=(cr, cg, cb))

        qr_img = self._make_qr_image(spool_id)
        QR_X = STRIPE_W + QR_MARGIN
        if qr_img:
            qr_img = qr_img.resize((QR_SIZE, QR_SIZE), Image.LANCZOS)
            tmp.paste(qr_img, (QR_X, QR_MARGIN))
        else:
            # Placeholder when qrcode library is not installed
            draw.rectangle(
                [QR_X, QR_MARGIN, QR_X + QR_SIZE - 1, QR_MARGIN + QR_SIZE - 1],
                outline=(210, 210, 210), width=2,
            )
            ph = f"#{spool_id}"
            draw.text(
                (QR_X + (QR_SIZE - _tw(ph, f_badge)) // 2,
                 QR_MARGIN + (QR_SIZE - _th(ph, f_badge)) // 2),
                ph, font=f_badge, fill=(210, 210, 210),
            )

        DIV_X = QR_X + QR_SIZE + QR_MARGIN
        draw.line([(DIV_X, 12), (DIV_X, TH - 12)], fill=(210, 210, 210), width=1)

        # ── Right zone: text ─────────────────────────────────────────
        TX     = DIV_X + 14   # text left margin
        TX_END = TW - 12      # text right edge
        TY     = 14           # text top margin

        # ID badge — top-right corner
        badge_str = f"#{spool_id}"
        bw = _tw(badge_str, f_badge)
        bh = _th(badge_str, f_badge)
        bpx, bpy = 8, 4
        bdg_x2 = TX_END
        bdg_x1 = bdg_x2 - bw - bpx * 2
        bdg_y1 = TY
        bdg_y2 = TY + bh + bpy * 2
        draw.rounded_rectangle([bdg_x1, bdg_y1, bdg_x2, bdg_y2], radius=4, fill=(17, 17, 17))
        draw.text((bdg_x1 + bpx, bdg_y1 + bpy), badge_str, font=f_badge, fill=(255, 255, 255))

        # Line 1: Vendor — auto-shrink to avoid badge overlap
        vendor_max_w = bdg_x1 - TX - 8
        vf = f_vendor
        for sz in [40, 34, 28, 22]:
            try:
                vf = ImageFont.truetype(BOLD, sz)
            except Exception:
                vf = f_vendor
            if _tw(vendor, vf) <= vendor_max_w:
                break
        draw.text((TX, TY), vendor, font=vf, fill=(17, 17, 17))
        y = TY + max(_th(vendor, vf), bh + bpy * 2) + 8

        # Line 2: Material [+ type]
        mat_str = f"{material} {mat_type}".strip() if mat_type else material
        draw.text((TX, y), mat_str, font=f_mat, fill=(51, 51, 51))
        y += _th(mat_str, f_mat) + 6

        # Line 3: Color name (muted gray)
        if color_name:
            draw.text((TX, y), color_name, font=f_color, fill=(136, 136, 136))
            y += _th(color_name, f_color) + 10

        # Divider
        draw.line([(TX, y), (TX_END, y)], fill=(218, 218, 218), width=1)
        y += 10

        # Line 4: Temps — only when profile provides values
        if profile:
            temp_parts = []
            if profile.temp_min is not None and profile.temp_max is not None:
                temp_parts.append(f"{profile.temp_min}-{profile.temp_max}°C")
            if profile.bed_temp_min is not None:
                temp_parts.append(f"Bed {profile.bed_temp_min}°C")
            if temp_parts:
                draw.text((TX, y), " · ".join(temp_parts), font=f_temp, fill=(85, 85, 85))

        # Rotate -90 CW into portrait W x H
        tmp_rotated = tmp.rotate(-90, expand=True)
        img = Image.new("RGB", (W, H), (255, 255, 255))
        img.paste(tmp_rotated, (0, 0))
        return img

    # ── d24 round label ───────────────────────────────────────────────

    def _generate_enhanced_d24(self, spool_data, filament_data, profile):
        """236x236 d24 round-label layout with profile print settings."""
        from PIL import Image, ImageDraw, ImageFont

        W, H = 236, 236
        img = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        color_hex = str(filament_data.get("color_hex") or "").strip().lstrip("#")
        try:
            r = int(color_hex[0:2], 16)
            g = int(color_hex[2:4], 16)
            b = int(color_hex[4:6], 16)
        except Exception:
            r, g, b = 128, 128, 128
        lum     = 0.299 * r + 0.587 * g + 0.114 * b
        fg      = (255, 255, 255) if lum < 128 else (17, 17, 17)
        div_col = (180, 180, 180) if lum < 128 else (100, 100, 100)

        draw.ellipse([0, 0, W - 1, H - 1], fill=(r, g, b))

        try:
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            font_med   = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except Exception:
            font_large = font_med = font_small = ImageFont.load_default()

        vendor   = str(filament_data.get("vendor", {}).get("name") or "")[:12]
        material = str(filament_data.get("material") or "").upper()
        spool_id = spool_data.get("id", "?")

        lines = [
            (f"#{spool_id}", font_large),
            (vendor,         font_med),
            (material,       font_med),
        ]

        has_temp = profile.temp_min is not None and profile.temp_max is not None
        has_bed  = profile.bed_temp_min is not None
        if has_temp or has_bed:
            lines.append(("---", None))
            parts = []
            if has_temp:
                parts.append(f"{profile.temp_min}-{profile.temp_max}°")
            if has_bed:
                parts.append(f"Bed:{profile.bed_temp_min}°")
            lines.append((" ".join(parts), font_small))

        if profile.flow_ratio is not None:
            lines.append((f"Flow:{profile.flow_ratio:.2f}", font_small))

        gap = 4
        row_heights = []
        for text, font in lines:
            if text == "---":
                row_heights.append(8)
            else:
                bb = draw.textbbox((0, 0), text, font=font)
                row_heights.append(bb[3] - bb[1])
        total_h = sum(row_heights) + gap * (len(lines) - 1)

        y = (H - total_h) // 2
        for i, (text, font) in enumerate(lines):
            if text == "---":
                mx = W // 5
                draw.line([(mx, y + 4), (W - mx, y + 4)], fill=div_col, width=1)
            else:
                bb = draw.textbbox((0, 0), text, font=font)
                x = (W - (bb[2] - bb[0])) // 2
                draw.text((x, y), text, font=font, fill=fg)
            y += row_heights[i] + gap

        return img

    # ── Font test ─────────────────────────────────────────────────────

    def _on_font_test_event(self, event_name, data, kwargs):
        """Print a font calibration label with sample text at various sizes."""
        try:
            image = self._generate_font_test_image()
            self.send_to_printer(image, 0)
            self.log("FONT_TEST_SENT", level="INFO")
        except Exception as e:
            self.log(f"FONT_TEST_ERROR: {e}", level="ERROR")

    def _generate_font_test_image(self):
        from PIL import Image, ImageDraw, ImageFont

        tmp = Image.new("RGB", (991, 306), (255, 255, 255))
        draw = ImageDraw.Draw(tmp)

        samples = [
            (40, "Bambu Lab PLA Black 220°C"),
            (50, "Bambu Lab PLA Black"),
            (60, "Black 220°C"),
            (72, "Bambu Lab"),
            (82, "PLA #9"),
        ]

        y = 8
        for size, text in samples:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
            except Exception:
                font = ImageFont.load_default()
            label = f"{size}px: {text}"
            draw.text((12, y), label, font=font, fill=(0, 0, 0))
            bb = draw.textbbox((0, 0), label, font=font)
            y += (bb[3] - bb[1]) + 8
            if y > 296:
                break

        tmp_rotated = tmp.rotate(-90, expand=True)
        img = Image.new("RGB", (306, 991), (255, 255, 255))
        img.paste(tmp_rotated, (0, 0))
        return img

    # ── Printer I/O ───────────────────────────────────────────────────

    def send_to_printer(self, image, spool_id):
        """Send label image to Brother QL printer.

        If dry_run=True, logs and returns without importing brother_ql.
        """
        if self.dry_run:
            self.log(
                f"DRY_RUN: would send label for spool {spool_id} "
                f"to {self.printer_url} model={self.printer_model} "
                f"label={self.label_size}",
                level="INFO",
            )
            return

        from brother_ql.conversion import convert
        from brother_ql.backends.helpers import send
        from brother_ql.raster import BrotherQLRaster

        qlr = BrotherQLRaster(self.printer_model)
        instructions = convert(
            qlr=qlr,
            images=[image],
            label=self.label_size,
            rotate="0",
            threshold=70,
            dither=False,
            compress=False,
            red=False,
            dpi_600=False,
            hq=True,
            cut=True,
        )
        send(
            instructions=instructions,
            printer_identifier=self.printer_url,
            backend_identifier="network",
            blocking=True,
        )
        self.log(
            f"LABEL_SENT spool_id={spool_id} printer={self.printer_url}",
            level="INFO",
        )

    def update_spool_location(self, spool_id, spool_data):
        """PATCH location to Shelf if currently New. Non-fatal on failure."""
        current_location = str(spool_data.get("location", "")).strip()
        if current_location != "New":
            self.log(
                f"LABEL_LOCATION_SKIP spool_id={spool_id} "
                f"location={current_location!r} (not 'New')",
                level="DEBUG",
            )
            return

        url = f"{self.spoolman_url}/api/v1/spool/{spool_id}"
        payload = json.dumps({"location": "Shelf"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            self.log(
                f"LABEL_LOCATION_UPDATED spool_id={spool_id} New → Shelf",
                level="INFO",
            )
        except Exception as e:
            self.log(
                f"LABEL_LOCATION_PATCH_FAILED spool_id={spool_id}: {e}",
                level="WARNING",
            )

    def fire_result_event(self, spool_id, success, error=None):
        """Fire filament_iq_label_result HA event."""
        event_data = {
            "spool_id": spool_id,
            "success": success,
            "error": error,
        }
        try:
            self.fire_event("filament_iq_label_result", **event_data)
            self.log(
                f"LABEL_RESULT spool_id={spool_id} success={success}"
                + (f" error={error}" if error else ""),
                level="INFO",
            )
        except Exception as e:
            self.log(f"LABEL_RESULT_FIRE_FAILED: {e}", level="ERROR")
