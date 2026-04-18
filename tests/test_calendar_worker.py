from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.calendar_worker import CalendarWorker


class CalendarCallbackCapture:
    def __init__(self) -> None:
        self.complete: list[dict] = []
        self.failed: list[dict] = []

    async def calendar_events_complete(
        self, request_id: str, selected_date: str, events: list[dict]
    ) -> None:
        self.complete.append(
            {"request_id": request_id, "selected_date": selected_date, "events": events}
        )

    async def calendar_events_failed(
        self, request_id: str, selected_date: str, error_text: str
    ) -> None:
        self.failed.append({"request_id": request_id, "error_text": error_text})


def make_raw_timed_event(
    id: str = "evt1",
    summary: str = "Standup",
    start_dt: str = "2026-04-10T09:00:00-05:00",
    end_dt: str = "2026-04-10T10:00:00-05:00",
    tz: str = "America/Chicago",
    location: str | None = None,
    description: str | None = None,
    html_link: str | None = None,
    status: str = "confirmed",
    color_id: str | None = None,
) -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": {"dateTime": start_dt, "timeZone": tz},
        "end": {"dateTime": end_dt, "timeZone": tz},
        "location": location,
        "description": description,
        "htmlLink": html_link,
        "status": status,
        "colorId": color_id,
    }


def make_raw_allday_event(id: str = "allday1", summary: str = "Holiday") -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": {"date": "2026-04-10"},
        "end": {"date": "2026-04-11"},
    }


def make_mock_service(events: list[dict]) -> MagicMock:
    mock_events = MagicMock()
    mock_events.list.return_value.execute.return_value = {"items": events}
    mock_svc = MagicMock()
    mock_svc.events.return_value = mock_events
    return mock_svc


@pytest.fixture
async def worker(monkeypatch):
    monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
    w = CalendarWorker(service_factory=lambda: MagicMock())
    yield w
    await w.shutdown()


# ── Static method tests (no asyncio needed) ────────────────────────────────────


class TestIsPrintableEvent:
    def test_timed_inside_window(self):
        raw = make_raw_timed_event(
            start_dt="2026-04-10T09:00:00-05:00", end_dt="2026-04-10T10:00:00-05:00"
        )
        assert CalendarWorker._is_printable_event(raw) is True

    def test_starts_at_08_00_boundary(self):
        raw = make_raw_timed_event(
            start_dt="2026-04-10T08:00:00-05:00", end_dt="2026-04-10T09:00:00-05:00"
        )
        assert CalendarWorker._is_printable_event(raw) is True

    def test_ends_at_18_00_boundary(self):
        raw = make_raw_timed_event(
            start_dt="2026-04-10T17:00:00-05:00", end_dt="2026-04-10T18:00:00-05:00"
        )
        assert CalendarWorker._is_printable_event(raw) is True

    def test_starts_before_08_excluded(self):
        raw = make_raw_timed_event(
            start_dt="2026-04-10T07:59:00-05:00", end_dt="2026-04-10T09:00:00-05:00"
        )
        assert CalendarWorker._is_printable_event(raw) is False

    def test_ends_after_18_excluded(self):
        raw = make_raw_timed_event(
            start_dt="2026-04-10T09:00:00-05:00", end_dt="2026-04-10T18:01:00-05:00"
        )
        assert CalendarWorker._is_printable_event(raw) is False

    def test_all_day_event_excluded(self):
        raw = make_raw_allday_event()
        assert CalendarWorker._is_printable_event(raw) is False

    def test_missing_start_datetime_excluded(self):
        raw = {"start": {}, "end": {"dateTime": "2026-04-10T10:00:00-05:00"}}
        assert CalendarWorker._is_printable_event(raw) is False


