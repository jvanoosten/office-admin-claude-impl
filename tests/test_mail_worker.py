from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


from src.mail_worker import MailWorker, _compose_draft_body, _extract_recipients


TEMPLATE = (
    "Test Subject\n\nHello,\n\nDate: {date}\nTime: {time}\nLocation: {location}\n"
)


class MailCallbackCapture:
    def __init__(self) -> None:
        self.complete: list[dict] = []
        self.skipped: list[dict] = []
        self.failed: list[dict] = []

    async def email_draft_complete(
        self, request_id: str, event_id: str, draft_id: str
    ) -> None:
        self.complete.append(
            {"request_id": request_id, "event_id": event_id, "draft_id": draft_id}
        )

    async def email_draft_skipped(self, request_id: str, event_id: str) -> None:
        self.skipped.append({"request_id": request_id, "event_id": event_id})

    async def email_draft_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        self.failed.append(
            {"request_id": request_id, "event_id": event_id, "error_text": error_text}
        )


def make_event(
    id: str = "evt1",
    description: str | None = None,
    start: str = "2026-04-11T09:00:00-05:00",
    end: str = "2026-04-11T10:00:00-05:00",
    location: str | None = None,
) -> dict:
    return {
        "id": id,
        "summary": "Service Reminder",
        "start": start,
        "end": end,
        "location": location,
        "description": description,
    }


def make_mock_gmail_service(draft_id: str = "draft_xyz") -> MagicMock:
    mock_draft_resp = MagicMock()
    mock_draft_resp.execute.return_value = {"id": draft_id}
    mock_drafts = MagicMock()
    mock_drafts.create.return_value = mock_draft_resp
    mock_users = MagicMock()
    mock_users.drafts.return_value = mock_drafts
    mock_svc = MagicMock()
    mock_svc.users.return_value = mock_users
    return mock_svc


# ── Unit tests for _extract_recipients ────────────────────────────────────────


class TestExtractRecipients:
    def test_single_email_in_description(self):
        result = _extract_recipients("Contact: user@example.com for details.")
        assert result == ["user@example.com"]

    def test_multiple_emails(self):
        result = _extract_recipients("Email a@b.com and c@d.org for info.")
        assert result == ["a@b.com", "c@d.org"]

    def test_duplicate_emails_deduplicated(self):
        result = _extract_recipients("Send to a@b.com and again a@b.com please.")
        assert result == ["a@b.com"]

    def test_no_email_returns_empty(self):
        result = _extract_recipients("No contact info here.")
        assert result == []

    def test_none_description_returns_empty(self):
        result = _extract_recipients(None)
        assert result == []

    def test_mailto_link_extracts_address(self):
        result = _extract_recipients("Contact: mailto:customer@example.com")
        assert "customer@example.com" in result

    def test_preserves_order_of_first_occurrence(self):
        result = _extract_recipients("b@b.com a@a.com b@b.com")
        assert result == ["b@b.com", "a@a.com"]

    def test_empty_string_returns_empty(self):
        assert _extract_recipients("") == []


class TestComposeDraftBody:
    def test_subject_is_first_line(self):
        subject, _ = _compose_draft_body(make_event(), ["a@b.com"], template=TEMPLATE)
        assert subject == "Test Subject"

    def test_body_contains_date_placeholder(self):
        _, body = _compose_draft_body(make_event(), ["a@b.com"], template=TEMPLATE)
        assert "Saturday, April 11, 2026" in body

    def test_body_contains_time_placeholder(self):
        _, body = _compose_draft_body(make_event(), ["a@b.com"], template=TEMPLATE)
        assert "9:00 AM" in body

    def test_body_contains_location_placeholder(self):
        event = make_event(location="123 Main St")
        _, body = _compose_draft_body(event, ["a@b.com"], template=TEMPLATE)
        assert "123 Main St" in body

    def test_missing_location_empty_string(self):
        _, body = _compose_draft_body(
            make_event(location=None), ["a@b.com"], template=TEMPLATE
        )
        assert "Location: \n" in body


# ── Worker integration tests ───────────────────────────────────────────────────


class TestMailWorkerIntegration:
    async def test_event_with_recipients_creates_draft(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        monkeypatch.setattr("src.mail_worker._load_template", lambda: TEMPLATE)
        svc = make_mock_gmail_service("draft_1")
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: svc)
        event = make_event(description="Contact user@example.com for details")
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        assert cap.complete[0]["draft_id"] == "draft_1"
        assert cap.skipped == []
        assert cap.failed == []
        await w.shutdown()

    async def test_event_without_recipients_triggers_skipped(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: MagicMock())
        event = make_event(description="No contact info here.")
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.skipped) == 1
        assert cap.skipped[0]["event_id"] == "evt1"
        assert cap.complete == []
        assert cap.failed == []
        await w.shutdown()

    async def test_none_description_triggers_skipped(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: MagicMock())
        event = make_event(description=None)
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.skipped) == 1
        await w.shutdown()

    async def test_gmail_api_failure_triggers_failed(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        monkeypatch.setattr("src.mail_worker._load_template", lambda: TEMPLATE)
        error_svc = MagicMock()
        error_svc.users.return_value.drafts.return_value.create.return_value.execute.side_effect = RuntimeError(
            "Gmail 503"
        )
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: error_svc)
        event = make_event(description="user@example.com")
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "Gmail 503" in cap.failed[0]["error_text"]
        await w.shutdown()

    async def test_service_factory_failure_triggers_failed(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        monkeypatch.setattr("src.mail_worker._load_template", lambda: TEMPLATE)

        def failing_factory():
            raise RuntimeError("OAuth failed")

        cap = MailCallbackCapture()
        w = MailWorker(service_factory=failing_factory)
        event = make_event(description="user@example.com")
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "OAuth failed" in cap.failed[0]["error_text"]
        await w.shutdown()

    async def test_cancel_before_processing(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: MagicMock())
        event = make_event(description="user@example.com")
        w.create_email_draft(cap, "req-cancel", event)
        w.cancel_request("req-cancel")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert cap.failed[0]["error_text"] == "Cancelled"
        assert cap.complete == []
        await w.shutdown()

    async def test_multiple_recipients_in_one_draft(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        monkeypatch.setattr("src.mail_worker._load_template", lambda: TEMPLATE)
        svc = make_mock_gmail_service("draft_multi")
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: svc)
        event = make_event(description="Contact a@example.com and b@example.com")
        w.create_email_draft(cap, "req-1", event)
        await asyncio.sleep(0.05)
        # One draft created (not two)
        assert len(cap.complete) == 1
        assert cap.complete[0]["draft_id"] == "draft_multi"
        await w.shutdown()

    async def test_every_item_produces_exactly_one_callback(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        monkeypatch.setattr("src.mail_worker._load_template", lambda: TEMPLATE)
        svc = make_mock_gmail_service("draft_1")
        cap = MailCallbackCapture()
        w = MailWorker(service_factory=lambda: svc)
        events = [
            make_event(id="e1", description="a@b.com"),
            make_event(id="e2", description="no email here"),
            make_event(id="e3", description="c@d.com"),
        ]
        for ev in events:
            w.create_email_draft(cap, "req-1", ev)
        await asyncio.sleep(0.1)
        total = len(cap.complete) + len(cap.skipped) + len(cap.failed)
        assert total == 3
        await w.shutdown()

    async def test_shutdown_completes(self, monkeypatch):
        monkeypatch.setattr("src.mail_worker._CANCEL_TTL_SECONDS", 0)
        w = MailWorker(service_factory=lambda: MagicMock())
        await w.shutdown()  # must not hang
