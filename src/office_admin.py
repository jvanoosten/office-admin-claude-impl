from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.calendar_worker import CalendarWorker
    from src.document_worker import DocumentWorker
    from src.mail_worker import MailWorker
    from src.printer_worker import PrinterWorker

logger = logging.getLogger(__name__)

TASK_TTL_SECONDS = 1800  # 30 minutes after terminal state

TERMINAL_STATUSES = {"COMPLETED", "CANCELLED", "ERROR"}


class OfficeAdminQueueFullError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_task_entry(request_id: str, task_type: str, selected_date: str) -> dict:
    base = {
        "request_id": request_id,
        "task_type": task_type,
        "status": "PENDING",
        "stage": "PENDING",
        "selected_date": selected_date,
        "calendar_event_count": 0,
        "events_retrieved": False,
        "cancel_requested": False,
        "errors": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if task_type == "PRINT_CALENDAR_EVENTS":
        base.update(
            {
                "documents_expected": 0,
                "documents_completed": 0,
                "documents_failed": 0,
                "prints_expected": 0,
                "prints_completed": 0,
                "prints_failed": 0,
                "document_paths": [],
                "_completed_docs": [],  # internal: list of (event_id, path) for print dispatch
            }
        )
    elif task_type == "SEND_EMAIL_NOTIFICATIONS":
        base.update(
            {
                "emails_expected": 0,
                "emails_completed": 0,
                "emails_skipped": 0,
                "emails_failed": 0,
                "draft_ids": [],
                "skipped_event_ids": [],
            }
        )
    return base


class OfficeAdmin:
    def __init__(
        self,
        calendar_worker: CalendarWorker,
        document_worker: DocumentWorker,
        printer_worker: PrinterWorker,
        mail_worker: MailWorker,
    ) -> None:
        self._calendar_worker = calendar_worker
        self._document_worker = document_worker
        self._printer_worker = printer_worker
        self._mail_worker = mail_worker
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._tasks: dict[str, dict] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    # ── Public submission methods ─────────────────────────────────────────────

    def submit_print_calendar_events(self, selected_date: str) -> str:
        if self._queue.full():
            raise OfficeAdminQueueFullError("Office admin queue is full")
        request_id = str(uuid.uuid4())
        self._tasks[request_id] = _make_task_entry(
            request_id, "PRINT_CALENDAR_EVENTS", selected_date
        )
        self._queue.put_nowait(
            {
                "request_id": request_id,
                "task_type": "PRINT_CALENDAR_EVENTS",
                "selected_date": selected_date,
            }
        )
        return request_id

    def submit_send_email_notifications(self, selected_date: str) -> str:
        if self._queue.full():
            raise OfficeAdminQueueFullError("Office admin queue is full")
        request_id = str(uuid.uuid4())
        self._tasks[request_id] = _make_task_entry(
            request_id, "SEND_EMAIL_NOTIFICATIONS", selected_date
        )
        self._queue.put_nowait(
            {
                "request_id": request_id,
                "task_type": "SEND_EMAIL_NOTIFICATIONS",
                "selected_date": selected_date,
            }
        )
        return request_id

    def get_status(self, request_id: str) -> dict:
        task = self._tasks.get(request_id)
        if task is None:
            return {"status": "UNKNOWN", "request_id": request_id}
        return dict(task)

    def cancel_request(self, request_id: str) -> dict:
        task = self._tasks.get(request_id)
        if task is None:
            return {"status": "UNKNOWN", "request_id": request_id}
        if task["status"] in TERMINAL_STATUSES:
            return dict(task)
        task["cancel_requested"] = True
        task["status"] = "CANCEL_REQUESTED"
        task["updated_at"] = _now_iso()
        self._calendar_worker.cancel_request(request_id)
        self._document_worker.cancel_request(request_id)
        self._printer_worker.cancel_request(request_id)
        self._mail_worker.cancel_request(request_id)
        return dict(task)

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    # ── Internal worker loop ──────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: dict) -> None:
        request_id = item["request_id"]
        task = self._tasks.get(request_id)
        if task is None:
            return
        task["status"] = "RUNNING"
        task["stage"] = "GETTING_CALENDAR_EVENTS"
        task["updated_at"] = _now_iso()
        self._calendar_worker.get_events_for_date(
            self, request_id, item["selected_date"]
        )

    # ── Callback methods ──────────────────────────────────────────────────────

    async def calendar_events_complete(
        self, request_id: str, selected_date: str, events: list[dict]
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["calendar_event_count"] = len(events)
        task["events_retrieved"] = True
        task["updated_at"] = _now_iso()

        if task["cancel_requested"]:
            task["status"] = "CANCELLED"
            task["stage"] = "CANCELLED"
            self._schedule_cleanup(request_id)
            return

        if not events:
            task["status"] = "COMPLETED"
            task["stage"] = "COMPLETED"
            self._schedule_cleanup(request_id)
            return

        if task["task_type"] == "PRINT_CALENDAR_EVENTS":
            task["stage"] = "CREATING_EVENT_PDFS"
            task["documents_expected"] = len(events)
            task["updated_at"] = _now_iso()
            for event in events:
                self._document_worker.create_event_document(self, request_id, event)
        else:  # SEND_EMAIL_NOTIFICATIONS
            task["stage"] = "CREATING_EMAIL_DRAFTS"
            task["emails_expected"] = len(events)
            task["updated_at"] = _now_iso()
            for event in events:
                self._mail_worker.create_email_draft(self, request_id, event)

    async def calendar_events_failed(
        self, request_id: str, selected_date: str, error_text: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        if task["cancel_requested"] and error_text == "Cancelled":
            task["status"] = "CANCELLED"
            task["stage"] = "CANCELLED"
        else:
            task["status"] = "ERROR"
            task["stage"] = "ERROR"
            task["errors"].append(error_text)
            self._propagate_cancel(request_id)
        task["updated_at"] = _now_iso()
        self._schedule_cleanup(request_id)

    async def document_complete(
        self, request_id: str, event_id: str, document_path: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["documents_completed"] += 1
        task["document_paths"].append(document_path)
        task["_completed_docs"].append((event_id, document_path))
        task["updated_at"] = _now_iso()
        if task["documents_completed"] == task["documents_expected"]:
            if task["cancel_requested"]:
                task["status"] = "CANCELLED"
                task["stage"] = "CANCELLED"
                self._schedule_cleanup(request_id)
            else:
                task["stage"] = "PRINTING_EVENT_PDFS"
                task["prints_expected"] = task["documents_completed"]
                task["updated_at"] = _now_iso()
                for eid, path in task["_completed_docs"]:
                    self._printer_worker.print_document(self, request_id, eid, path)

    async def document_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["documents_failed"] += 1
        task["updated_at"] = _now_iso()
        if task["cancel_requested"]:
            if (
                task["documents_completed"] + task["documents_failed"]
                == task["documents_expected"]
            ):
                task["status"] = "CANCELLED"
                task["stage"] = "CANCELLED"
                self._schedule_cleanup(request_id)
        else:
            task["status"] = "ERROR"
            task["stage"] = "ERROR"
            task["errors"].append(error_text)
            self._propagate_cancel(request_id)
            self._schedule_cleanup(request_id)

    async def print_complete(
        self, request_id: str, event_id: str, document_path: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["prints_completed"] += 1
        task["updated_at"] = _now_iso()
        if task["prints_completed"] == task["prints_expected"]:
            task["status"] = "COMPLETED"
            task["stage"] = "COMPLETED"
            self._schedule_cleanup(request_id)

    async def print_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["prints_failed"] += 1
        task["updated_at"] = _now_iso()
        if task["cancel_requested"]:
            if (
                task["prints_completed"] + task["prints_failed"]
                == task["prints_expected"]
            ):
                task["status"] = "CANCELLED"
                task["stage"] = "CANCELLED"
                self._schedule_cleanup(request_id)
        else:
            task["status"] = "ERROR"
            task["stage"] = "ERROR"
            task["errors"].append(error_text)
            self._propagate_cancel(request_id)
            self._schedule_cleanup(request_id)

    async def email_draft_complete(
        self, request_id: str, event_id: str, draft_id: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["emails_completed"] += 1
        task["draft_ids"].append(draft_id)
        task["updated_at"] = _now_iso()
        self._check_email_finalization(request_id, task)

    async def email_draft_skipped(self, request_id: str, event_id: str) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["emails_skipped"] += 1
        task["skipped_event_ids"].append(event_id)
        task["updated_at"] = _now_iso()
        self._check_email_finalization(request_id, task)

    async def email_draft_failed(
        self, request_id: str, event_id: str, error_text: str
    ) -> None:
        task = self._tasks.get(request_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        task["emails_failed"] += 1
        task["updated_at"] = _now_iso()
        if task["cancel_requested"]:
            self._check_email_finalization(request_id, task)
        else:
            task["status"] = "ERROR"
            task["stage"] = "ERROR"
            task["errors"].append(error_text)
            self._propagate_cancel(request_id)
            self._schedule_cleanup(request_id)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_email_finalization(self, request_id: str, task: dict) -> None:
        total = (
            task["emails_completed"] + task["emails_skipped"] + task["emails_failed"]
        )
        if total == task["emails_expected"]:
            if task["cancel_requested"]:
                task["status"] = "CANCELLED"
                task["stage"] = "CANCELLED"
            else:
                task["status"] = "COMPLETED"
                task["stage"] = "COMPLETED"
            self._schedule_cleanup(request_id)

    def _propagate_cancel(self, request_id: str) -> None:
        self._calendar_worker.cancel_request(request_id)
        self._document_worker.cancel_request(request_id)
        self._printer_worker.cancel_request(request_id)
        self._mail_worker.cancel_request(request_id)

    def _schedule_cleanup(self, request_id: str) -> None:
        asyncio.create_task(self._cleanup_after_ttl(request_id))

    async def _cleanup_after_ttl(self, request_id: str) -> None:
        await asyncio.sleep(TASK_TTL_SECONDS)
        self._tasks.pop(request_id, None)