class TestNormalize:
    def test_timed_event_fields(self):
        raw = make_raw_timed_event(
            id="ev1",
            summary="Standup",
            start_dt="2026-04-10T09:00:00-05:00",
            end_dt="2026-04-10T10:00:00-05:00",
            tz="America/Chicago",
            location="Room A",
            description="Notes",
            html_link="https://example.com/event",
            status="confirmed",
            color_id="7",
        )
        result = CalendarWorker._normalize(raw)
        assert result["id"] == "ev1"
        assert result["summary"] == "Standup"
        assert result["start"] == "2026-04-10T09:00:00-05:00"
        assert result["end"] == "2026-04-10T10:00:00-05:00"
        assert result["timezone"] == "America/Chicago"
        assert result["location"] == "Room A"
        assert result["description"] == "Notes"
        assert result["html_link"] == "https://example.com/event"
        assert result["status"] == "confirmed"
        assert result["colorId"] == "7"

    def test_all_day_event_uses_date(self):
        raw = make_raw_allday_event()
        result = CalendarWorker._normalize(raw)
        assert result["start"] == "2026-04-10"
        assert result["end"] == "2026-04-11"

    def test_missing_fields_default_to_none_or_empty(self):
        raw = {
            "id": "ev2",
            "start": {"dateTime": "2026-04-10T09:00:00-05:00"},
            "end": {},
        }
        result = CalendarWorker._normalize(raw)
        assert result["summary"] == ""
        assert result["location"] is None
        assert result["description"] is None
        assert result["colorId"] is None
        assert result["timezone"] is None


# ── Worker integration tests ───────────────────────────────────────────────────


class TestCalendarWorkerIntegration:
    async def test_get_events_enqueues_and_returns_immediately(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        svc = make_mock_service([])
        w = CalendarWorker(service_factory=lambda: svc)
        cap = CalendarCallbackCapture()
        w.get_events_for_date(cap, "req-1", "2026-04-10")
        assert cap.complete == []  # not processed yet
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        await w.shutdown()

    async def test_success_callback_with_events(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        raw = make_raw_timed_event()
        svc = make_mock_service([raw])
        w = CalendarWorker(service_factory=lambda: svc)
        cap = CalendarCallbackCapture()
        w.get_events_for_date(cap, "req-1", "2026-04-10")
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        events = cap.complete[0]["events"]
        assert len(events) == 1
        assert events[0]["id"] == "evt1"
        await w.shutdown()

    async def test_all_day_events_filtered_out(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        raw = make_raw_allday_event()
        svc = make_mock_service([raw])
        w = CalendarWorker(service_factory=lambda: svc)
        cap = CalendarCallbackCapture()
        w.get_events_for_date(cap, "req-1", "2026-04-10")
        await asyncio.sleep(0.05)
        assert len(cap.complete) == 1
        assert cap.complete[0]["events"] == []
        await w.shutdown()

    async def test_api_error_triggers_failed_callback(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        error_svc = MagicMock()
        error_svc.events.return_value.list.return_value.execute.side_effect = (
            RuntimeError("503 error")
        )
        w = CalendarWorker(service_factory=lambda: error_svc)
        cap = CalendarCallbackCapture()
        w.get_events_for_date(cap, "req-1", "2026-04-10")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert "503 error" in cap.failed[0]["error_text"]
        await w.shutdown()

    async def test_cancel_before_processing(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        svc = make_mock_service([make_raw_timed_event()])
        w = CalendarWorker(service_factory=lambda: svc)
        cap = CalendarCallbackCapture()
        w.get_events_for_date(cap, "req-cancel", "2026-04-10")
        w.cancel_request("req-cancel")
        await asyncio.sleep(0.05)
        assert len(cap.failed) == 1
        assert cap.failed[0]["error_text"] == "Cancelled"
        assert cap.complete == []
        await w.shutdown()

    async def test_shutdown_completes(self, monkeypatch):
        monkeypatch.setattr("src.calendar_worker._CANCEL_TTL_SECONDS", 0)
        w = CalendarWorker(service_factory=lambda: MagicMock())
        await w.shutdown()  # must not hang
