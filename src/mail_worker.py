from __future__ import annotations

import asyncio
import base64
import datetime
import email as email_lib
import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CANCEL_TTL_SECONDS = 3600
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")
_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates" / "email_notification_template"
)


def _extract_recipients(description: str | None) -> list[str]:
    matches = _EMAIL_RE.findall(description or "")
    seen: set[str] = set()
    result: list[str] = []
    for addr in matches:
        if addr not in seen:
            seen.add(addr)
            result.append(addr)
    return result


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _format_date(start: str) -> str:
    try:
        date_str = start.split("T")[0] if "T" in start else start
        d = datetime.date.fromisoformat(date_str)
        return d.strftime("%A, %B %-d, %Y")
    except (ValueError, AttributeError):
        return start


def _format_time_range(start: str, end: str) -> str:
    try:
        start_dt = datetime.datetime.fromisoformat(
            start.replace("Z", "+00:00")
        ).astimezone()
        end_dt = datetime.datetime.fromisoformat(
            end.replace("Z", "+00:00")
        ).astimezone()

        def fmt(dt: datetime.datetime) -> str:
            h = dt.hour % 12 or 12
            m = dt.minute
            ap = "AM" if dt.hour < 12 else "PM"
            return f"{h}:{m:02d} {ap}"

        return f"{fmt(start_dt)} \u2013 {fmt(end_dt)}"
    except (ValueError, AttributeError):
        return "(all day)"


def _compose_draft_body(
    event: dict, recipients: list[str], template: str | None = None
) -> tuple[str, str]:
    content = template if template is not None else _load_template()
    lines = content.splitlines(keepends=True)
    subject = lines[0].strip() if lines else ""
    body_raw = "".join(lines[1:]) if len(lines) > 1 else ""

    start = event.get("start", "")
    end = event.get("end", "")
    location = event.get("location") or ""

    date_str = _format_date(start)
    time_str = _format_time_range(start, end)

    body = body_raw.format(date=date_str, time=time_str, location=location)
    return subject, body


def _build_raw_message(subject: str, body: str, recipients: list[str]) -> str:
    msg = email_lib.message.EmailMessage()
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return raw_bytes


class MailWorker:
    def __init__(
        self,
        service_factory: Callable[[], Any] | None = None,
        credentials_path: str = "gmail_credentials.json",
        token_path: str = "gmail_token.json",
    ) -> None:
        self._service_factory = service_factory
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._service: Any = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancelled: dict[str, bool] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    def create_email_draft(
        self, office_admin_ref: Any, request_id: str, event: dict
    ) -> None:
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event": event,
            }
        )

    def cancel_request(self, request_id: str) -> None:
        self._cancelled[request_id] = True
        asyncio.create_task(self._cleanup_cancel_after_ttl(request_id))

    async def shutdown(self) -> None:
        await self._queue.put(None)
        await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            await self._process_item(item)

    async def _process_item(self, item: dict) -> None:
        office_admin_ref = item["office_admin_ref"]
        request_id = item["request_id"]
        event = item["event"]
        event_id = event.get("id", "")

        if self._cancelled.get(request_id):
            await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
            return

        recipients = _extract_recipients(event.get("description"))

        if not recipients:
            if self._cancelled.get(request_id):
                await office_admin_ref.email_draft_failed(
                    request_id, event_id, "Cancelled"
                )
                return
            await office_admin_ref.email_draft_skipped(request_id, event_id)
            return

        loop = asyncio.get_running_loop()
        try:
            service = await loop.run_in_executor(None, self._get_or_build_service)
            subject, body = _compose_draft_body(event, recipients)
            raw = _build_raw_message(subject, body, recipients)
            response = await loop.run_in_executor(
                None,
                lambda: (
                    service.users()
                    .drafts()
                    .create(userId="me", body={"message": {"raw": raw}})
                    .execute()
                ),
            )
            draft_id = response["id"]
        except Exception as exc:
            await office_admin_ref.email_draft_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.email_draft_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.email_draft_complete(request_id, event_id, draft_id)

    def _get_or_build_service(self) -> Any:
        if self._service is not None:
            return self._service
        if self._service_factory is not None:
            self._service = self._service_factory()
            return self._service
        self._service = self._build_real_service()
        return self._service

    def _build_real_service(self) -> Any:
        import os
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
        creds = None
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(self._token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self._token_path, "w") as f:
                f.write(creds.to_json())
        return build("gmail", "v1", credentials=creds)

    async def _cleanup_cancel_after_ttl(self, request_id: str) -> None:
        await asyncio.sleep(_CANCEL_TTL_SECONDS)
        self._cancelled.pop(request_id, None)
