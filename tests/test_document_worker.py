from __future__ import annotations

import asyncio
import time
from pathlib import Path


from src.document_worker import (
    DocumentWorker,
    _clean_description,
    _make_filename,
    _sanitize,
)


class DocumentCallbackCapture:
    def __init__(self) -> None:
        self.complete: list[dict] = []
        self.failed: list[dict] = []

    async def document_complete(
        self, request_id: str, event_id: str, document_path: str
    ) -> None:
        self.complete.append(
            {
                "request_id": request_id,
                "event_id": event_id,
                "document_path": document_path,
            }
        )

    async def document_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        self.failed.append(
            {"request_id": request_id, "event_id": event_id, "error_text": error_text}
        )


def make_event(id: str = "evt001", summary: str = "Team Standup") -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": "2026-04-10T09:00:00-05:00",
        "end": "2026-04-10T10:00:00-05:00",
        "location": None,
        "description": None,
        "html_link": None,
        "status": "confirmed",
        "colorId": None,
        "timezone": None,
    }


def noop_generator(event: dict, output_path: str) -> None:
    Path(output_path).touch()


# ── Unit tests (no asyncio) ────────────────────────────────────────────────────


class TestSanitize:
    def test_replaces_non_alphanumeric_with_dash(self):
        assert _sanitize("Hello World!", 50) == "hello-world"

    def test_collapses_multiple_dashes(self):
        assert _sanitize("a  b   c", 50) == "a-b-c"

    def test_strips_leading_trailing_dashes(self):
        assert _sanitize("!hello!", 50) == "hello"

    def test_truncates_to_max_len(self):
        result = _sanitize("a" * 100, 10)
        assert len(result) == 10

    def test_empty_string(self):
        assert _sanitize("", 10) == ""


class TestMakeFilename:
    def test_includes_request_id_prefix(self):
        event = make_event(id="evt001")
        name = _make_filename("3f2504e0-4f89-11d3-9a0c-0305e82c3301", event)
        assert name.startswith("3f2504e0_")

    def test_ends_with_pdf_extension(self):
        name = _make_filename("3f2504e0-4f89-11d3-9a0c-0305e82c3301", make_event())
        assert name.endswith(".pdf")

    def test_filename_is_filesystem_safe(self):
        event = make_event(id="evt/dangerous", summary="My Event: Special!")
        name = _make_filename("3f2504e0-4f89-11d3-9a0c-0305e82c3301", event)
        assert "/" not in name
        assert ":" not in name
        assert " " not in name

    def test_includes_sanitized_summary(self):
        event = make_event(summary="Team Standup")
        name = _make_filename("3f2504e0-4f89-11d3-9a0c-0305e82c3301", event)
        assert "team-standup" in name

    def test_summary_truncated_to_40_chars(self):
        event = make_event(summary="A" * 100)
        name = _make_filename("3f2504e0-0000-0000-0000-000000000000", event)
        # Summary fragment should not exceed 40 chars
        parts = name.removesuffix(".pdf").split("_", 2)
        assert len(parts[2]) <= 40

    def test_missing_id_uses_unknown(self):
        event = {"id": "", "summary": "Test"}
        name = _make_filename("3f2504e0-0000-0000-0000-000000000000", event)
        assert "unknown" in name


class TestCleanDescription:
    def test_br_tag_converted_to_newline(self):
        assert _clean_description("Line1<br>Line2") == "Line1\nLine2"

    def test_br_self_closing(self):
        assert _clean_description("Line1<br/>Line2") == "Line1\nLine2"

    def test_html_tags_stripped(self):
        assert _clean_description("<b>Bold</b> text") == "Bold text"

    def test_multiple_blank_lines_collapsed(self):
        result = _clean_description("A\n\n\n\nB")
        assert result == "A\n\nB"

    def test_strips_leading_trailing_whitespace(self):
        result = _clean_description("  Hello  ")
        assert result == "Hello"


# ── Worker integration tests ───────────────────────────────────────────────────


class TestDocumentWorkerIntegration:
    async def test_success_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        cap = DocumentCallbackCapture()
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=noop_generator)
        event = make_event()
        w.create_event_document(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        assert cap.complete[0]["request_id"] == "req-1"
        assert cap.complete[0]["event_id"] == "evt001"
        assert Path(cap.complete[0]["document_path"]).exists()
        await w.shutdown()

    async def test_output_path_inside_output_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        cap = DocumentCallbackCapture()
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=noop_generator)
        w.create_event_document(cap, "req-1", make_event())
        await asyncio.sleep(0.05)
        path = cap.complete[0]["document_path"]
        assert path.startswith(str(tmp_path))
        await w.shutdown()

    async def test_generator_exception_triggers_failed_callback(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)

        def failing_generator(event: dict, output_path: str) -> None:
            raise RuntimeError("PDF library error")

        cap = DocumentCallbackCapture()
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=failing_generator)
        w.create_event_document(cap, "req-1", make_event())
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "PDF library error" in cap.failed[0]["error_text"]
        await w.shutdown()

    async def test_cancel_before_processing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        cap = DocumentCallbackCapture()
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=noop_generator)
        event = make_event()
        w.create_event_document(cap, "req-cancel", event)
        w.cancel_request("req-cancel")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert cap.failed[0]["error_text"] == "Cancelled"
        assert cap.complete == []
        await w.shutdown()

    async def test_multiple_events_each_get_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        cap = DocumentCallbackCapture()
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=noop_generator)
        events = [
            make_event(id="e1", summary="Standup"),
            make_event(id="e2", summary="Meeting"),
        ]
        for ev in events:
            w.create_event_document(cap, "req-1", ev)
        await asyncio.sleep(0.1)
        assert len(cap.complete) == 2
        event_ids = {r["event_id"] for r in cap.complete}
        assert event_ids == {"e1", "e2"}
        await w.shutdown()

    async def test_prune_stale_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        # Create a stale PDF (modified 8 days ago)
        stale = tmp_path / "old.pdf"
        stale.touch()
        stale_time = time.time() - 8 * 86400
        import os

        os.utime(str(stale), (stale_time, stale_time))

        # Create a recent PDF
        recent = tmp_path / "new.pdf"
        recent.touch()

        cap = DocumentCallbackCapture()
        w = DocumentWorker(
            output_dir=str(tmp_path), pdf_generator=noop_generator, prune_age_days=7
        )
        w.create_event_document(cap, "req-1", make_event())
        await asyncio.sleep(0.1)
        assert not stale.exists()
        assert recent.exists()
        await w.shutdown()

    async def test_shutdown_completes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.document_worker._CANCEL_TTL_SECONDS", 0)
        w = DocumentWorker(output_dir=str(tmp_path), pdf_generator=noop_generator)
        await w.shutdown()  # must not hang
