"""Обработчики команд Telegram-бота."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from . import config
from .service import TicketService


def _welcome_text(company_name: str, interval: int, subscribers: int) -> str:
    return (
        "👋 <b>Бот следит за наличием билетов</b>\n"
        f"🏛 {company_name}\n\n"
        "Вы подписаны на уведомления. Я проверяю афишу каждые "
        f"{interval} мин и пишу, когда:\n"
        "• 🔔 появляются билеты\n"
        "• ⚠️ билеты заканчиваются\n"
        "• ℹ️ меняется число свободных мест\n\n"
        f"Подписчиков сейчас: <b>{subscribers}</b>\n\n"
        "Команды:\n"
        "/list — текущий статус билетов\n"
        "/check — проверить прямо сейчас\n"
        "/stop — отписаться от уведомлений\n"
        "/help — помощь"
    )


def build_router(service: TicketService) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        user = message.from_user
        await service.db.add_subscriber(
            message.chat.id,
            user.username if user else None,
            user.first_name if user else None,
        )
        count = await service.db.subscribers_count()
        await message.answer(
            _welcome_text(
                service.company_name, config.CHECK_INTERVAL_MINUTES, count
            )
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        count = await service.db.subscribers_count()
        await message.answer(
            _welcome_text(
                service.company_name, config.CHECK_INTERVAL_MINUTES, count
            )
        )

    @router.message(Command("stop", "unsubscribe"))
    async def cmd_stop(message: Message) -> None:
        await service.db.remove_subscriber(message.chat.id)
        await message.answer(
            "✅ Вы отписаны от уведомлений.\n"
            "Чтобы подписаться снова — отправьте /start."
        )

    @router.message(Command("list", "status"))
    async def cmd_list(message: Message) -> None:
        await message.answer(await service.status_text())

    @router.message(Command("check"))
    async def cmd_check(message: Message) -> None:
        wait = service.manual_check_available_in()
        if wait > 0:
            await message.answer(
                f"⏳ Проверка уже выполнялась недавно. Попробуйте через {wait} сек."
            )
            return
        await message.answer("🔄 Проверяю афишу…")
        done, _ = await service.run_manual()
        await message.answer(await service.status_text())
        _ = done

    @router.message()
    async def fallback(message: Message) -> None:
        await message.answer(
            "Я понимаю только команды:\n"
            "/start — подписаться\n"
            "/list — статус билетов\n"
            "/check — проверить сейчас\n"
            "/stop — отписаться"
        )

    return router
