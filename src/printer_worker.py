from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CANCEL_TTL_SECONDS = 3600


def _default_print_adapter(document_path: str) -> None:
    result = subprocess.run(["lp", document_path], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode().strip() or f"lp failed with code {result.returncode}"
        )


class PrinterWorker:
    def __init__(self, print_adapter: Callable[[str], None] | None = None) -> None:
        self._print_adapter = print_adapter or _default_print_adapter
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancelled: dict[str, bool] = {}
        self._worker_task = asyncio.create_task(self._worker_loop())

    def print_document(
        self, office_admin_ref: Any, request_id: str, event_id: str, document_path: str
    ) -> None:
        if not document_path:
            raise ValueError("document_path must be a non-empty string")
        self._queue.put_nowait(
            {
                "office_admin_ref": office_admin_ref,
                "request_id": request_id,
                "event_id": event_id,
                "document_path": document_path,
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
        event_id = item["event_id"]
        document_path = item["document_path"]

        if self._cancelled.get(request_id):
            await office_admin_ref.print_failed(request_id, event_id, "Cancelled")
            return

        if not os.path.exists(document_path):
            await office_admin_ref.print_failed(
                request_id, event_id, f"File not found: {document_path}"
            )
            return

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._print_adapter, document_path)
        except Exception as exc:
            await office_admin_ref.print_failed(request_id, event_id, str(exc))
            return

        if self._cancelled.get(request_id):
            await office_admin_ref.print_failed(request_id, event_id, "Cancelled")
            return

        await office_admin_ref.print_complete(request_id, event_id, document_path)

    async def _cleanup_cancel_after_ttl(self, request_id: str) -> None:
        await asyncio.sleep(_CANCEL_TTL_SECONDS)
        self._cancelled.pop(request_id, None)
