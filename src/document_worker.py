from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CANCEL_TTL_SECONDS = 3600

# PDF layout constants
CUSTOMER_EMAIL = "examplecompany@gmail.com"
CUSTOMER_TITLE = "Created by: Example Company"
COLUMN_WIDTH = 4.25  # inches

_COLOR_MAP: dict[str, tuple[float, float, float]] = {
    "1": (0.475, 0.525, 0.796),  # Lavender
    "2": (0.153, 0.941, 0.729),  # Mint
    "3": (0.557, 0.141, 0.667),  # Grape
    "4": (0.902, 0.486, 0.451),  # Flamingo
    "5": (0.965, 0.749, 0.149),  # Banana
    "6": (0.957, 0.318, 0.118),  # Tangerine
    "7": (0.012, 0.608, 0.898),  # Peacock
    "8": (0.380, 0.380, 0.380),  # Graphite
    "9": (0.247, 0.318, 0.710),  # Blueberry
    "10": (0.043, 0.502, 0.263),  # Basil
    "11": (0.835, 0.000, 0.000),  # Tomato
}
_DEFAULT_COLOR = (0.530, 0.810, 0.980)  # light blue

_TZ_STANDARD_NAMES: dict[str, str] = {
    "America/New_York": "Eastern Time",
    "America/Detroit": "Eastern Time",
    "America/Chicago": "Central Time",
    "America/Denver": "Mountain Time",
    "America/Phoenix": "Mountain Time",
    "America/Los_Angeles": "Pacific Time",
    "America/Anchorage": "Alaska Time",
    "America/Adak": "Hawaii-Aleutian Time",
    "Pacific/Honolulu": "Hawaii Time",
    "Europe/London": "Greenwich Mean Time",
    "Europe/Paris": "Central European Time",
    "Europe/Berlin": "Central European Time",
    "Asia/Tokyo": "Japan Time",
    "Asia/Shanghai": "China Time",
    "Asia/Kolkata": "India Time",
    "Australia/Sydney": "Australian Eastern Time",
    "UTC": "UTC",
}


def _sanitize(text: str, max_len: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]", "-", text)
    safe = re.sub(r"-+", "-", safe).strip("-").lower()
    return safe[:max_len]


def _make_filename(request_id: str, event: dict) -> str:
    req_prefix = request_id[:8]
    event_id = _sanitize(event.get("id") or "unknown", 20)
    summary = event.get("summary") or "event"
    summary_frag = _sanitize(summary, 40)
    return f"{req_prefix}_{event_id}_{summary_frag}.pdf"


def _format_timezone_display(iana: str) -> str:
    city = iana.rsplit("/", 1)[-1].replace("_", " ")
    standard = _TZ_STANDARD_NAMES.get(iana)
    if standard:
        return f"{standard} - {city}"
    return city


def _format_time_12h(dt: datetime.datetime) -> str:
    hour = dt.hour % 12 or 12
    minute = dt.minute
    am_pm = "am" if dt.hour < 12 else "pm"
    if minute == 0:
        return f"{hour}{am_pm}"
    return f"{hour}:{minute:02d}{am_pm}"


