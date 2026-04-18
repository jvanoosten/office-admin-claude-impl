from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CANCEL_TTL_SECONDS = 3600  # 1 hour


class CalendarWorker:
    def __init__(
        self,
        credentials_path: str = "credentials.json",
        token_path: str = "token.json",
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._service_factory = service_factory
        self._service: Any = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancelled: dict[str, bool] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    def get_events_for_date(
        self, office_admin_ref: Any, request_id: str, selected_date: str
    ) -> None:
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "selected_date": selected_date,
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
        selected_date = item["selected_date"]

        if self._cancelled.get(request_id):
            await office_admin_ref.calendar_events_failed(
                request_id, selected_date, "Cancelled"
            )
            return

        try:
            loop = asyncio.get_running_loop()
            service = await loop.run_in_executor(None, self._get_or_build_service)
            raw_events = await loop.run_in_executor(
                None, self._fetch_events, service, selected_date
            )
            events = [
                self._normalize(e) for e in raw_events if self._is_printable_event(e)
            ]
        except Exception as exc:
            await office_admin_ref.calendar_events_failed(
                request_id, selected_date, str(exc)
            )
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.calendar_events_failed(
                request_id, selected_date, "Cancelled"
            )
            return

        await office_admin_ref.calendar_events_complete(
            request_id, selected_date, events
        )

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

        SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
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
        return build("calendar", "v3", credentials=creds)

    def _fetch_events(self, service: Any, selected_date: str) -> list[dict]:
        date = datetime.date.fromisoformat(selected_date)
        next_day = date + datetime.timedelta(days=1)
        local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
        time_min = datetime.datetime(
            date.year, date.month, date.day, 0, 0, 0, tzinfo=local_tz
        ).isoformat()
        time_max = datetime.datetime(
            next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=local_tz
        ).isoformat()
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return result.get("items", [])

    @staticmethod
    def _is_printable_event(raw: dict) -> bool:
        start_str = raw.get("start", {}).get("dateTime")
        end_str = raw.get("end", {}).get("dateTime")
        if not start_str or not end_str:
            return False
        try:
            start_local = datetime.datetime.fromisoformat(
                start_str.replace("Z", "+00:00")
            ).astimezone()
            end_local = datetime.datetime.fromisoformat(
                end_str.replace("Z", "+00:00")
            ).astimezone()
            return start_local.time() >= datetime.time(
                8, 0
            ) and end_local.time() <= datetime.time(18, 0)
        except (ValueError, AttributeError):
            logger.warning("Could not parse event times; excluding event")
            return False

    @staticmethod
    def _normalize(raw: dict) -> dict:
        start = raw.get("start", {})
        end = raw.get("end", {})
        return {
            "id": raw.get("id", ""),
            "summary": raw.get("summary", ""),
            "start": start.get("dateTime") or start.get("date", ""),
            "end": end.get("dateTime") or end.get("date", ""),
            "timezone": start.get("timeZone"),
            "location": raw.get("location"),
            "description": raw.get("description"),
            "html_link": raw.get("htmlLink"),
            "status": raw.get("status"),
            "colorId": raw.get("colorId"),
        }

    async def _cleanup_cancel_after_ttl(self, request_id: str) -> None:
        await asyncio.sleep(_CANCEL_TTL_SECONDS)
        self._cancelled.pop(request_id, None)
