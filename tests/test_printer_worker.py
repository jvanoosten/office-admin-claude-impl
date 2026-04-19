from __future__ import annotations

import asyncio

import pytest

from src.printer_worker import PrinterWorker


class PrinterCallbackCapture:
    def __init__(self) -> None:
        self.complete: list[dict] = []
        self.failed: list[dict] = []

    async def print_complete(
        self, request_id: str, event_id: str, document_path: str
    ) -> None:
        self.complete.append(
            {
                "request_id": request_id,
                "event_id": event_id,
                "document_path": document_path,
            }
        )

    async def print_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        self.failed.append(
            {
                "request_id": request_id,
                "event_id": event_id,
                "error_text": error_text,
            }
        )


class TestPrinterWorker:
    async def test_success_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        doc = tmp_path / "event.pdf"
        doc.touch()
        printed: list[str] = []

        def fake_adapter(path: str) -> None:
            printed.append(path)

        cap = PrinterCallbackCapture()
        w = PrinterWorker(print_adapter=fake_adapter)
        w.print_document(cap, "req-1", "evt1", str(doc))
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        assert cap.complete[0]["request_id"] == "req-1"
        assert cap.complete[0]["event_id"] == "evt1"
        assert str(doc) in printed
        await w.shutdown()

    async def test_file_not_found_triggers_failed_callback(self, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        cap = PrinterCallbackCapture()
        w = PrinterWorker(print_adapter=lambda p: None)
        w.print_document(cap, "req-1", "evt1", "/nonexistent/path/doc.pdf")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "not found" in cap.failed[0]["error_text"].lower()
        await w.shutdown()

    async def test_adapter_error_triggers_failed_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        doc = tmp_path / "event.pdf"
        doc.touch()

        def failing_adapter(path: str) -> None:
            raise RuntimeError("Printer offline")

        cap = PrinterCallbackCapture()
        w = PrinterWorker(print_adapter=failing_adapter)
        w.print_document(cap, "req-1", "evt1", str(doc))
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "Printer offline" in cap.failed[0]["error_text"]
        await w.shutdown()

    async def test_cancel_before_processing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        doc = tmp_path / "event.pdf"
        doc.touch()
        cap = PrinterCallbackCapture()
        w = PrinterWorker(print_adapter=lambda p: None)
        w.print_document(cap, "req-cancel", "evt1", str(doc))
        w.cancel_request("req-cancel")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert cap.failed[0]["error_text"] == "Cancelled"
        assert cap.complete == []
        await w.shutdown()

    async def test_multiple_print_jobs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        docs = []
        for i in range(3):
            d = tmp_path / f"event{i}.pdf"
            d.touch()
            docs.append(d)

        cap = PrinterCallbackCapture()
        w = PrinterWorker(print_adapter=lambda p: None)
        for i, d in enumerate(docs):
            w.print_document(cap, "req-1", f"evt{i}", str(d))
        await asyncio.sleep(0.1)
        assert len(cap.complete) == 3
        await w.shutdown()

    async def test_empty_document_path_raises(self):
        w = PrinterWorker(print_adapter=lambda p: None)
        with pytest.raises(ValueError):
            w.print_document(None, "req-1", "evt1", "")
        await w.shutdown()

    async def test_shutdown_completes(self, monkeypatch):
        monkeypatch.setattr("src.printer_worker._CANCEL_TTL_SECONDS", 0)
        w = PrinterWorker(print_adapter=lambda p: None)
        await w.shutdown()  # must not hang
