# Вечерняя сводка руководителю (20:00): цифры считает код из daily_metrics, AI пишет
# только выводы. Без OpenAI или при ошибке приходят те же цифры без выводов.

import asyncio
from datetime import date, timedelta

from bot import ai, checkins, db, goals, stats
from bot.config import EMPLOYEES

_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"]
_AVG_REPORTS = 7       # «обычный день» — среднее по последним N отчётам с цифрами
_AVG_LOOKBACK = 21     # ... в пределах этого числа дней
_STREAK_LOOKBACK = 120
_TG_LIMIT = 4000

# Что показываем строкой у каждого: поле daily_metrics -> иконка.
LINE_FIELDS = [
    ("calls", "📞"), ("meetings_held", "🤝"), ("meetings_new", "📅"),
    ("kp_count", "📄"), ("contracts_count", "✍️"), ("payments_sum", "💰"),
]
LEGEND = "📞 звонки · 🤝 встреч проведено · 📅 назначено · 📄 КП · ✍️ договоры · 💰 поступило"


def short_money(amount: int) -> str:
    if amount >= 10**6:
        return f"{amount / 10**6:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " млн"
    if amount >= 10**3:
        return f"{round(amount / 10**3)} тыс"
    return str(amount)


def metrics_line(values: dict) -> str:
    parts = []
    for field, icon in LINE_FIELDS:
        v = values.get(field) or 0
        parts.append(f"{icon} {short_money(v) if field == 'payments_sum' else v}")
    return " · ".join(parts)


def _work_days():
    return checkins.SCHEDULE[checkins.EVENING]["days"]


def streak(report_dates: set, today: date) -> int:
    """Рабочих дней подряд со сданными итогами дня. Сегодня без отчёта серию не
    обрывает — день ещё не закончился."""
    day = today if today.isoformat() in report_dates else today - timedelta(days=1)
    count = 0
    for _ in range(_STREAK_LOOKBACK):
        if day.weekday() in _work_days():
            if day.isoformat() not in report_dates:
                break
            count += 1
        day -= timedelta(days=1)
    return count


def missed_days(report_dates: set, today: date):
    """Рабочих дней до сегодняшнего без отчёта с последнего сданного; None — не сдавал."""
    if not report_dates:
        return None
    last = date.fromisoformat(max(report_dates))
    count, day = 0, today - timedelta(days=1)
    while day > last:
        if day.weekday() in _work_days():
            count += 1
        day -= timedelta(days=1)
    return count


