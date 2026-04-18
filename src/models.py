from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from pydantic import BaseModel


# ── Work item TypedDicts ──────────────────────────────────────────────────────


class OfficeAdminWorkItem(TypedDict):
    request_id: str
    task_type: str
    selected_date: str


class CalendarWorkItem(TypedDict):
    office_admin_ref: Any  # OfficeAdmin (avoid circular import)
    request_id: str
    selected_date: str


class CalendarEvent(TypedDict, total=False):
    id: str
    summary: str
    start: str
    end: str
    timezone: str | None
    location: str | None
    description: str | None
    html_link: str | None
    status: str | None
    colorId: str | None


class DocumentWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent


class PrinterWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event_id: str
    document_path: str


class MailWorkItem(TypedDict):
    office_admin_ref: Any
    request_id: str
    event: CalendarEvent


# ── Pydantic API models ───────────────────────────────────────────────────────


class PrintCalendarEventsRequest(BaseModel):
    selected_date: date


class SendEmailNotificationsRequest(BaseModel):
    selected_date: date


class SubmitResponse(BaseModel):
    request_id: str


class StatusResponse(BaseModel):
    request_id: str
    task_type: str
    status: str
    stage: str
    selected_date: str | None
    calendar_event_count: int
    events_retrieved: bool
    cancel_requested: bool
    errors: list[str]
    created_at: str
    updated_at: str
    # PRINT_CALENDAR_EVENTS fields (0/[] when task type is SEND_EMAIL_NOTIFICATIONS)
    documents_expected: int = 0
    documents_completed: int = 0
    documents_failed: int = 0
    prints_expected: int = 0
    prints_completed: int = 0
    prints_failed: int = 0
    document_paths: list[str] = []
    # SEND_EMAIL_NOTIFICATIONS fields (0/[] when task type is PRINT_CALENDAR_EVENTS)
    emails_expected: int = 0
    emails_completed: int = 0
    emails_skipped: int = 0
    emails_failed: int = 0
    draft_ids: list[str] = []
    skipped_event_ids: list[str] = []
