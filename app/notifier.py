"""Формирование текстов уведомлений (по всем показам сразу)."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Optional

from .checker import ShowEvent, TargetStatus

MAX_MESSAGE_LEN = 3500


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)


def _accepted(state: Optional[dict[str, Any]]) -> bool:
    if not state:
        return False
    return bool(
        state.get("has_free")
        and not state.get("sale_stopped")
        and (state.get("free_places") or 0) > 0
    )


def _when(begin_date: Optional[str], date_formatted: Optional[str] = None) -> str:
    if date_formatted:
        return date_formatted
    if begin_date:
        try:
            return datetime.strptime(begin_date, "%Y-%m-%d %H:%M:%S").strftime(
                "%d.%m.%Y, %H:%M"
            )
        except (ValueError, TypeError):
            pass
    return begin_date or "?"


def _price(low: Optional[int], high: Optional[int]) -> str:
    low, high = low if isinstance(low, int) else None, high if isinstance(high, int) else None
    if not low or not high:
        return ""
    if low == high:
        return f"{low} ₽"
    return f"{low}–{high} ₽"


def _link_lines(links: Optional[list[str]]) -> list[str]:
    """Строки со ссылками (показываем сразу под названием спектакля)."""
    lines: list[str] = []
    for link in links or []:
        if link:
            lines.append(f'🔗 <a href="{esc(link)}">{esc(link)}</a>')
    return lines


def build_change_message(
    status: TargetStatus, prev: Optional[dict[str, Any]]
) -> Optional[str]:
    """Собирает одно сообщение со всеми изменениями по спектаклю.

    prev — сохранённое ранее состояние (dict) или None при первом запуске.
    """
    if prev is None:
        # Первый прогон — запоминаем состояние, не спамим.
        return None

    prev_events: dict[str, dict[str, Any]] = prev.get("events") or {}
    current: dict[str, ShowEvent] = {e.event_id: e for e in status.events}
    lines: list[str] = []

    # Показы, которые исчезли из афиши.
    for event_id, old in prev_events.items():
        if event_id in current:
            continue
        when = esc(_when(old.get("begin_date"), old.get("date_formatted")))
        lines.append(f"❌ Показ {when} пропал из афиши")

    # Новые показы и изменения по существующим.
    for event_id, event in current.items():
        when = esc(event.when)
        old = prev_events.get(event_id)

        if old is None:
            if event.accepted:
                lines.append(
                    f"🔔 {when} — появились билеты! "
                    f"Свободных мест: <b>{event.free_places}</b>"
                )
            else:
                lines.append(f"🆕 {when} — новый показ в афише (билетов пока нет)")
            continue

        was_accepted = _accepted(old)

        if event.sale_stopped and not old.get("sale_stopped"):
            lines.append(f"🚫 {when} — продажа остановлена")
        elif event.accepted and not was_accepted:
            price = _price(event.min_price, event.max_price)
            extra = f", цена {price}" if price else ""
            lines.append(
                f"🔔 {when} — появились билеты! "
                f"Свободных мест: <b>{event.free_places}</b>{extra}"
            )
        elif was_accepted and not event.accepted:
            lines.append(f"⚠️ {when} — билеты закончились")
        elif was_accepted and event.accepted and old.get("free_places") != event.free_places:
            lines.append(
                f"ℹ️ {when} — свободных мест: было {old.get('free_places')}, "
                f"стало <b>{event.free_places}</b>"
            )

    if not lines:
        return None

    header = [f"🎭 <b>{esc(status.title)}</b>"]
    header.extend(_link_lines(status.links))
    body = "\n".join(f"• {line}" for line in lines)
    text = (
        "\n".join(header)
        + "\n"
        + body
        + "\n"
        + f'<a href="{esc(status.url)}">Открыть афишу</a>'
    )
    return text[:MAX_MESSAGE_LEN]


def build_error_message(
    status: TargetStatus, prev: Optional[dict[str, Any]]
) -> Optional[str]:
    """Сообщение об ошибке проверки. Шлём только при смене текста ошибки."""
    if prev is not None and prev.get("error") == status.error:
        return None
    return (
        f"⚠️ Не удалось проверить наличие билетов\n"
        f"🎭 <b>{esc(status.title)}</b>\n"
        f"Причина: {esc(status.error)}"
    )


def format_target_block(
    title: str, links: Optional[list[str]], state: Optional[dict[str, Any]]
) -> str:
    """Блок одного спектакля со всеми показами — для команды /list."""
    lines = [f"🎭 <b>{esc(title)}</b>"]
    lines.extend(_link_lines(links))

    if state is None:
        lines.append("• ещё не проверялся")
        return "\n".join(lines)

    events = sorted(
        (state.get("events") or {}).values(),
        key=lambda e: e.get("begin_date") or "",
    )

    if state.get("error"):
        lines.append(f"⚠️ ошибка проверки: {esc(state['error'])}")

    if not events:
        lines.append("• показов в афише нет")
        return "\n".join(lines)

    for event in events[:25]:
        when = esc(_when(event.get("begin_date"), event.get("date_formatted")))
        if event.get("sale_stopped"):
            mark = "🚫 продажа остановлена"
        elif _accepted(event):
            price = _price(event.get("min_price"), event.get("max_price"))
            extra = f" ({price})" if price else ""
            mark = f"🔔 свободных мест: <b>{event.get('free_places')}</b>{extra}"
        else:
            mark = "⚪️ билетов нет"
        lines.append(f"• {when} — {mark}")

    if len(events) > 25:
        lines.append(f"… и ещё {len(events) - 25}")

    return "\n".join(lines)
