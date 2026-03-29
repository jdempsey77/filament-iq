"""
Label Printer — generates and prints spool labels on a Brother QL-810W.

Listens for HA event `filament_iq_print_label` with payload { spool_id: int }.
Fetches spool + filament data from Spoolman, generates a 236x236 circular
label image with Pillow, and sends to the printer via brother_ql.

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

# Label dimensions (d24 round die-cut)
LABEL_SIZE_PX = 236


class LabelPrinter(FilamentIQBase):

    def initialize(self):
        self._validate_config(["spoolman_url"])

        self.spoolman_url = str(self.args.get("spoolman_url", "")).rstrip("/")
        self.printer_url = str(self.args.get("printer_url", "")).strip()
        self.printer_model = str(self.args.get("printer_model", "QL-810W")).strip()
        self.label_size = str(self.args.get("label_size", "d24")).strip()
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

    def generate_label_image(self, spool_data, filament_data):
        """Generate a 236x236 circular label image.

        Returns a PIL Image in RGB mode.
        """
        from PIL import Image, ImageDraw, ImageFont

        size = LABEL_SIZE_PX
        img = Image.new("RGB", (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Parse color
        color_hex = str(
            filament_data.get("color_hex")
            or filament_data.get("color_hex", "")
            or "808080"
        ).replace("#", "")[:6]
        if len(color_hex) < 6:
            color_hex = "808080"
        try:
            r, g, b = int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16)
        except ValueError:
            r, g, b = 128, 128, 128

        # Fill circle with filament color
        draw.ellipse([0, 0, size - 1, size - 1], fill=(r, g, b))

        # Auto-contrast text color
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        text_color = (255, 255, 255) if luminance < 0.4 else (30, 30, 30)

        # Text content
        material = str(filament_data.get("material", "")).upper() or "?"
        vendor_obj = filament_data.get("vendor")
        vendor_name = str(vendor_obj.get("name", "") if isinstance(vendor_obj, dict) else (vendor_obj or ""))[:10]
        filament_name = str(filament_data.get("name", ""))[:14]

        # Fonts — use default (no external dependency)
        try:
            font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
            font_medium = ImageFont.truetype("DejaVuSans.ttf", 18)
            font_small = ImageFont.truetype("DejaVuSans.ttf", 14)
        except (IOError, OSError):
            font_large = ImageFont.load_default()
            font_medium = font_large
            font_small = font_large

        # Draw text centered
        for text, font, y in [
            (material, font_large, 85),
            (vendor_name, font_medium, 125),
            (filament_name, font_small, 155),
        ]:
            if text:
                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                x = (size - tw) // 2
                draw.text((x, y), text, fill=text_color, font=font)

        # White mask outside circle
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, size - 1, size - 1], fill=255)
        white = Image.new("RGB", (size, size), (255, 255, 255))
        img = Image.composite(img, white, mask)

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
            rotate="auto",
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
