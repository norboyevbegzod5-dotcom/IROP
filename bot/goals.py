# Цели по цифрам: «150 звонков за неделю», «20 млн поступлений за месяц». Это обычная
# задача руководителя, у которой есть metric (поле daily_metrics) и target. Факт —
# сумма цифр из вечерних отчётов за окно задачи; отсюда прогноз по темпу и сколько
# нужно в день, чтобы успеть. Закрывается сама: 🏆 при достижении, итог — по сроку.

import logging
import math
from datetime import date, datetime, time, timedelta

from telegram.error import TelegramError

from bot import checkins, db
from bot.config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

# metric -> (иконка, название, деньги ли)
METRICS = {
    "calls": ("📞", "звонки", False),
    "meetings_held": ("🤝", "проведённые встречи", False),
    "meetings_new": ("📅", "назначенные встречи", False),
    "kp_count": ("📄", "КП", False),
    "contracts_count": ("✍️", "договоры", False),
    "contracts_sum": ("✍️", "сумма договоров", True),
    "payments_sum": ("💰", "поступления", True),
    "new_connections": ("🔌", "новые подключения", False),
}

# Ниже этой доли от цели прогноз — «не успевает», выше — «на грани».
AT_RISK_SHARE = 0.85

STATE_LABELS = {
    "achieved": "✅ выполнено",
    "on_track": "🟢 успевает",
    "at_risk": "🟡 на грани",
    "behind": "🔴 не успевает",
    "new": "⚪️ только начали",
}


def end_date_for(start: date, deadline_days: int) -> date:
    """Последний день цели. N ≥ 2 дней — N дней включая сегодня («недельная» с
    понедельника — до воскресенья); 0 — сегодня, 1 — завтра."""
    if deadline_days >= 2:
        return start + timedelta(days=deadline_days - 1)
    return start + timedelta(days=max(deadline_days, 0))


def deadline_for(start: date, deadline_days: int) -> datetime:
    # Конец последнего дня — чтобы вечерний отчёт этого дня успел попасть в факт.
    return datetime.combine(end_date_for(start, deadline_days), time(23, 59))


def _work_days():
    return checkins.SCHEDULE[checkins.EVENING]["days"]


def fmt(metric: str, value) -> str:
    if value is None:
        return "—"
    if METRICS[metric][2]:
        return f"{value:,}".replace(",", " ") + " сум"
    return str(value)


def progress(task, today: date) -> dict:
    start = date.fromisoformat(task["task_date"])
    end = date.fromisoformat(task["deadline_at"][:10])
    metric, target = task["metric"], task["target"]

    rows = db.get_metric_rows(task["employee_key"], start.isoformat(), min(end, today).isoformat())
    fact = sum(r[metric] or 0 for r in rows)
    reported_today = any(r["metric_date"] == today.isoformat() for r in rows)

    work_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    work_days = [d for d in work_days if d.weekday() in _work_days()]
    elapsed = sum(1 for d in work_days if d < today or (d == today and reported_today))
    remaining = len(work_days) - elapsed

    forecast = round(fact + fact / elapsed * remaining) if elapsed else None
    left = max(0, target - fact)
    per_day = math.ceil(left / remaining) if left and remaining else None

    if fact >= target:
        state = "achieved"
    elif forecast is None:
        state = "new"
    elif forecast >= target:
        state = "on_track"
    elif forecast >= target * AT_RISK_SHARE:
        state = "at_risk"
    else:
        state = "behind"

    icon, label, _ = METRICS[metric]
    return {
        "id": task["id"],
        "employee_key": task["employee_key"],
        "title": task["title"],
        "metric": metric,
        "metric_icon": icon,
        "metric_label": label,
        "money": METRICS[metric][2],
        "target": target,
        "fact": fact,
        "left": left,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "work_days_left": remaining,
        "per_day": per_day,
        "forecast": forecast,
        "state": state,
        "state_label": STATE_LABELS[state],
        "accepted": task["status"] == "accepted",
        "goal_status": task["goal_status"],
    }