def _average(employee_key: str, today: date):
    rows = db.get_metric_rows(
        employee_key, (today - timedelta(days=_AVG_LOOKBACK)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    )[-_AVG_REPORTS:]
    if not rows:
        return None
    return {f: round(sum(r[f] or 0 for r in rows) / len(rows), 1) for f in db.METRIC_FIELDS}


def collect(today: date) -> dict:
    """Факты дня по сотрудникам — и для текста сводки, и для AI."""
    today_str = today.isoformat()
    since = (today - timedelta(days=_STREAK_LOOKBACK)).isoformat()
    metrics_today = {m["employee_key"]: m for m in db.get_metrics_between(today_str, today_str)}
    open_goals = goals.open_progress(today=today)

    people = []
    for e in EMPLOYEES:
        standup = db.get_session_on(e.key, today_str, checkins.STANDUP)
        evening = db.get_session_on(e.key, today_str, checkins.EVENING)
        row = metrics_today.get(e.key)
        dates = db.get_evening_dates(e.key, since)
        people.append({
            "key": e.key,
            "name": e.full_name,
            "standup_report": standup["report"] if standup and standup["status"] == "completed" else None,
            "evening_report": evening["report"] if evening and evening["status"] == "completed" else None,
            "evening_done": bool(evening and evening["status"] == "completed"),
            "metrics_today": {f: row[f] for f in db.METRIC_FIELDS} if row else None,
            "usual_day_average": _average(e.key, today),
            "plan": stats.plan_context(e.key) or None,
            "streak_days": streak(dates, today),
            "missed_work_days_before_today": missed_days(dates, today),
            "goals": [
                {"title": p["title"], "metric": p["metric_label"], "fact": p["fact"], "target": p["target"],
                 "state": p["state"], "per_day_needed": p["per_day"], "until": p["end"]}
                for p in open_goals if p["employee_key"] == e.key
            ],
        })
    return {"date": today_str, "weekday": _WEEKDAYS[today.weekday()], "employees": people}


def _sum(people: list, field: str) -> dict:
    total = {f: 0 for f in db.METRIC_FIELDS}
    for p in people:
        for f in db.METRIC_FIELDS:
            total[f] += (p[field] or {}).get(f) or 0
    return total


def numbers_block(facts: dict, today: date) -> str:
    people = facts["employees"]
    lines = [f"📊 Итоги дня · {_WEEKDAYS[today.weekday()]}, {today.day} {_MONTHS[today.month - 1]}"]

    reported = [p for p in people if p["metrics_today"]]
    if reported:
        lines += ["", f"Команда: {metrics_line(_sum(people, 'metrics_today'))}"]
        # Сравниваем только тех, у кого есть история, — иначе новичок «раздует» рост.
        with_history = [p for p in reported if p["usual_day_average"]]
        now = _sum(with_history, "metrics_today")
        avg = _sum(with_history, "usual_day_average")
        deltas = []
        for field, icon in (("calls", "📞"), ("meetings_new", "📅")):
            if avg[field]:
                pct = round((now[field] - avg[field]) / avg[field] * 100)
                if pct:
                    deltas.append(f"{icon} {'▲' if pct > 0 else '▼'}{abs(pct)}%")
        if deltas:
            lines.append("К обычному дню этих же людей: " + " · ".join(deltas))

    lines.append("")
    for p in people:
        if p["metrics_today"]:
            lines.append(f"{p['name']}: {metrics_line(p['metrics_today'])}")
        elif p["evening_done"]:
            lines.append(f"{p['name']}: итоги сданы, цифр AI не нашёл")
        else:
            lines.append(f"{p['name']}: ❌ нет итогов дня")
    if reported:
        lines.append(LEGEND)
    return "\n".join(lines)


def goals_block(facts: dict) -> str:
    names = {p["key"]: p["name"] for p in facts["employees"]}
    lines = [f"{names.get(g['employee_key'], g['employee_key'])} — {goals.short_line(g)}"
             for g in goals.open_progress()]
    return "\n".join(["🎯 Цели:", *lines]) if lines else ""


async def build(today: date, tasks_block: str = "") -> str:
    facts = collect(today)
    parts = [numbers_block(facts, today)]

    if any(p["standup_report"] or p["evening_report"] for p in facts["employees"]):
        insights = await asyncio.to_thread(ai.daily_insights, facts)
        if insights:
            parts.append(insights)

    for block in (goals_block(facts), tasks_block):
        if block:
            parts.append(block)

    text = "\n\n".join(parts)
    return text if len(text) <= _TG_LIMIT else text[:_TG_LIMIT - 1] + "…"


def full_reports(day: date) -> list:
    """Все отчёты дня целиком — по сообщению на сотрудника."""
    messages = []
    for e in EMPLOYEES:
        parts = []
        for kind in (checkins.STANDUP, checkins.EVENING):
            s = db.get_session_on(e.key, day.isoformat(), kind)
            if s is not None and s["status"] == "completed" and s["report"]:
                parts.append(checkins.build_report(e.full_name, kind, day.isoformat(), s["report"]))
        if not parts:
            parts.append(f"📋 {e.full_name} — отчётов за {day.isoformat()} нет")
        messages.append("\n\n".join(parts)[:_TG_LIMIT])
    return messages
