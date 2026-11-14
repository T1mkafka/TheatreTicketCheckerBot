"""Точка входа Telegram-бота мониторинга билетов pkteatr.ru."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import config
from app.db import Database
from app.handlers import build_router
from app.service import TicketService

log = logging.getLogger("ticketbot")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(
            RotatingFileHandler(
                config.BASE_DIR / "bot.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _fmt_price(low, high) -> str:
    if low and high:
        return f"{low} ₽" if low == high else f"{low}–{high} ₽"
    return "—"


async def run_once() -> None:
    """Одноразовая проверка без Telegram — для отладки и ручного контроля."""
    config_data = config.load_targets()
    db = Database(":memory:")
    await db.connect()
    try:
        service = TicketService(bot=None, db=db, config_data=config_data)  # type: ignore[arg-type]
        results = await service.check_all(notify=False)
    finally:
        await db.close()

    print("\n=== Результат проверки ===")
    for status in results:
        print(f"\n🎭 {status.title}")
        if status.error:
            print(f"  ошибка: {status.error}")
            continue
        if not status.events:
            print("  показов в афише нет")
            continue
        for event in status.events:
            state = "ПРОДАЖА ОСТАНОВЛЕНА" if event.sale_stopped else (
                f"свободно {event.free_places}" if event.accepted else "билетов нет"
            )
            print(
                f"  {event.begin_date}  {state}"
                f"  (цена: {_fmt_price(event.min_price, event.max_price)})"
            )
    print()


async def main() -> int:
    if not config.BOT_TOKEN:
        print(
            "BOT_TOKEN не задан.\n"
            "Скопируйте .env.example в .env и укажите токен от @BotFather."
        )
        return 1

    config_data = config.load_targets()

    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()

    db = Database(config.DB_PATH)
    await db.connect()

    service = TicketService(bot, db, config_data)
    dispatcher.include_router(build_router(service))

    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        service.run_periodic,
        trigger="interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        max_instances=1,
        coalesce=True,
        jitter=30,
    )
    scheduler.start()

    # Базовая проверка при старте: состояние запомнится без рассылки.
    asyncio.create_task(service.run_periodic())

    await bot.delete_webhook(drop_pending_updates=True)
    log.info(
        "Бот запущен. Интервал проверки: %s мин. Спектаклей: %s.",
        config.CHECK_INTERVAL_MINUTES,
        len(service.targets),
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()
    return 0


def cli() -> None:
    parser = argparse.ArgumentParser(description="Telegram-бот мониторинга билетов pkteatr.ru")
    parser.add_argument(
        "--check-once",
        action="store_true",
        help="выполнить одну проверку без Telegram и выйти",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    setup_logging()

    try:
        if args.check_once:
            asyncio.run(run_once())
        else:
            raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit) as exc:
        code = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 0
        if code:
            raise SystemExit(code)


if __name__ == "__main__":
    cli()