def open_progress(employee_key: str = None, today: date = None) -> list:
    today = today or date.today()
    return [progress(t, today) for t in db.get_open_goals(employee_key)]


def short_line(p: dict) -> str:
    return (f"🎯 {p['title']}: {p['metric_icon']} {fmt(p['metric'], p['fact'])} из "
            f"{fmt(p['metric'], p['target'])} — {p['state_label']}")


def nudge_text(p: dict) -> str:
    end = date.fromisoformat(p["end"]).strftime("%d.%m")
    m = p["metric"]
    return (f"⏰ {p['title']}: {fmt(m, p['fact'])} из {fmt(m, p['target'])}.\n"
            f"Осталось {fmt(m, p['left'])} — чтобы успеть к {end}, нужно {fmt(m, p['per_day'])} в день.")


def _achieved_texts(full_name: str, p: dict):
    m = p["metric"]
    return (
        f"🏆 Цель выполнена: {p['title']} — {fmt(m, p['fact'])} из {fmt(m, p['target'])}!",
        f"🏆 {full_name} выполнил цель «{p['title']}»: {fmt(m, p['fact'])} из {fmt(m, p['target'])}",
    )


def _missed_texts(full_name: str, p: dict):
    m = p["metric"]
    pct = round(p["fact"] / p["target"] * 100) if p["target"] else 0
    return (
        f"⌛️ Срок цели «{p['title']}» вышел: {fmt(m, p['fact'])} из {fmt(m, p['target'])}.",
        f"🔴 {full_name} не выполнил цель «{p['title']}»: "
        f"{fmt(m, p['fact'])} из {fmt(m, p['target'])} ({pct}%)",
    )


async def _send(bot, chat_id, text):
    if chat_id is None:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.exception("Не удалось отправить сообщение о цели")


async def _finish(bot, employee, p: dict, status: str):
    if not db.set_goal_status(p["id"], status):
        return
    to_employee, to_admin = (_achieved_texts if status == "achieved" else _missed_texts)(
        employee["full_name"], p
    )
    await _send(bot, employee["chat_id"], to_employee)
    await _send(bot, ADMIN_CHAT_ID, to_admin)


async def check_achieved(bot, employee) -> list:
    """После нового вечернего отчёта: закрывает достигнутые цели (🏆 обоим).
    -> прогресс по остальным открытым целям сотрудника."""
    today = date.today()
    still_open = []
    for task in db.get_open_goals(employee["key"]):
        p = progress(task, today)
        if p["state"] == "achieved":
            await _finish(bot, employee, p, "achieved")
        else:
            still_open.append(p)
    return still_open


async def close_expired(bot, now: datetime):
    """Цели с вышедшим сроком: добранные → achieved, принятые и недобранные → missed.
    Непринятые к сроку закрывает обычная логика просрочки задач."""
    today = now.date()
    for task in db.get_open_goals():
        if task["deadline_at"] > now.isoformat(timespec="seconds"):
            continue
        p = progress(task, today)
        employee = db.get_employee(task["employee_key"])
        if p["state"] == "achieved":
            await _finish(bot, employee, p, "achieved")
        elif task["status"] == "accepted":
            await _finish(bot, employee, p, "missed")


async def nudge_behind(bot, today: date):
    """Дневное напоминание тем, кто по прогнозу не успевает (раз в день на цель)."""
    if today.weekday() not in _work_days():
        return
    for task in db.get_open_goals():
        if task["nudged_on"] == today.isoformat():
            continue
        p = progress(task, today)
        if p["state"] not in ("at_risk", "behind") or not p["per_day"]:
            continue
        employee = db.get_employee(task["employee_key"])
        if employee is None or employee["chat_id"] is None:
            continue
        db.mark_goal_nudged(task["id"], today.isoformat())
        await _send(bot, employee["chat_id"], nudge_text(p))
