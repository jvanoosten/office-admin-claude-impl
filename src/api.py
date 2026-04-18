from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.calendar_worker import CalendarWorker
from src.document_worker import DocumentWorker
from src.mail_worker import MailWorker
from src.models import (
    PrintCalendarEventsRequest,
    SendEmailNotificationsRequest,
    StatusResponse,
    SubmitResponse,
)
from src.office_admin import OfficeAdmin, OfficeAdminQueueFullError
from src.printer_worker import PrinterWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    calendar_worker = CalendarWorker()
    document_worker = DocumentWorker()
    printer_worker = PrinterWorker()
    mail_worker = MailWorker()
    office_admin = OfficeAdmin(
        calendar_worker, document_worker, printer_worker, mail_worker
    )
    app.state.office_admin = office_admin
    app.state.mail_worker = mail_worker
    app.state.printer_worker = printer_worker
    app.state.document_worker = document_worker
    app.state.calendar_worker = calendar_worker
    yield
    await office_admin.shutdown()
    await mail_worker.shutdown()
    await printer_worker.shutdown()
    await document_worker.shutdown()
    await calendar_worker.shutdown()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("templates/index.html")


@app.post("/api/office/print-calendar-events", status_code=202)
async def print_calendar_events(body: PrintCalendarEventsRequest, request: Request):
    office_admin: OfficeAdmin = request.app.state.office_admin
    try:
        request_id = office_admin.submit_print_calendar_events(str(body.selected_date))
    except OfficeAdminQueueFullError:
        raise HTTPException(
            status_code=429, detail="Server is busy. Try again shortly."
        )
    return SubmitResponse(request_id=request_id)


@app.post("/api/office/send-email-notifications", status_code=202)
async def send_email_notifications(
    body: SendEmailNotificationsRequest, request: Request
):
    office_admin: OfficeAdmin = request.app.state.office_admin
    try:
        request_id = office_admin.submit_send_email_notifications(
            str(body.selected_date)
        )
    except OfficeAdminQueueFullError:
        raise HTTPException(
            status_code=429, detail="Server is busy. Try again shortly."
        )
    return SubmitResponse(request_id=request_id)


@app.get("/api/office/status/{request_id}")
async def get_status(request_id: str, request: Request):
    office_admin: OfficeAdmin = request.app.state.office_admin
    status = office_admin.get_status(request_id)
    if status.get("status") == "UNKNOWN":
        return JSONResponse(status_code=404, content=status)
    return _fill_status_response(status)


@app.post("/api/office/cancel/{request_id}")
async def cancel_request(request_id: str, request: Request):
    office_admin: OfficeAdmin = request.app.state.office_admin
    status = office_admin.cancel_request(request_id)
    if status.get("status") == "UNKNOWN":
        return JSONResponse(status_code=404, content=status)
    return _fill_status_response(status)


@app.get("/api/office/tasks")
async def list_tasks(request: Request):
    office_admin: OfficeAdmin = request.app.state.office_admin
    return [_fill_status_response(t) for t in office_admin._tasks.values()]


def _fill_status_response(task: dict) -> StatusResponse:
    """Return StatusResponse with default-zero fields for missing task-type-specific fields."""
    return StatusResponse(
        request_id=task["request_id"],
        task_type=task["task_type"],
        status=task["status"],
        stage=task["stage"],
        selected_date=task.get("selected_date"),
        calendar_event_count=task.get("calendar_event_count", 0),
        events_retrieved=task.get("events_retrieved", False),
        cancel_requested=task.get("cancel_requested", False),
        errors=task.get("errors", []),
        created_at=task.get("created_at", ""),
        updated_at=task.get("updated_at", ""),
        documents_expected=task.get("documents_expected", 0),
        documents_completed=task.get("documents_completed", 0),
        documents_failed=task.get("documents_failed", 0),
        prints_expected=task.get("prints_expected", 0),
        prints_completed=task.get("prints_completed", 0),
        prints_failed=task.get("prints_failed", 0),
        document_paths=task.get("document_paths", []),
        emails_expected=task.get("emails_expected", 0),
        emails_completed=task.get("emails_completed", 0),
        emails_skipped=task.get("emails_skipped", 0),
        emails_failed=task.get("emails_failed", 0),
        draft_ids=task.get("draft_ids", []),
        skipped_event_ids=task.get("skipped_event_ids", []),
    )
