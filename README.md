# Telegram-бот: мониторинг билетов pkteatr.ru

Бот следит за наличием билетов на выбранные спектакли театра «На Садовой»
(pkteatr.ru) и рассылает уведомления всем подписчикам. Отслеживаются **все
показы спектакля** — на любые даты и любое время, которые есть в афише, а не
одна конкретная пара дата/время.

## Откуда берутся данные о билетах

Билеты на сайте продаются через виджет **Profticket**. Контент подгружается
скриптом, поэтому в HTML страницы данных о местах нет. Бот обращается напрямую
к JSON-API виджета:

```
GET https://widget.profticket.ru/api/v1/company/<company_id>/event/show/?show_id=<show_id>&language=ru-RU
```

Например: `https://widget.profticket.ru/api/v1/company/77/event/show/?show_id=49`

Ответ содержит `response.events_groups[].events[]` — это **все показы**
спектакля. Для каждого показа есть:

| Поле | Значение |
|---|---|
| `id` | идентификатор показа |
| `begin_date` | дата и время, `"2026-12-11 19:00:00"` |
| `date_formatted` | «11 декабря 2026, пт, 19:00» |
| `free_places_count` | **число свободных мест** |
| `has_free_places` | есть ли места |
| `min_price` / `max_price` | цены, ₽ |
| `sale_stopped` | продажа остановлена |

Сверка с помесячной афишей сайта показала, что API отдаёт тот же список и даже
больше (например, ещё не опубликованные в афише даты) — поэтому источник полный.

`company_id` и `show_id` берутся из `targets.json`. Если `show_id` не указан,
бот определит его сам по HTML страницы спектакля (ищет вызов
`profTicketWidgetApi.button(..., {"companyId":77,"showId":"49"})`).

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

## Настройка

1. Получите токен у [@BotFather](https://t.me/BotFather).
2. Скопируйте `.env.example` в `.env` и укажите токен:

```env
BOT_TOKEN=123456:AAAA...
CHECK_INTERVAL_MINUTES=7
TIMEZONE=Europe/Moscow
```

3. Отредактируйте `targets.json` — список спектаклей:

```json
{
  "company_id": 77,
  "company_name": "СПб ГБУК «Театр на Садовой»",
  "targets": [
    {
      "key": "volki-i-ovtsy",
      "title": "Волки и овцы",
      "url": "https://pkteatr.ru/theatr/performances/volki-i-ovtsy",
      "links": [
        "https://pkteatr.ru/theatr/performances/volki-i-ovtsy?date=2026-12-11&time=19:00:00"
      ],
      "show_id": 49
    }
  ]
}
```

* `url` — страница спектакля (по ней определяется `show_id` и ссылка «Открыть афишу»).
* `links` — ссылки, которые показываются **сразу под названием спектакля**
  в уведомлениях и в `/list`. Можно указать несколько.

Даты и время указывать не нужно — бот сам находит все показы в афише.
`show_id` можно не указывать — бот определит его по странице `url`.

## Запуск

```bash
python bot.py
```

Проверка без Telegram (для отладки):

```bash
python bot.py --check-once
```

## Команды бота

| Команда | Действие |
|---|---|
| `/start` | подписаться на уведомления |
| `/stop` | отписаться |
| `/list` | список всех показов и число мест по каждому |
| `/check` | запустить проверку вручную (не чаще 1 раза в минуту) |
| `/help` | справка |

## Логика уведомлений

Состояние каждого показа хранится в SQLite и сравнивается с предыдущим. Все
изменения по спектаклю собираются в одно сообщение:

* 🆕 новый показ появился в афише;
* 🔔 на показ появились билеты (`0 мест` → `есть`) — с числом мест и ценой;
* ⚠️ билеты на показ закончились;
* ℹ️ число свободных мест изменилось («было X, стало Y»);
* 🚫 продажа на показ остановлена;
* ❌ показ пропал из афиши;
* сообщение об ошибке, если не удалось проверить (без ложных срабатываний).

На первом запуске состояние просто запоминается, рассылки нет — чтобы не спамить.

## Развёртывание на сервере

Пример `/etc/systemd/system/ticketbot.service`:

```ini
[Unit]
Description=Ticket monitor bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/ticketbot
ExecStart=/opt/ticketbot/.venv/bin/python /opt/ticketbot/bot.py
Restart=always
RestartSec=10
User=ticketbot

[Install]
WantedBy=multi-user.target
```

Альтернативы: `screen`/`tmux`, Docker, любой process manager.

## Файлы проекта

```
bot.py            — точка входа, планировщик, режим --check-once
targets.json      — список отслеживаемых спектаклей
app/config.py     — чтение .env и конфигурации
app/checker.py    — работа с API Profticket (все показы спектакля)
app/db.py         — SQLite: подписчики, состояния, служебные значения
app/notifier.py   — тексты уведомлений
app/service.py    — логика проверки и рассылки
app/handlers.py   — команды Telegram
data/bot.db       — база данных (создаётся автоматически)
```

## Замечания

* Все четыре спектакля относятся к компании `company_id: 77`.
* При блокировке бота пользователем он автоматически исключается из рассылки.
* Если театр изменит структуру API, бот пришлёт сообщение об ошибке проверки,
  а не молча перестанет работать.
