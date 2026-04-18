from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import _fill_status_response, app
from src.office_admin import OfficeAdminQueueFullError


def make_task_dict(
    request_id: str = "test-uuid-1234",
    task_type: str = "PRINT_CALENDAR_EVENTS",
    status: str = "PENDING",
    stage: str = "PENDING",
    selected_date: str = "2026-04-10",
    cancel_requested: bool = False,
    errors: list[str] | None = None,
    **kwargs,
) -> dict:
    base = {
        "request_id": request_id,
        "task_type": task_type,
        "status": status,
        "stage": stage,
        "selected_date": selected_date,
        "calendar_event_count": 0,
        "events_retrieved": False,
        "cancel_requested": cancel_requested,
        "errors": errors or [],
        "created_at": "2026-04-10T00:00:00+00:00",
        "updated_at": "2026-04-10T00:00:00+00:00",
        "documents_expected": 0,
        "documents_completed": 0,
        "documents_failed": 0,
        "prints_expected": 0,
        "prints_completed": 0,
        "prints_failed": 0,
        "document_paths": [],
        "emails_expected": 0,
        "emails_completed": 0,
        "emails_skipped": 0,
        "emails_failed": 0,
        "draft_ids": [],
        "skipped_event_ids": [],
    }
    base.update(kwargs)
    return base


@pytest.fixture
def mock_admin():
    m = MagicMock()
    m.submit_print_calendar_events.return_value = "test-uuid-print"
    m.submit_send_email_notifications.return_value = "test-uuid-email"
    m.get_status.return_value = make_task_dict(request_id="test-uuid-print")
    m.cancel_request.return_value = make_task_dict(
        request_id="test-uuid-print", status="CANCEL_REQUESTED", cancel_requested=True
    )
    m._tasks = {}
    return m


@pytest.fixture
async def client(mock_admin):
    # ASGITransport does not trigger lifespan; inject state directly.
    app.state.office_admin = mock_admin
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    del app.state.office_admin


# ── Print Calendar Events endpoint ─────────────────────────────────────────────


