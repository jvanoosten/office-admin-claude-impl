from __future__ import annotations

import asyncio
import uuid

import pytest

from src.office_admin import OfficeAdmin, OfficeAdminQueueFullError
from tests.conftest import (
    make_event,
)


# ── PRINT_CALENDAR_EVENTS ──────────────────────────────────────────────────────


class TestPrintCalendarEvents:
    async def test_submit_returns_uuid4(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        assert uuid.UUID(rid, version=4)

    async def test_submit_creates_pending_entry(self, admin):
        rid = admin.submit_print_calendar_events("2026-04-10")
        status = admin.get_status(rid)
        assert status["status"] == "PENDING"
        assert status["stage"] == "PENDING"
        assert status["task_type"] == "PRINT_CALENDAR_EVENTS"
        assert status["selected_date"] == "2026-04-10"
        assert status["cancel_requested"] is False
        assert status["documents_expected"] == 0
        assert status["prints_expected"] == 0
        assert status["document_paths"] == []

    async def test_worker_loop_sets_running_and_dispatches(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        status = admin.get_status(rid)
        assert status["status"] == "RUNNING"
        assert status["stage"] == "GETTING_CALENDAR_EVENTS"
        assert len(fake_cal._items) == 1
        assert fake_cal._items[0]["request_id"] == rid

    async def test_zero_event_day_completes_immediately(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[])
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["stage"] == "COMPLETED"
        assert status["events_retrieved"] is True
        assert status["calendar_event_count"] == 0

    async def test_calendar_complete_dispatches_document_jobs(
        self, admin, fake_cal, fake_doc
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        status = admin.get_status(rid)
        assert status["stage"] == "CREATING_EVENT_PDFS"
        assert status["documents_expected"] == 1
        assert len(fake_doc._items) == 1
        assert fake_doc._items[0]["request_id"] == rid

    async def test_happy_path_single_event(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete(document_path="/tmp/doc1.pdf")
        status = admin.get_status(rid)
        assert status["stage"] == "PRINTING_EVENT_PDFS"
        assert status["prints_expected"] == 1
        assert len(fake_printer._items) == 1
        await fake_printer.trigger_complete()
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["stage"] == "COMPLETED"
        assert status["prints_completed"] == 1

    async def test_happy_path_two_events(self, admin, fake_cal, fake_doc, fake_printer):
        events = [make_event(id="e1"), make_event(id="e2", summary="Meeting")]
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        status = admin.get_status(rid)
        assert status["documents_expected"] == 2
        assert len(fake_doc._items) == 2
        await fake_doc.trigger_complete(document_path="/tmp/doc1.pdf")
        status = admin.get_status(rid)
        assert status["stage"] == "CREATING_EVENT_PDFS"  # not done yet
        assert status["documents_completed"] == 1
        await fake_doc.trigger_complete(document_path="/tmp/doc2.pdf")
        status = admin.get_status(rid)
        assert status["stage"] == "PRINTING_EVENT_PDFS"
        assert status["prints_expected"] == 2
        assert set(status["document_paths"]) == {"/tmp/doc1.pdf", "/tmp/doc2.pdf"}
        await fake_printer.trigger_complete()
        await fake_printer.trigger_complete()
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["prints_completed"] == 2

    async def test_calendar_failed_sets_error(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_failed(error_text="Network error")
        status = admin.get_status(rid)
        assert status["status"] == "ERROR"
        assert status["stage"] == "ERROR"
        assert "Network error" in status["errors"]

    async def test_document_failed_sets_error_and_propagates_cancel(
        self, admin, fake_cal, fake_doc, fake_printer, fake_mail
    ):
        events = [make_event(id="e1"), make_event(id="e2")]
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_doc.trigger_failed(error_text="PDF render failed")
        status = admin.get_status(rid)
        assert status["status"] == "ERROR"
        assert status["stage"] == "ERROR"
        assert "PDF render failed" in status["errors"]
        assert rid in fake_doc._cancelled
        assert rid in fake_printer._cancelled

    async def test_print_failed_sets_error(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete()
        await fake_printer.trigger_failed(error_text="Printer offline")
        status = admin.get_status(rid)
        assert status["status"] == "ERROR"
        assert status["stage"] == "ERROR"
        assert "Printer offline" in status["errors"]

    async def test_cancel_during_calendar_stage(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        admin.cancel_request(rid)
        status = admin.get_status(rid)
        assert status["cancel_requested"] is True
        assert status["status"] == "CANCEL_REQUESTED"
        assert rid in fake_cal._cancelled
        await fake_cal.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"
        assert status["stage"] == "CANCELLED"

    async def test_cancel_during_document_stage(self, admin, fake_cal, fake_doc):
        events = [make_event(id="e1"), make_event(id="e2")]
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        admin.cancel_request(rid)
        assert rid in fake_doc._cancelled
        await fake_doc.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        # Only one of two callbacks received; still CANCEL_REQUESTED
        assert status["status"] == "CANCEL_REQUESTED"
        assert status["documents_failed"] == 1
        await fake_doc.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"
        assert status["stage"] == "CANCELLED"

    async def test_cancel_during_print_stage(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete()
        admin.cancel_request(rid)
        assert rid in fake_printer._cancelled
        await fake_printer.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"
        assert status["stage"] == "CANCELLED"
        assert status["prints_failed"] == 1

    async def test_late_callback_discarded(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete()
        await fake_printer.trigger_complete()
        assert admin.get_status(rid)["status"] == "COMPLETED"
        # Late callback should not change the terminal state
        await admin.print_failed(rid, "e1", "late failure")
        assert admin.get_status(rid)["status"] == "COMPLETED"

    async def test_duplicate_cancel_is_idempotent(self, admin, fake_cal):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        admin.cancel_request(rid)
        s1 = admin.get_status(rid)
        admin.cancel_request(rid)
        s2 = admin.get_status(rid)
        assert s1["status"] == s2["status"]
        assert s1["cancel_requested"] == s2["cancel_requested"]

    async def test_cancel_on_completed_task_no_change(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete()
        await fake_printer.trigger_complete()
        assert admin.get_status(rid)["status"] == "COMPLETED"
        result = admin.cancel_request(rid)
        assert result["status"] == "COMPLETED"
        assert admin.get_status(rid)["status"] == "COMPLETED"

    async def test_queue_full_rejection(
        self, fake_cal, fake_doc, fake_printer, fake_mail, monkeypatch
    ):
        monkeypatch.setattr("src.office_admin.TASK_TTL_SECONDS", 0)
        oa = OfficeAdmin(fake_cal, fake_doc, fake_printer, fake_mail)
        try:
            # Fill queue to capacity (maxsize=10)
            for _ in range(10):
                oa.submit_print_calendar_events("2026-04-10")
            with pytest.raises(OfficeAdminQueueFullError):
                oa.submit_print_calendar_events("2026-04-10")
        finally:
            await oa.shutdown()

    async def test_unknown_request_id(self, admin):
        status = admin.get_status("00000000-0000-0000-0000-000000000000")
        assert status["status"] == "UNKNOWN"

    async def test_get_status_returns_copy(self, admin):
        rid = admin.submit_print_calendar_events("2026-04-10")
        status = admin.get_status(rid)
        status["status"] = "MUTATED"
        assert admin.get_status(rid)["status"] == "PENDING"

    async def test_cancel_propagated_to_all_workers(
        self, admin, fake_cal, fake_doc, fake_printer, fake_mail
    ):
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        admin.cancel_request(rid)
        assert rid in fake_cal._cancelled
        assert rid in fake_doc._cancelled
        assert rid in fake_printer._cancelled
        assert rid in fake_mail._cancelled

    async def test_document_paths_recorded(
        self, admin, fake_cal, fake_doc, fake_printer
    ):
        event = make_event()
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[event])
        await fake_doc.trigger_complete(document_path="/reports/abc.pdf")
        status = admin.get_status(rid)
        assert "/reports/abc.pdf" in status["document_paths"]

    async def test_partial_cancel_with_some_completed(self, admin, fake_cal, fake_doc):
        events = [make_event(id="e1"), make_event(id="e2"), make_event(id="e3")]
        rid = admin.submit_print_calendar_events("2026-04-10")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_doc.trigger_complete(document_path="/tmp/e1.pdf")
        admin.cancel_request(rid)
        await fake_doc.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["documents_completed"] == 1
        assert status["documents_failed"] == 1
        assert status["status"] == "CANCEL_REQUESTED"  # still waiting for 3rd
        await fake_doc.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"


# ── SEND_EMAIL_NOTIFICATIONS ───────────────────────────────────────────────────


class TestSendEmailNotifications:
    async def test_submit_returns_uuid4(self, admin):
        rid = admin.submit_send_email_notifications("2026-04-11")
        assert uuid.UUID(rid, version=4)

    async def test_submit_creates_pending_entry(self, admin):
        rid = admin.submit_send_email_notifications("2026-04-11")
        status = admin.get_status(rid)
        assert status["status"] == "PENDING"
        assert status["stage"] == "PENDING"
        assert status["task_type"] == "SEND_EMAIL_NOTIFICATIONS"
        assert status["emails_expected"] == 0
        assert status["emails_completed"] == 0
        assert status["emails_skipped"] == 0
        assert status["emails_failed"] == 0
        assert status["draft_ids"] == []
        assert status["skipped_event_ids"] == []

    async def test_worker_sets_running_and_dispatches_calendar(self, admin, fake_cal):
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        status = admin.get_status(rid)
        assert status["status"] == "RUNNING"
        assert status["stage"] == "GETTING_CALENDAR_EVENTS"
        assert len(fake_cal._items) == 1

    async def test_zero_event_day_completes_without_mail(
        self, admin, fake_cal, fake_mail
    ):
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=[])
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["stage"] == "COMPLETED"
        assert len(fake_mail._items) == 0

    async def test_calendar_complete_dispatches_mail_jobs(
        self, admin, fake_cal, fake_mail
    ):
        events = [make_event(id="e1"), make_event(id="e2")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        status = admin.get_status(rid)
        assert status["stage"] == "CREATING_EMAIL_DRAFTS"
        assert status["emails_expected"] == 2
        assert len(fake_mail._items) == 2

    async def test_happy_path_all_complete(self, admin, fake_cal, fake_mail):
        events = [make_event(id="e1"), make_event(id="e2")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_complete(draft_id="draft_1")
        await fake_mail.trigger_complete(draft_id="draft_2")
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["emails_completed"] == 2
        assert set(status["draft_ids"]) == {"draft_1", "draft_2"}

    async def test_all_skipped_completes(self, admin, fake_cal, fake_mail):
        events = [make_event(id="e1"), make_event(id="e2")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_skipped()
        status = admin.get_status(rid)
        assert status["status"] == "RUNNING"  # waiting for second
        await fake_mail.trigger_skipped()
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["emails_skipped"] == 2
        assert status["skipped_event_ids"] == ["e1", "e2"]

    async def test_mixed_complete_and_skipped(self, admin, fake_cal, fake_mail):
        events = [make_event(id="e1"), make_event(id="e2"), make_event(id="e3")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_complete(draft_id="draft_1")
        await fake_mail.trigger_skipped()
        status = admin.get_status(rid)
        assert status["status"] == "RUNNING"
        await fake_mail.trigger_complete(draft_id="draft_3")
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["emails_completed"] == 2
        assert status["emails_skipped"] == 1

    async def test_email_failure_sets_error(self, admin, fake_cal, fake_mail):
        events = [make_event(id="e1")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_failed(error_text="Gmail API failure")
        status = admin.get_status(rid)
        assert status["status"] == "ERROR"
        assert "Gmail API failure" in status["errors"]

    async def test_cancel_during_calendar(self, admin, fake_cal):
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        admin.cancel_request(rid)
        await fake_cal.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"
        assert status["stage"] == "CANCELLED"

    async def test_cancel_during_drafts_mixed_callbacks(
        self, admin, fake_cal, fake_mail
    ):
        events = [make_event(id="e1"), make_event(id="e2"), make_event(id="e3")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        admin.cancel_request(rid)
        await fake_mail.trigger_failed(error_text="Cancelled")
        await fake_mail.trigger_skipped()
        status = admin.get_status(rid)
        assert status["status"] == "CANCEL_REQUESTED"  # still one outstanding
        await fake_mail.trigger_failed(error_text="Cancelled")
        status = admin.get_status(rid)
        assert status["status"] == "CANCELLED"
        assert status["emails_failed"] == 2
        assert status["emails_skipped"] == 1

    async def test_late_callback_discarded_after_completed(
        self, admin, fake_cal, fake_mail
    ):
        events = [make_event(id="e1")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_complete(draft_id="draft_1")
        assert admin.get_status(rid)["status"] == "COMPLETED"
        await admin.email_draft_failed(rid, "e1", "late failure")
        assert admin.get_status(rid)["status"] == "COMPLETED"

    async def test_skipped_counts_toward_finalization(self, admin, fake_cal, fake_mail):
        # Regression: all-skipped day must complete, not hang
        events = [make_event(id="e1")]
        rid = admin.submit_send_email_notifications("2026-04-11")
        await asyncio.sleep(0)
        await fake_cal.trigger_complete(events=events)
        await fake_mail.trigger_skipped()
        status = admin.get_status(rid)
        assert status["status"] == "COMPLETED"
        assert status["emails_skipped"] == 1
        assert status["emails_expected"] == 1

    async def test_queue_full_rejection(
        self, fake_cal, fake_doc, fake_printer, fake_mail, monkeypatch
    ):
        monkeypatch.setattr("src.office_admin.TASK_TTL_SECONDS", 0)
        oa = OfficeAdmin(fake_cal, fake_doc, fake_printer, fake_mail)
        try:
            for _ in range(10):
                oa.submit_send_email_notifications("2026-04-11")
            with pytest.raises(OfficeAdminQueueFullError):
                oa.submit_send_email_notifications("2026-04-11")
        finally:
            await oa.shutdown()
