# Планы руководителя и факт из вечерних отчётов: расчёт план/факт по периодам.
# Используется админкой и AI (чтобы в итогах дня сравнивать с планом).

import calendar
from datetime import date, timedelta

from bot import db
from bot.config import EMPLOYEES

PERIODS = {"today": "Сегодня", "week": "Эта неделя", "month": "Этот месяц"}


def _workdays(start: date, end: date) -> int:
    from bot import checkins  # здесь, а не наверху: checkins сам импортирует stats

    days = checkins.SCHEDULE[checkins.EVENING]["days"]
    return sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() in days
    )


def period_range(period: str, today: date = None):
    today = today or date.today()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    return today.replace(day=1), today


def _month_workdays(today: date) -> int:
    last = calendar.monthrange(today.year, today.month)[1]
    return _workdays(today.replace(day=1), today.replace(day=last)) or 1


def plan_for_period(plan: dict, period: str, today: date = None) -> dict:
    """Звонки — дневной план × рабочие дни периода; продажи и подключения — месячный
    план, для дня/недели — пропорционально рабочим дням месяца."""
    today = today or date.today()
    start, end = period_range(period, today)
    wd = _workdays(start, end)
    share = 1 if period == "month" else wd / _month_workdays(today)
    plan = plan or {}

    def scaled(value, factor):
        return None if value is None else round(value * factor)

    return {
        "calls": scaled(plan.get("calls_daily"), wd),
        "sales": scaled(plan.get("sales_monthly"), share),
        "connections": scaled(plan.get("connections_monthly"), share),
        "workdays": wd,
    }


def facts_between(start: date, end: date) -> dict:
    """{employee_key: {метрика: сумма}} по вечерним отчётам за период."""
    result = {e.key: {f: 0 for f in db.METRIC_FIELDS} for e in EMPLOYEES}
    for row in db.get_metrics_between(start.isoformat(), end.isoformat()):
        bucket = result.setdefault(row["employee_key"], {f: 0 for f in db.METRIC_FIELDS})
        for f in db.METRIC_FIELDS:
            bucket[f] += row[f] or 0
    return result


def _money(value) -> str:
    return f"{value:,}".replace(",", " ") + " сум"


def plan_context(employee_key: str) -> str:
    """Строка для промпта AI: планы руководителя и выполнение с начала месяца."""
    plan = db.get_plan(employee_key)
    if not plan or all(plan.get(f) is None for f in db.PLAN_FIELDS):
        return ""
    today = date.today()
    fact = facts_between(today.replace(day=1), today).get(employee_key, {})
    parts = []
    if plan.get("calls_daily") is not None:
        parts.append(f"звонков в день — {plan['calls_daily']}")
    if plan.get("sales_monthly") is not None:
        parts.append(
            f"продажи (поступления денег) в месяц — {_money(plan['sales_monthly'])}, "
            f"с начала месяца поступило — {_money(fact.get('payments_sum', 0))}"
        )
    if plan.get("connections_monthly") is not None:
        parts.append(
            f"новых подключений в месяц — {plan['connections_monthly']}, "
            f"с начала месяца — {fact.get('new_connections', 0)}"
        )
    return "План руководителя для сотрудника: " + "; ".join(parts) + "."