class TestPrintCalendarEventsEndpoint:
    async def test_success_returns_202_and_request_id(self, client, mock_admin):
        resp = await client.post(
            "/api/office/print-calendar-events", json={"selected_date": "2026-04-10"}
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["request_id"] == "test-uuid-print"
        mock_admin.submit_print_calendar_events.assert_called_once_with("2026-04-10")

    async def test_invalid_date_returns_422(self, client):
        resp = await client.post(
            "/api/office/print-calendar-events", json={"selected_date": "not-a-date"}
        )
        assert resp.status_code == 422

    async def test_missing_date_returns_422(self, client):
        resp = await client.post("/api/office/print-calendar-events", json={})
        assert resp.status_code == 422

    async def test_queue_full_returns_429(self, client, mock_admin):
        mock_admin.submit_print_calendar_events.side_effect = OfficeAdminQueueFullError(
            "full"
        )
        resp = await client.post(
            "/api/office/print-calendar-events", json={"selected_date": "2026-04-10"}
        )
        assert resp.status_code == 429
        assert "busy" in resp.json()["detail"].lower()


# ── Send Email Notifications endpoint ─────────────────────────────────────────


class TestSendEmailNotificationsEndpoint:
    async def test_success_returns_202_and_request_id(self, client, mock_admin):
        resp = await client.post(
            "/api/office/send-email-notifications", json={"selected_date": "2026-04-11"}
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["request_id"] == "test-uuid-email"
        mock_admin.submit_send_email_notifications.assert_called_once_with("2026-04-11")

    async def test_invalid_date_returns_422(self, client):
        resp = await client.post(
            "/api/office/send-email-notifications", json={"selected_date": "2026-13-40"}
        )
        assert resp.status_code == 422

    async def test_queue_full_returns_429(self, client, mock_admin):
        mock_admin.submit_send_email_notifications.side_effect = (
            OfficeAdminQueueFullError("full")
        )
        resp = await client.post(
            "/api/office/send-email-notifications", json={"selected_date": "2026-04-11"}
        )
        assert resp.status_code == 429


# ── Status endpoint ────────────────────────────────────────────────────────────


class TestStatusEndpoint:
    async def test_known_id_returns_200_with_payload(self, client, mock_admin):
        mock_admin.get_status.return_value = make_task_dict(
            request_id="test-uuid-print", status="RUNNING"
        )
        resp = await client.get("/api/office/status/test-uuid-print")
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "test-uuid-print"
        assert body["status"] == "RUNNING"

    async def test_unknown_id_returns_404(self, client, mock_admin):
        mock_admin.get_status.return_value = {
            "status": "UNKNOWN",
            "request_id": "unknown-id",
        }
        resp = await client.get("/api/office/status/unknown-id")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == "UNKNOWN"

    async def test_status_payload_includes_all_required_fields(
        self, client, mock_admin
    ):
        mock_admin.get_status.return_value = make_task_dict(
            request_id="req-1",
            status="COMPLETED",
            stage="COMPLETED",
            documents_expected=2,
            documents_completed=2,
            prints_completed=2,
        )
        resp = await client.get("/api/office/status/req-1")
        assert resp.status_code == 200
        body = resp.json()
        required_fields = [
            "request_id",
            "task_type",
            "status",
            "stage",
            "selected_date",
            "calendar_event_count",
            "events_retrieved",
            "cancel_requested",
            "errors",
            "created_at",
            "updated_at",
            "documents_expected",
            "documents_completed",
            "documents_failed",
            "prints_expected",
            "prints_completed",
            "prints_failed",
            "document_paths",
            "emails_expected",
            "emails_completed",
            "emails_skipped",
            "emails_failed",
            "draft_ids",
            "skipped_event_ids",
        ]
        for field in required_fields:
            assert field in body, f"Missing field: {field}"


# ── Cancel endpoint ────────────────────────────────────────────────────────────


class TestCancelEndpoint:
    async def test_cancel_known_id_returns_200(self, client, mock_admin):
        resp = await client.post("/api/office/cancel/test-uuid-print")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "CANCEL_REQUESTED"
        mock_admin.cancel_request.assert_called_once_with("test-uuid-print")

    async def test_cancel_unknown_id_returns_404(self, client, mock_admin):
        mock_admin.cancel_request.return_value = {
            "status": "UNKNOWN",
            "request_id": "missing",
        }
        resp = await client.post("/api/office/cancel/missing")
        assert resp.status_code == 404


# ── Tasks endpoint ─────────────────────────────────────────────────────────────


class TestTasksEndpoint:
    async def test_returns_200_with_task_list(self, client, mock_admin):
        task = make_task_dict(request_id="req-1", status="RUNNING")
        mock_admin._tasks = {"req-1": task}
        resp = await client.get("/api/office/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert body[0]["request_id"] == "req-1"

    async def test_empty_task_list(self, client, mock_admin):
        mock_admin._tasks = {}
        resp = await client.get("/api/office/tasks")
        assert resp.status_code == 200
        assert resp.json() == []


# ── StatusResponse shape ───────────────────────────────────────────────────────


class TestFillStatusResponse:
    def test_print_task_fields_populated(self):
        task = make_task_dict(
            documents_expected=3,
            documents_completed=2,
            prints_completed=1,
            document_paths=["/tmp/a.pdf"],
        )
        resp = _fill_status_response(task)
        assert resp.documents_expected == 3
        assert resp.documents_completed == 2
        assert resp.prints_completed == 1
        assert resp.document_paths == ["/tmp/a.pdf"]

    def test_email_task_fields_populated(self):
        task = make_task_dict(
            task_type="SEND_EMAIL_NOTIFICATIONS",
            emails_expected=5,
            emails_completed=3,
            emails_skipped=2,
            draft_ids=["d1", "d2", "d3"],
            skipped_event_ids=["e4", "e5"],
        )
        resp = _fill_status_response(task)
        assert resp.emails_expected == 5
        assert resp.emails_completed == 3
        assert resp.emails_skipped == 2
        assert resp.draft_ids == ["d1", "d2", "d3"]

    def test_missing_type_specific_fields_default_to_zero(self):
        task = {
            "request_id": "r1",
            "task_type": "PRINT_CALENDAR_EVENTS",
            "status": "PENDING",
            "stage": "PENDING",
            "selected_date": "2026-04-10",
            "calendar_event_count": 0,
            "events_retrieved": False,
            "cancel_requested": False,
            "errors": [],
            "created_at": "2026-04-10T00:00:00+00:00",
            "updated_at": "2026-04-10T00:00:00+00:00",
        }
        resp = _fill_status_response(task)
        assert resp.documents_expected == 0
        assert resp.emails_expected == 0
        assert resp.draft_ids == []
