# Данные из GFSupport (CRM отдела): объективные цифры и состояние воронки по менеджерам.
#
# CRM знает звонки (с АТС), КП, выигранные сделки и оплаты точнее самоотчёта, поэтому
# «Шеф» берёт их оттуда, а у человека спрашивает то, чего система знать не может. Если
# CRM недоступна, бот работает как раньше: интеграция усиливает разговор, но не является
# его условием. Границы данных (звонок привязан к обращению, а не к человеку — факт CRM
# значит «не меньше, чем») описаны в GFSUPPORT_INTEGRATION.md.

import asyncio
import logging
from datetime import date, datetime, timedelta

import aiohttp

from bot.config import CRM_FACTS_KEY, CRM_FACTS_URL, EMPLOYEES

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_CACHE_TTL_SEC = 300
_MAX_PERIOD_DAYS = 92
_cache = {}  # (params) -> (время, ответ)

# Поля цифр дня, совпадающие с db.METRIC_FIELDS (new_connections CRM не считает).
FACT_FIELDS = ("calls", "meetings_held", "meetings_new", "kp_count", "kp_sum",
               "contracts_count", "contracts_sum", "payments_sum")


def enabled() -> bool:
    return bool(CRM_FACTS_URL and CRM_FACTS_KEY)


async def _get(params: dict) -> dict:
    """Запрос к CRM. Пустой словарь — «система не ответила», это не нули."""
    if not enabled():
        return {}
    cache_key = tuple(sorted(params.items()))
    now = datetime.now().timestamp()
    hit = _cache.get(cache_key)
    if hit and now - hit[0] < _CACHE_TTL_SEC:
        return hit[1]
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(CRM_FACTS_URL, params={**params, "key": CRM_FACTS_KEY}) as resp:
                if resp.status != 200:
                    logger.warning("CRM: статус %s", resp.status)
                    return {}
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        # Только тип ошибки: в тексте исключения может оказаться адрес вместе с ключом.
        logger.warning("CRM недоступна: %s", type(exc).__name__)
        return {}
    _cache[cache_key] = (now, data)
    return data


def _by_who(rows) -> dict:
    # who=null — сотрудники GFSupport, которых нет в IROP.
    return {r["who"]: r for r in (rows or []) if r.get("who")}


async def facts_for_day(day: str = None) -> dict:
    """Цифры за день: {employees.key: {calls, calls_answered, kp_count, ...}}."""
    day = day or date.today().isoformat()
    data = await _get({"date": day})
    if not data:
        return {}  # CRM молчит — это «нет данных», а не нули
    facts = _by_who(data.get("items"))
    # Кого нет в ответе, у того за день в CRM ничего не было: это ноль, а не «нет данных».
    zero = {f: 0 for f in (*FACT_FIELDS, "calls_answered", "calls_missed")}
    for e in EMPLOYEES:
        facts.setdefault(e.key, {"who": e.key, "date": day, **zero})
    return facts


async def snapshot(days: int = 7) -> dict:
    """Полный снимок за последние N дней: {employees.key: {days, total, pipeline,
    tasks, closed, deals, called_today}}. called_today — за сегодня."""
    today = date.today()
    return await period(today - timedelta(days=days - 1), today)


async def period(start: date, end: date) -> dict:
    """Снимок за период (не длиннее 92 дней — так ограничено на стороне CRM)."""
    start = max(start, end - timedelta(days=_MAX_PERIOD_DAYS - 1))
    data = await _get({"from": start.isoformat(), "to": end.isoformat(), "date": end.isoformat()})
    return _by_who(data.get("people"))


def _money(value) -> str:
    return f"{value or 0:,}".replace(",", " ")


def line(fact: dict = None) -> str:
    """Строка цифр для промпта. Без факта — честное «система молчит»."""
    if not fact:
        return "по системе: данных нет"
    return (
        f"по системе: звонки {fact.get('calls', 0)} "
        f"(разговор {fact.get('calls_answered', 0)}, недозвон {fact.get('calls_missed', 0)}); "
        f"встреч проведено {fact.get('meetings_held', 0)}, назначено {fact.get('meetings_new', 0)}; "
        f"КП {fact.get('kp_count', 0)} на {_money(fact.get('kp_sum'))} сум; "
        f"договоры {fact.get('contracts_count', 0)} на {_money(fact.get('contracts_sum'))} сум; "
        f"поступило {_money(fact.get('payments_sum'))} сум"
    )


def pipeline_line(snap: dict = None) -> str:
    """Состояние воронки на сейчас: о чём спрашивать на стендапе."""
    if not snap:
        return ""
    p = snap.get("pipeline") or {}
    t = snap.get("tasks") or {}
    parts = [
        f"открытых сделок {p.get('open', 0)}",
        f"зависло {p['stalled']}" if p.get("stalled") else "",
        f"без следующего шага {p['no_next_step']}" if p.get("no_next_step") else "",
        f"задач просрочено {t['overdue']}" if t.get("overdue") else "",
    ]
    return "по системе: " + ", ".join(x for x in parts if x)


def stuck_deals(snap: dict = None, limit: int = 5) -> list:
    """Зависшие сделки и сделки без следующего шага — о них стоит спрашивать поимённо."""
    deals = (snap or {}).get("deals") or []
    stuck = [d for d in deals if d.get("stalled") or not d.get("next_step")]
    stuck.sort(key=lambda d: d.get("days_on_stage") or 0, reverse=True)
    return stuck[:limit]


def deals_line(snap: dict = None, limit: int = 5) -> str:
    rows = stuck_deals(snap, limit)
    if not rows:
        return ""
    items = [f"{d.get('brand') or 'без названия'} — {d.get('stage')}, {d.get('days_on_stage')} дн."
             + ("" if d.get("next_step") else ", без следующего шага")
             for d in rows]
    return "давно без движения: " + "; ".join(items)


def called_line(snap: dict = None) -> str:
    """С кем говорил в последний день периода."""
    rows = (snap or {}).get("called_today") or []
    if not rows:
        return ""
    talked = [r["name"] for r in rows if r.get("outcome") == "разговор"]
    missed = [r["name"] for r in rows if r.get("outcome") != "разговор"]
    out = []
    if talked:
        out.append("поговорил: " + ", ".join(talked[:8]))
    if missed:
        out.append("не дозвонился: " + ", ".join(missed[:8]))
    return "; ".join(out)


def lost_line(snap: dict = None) -> str:
    closed = (snap or {}).get("closed") or {}
    reasons = ", ".join(f"{r['reason']} — {r['count']}" for r in (closed.get("lost_reasons") or [])[:4])
    if not closed.get("won") and not closed.get("lost"):
        return ""
    return (f"закрыто: выиграно {closed.get('won', 0)} на {_money(closed.get('won_sum'))} сум, "
            f"проиграно {closed.get('lost', 0)}" + (f" ({reasons})" if reasons else ""))
