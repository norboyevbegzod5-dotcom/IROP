# Копирует факты дня из GFSupport в таблицу crm_metrics. Весь бот читает цифры из базы
# синхронно (статистика, цели, сводка, AI), поэтому CRM подтягивается сюда заранее:
# по расписанию каждые 5 минут и перед местами, где нужна свежая цифра.

import logging
import time
from datetime import date, timedelta

from bot import crm, db

logger = logging.getLogger(__name__)

# Сколько дней назад держать факты: хватает на месяц статистики и месячные цели.
SYNC_DAYS = 62
# Чаще не ходим — ответ CRM всё равно кэшируется на 5 минут.
_MIN_INTERVAL_SEC = 60
_last_sync = 0.0


async def sync(force: bool = False) -> bool:
    """True — факты обновлены. Если CRM не ответила, в базе остаются прошлые факты."""
    global _last_sync
    if not crm.enabled():
        return False
    if not force and time.monotonic() - _last_sync < _MIN_INTERVAL_SEC:
        return True
    today = date.today()
    start = today - timedelta(days=SYNC_DAYS - 1)
    people = await crm.period(start, today)
    if not people:
        return False
    rows = [
        {"employee_key": who, "metric_date": day, **(values or {})}
        for who, person in people.items()
        for day, values in (person.get("days") or {}).items()
        if start.isoformat() <= day <= today.isoformat()
    ]
    db.replace_crm_metrics(start.isoformat(), today.isoformat(), rows)
    _last_sync = time.monotonic()
    return True


async def sync_job(context):
    if not await sync(force=True):
        logger.warning("CRM: синхронизация фактов не удалась, используются прошлые")
