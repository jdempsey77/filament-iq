"""
Label Printer — generates and prints spool labels on a Brother QL-810W.

Listens for HA event `filament_iq_print_label` with payload { spool_id: int }.
Fetches spool + filament data from Spoolman, generates a rectangular
DK-1201 29×90mm label image with Pillow, and sends to the printer via
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

logger = logging.getLogger(__name__)

# Label dimensions — DK-1201 29×90mm landscape temp canvas
LABEL_W, LABEL_H = 991, 306      # landscape temp canvas (DK-1201 29x90mm)
SWATCH_W = 200                    # color swatch width px
SWATCH_MARGIN = 20                # margin around swatch
TEXT_X = SWATCH_W + SWATCH_MARGIN * 2  # text block start x
TEXT_MAX_W = LABEL_W - TEXT_X - SWATCH_MARGIN  # max text width


class LabelPrinter(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        self.printer_url = str(self.args.get("printer_url", "")).strip()
        self.printer_model = str(self.args.get("printer_model", "QL-810W")).strip()
        self.label_size = str(self.args.get("label_size", "62")).strip()
        self.dry_run = bool(self.args.get("dry_run", True))

        self.listen_event(self._on_print_label_event, "filament_iq_print_label")
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

    def generate_label_image(self, spool, filament):
        """Generate a rectangular DK-1201 29×90mm label image.

        Builds a landscape canvas (991×306) then rotates to portrait for
        brother_ql. Returns a PIL Image in RGB mode.
        """
        from PIL import Image, ImageDraw, ImageFont
        import os

        # Parse color
        color_hex = (filament.get("color_hex") or "808080").lstrip("#")
        try:
            r, g, b = int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16)
        except Exception:
            r, g, b = 128, 128, 128

        # Auto-contrast text color for swatch area
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        swatch_text_color = "#000000" if luminance > 128 else "#ffffff"
        text_color = "#1a1a1a"

        # Build landscape canvas
        img = Image.new("RGB", (LABEL_W, LABEL_H), "white")
        draw = ImageDraw.Draw(img)

        # Color swatch (vertically centered, left side)
        swatch_top = SWATCH_MARGIN
        swatch_bottom = LABEL_H - SWATCH_MARGIN
        draw.rectangle(
            [SWATCH_MARGIN, swatch_top, SWATCH_MARGIN + SWATCH_W, swatch_bottom],
            fill=(r, g, b),
            outline="white",
            width=3,
        )

        # Fonts
        def load_font(bold=False, size=40):
            font_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
            for path in [
                f"/usr/share/fonts/truetype/dejavu/{font_name}",
                f"/usr/share/fonts/dejavu/{font_name}",
                f"/usr/local/share/fonts/{font_name}",
            ]:
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
            return ImageFont.load_default()

        # Auto-shrink helper
        def fit_font(text, base_size, max_w, bold=False):
            size = base_size
            while size > 12:
                f = load_font(bold=bold, size=size)
                bbox = draw.textbbox((0, 0), text, font=f)
                if (bbox[2] - bbox[0]) <= max_w:
                    return f
                size -= 2
            return load_font(bold=bold, size=12)

        # Text content
        vendor   = (filament.get("vendor", {}) or {}).get("name", "") or ""
        name     = filament.get("name", "") or ""
        material = filament.get("material", "") or ""
        spool_id = f"#{spool.get('id', '?')}"

        # Layout text block (top-to-bottom in landscape)
        pad = 10
        y = SWATCH_MARGIN + pad

        # Vendor (small)
        f_vendor = fit_font(vendor, 36, TEXT_MAX_W, bold=False)
        draw.text((TEXT_X, y), vendor, font=f_vendor, fill=text_color)
        bbox = draw.textbbox((TEXT_X, y), vendor, font=f_vendor)
        y += (bbox[3] - bbox[1]) + pad

        # Name (large bold)
        f_name = fit_font(name, 53, TEXT_MAX_W, bold=True)
        draw.text((TEXT_X, y), name, font=f_name, fill=text_color)
        bbox = draw.textbbox((TEXT_X, y), name, font=f_name)
        y += (bbox[3] - bbox[1]) + pad

        # Material (small)
        f_material = fit_font(material, 36, TEXT_MAX_W, bold=False)
        draw.text((TEXT_X, y), material, font=f_material, fill=text_color)
        bbox = draw.textbbox((TEXT_X, y), material, font=f_material)
        y += (bbox[3] - bbox[1]) + pad

        # Spool ID (monospace, bottom)
        f_id = fit_font(spool_id, 32, TEXT_MAX_W, bold=False)
        draw.text((TEXT_X, y), spool_id, font=f_id, fill=text_color)

        # Rotate to portrait for brother_ql
        img = img.rotate(-90, expand=True)
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
