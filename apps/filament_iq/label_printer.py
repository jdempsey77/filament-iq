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
        try:
            profile = self._get_profile(filament_data)
            if profile and profile.confidence in ("high", "medium"):
                return self._generate_enhanced_label(spool_data, filament_data, profile)
        except Exception as e:
            self.log(
                f"LABEL: Enhanced label failed, falling back to standard: {e}",
                level="WARNING",
            )
        return self._generate_standard_label(spool_data, filament_data)

    def _generate_standard_label(self, spool_data, filament_data):
        """Draw landscape on 991x306, rotate -90° into 306x991 for brother_ql. rotate='0' in convert."""
        from PIL import Image, ImageDraw, ImageFont

        W, H = 306, 991
        TW, TH = 991, 306

        tmp = Image.new("RGB", (TW, TH), (255, 255, 255))
        draw = ImageDraw.Draw(tmp)

        try:
            font_main = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 53)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
            font_mono = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 36)
        except Exception:
            font_main = ImageFont.load_default()
            font_small = font_main
            font_mono = font_main

        vendor = str(filament_data.get("vendor", {}).get("name") or "Unknown")
        material = str(filament_data.get("material") or "?").upper()
        color_name = str(filament_data.get("name") or "")
        color_hex = str(filament_data.get("color_hex") or "").strip().lstrip("#")
        hex_display = f"#{color_hex.upper()}" if color_hex else ""
        ext_temp = filament_data.get("settings_extruder_temp")
        bed_temp = filament_data.get("settings_bed_temp")
        spool_id = spool_data.get("id", "?")

        # --- Black strip x=0→200 ---
        draw.rectangle([0, 0, 240, TH], fill=(17, 17, 17))

        # Vendor 36px white centered at y=80
        vb = draw.textbbox((0, 0), vendor, font=font_small)
        draw.text(((240 - (vb[2] - vb[0])) // 2, 80), vendor, font=font_small, fill=(255, 255, 255))

        # Material 53px white bold centered at y=160
        mb = draw.textbbox((0, 0), material, font=font_main)
        draw.text(((240 - (mb[2] - mb[0])) // 2, 160), material, font=font_main, fill=(255, 255, 255))

        # --- Content zone starting at x=230 ---
        x = 260
        y = 30

        # Color name — auto-shrink if too wide
        if color_name:
            max_w = TW - x - 30
            cn_font = font_main
            for sz in [53, 44, 36, 28]:
                try:
                    from PIL import ImageFont as IF
                    cn_font = IF.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", sz)
                except Exception:
                    cn_font = font_main
                cn_bb = draw.textbbox((0, 0), color_name, font=cn_font)
                if (cn_bb[2] - cn_bb[0]) <= max_w:
                    break
            draw.text((x, y), color_name, font=cn_font, fill=(0, 0, 0))
            cn_bb = draw.textbbox((0, 0), color_name, font=cn_font)
            y += (cn_bb[3] - cn_bb[1]) + 8

        # Hex 36px mono gray
        if hex_display:
            draw.text((x, y), hex_display, font=font_mono, fill=(102, 102, 102))
            hb = draw.textbbox((0, 0), hex_display, font=font_mono)
            y += (hb[3] - hb[1]) + 12

        # Divider 1px #eee
        draw.line([(x, y), (TW - 30, y)], fill=(238, 238, 238), width=1)
        y += 12

        # Temps on one line: "220°C nozzle · 60°C bed"
        temp_parts = []
        if ext_temp:
            temp_parts.append(f"{ext_temp}\u00b0C nozzle")
        if bed_temp:
            temp_parts.append(f"{bed_temp}\u00b0C bed")
        if temp_parts:
            temp_text = " \u00b7 ".join(temp_parts)
            draw.text((x, y), temp_text, font=font_small, fill=(85, 85, 85))

        # ID badge bottom-right: black pill, white mono
        id_text = f"#{spool_id}"
        ib = draw.textbbox((0, 0), id_text, font=font_mono)
        id_tw = ib[2] - ib[0]
        id_th = ib[3] - ib[1]
        pad_x, pad_y = 10, 5
        bx2 = TW - 10
        bx1 = bx2 - id_tw - pad_x * 2
        by2 = TH - 10
        by1 = by2 - id_th - pad_y * 2
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=4, fill=(17, 17, 17))
        draw.text((bx1 + pad_x, by1 + pad_y), id_text, font=font_mono, fill=(255, 255, 255))

        # Rotate -90° CW into 306×991
        tmp_rotated = tmp.rotate(-90, expand=True)
        img = Image.new("RGB", (W, H), (255, 255, 255))
        img.paste(tmp_rotated, (0, 0))

        return img

    def _generate_enhanced_label(self, spool_data, filament_data, profile):
        """Route to d24 round layout or portrait layout based on label_size."""
        if self.label_size == "d24":
            return self._generate_enhanced_d24(spool_data, filament_data, profile)
        return self._generate_enhanced_portrait(spool_data, filament_data, profile)

    def _generate_enhanced_portrait(self, spool_data, filament_data, profile):
        """Portrait enhanced label matching the standard label layout with profile temps/flow."""
        from PIL import Image, ImageDraw, ImageFont

        W, H = LABEL_DIMENSIONS.get(self.label_size, (306, 991))
        TW, TH = H, W  # landscape canvas, rotated to portrait

        tmp = Image.new("RGB", (TW, TH), (255, 255, 255))
        draw = ImageDraw.Draw(tmp)

        try:
            font_main  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 53)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
            font_mono  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 36)
        except Exception:
            font_main = ImageFont.load_default()
            font_small = font_main
            font_mono = font_main

        vendor     = str(filament_data.get("vendor", {}).get("name") or "Unknown")
        material   = str(filament_data.get("material") or "?").upper()
        color_name = str(filament_data.get("name") or "")
        color_hex  = str(filament_data.get("color_hex") or "").strip().lstrip("#")
        hex_display = f"#{color_hex.upper()}" if color_hex else ""
        spool_id   = spool_data.get("id", "?")

        # Left black strip
        draw.rectangle([0, 0, 240, TH], fill=(17, 17, 17))
        vb = draw.textbbox((0, 0), vendor, font=font_small)
        draw.text(((240 - (vb[2] - vb[0])) // 2, 80), vendor, font=font_small, fill=(255, 255, 255))
        mb = draw.textbbox((0, 0), material, font=font_main)
        draw.text(((240 - (mb[2] - mb[0])) // 2, 160), material, font=font_main, fill=(255, 255, 255))

        x = 260
        y = 30

        if color_name:
            max_w = TW - x - 30
            cn_font = font_main
            for sz in [53, 44, 36, 28]:
                try:
                    from PIL import ImageFont as IF
                    cn_font = IF.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", sz)
                except Exception:
                    cn_font = font_main
                cn_bb = draw.textbbox((0, 0), color_name, font=cn_font)
                if (cn_bb[2] - cn_bb[0]) <= max_w:
                    break
            draw.text((x, y), color_name, font=cn_font, fill=(0, 0, 0))
            y += (draw.textbbox((0, 0), color_name, font=cn_font)[3]
                  - draw.textbbox((0, 0), color_name, font=cn_font)[1]) + 8

        if hex_display:
            draw.text((x, y), hex_display, font=font_mono, fill=(102, 102, 102))
            y += (draw.textbbox((0, 0), hex_display, font=font_mono)[3]
                  - draw.textbbox((0, 0), hex_display, font=font_mono)[1]) + 12

        draw.line([(x, y), (TW - 30, y)], fill=(238, 238, 238), width=1)
        y += 12

        temp_parts = []
        if profile.temp_min is not None and profile.temp_max is not None:
            temp_parts.append(f"{profile.temp_min}-{profile.temp_max}°C nozzle")
        elif filament_data.get("settings_extruder_temp"):
            temp_parts.append(f"{filament_data['settings_extruder_temp']}°C nozzle")
        if profile.bed_temp_min is not None:
            temp_parts.append(f"{profile.bed_temp_min}°C bed")
        elif filament_data.get("settings_bed_temp"):
            temp_parts.append(f"{filament_data['settings_bed_temp']}°C bed")
        if temp_parts:
            temp_text = " · ".join(temp_parts)
            draw.text((x, y), temp_text, font=font_small, fill=(85, 85, 85))
            y += (draw.textbbox((0, 0), temp_text, font=font_small)[3]
                  - draw.textbbox((0, 0), temp_text, font=font_small)[1]) + 8

        flow_parts = []
        if profile.flow_ratio is not None:
            flow_parts.append(f"Flow {profile.flow_ratio:.2f}")
        if profile.max_volumetric_speed is not None:
            flow_parts.append(f"Vol {profile.max_volumetric_speed:.1f}mm³/s")
        if flow_parts:
            draw.text((x, y), " · ".join(flow_parts), font=font_small, fill=(85, 85, 85))

        id_text = f"#{spool_id}"
        ib = draw.textbbox((0, 0), id_text, font=font_mono)
        id_tw, id_th = ib[2] - ib[0], ib[3] - ib[1]
        pad_x, pad_y = 10, 5
        bx2 = TW - 10
        bx1 = bx2 - id_tw - pad_x * 2
        by2 = TH - 10
        by1 = by2 - id_th - pad_y * 2
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=4, fill=(17, 17, 17))
        draw.text((bx1 + pad_x, by1 + pad_y), id_text, font=font_mono, fill=(255, 255, 255))

        tmp_rotated = tmp.rotate(-90, expand=True)
        img = Image.new("RGB", (W, H), (255, 255, 255))
        img.paste(tmp_rotated, (0, 0))
        return img

    def _generate_enhanced_d24(self, spool_data, filament_data, profile):
        """236×236 d24 round-label layout with profile print settings."""
        from PIL import Image, ImageDraw, ImageFont

        W, H = 236, 236
        img = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Color + contrast
        color_hex = str(filament_data.get("color_hex") or "").strip().lstrip("#")
        try:
            r = int(color_hex[0:2], 16)
            g = int(color_hex[2:4], 16)
            b = int(color_hex[4:6], 16)
        except Exception:
            r, g, b = 128, 128, 128
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg       = (255, 255, 255) if lum < 128 else (17, 17, 17)
        div_col  = (180, 180, 180) if lum < 128 else (100, 100, 100)

        # Circle background
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

        # Build line list: (text, font) — "---" sentinel = divider
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

        # Measure total height
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
            (40, "Bambu Lab PLA Black 220\u00b0C"),
            (50, "Bambu Lab PLA Black"),
            (60, "Black 220\u00b0C"),
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

        # Import brother_ql only when actually printing — avoids startup
        # failure if the package isn't installed yet.
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
