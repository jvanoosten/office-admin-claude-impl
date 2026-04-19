from __future__ import annotations

import pytest

from src.office_admin import OfficeAdmin


def make_event(
    id: str = "evt001",
    summary: str = "Team Standup",
    start: str = "2026-04-10T09:00:00-05:00",
    end: str = "2026-04-10T10:00:00-05:00",
    location: str | None = None,
    description: str | None = None,
    colorId: str | None = None,
    timezone: str | None = None,
) -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": start,
        "end": end,
        "location": location,
        "description": description,
        "html_link": "https://calendar.google.com/event/123",
        "status": "confirmed",
        "colorId": colorId,
        "timezone": timezone,
    }


class FakeCalendarWorker:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cancelled: set[str] = set()

    def get_events_for_date(
        self, office_admin_ref, request_id: str, selected_date: str
    ) -> None:
        self._items.append(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "selected_date": selected_date,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled.add(request_id)

    async def shutdown(self) -> None:
        pass

    async def trigger_complete(self, events: list[dict] | None = None) -> None:
        item = self._items.pop(0)
        await item["office_admin_ref"].calendar_events_complete(
            item["request_id"], item["selected_date"], events or []
        )

    async def trigger_failed(self, error_text: str = "API error") -> None:
        item = self._items.pop(0)
        await item["office_admin_ref"].calendar_events_failed(
            item["request_id"], item["selected_date"], error_text
        )


class FakeDocumentWorker:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cancelled: set[str] = set()

    def create_event_document(
        self, office_admin_ref, request_id: str, event: dict
    ) -> None:
        self._items.append(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled.add(request_id)

    async def shutdown(self) -> None:
        pass

    async def trigger_complete(
        self, document_path: str = "/tmp/test.pdf", index: int = 0
    ) -> None:
        item = self._items.pop(index)
        event_id = item["event"].get("id", "")
        await item["office_admin_ref"].document_complete(
            item["request_id"], event_id, document_path
        )

    async def trigger_failed(
        self, error_text: str = "PDF error", index: int = 0
    ) -> None:
        item = self._items.pop(index)
        event_id = item["event"].get("id", "")
        await item["office_admin_ref"].document_failed(
            item["request_id"], event_id, error_text
        )


class FakePrinterWorker:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cancelled: set[str] = set()

    def print_document(
        self, office_admin_ref, request_id: str, event_id: str, document_path: str
    ) -> None:
        self._items.append(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event_id": event_id,
                "document_path": document_path,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled.add(request_id)

    async def shutdown(self) -> None:
        pass

    async def trigger_complete(self, index: int = 0) -> None:
        item = self._items.pop(index)
        await item["office_admin_ref"].print_complete(
            item["request_id"], item["event_id"], item["document_path"]
        )

    async def trigger_failed(
        self, error_text: str = "Print error", index: int = 0
    ) -> None:
        item = self._items.pop(index)
        await item["office_admin_ref"].print_failed(
            item["request_id"], item["event_id"], error_text
        )


class FakeMailWorker:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cancelled: set[str] = set()

    def create_email_draft(
        self, office_admin_ref, request_id: str, event: dict
    ) -> None:
        self._items.append(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled.add(request_id)

    async def shutdown(self) -> None:
        pass

    async def trigger_complete(
        self, draft_id: str = "draft_abc", index: int = 0
    ) -> None:
        item = self._items.pop(index)
        event_id = item["event"].get("id", "")
        await item["office_admin_ref"].email_draft_complete(
            item["request_id"], event_id, draft_id
        )

    async def trigger_skipped(self, index: int = 0) -> None:
        item = self._items.pop(index)
        event_id = item["event"].get("id", "")
        await item["office_admin_ref"].email_draft_skipped(item["request_id"], event_id)

    async def trigger_failed(
        self, error_text: str = "Gmail error", index: int = 0
    ) -> None:
        item = self._items.pop(index)
        event_id = item["event"].get("id", "")
        await item["office_admin_ref"].email_draft_failed(
            item["request_id"], event_id, error_text
        )


@pytest.fixture
def fake_cal() -> FakeCalendarWorker:
    return FakeCalendarWorker()


@pytest.fixture
def fake_doc() -> FakeDocumentWorker:
    return FakeDocumentWorker()


@pytest.fixture
def fake_printer() -> FakePrinterWorker:
    return FakePrinterWorker()


@pytest.fixture
def fake_mail() -> FakeMailWorker:
    return FakeMailWorker()


@pytest.fixture
async def admin(
    fake_cal: FakeCalendarWorker,
    fake_doc: FakeDocumentWorker,
    fake_printer: FakePrinterWorker,
    fake_mail: FakeMailWorker,
    monkeypatch,
) -> OfficeAdmin:
    monkeypatch.setattr("src.office_admin.TASK_TTL_SECONDS", 0)
    oa = OfficeAdmin(fake_cal, fake_doc, fake_printer, fake_mail)
    yield oa
    await oa.shutdown()
