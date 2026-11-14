"""Сервис проверки билетов и рассылки уведомлений."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from . import checker
from .db import Database
from .notifier import build_change_message, build_error_message, format_target_block

log = logging.getLogger(__name__)

MANUAL_CHECK_COOLDOWN = 60  # секунд


class TicketService:
    def __init__(self, bot: Bot, db: Database, config_data: dict[str, Any]) -> None:
        self.bot = bot
        self.db = db
        self.company_id: Optional[int] = config_data.get("company_id")
        self.company_name: str = config_data.get("company_name") or "театр"
        self.targets: list[dict[str, Any]] = config_data.get("targets", [])
        self._check_lock = asyncio.Lock()
        self._last_manual_check = 0.0
        self.last_results: list[checker.TargetStatus] = []

    # ---------- Вспомогательное ----------

    async def _ensure_show_ids(self, client) -> None:
        """Доопределяет show_id для ссылок, где он не задан в targets.json."""
        for target in self.targets:
            if target.get("show_id"):
                continue
            cached = await self.db.get_meta(f"show_id:{target['key']}")
            if cached:
                target["show_id"] = int(cached)
                continue
            company_id, show_id = await checker.resolve_show_id(client, target["url"])
            if show_id:
                target["show_id"] = show_id
                target["company_id"] = company_id or target.get("company_id")
                await self.db.set_meta(f"show_id:{target['key']}", str(show_id))
                log.info("Определён show_id=%s для %s", show_id, target["key"])

    def manual_check_available_in(self) -> int:
        elapsed = time.monotonic() - self._last_manual_check
        return max(0, int(MANUAL_CHECK_COOLDOWN - elapsed))

    # ---------- Основной цикл ----------

    async def check_all(self, notify: bool = True) -> list[checker.TargetStatus]:
        async with self._check_lock:
            return await self._run_check(notify)

    async def _run_check(self, notify: bool) -> list[checker.TargetStatus]:
        async with checker.make_client() as client:
            try:
                await self._ensure_show_ids(client)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось определить show_id: %s", exc)
            results = await asyncio.gather(
                *(
                    checker.check_target(client, target, self.company_id)
                    for target in self.targets
                )
            )

        self.last_results = list(results)

        for status in results:
            prev = await self.db.get_state(status.key)

            if status.error:
                # Сохраняем прежнюю афишу, добавляя пометку об ошибке, чтобы
                # при восстановлении связи не было ложных уведомлений.
                merged = dict(prev) if prev else status.to_state()
                if prev:
                    merged["events"] = prev.get("events") or {}
                merged["error"] = status.error
                await self.db.set_state(status.key, merged)
                log.warning("Ошибка проверки %s: %s", status.key, status.error)
                if notify:
                    message = build_error_message(status, prev)
                    if message:
                        await self.broadcast(message)
                continue

            await self.db.set_state(status.key, status.to_state())
            available = status.accepted_events
            log.info(
                "%-24s показов=%d, с билетами=%d%s",
                status.key,
                len(status.events),
                len(available),
                (" -> " + ", ".join(e.when for e in available)) if available else "",
            )

            if notify:
                message = build_change_message(status, prev)
                if message:
                    await self.broadcast(message)

        return results

    async def run_periodic(self) -> None:
        """Задача планировщика."""
        try:
            await self.check_all(notify=True)
        except Exception:  # noqa: BLE001
            log.exception("Непредвиденная ошибка при периодической проверке")

    async def run_manual(self) -> tuple[bool, int]:
        """Ручная проверка. Возвращает (выполнена ли, сколько секунд ждать)."""
        wait = self.manual_check_available_in()
        if wait > 0:
            return False, wait
        self._last_manual_check = time.monotonic()
        await self.check_all(notify=True)
        return True, 0

    # ---------- Рассылка ----------

    async def broadcast(self, text: str) -> int:
        chat_ids = await self.db.subscribers()
        if not chat_ids:
            log.info("Нет подписчиков для рассылки.")
            return 0
        sent = 0
        for chat_id in chat_ids:
            try:
                await self.bot.send_message(
                    chat_id, text, disable_web_page_preview=True
                )
                sent += 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await self.bot.send_message(
                        chat_id, text, disable_web_page_preview=True
                    )
                    sent += 1
                except TelegramAPIError:
                    pass
            except TelegramForbiddenError:
                log.info("Пользователь %s заблокировал бота — отключаю.", chat_id)
                await self.db.deactivate_subscriber(chat_id)
            except TelegramAPIError as exc:
                log.warning("Не удалось отправить сообщение %s: %s", chat_id, exc)
            await asyncio.sleep(0.05)  # мягкий лимит ~20 сообщений/сек
        log.info("Отправлено уведомлений: %s/%s", sent, len(chat_ids))
        return sent

    # ---------- Тексты для команд ----------

    async def status_text(self) -> str:
        states = await self.db.all_states()
        blocks = [f"🎟 <b>Афиша и билеты — {self.company_name}</b>"]
        for target in self.targets:
            blocks.append(
                format_target_block(
                    target.get("title") or target["key"],
                    target.get("links") or [target.get("url")],
                    states.get(target["key"]),
                )
            )
        return "\n\n".join(blocks)
