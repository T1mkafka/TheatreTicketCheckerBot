"""Конфигурация бота: переменные окружения и список отслеживаемых спектаклей."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
CHECK_INTERVAL_MINUTES: int = _int_env("CHECK_INTERVAL_MINUTES", 7)
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

_db_path = os.getenv("DB_PATH", "").strip()
DB_PATH: Path = Path(_db_path) if _db_path else BASE_DIR / "data" / "bot.db"
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

TARGETS_FILE: Path = BASE_DIR / "targets.json"

ADMIN_IDS: list[int] = [
    int(x)
    for x in os.getenv("ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().lstrip("-").isdigit()
]


def load_targets() -> dict:
    """Читает targets.json и проставляет company_id по умолчанию."""
    data = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    company_id = data.get("company_id")
    for target in data.get("targets", []):
        target.setdefault("company_id", company_id)
    return data