def _clean_description(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _generate_pdf(event: dict, output_path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    PAGE_W, PAGE_H = letter
    HEADER_H = 0.5 * inch
    LEFT_MARGIN = 0.5 * inch
    RIGHT_MARGIN = 0.5 * inch
    BOTTOM_MARGIN = 0.5 * inch
    TOP_CONTENT_START = PAGE_H - HEADER_H - 0.35 * inch

    color_id = event.get("colorId")
    header_color = (
        _COLOR_MAP.get(str(color_id), _DEFAULT_COLOR) if color_id else _DEFAULT_COLOR
    )

    c = rl_canvas.Canvas(output_path, pagesize=letter)

    # Header bar: 1-inch wide color block at left edge
    c.setFillColorRGB(*header_color)
    c.rect(0, PAGE_H - HEADER_H, inch, HEADER_H, fill=1, stroke=0)

    # Customer email right-justified in black
    font_size_email = 10
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", font_size_email)
    email_y = PAGE_H - HEADER_H + (HEADER_H - font_size_email) / 2
    c.drawRightString(PAGE_W - RIGHT_MARGIN, email_y, CUSTOMER_EMAIL)

    # Event title
    title = event.get("summary") or "(No Title)"
    TITLE_FONT_SIZE = 24
    TITLE_TOP_PADDING = 0.1 * inch
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", TITLE_FONT_SIZE)
    usable_width = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN
    words = title.split()
    lines: list[str] = []
    current_line = ""
    for word in words:
        test = f"{current_line} {word}".strip()
        if c.stringWidth(test, "Helvetica-Bold", TITLE_FONT_SIZE) <= usable_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    if not lines:
        lines = ["(No Title)"]

    y = TOP_CONTENT_START - TITLE_TOP_PADDING
    for line in lines:
        c.drawString(LEFT_MARGIN, y, line)
        y -= TITLE_FONT_SIZE + 2

    # Customer title line
    CUSTOMER_FONT_SIZE = 9
    CUSTOMER_GAP = 0.05 * inch
    c.setFont("Helvetica", CUSTOMER_FONT_SIZE)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    y -= CUSTOMER_GAP
    c.drawString(LEFT_MARGIN, y, CUSTOMER_TITLE)
    y -= CUSTOMER_FONT_SIZE + 2

    ITEM_GAP = 0.5 * inch
    LABEL_FONT_SIZE = 8
    VALUE_FONT_SIZE = 18
    LABEL_COLOR = (0.45, 0.45, 0.45)
    col_width = COLUMN_WIDTH * inch

    def draw_label(label: str, cur_y: float) -> float:
        c.setFont("Helvetica", LABEL_FONT_SIZE)
        c.setFillColorRGB(*LABEL_COLOR)
        cur_y -= ITEM_GAP
        c.drawString(LEFT_MARGIN, cur_y, label)
        return cur_y - VALUE_FONT_SIZE - 2

    def draw_value_bold(text: str, cur_y: float) -> float:
        c.setFont("Helvetica-Bold", VALUE_FONT_SIZE)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(LEFT_MARGIN, cur_y, text)
        return cur_y - VALUE_FONT_SIZE - 2

    # TIME
    start_str = event.get("start", "")
    end_str = event.get("end", "")
    tz_str = event.get("timezone")
    time_text = "All Day"
    try:
        start_dt = datetime.datetime.fromisoformat(
            start_str.replace("Z", "+00:00")
        ).astimezone()
        end_dt = datetime.datetime.fromisoformat(
            end_str.replace("Z", "+00:00")
        ).astimezone()
        time_text = f"{_format_time_12h(start_dt)} \u2013 {_format_time_12h(end_dt)}"
        if tz_str:
            time_text += f" ({_format_timezone_display(tz_str)})"
    except (ValueError, AttributeError):
        pass

    y = draw_label("TIME", y)
    y = draw_value_bold(time_text, y)

    # DATE
    date_text = ""
    try:
        date_src = start_str.split("T")[0] if "T" in start_str else start_str
        d = datetime.date.fromisoformat(date_src)
        date_text = d.strftime("%-d").lstrip("0")  # day without leading zero
        date_text = d.strftime(f"%a %b {d.day}, %Y")
    except (ValueError, AttributeError):
        date_text = start_str

    y = draw_label("DATE", y)
    y = draw_value_bold(date_text, y)

    # WHERE
    location = event.get("location")
    if location:
        y = draw_label("WHERE", y)
        # wrap location within col_width
        c.setFont("Helvetica-Bold", VALUE_FONT_SIZE)
        c.setFillColorRGB(0, 0, 0)
        loc_words = location.split()
        loc_lines: list[str] = []
        cur_line = ""
        for w in loc_words:
            test = f"{cur_line} {w}".strip()
            if c.stringWidth(test, "Helvetica-Bold", VALUE_FONT_SIZE) <= col_width:
                cur_line = test
            else:
                if cur_line:
                    loc_lines.append(cur_line)
                cur_line = w
        if cur_line:
            loc_lines.append(cur_line)
        for ll in loc_lines:
            c.drawString(LEFT_MARGIN, y, ll)
            y -= VALUE_FONT_SIZE + 2

    # DESCRIPTION
    description = event.get("description")
    if description:
        cleaned = _clean_description(description)
        if cleaned:
            y = draw_label("DESCRIPTION", y)
            DESC_FONT_SIZE = 12
            c.setFont("Helvetica", DESC_FONT_SIZE)
            c.setFillColorRGB(0, 0, 0)
            desc_lines: list[str] = []
            for para in cleaned.split("\n"):
                if not para:
                    desc_lines.append("")
                    continue
                dwords = para.split()
                dl = ""
                for w in dwords:
                    test = f"{dl} {w}".strip()
                    if c.stringWidth(test, "Helvetica", DESC_FONT_SIZE) <= col_width:
                        dl = test
                    else:
                        if dl:
                            desc_lines.append(dl)
                        dl = w
                if dl:
                    desc_lines.append(dl)

            # render lines, truncate if needed
            for i, dl in enumerate(desc_lines):
                if y - DESC_FONT_SIZE < BOTTOM_MARGIN:
                    # truncate with ellipsis
                    if desc_lines and i > 0:
                        prev_y = y + DESC_FONT_SIZE + 2
                        c.drawString(LEFT_MARGIN, prev_y, desc_lines[i - 1] + " \u2026")
                    break
                c.drawString(LEFT_MARGIN, y, dl)
                y -= DESC_FONT_SIZE + 2

    c.save()


class DocumentWorker:
    def __init__(
        self,
        output_dir: str = "reports",
        pdf_generator: Callable[[dict, str], None] | None = None,
        prune_age_days: int = 7,
    ) -> None:
        self._output_dir = output_dir
        self._pdf_generator = pdf_generator or _generate_pdf
        self._prune_age_days = prune_age_days
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancelled: dict[str, bool] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    def create_event_document(
        self, office_admin_ref: Any, request_id: str, event: dict
    ) -> None:
        if not event.get("id"):
            raise ValueError("event must have an 'id' field")
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled[request_id] = True
        asyncio.create_task(self._cleanup_cancel_after_ttl(request_id))

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: dict) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        event = item["event"]
        event_id = event.get("id", "")

        if self._cancelled.get(request_id):
            await office_admin_ref.document_failed(request_id, event_id, "Cancelled")
            return

        os.makedirs(self._output_dir, exist_ok=True)
        filename = _make_filename(request_id, event)
        output_path = str(Path(self._output_dir).resolve() / filename)

        loop = asyncio.get_running_loop()
        # Prune stale PDFs first
        await loop.run_in_executor(None, self._prune_stale_files)

        try:
            await loop.run_in_executor(None, self._pdf_generator, event, output_path)
        except Exception as exc:
            await office_admin_ref.document_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.document_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.document_complete(request_id, event_id, output_path)

    def _prune_stale_files(self) -> None:
        threshold = time.time() - self._prune_age_days * 86400
        try:
            entries = list(Path(self._output_dir).iterdir())
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.is_file() and entry.suffix.lower() == ".pdf":
                try:
                    if entry.stat().st_mtime < threshold:
                        entry.unlink()
                except Exception as exc:
                    logger.warning("Could not delete stale PDF %s: %s", entry, exc)

    async def _cleanup_cancel_after_ttl(self, request_id: str) -> None:
        await asyncio.sleep(_CANCEL_TTL_SECONDS)
        self._cancelled.pop(request_id, None)
