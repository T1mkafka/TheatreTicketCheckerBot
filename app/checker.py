"""Проверка наличия билетов через JSON API виджета Profticket.

Виджет Profticket (используется на pkteatr.ru) отдаёт данные по адресу
    GET https://widget.profticket.ru/api/v1/company/<company_id>/event/show/?show_id=<show_id>
В ответе перечислены ВСЕ показы спектакля (events_groups -> events), для каждого:
    id, begin_date ("YYYY-MM-DD HH:MM:SS"), date_formatted,
    free_places_count, has_free_places, min_price, max_price, sale_stopped

Это надёжнее парсинга HTML, который подгружается только через JavaScript,
и, судя по сверке, полнее помесячной афиши на сайте.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import httpx

WIDGET_API = "https://widget.profticket.ru/api/v1/company/{company_id}/event/show/"
SPA_REFERER = "https://spa.profticket.ru/"
PAGE_REFERER = "https://pkteatr.ru/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
# Конфиг кнопки виджета в HTML страницы спектакля: {"companyId":77,"showId":"49"}
_WIDGET_ID_RE = re.compile(r'\{"companyId":(\d+),"showId":"(\d+)"\}')


@dataclass
class ShowEvent:
    """Один показ спектакля: конкретная дата и время."""

    event_id: str
    begin_date: str  # "2026-12-11 19:00:00"
    date_formatted: Optional[str] = None
    free_places: Optional[int] = None
    has_free: bool = False
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    sale_stopped: bool = False

    @property
    def accepted(self) -> bool:
        """Есть ли билеты в продаже на этот показ."""
        return bool(
            self.has_free
            and not self.sale_stopped
            and (self.free_places or 0) > 0
        )

    @property
    def date_part(self) -> str:
        return self.begin_date[:10]

    @property
    def time_part(self) -> str:
        return self.begin_date[11:16]

    @property
    def when(self) -> str:
        if self.date_formatted:
            return self.date_formatted
        try:
            from datetime import datetime

            return datetime.strptime(self.begin_date, "%Y-%m-%d %H:%M:%S").strftime(
                "%d.%m.%Y, %H:%M"
            )
        except (ValueError, TypeError):
            return self.begin_date

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShowEvent":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TargetStatus:
    """Состояние одного спектакля: список всех его показов."""

    key: str
    title: str
    url: str
    links: list[str] = field(default_factory=list)
    events: list[ShowEvent] = field(default_factory=list)
    show_name: Optional[str] = None
    error: Optional[str] = None

    @property
    def found(self) -> bool:
        return bool(self.events)

    @property
    def accepted_events(self) -> list[ShowEvent]:
        return [e for e in self.events if e.accepted]

    def to_state(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "url": self.url,
            "links": list(self.links),
            "show_name": self.show_name,
            "error": self.error,
            "events": {e.event_id: e.to_dict() for e in self.events},
        }


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": SPA_REFERER,
        },
        follow_redirects=True,
    )


async def resolve_show_id(
    client: httpx.AsyncClient, page_url: str
) -> tuple[Optional[int], Optional[int]]:
    """Определяет (company_id, show_id) по HTML страницы спектакля."""
    response = await client.get(page_url, headers={"Referer": PAGE_REFERER})
    response.raise_for_status()
    match = _WIDGET_ID_RE.search(response.text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


async def _fetch_events(
    client: httpx.AsyncClient, company_id: int, show_id: int
) -> dict[str, Any]:
    url = WIDGET_API.format(company_id=company_id)
    response = await client.get(url, params={"show_id": show_id, "language": "ru-RU"})
    response.raise_for_status()
    payload = response.json()
    return payload.get("response") or {}


async def check_target(
    client: httpx.AsyncClient,
    target: dict[str, Any],
    company_id: Optional[int] = None,
) -> TargetStatus:
    """Проверяет спектакль и возвращает состояние всех его показов."""
    status = TargetStatus(
        key=target["key"],
        title=target.get("title") or target["key"],
        url=target["url"],
        links=list(target.get("links") or [target.get("url")]),
    )
    status.links = [link for link in status.links if link]

    show_id = target.get("show_id")
    company_id = company_id or target.get("company_id")

    try:
        if not show_id:
            resolved_company, resolved_show = await resolve_show_id(client, target["url"])
            company_id = resolved_company or company_id
            show_id = resolved_show
        if not show_id or not company_id:
            status.error = "не удалось определить company_id/show_id"
            return status

        # Запоминаем определённые значения на время работы бота.
        target["show_id"] = show_id
        target["company_id"] = company_id

        data = await _fetch_events(client, int(company_id), int(show_id))
    except httpx.HTTPError as exc:
        status.error = f"сетевая ошибка: {exc.__class__.__name__}"
        return status
    except ValueError as exc:
        status.error = f"некорректный ответ API: {exc}"
        return status

    status.show_name = (data.get("show") or {}).get("name")

    for group in data.get("events_groups") or []:
        for event in group.get("events") or []:
            status.events.append(
                ShowEvent(
                    event_id=str(event.get("id")),
                    begin_date=event.get("begin_date") or "",
                    date_formatted=event.get("date_formatted"),
                    free_places=event.get("free_places_count"),
                    has_free=bool(event.get("has_free_places")),
                    min_price=event.get("min_price"),
                    max_price=event.get("max_price"),
                    sale_stopped=bool(event.get("sale_stopped")),
                )
            )

    status.events.sort(key=lambda e: e.begin_date)
    return status
