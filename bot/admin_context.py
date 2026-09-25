# Снимок данных команды для AI-чата руководителя: планы, цифры из отчётов, сами отчёты,
# статус чек-инов, задачи и сегодняшняя переписка сотрудников. Команда маленькая
# (три человека), поэтому отдаём AI всё нужное одним текстом, без поиска по базе.

from datetime import date, datetime, timedelta

from bot import checkins, db, stats
from bot.config import EMPLOYEES

METRICS_DAYS = 31        # цифры по дням — за месяц
REPORTS_DAYS = 3         # полные тексты отчётов — за последние дни
CHAT_MESSAGES = 15       # последних сообщений сотрудника за сегодня
MESSAGE_CHARS = 200

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _num(value) -> str:
    return "—" if value is None else f"{value:,}".replace(",", " ")


def _metrics_line(m) -> str:
    return (
        f"звонки {_num(m['calls'])}; встреч проведено {_num(m['meetings_held'])}, "
        f"назначено {_num(m['meetings_new'])}; КП {_num(m['kp_count'])} на {_num(m['kp_sum'])} сум; "
        f"договоры {_num(m['contracts_count'])} на {_num(m['contracts_sum'])} сум; "
        f"поступило {_num(m['payments_sum'])} сум; новых подключений {_num(m['new_connections'])}"
    )


def _cut(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MESSAGE_CHARS else text[: MESSAGE_CHARS - 1] + "…"


def build_snapshot() -> str:
    now = datetime.now()
    today = now.date()
    today_str = today.isoformat()
    names = {e.key: e.full_name for e in EMPLOYEES}
    rows = {r["key"]: r for r in db.all_employees()}
    plans = db.get_plans()

    parts = [f"Сейчас: {now:%Y-%m-%d %H:%M}, {_WEEKDAYS[today.weekday()]}."]

    # Сотрудники и планы
    lines = ["СОТРУДНИКИ И ПЛАНЫ:"]
    for e in EMPLOYEES:
        plan = plans.get(e.key) or {}
        connected = "подключён к боту" if rows.get(e.key) and rows[e.key]["chat_id"] else "НЕ подключён к боту"
        lines.append(
            f"- {e.full_name} ({connected}). План: звонков в день {_num(plan.get('calls_daily'))}, "
            f"продажи в месяц {_num(plan.get('sales_monthly'))} сум, "
            f"новых подключений в месяц {_num(plan.get('connections_monthly'))}"
        )
    parts.append("\n".join(lines))

    # Итоги месяца
    month_facts = stats.facts_between(today.replace(day=1), today)
    lines = ["С НАЧАЛА МЕСЯЦА (сумма по вечерним отчётам):"]
    for e in EMPLOYEES:
        lines.append(f"- {e.full_name}: {_metrics_line(month_facts.get(e.key, {}))}")
    parts.append("\n".join(lines))

    # Цифры по дням
    start = today - timedelta(days=METRICS_DAYS - 1)
    daily = sorted(
        db.get_metrics_between(start.isoformat(), today_str),
        key=lambda m: (m["metric_date"], m["employee_key"]),
        reverse=True,
    )
    lines = ["ЦИФРЫ ПО ДНЯМ (из вечерних отчётов, новые сверху):"]
    lines += [f"- {m['metric_date']} {names.get(m['employee_key'], m['employee_key'])}: {_metrics_line(m)}" for m in daily]
    if not daily:
        lines.append("- пока нет ни одного вечернего отчёта с цифрами")
    parts.append("\n".join(lines))

    # Статус чек-инов сегодня
    todays = db.get_reports_between(today_str, today_str)
    lines = ["ЧЕК-ИНЫ СЕГОДНЯ:"]
    for e in EMPLOYEES:
        done = {r["kind"] for r in todays if r["employee_key"] == e.key}
        active = db.get_active_session(e.key)
        status = [
            f"стендап {'сдан' if checkins.STANDUP in done else 'не сдан'}",
            f"итоги дня {'сданы' if checkins.EVENING in done else 'не сданы'}",
        ]
        if active is not None:
            status.append(f"сейчас проходит «{checkins.TITLES[active['kind']]}»")
        lines.append(f"- {e.full_name}: " + ", ".join(status))
    parts.append("\n".join(lines))

    # Тексты отчётов
    reports = db.get_reports_between((today - timedelta(days=REPORTS_DAYS - 1)).isoformat(), today_str)
    lines = [f"ОТЧЁТЫ ЗА ПОСЛЕДНИЕ {REPORTS_DAYS} ДНЯ (новые сверху):"]
    for r in reports:
        lines.append(
            f"--- {names.get(r['employee_key'], r['employee_key'])}, "
            f"{checkins.TITLES.get(r['kind'], r['kind'])}, {r['session_date']} "
            f"{(r['completed_at'] or '')[11:16]}:\n{r['report'] or '—'}"
        )
    if not reports:
        lines.append("- отчётов нет")
    parts.append("\n".join(lines))

    # Задачи
    lines = ["ОТКРЫТЫЕ ЗАДАЧИ:"]
    for e in EMPLOYEES:
        for t in db.get_open_tasks(e.key, today_str):
            source = {"manual": "от руководителя", "auto": "AI из чата"}.get(t["source"], "по шаблону")
            lines.append(
                f"- {e.full_name}: «{t['title']}» ({source}), срок {t['deadline_at'][:16].replace('T', ' ')}, "
                f"статус {t['status']}"
            )
    if len(lines) == 1:
        lines.append("- нет")
    parts.append("\n".join(lines))

    # Сегодняшняя переписка
    lines = ["СЕГОДНЯШНИЕ СООБЩЕНИЯ СОТРУДНИКОВ БОТУ:"]
    for e in EMPLOYEES:
        messages = [m for m in db.get_messages_for_day(e.key, today_str) if m["sender"] == "employee"]
        for m in messages[-CHAT_MESSAGES:]:
            lines.append(f"- {e.full_name} {m['created_at'][11:16]}: {_cut(m['text'])}")
    if len(lines) == 1:
        lines.append("- сегодня сотрудники боту не писали")
    parts.append("\n".join(lines))

    return "\n\n".join(parts)
