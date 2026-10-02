# Диалоговые чек-ины: AI сам ведёт разговор с сотрудником — задаёт вопросы по одному,
# исходя из цели чек-ина и предыдущих ответов, а в конце готовый отчёт уходит руководителю.

import asyncio
import json
from datetime import date, datetime, timedelta

from bot import ai, crm, db, knowledge, stats

STANDUP = "standup"
EVENING = "evening"

TITLES = {
    STANDUP: "Стендап",
    EVENING: "Итоги дня",
}

# Не вопросы, а цель разговора: что AI должен выяснить. Формулировки и порядок
# вопросов AI выбирает сам.
GOALS = {
    STANDUP: (
        "Утренний стендап. Выясни: с какими клиентами сотрудник вчера поговорил и что они "
        "ответили (договорились о чём-то, отказали, попросили перезвонить), какие следующие "
        "шаги по ним, и с какими клиентами/брендами он планирует говорить сегодня."
    ),
    EVENING: (
        "Вечерние итоги дня. Выясни точные цифры: сколько звонков сделал; сколько встреч "
        "проведено и сколько новых назначено; сколько КП отправил и на какую общую сумму; "
        "сколько договоров подписано и на какую сумму; сколько денег фактически поступило "
        "от клиентов; сколько новых клиентов подключено. Общие слова («много», «нормально», "
        "«около 30») не принимай — нужна точная цифра. Сверься с утренним планом: с кем из "
        "запланированных удалось поговорить, а с кем нет и почему."
    ),
}

# Страховка от бесконечного диалога: после стольких ответов сотрудника AI обязан закончить.
MAX_EMPLOYEE_TURNS = 10

# days_of_week: 0=Пн ... 6=Вс
SCHEDULE = {
    STANDUP: {"days": {0, 1, 2, 3, 4, 5}, "time": "09:00", "deadline_minutes": 30},
    EVENING: {"days": {0, 1, 2, 3, 4, 5}, "time": "18:30", "deadline_minutes": 90},
}


def _turns(session) -> list:
    turns = json.loads(session["answers"])
    # Сессии, начатые до перехода на AI, хранили просто список ответов-строк.
    return [t if isinstance(t, dict) else {"sender": "employee", "text": t} for t in turns]


async def _crm_context(employee, session) -> dict:
    """Что знает CRM: для итогов дня — цифры дня; для стендапа — воронка, зависшие
    сделки и с кем говорил вчера. Пусто, если CRM не настроена или молчит."""
    if not crm.enabled():
        return {}
    day = session["session_date"]
    if session["kind"] == EVENING:
        fact = (await crm.facts_for_day(day)).get(employee["key"])
        return {"crm": crm.line(fact)}
    snap = (await crm.snapshot(7)).get(employee["key"])
    # called_today в снимке — за последний день периода; утром нужен вчерашний.
    yesterday = date.fromisoformat(day) - timedelta(days=1)
    prev = (await crm.period(yesterday, yesterday)).get(employee["key"])
    lines = [crm.pipeline_line(snap), crm.deals_line(snap)]
    called = crm.called_line(prev)
    if called:
        lines.append(f"вчера {called}")
    return {"crm": "\n".join(x for x in lines if x) or "по системе: данных нет"}


async def _context(employee, session, query: str = "") -> dict:
    return {
        **await _crm_context(employee, session),
        "kind": session["kind"],
        "previous_report": db.get_previous_report(employee["key"], session["id"]),
        "tasks": db.get_tasks_for_employee_on(employee["key"], session["session_date"]),
        "style": employee["rop_style"],
        # База знаний и образцы руководителя, подобранные под последний ответ сотрудника.
        "knowledge": knowledge.prompt_block(query),
        # Планы из админки и выполнение с начала месяца.
        "plan": stats.plan_context(employee["key"]),
    }


def _fallback_opening(kind: str) -> str:
    # Только на случай, если AI недоступен в момент старта.
    if kind == STANDUP:
        return f"🌅 {TITLES[kind]}\n\nРасскажи, с кем вчера поговорил и какие планы на сегодня?"
    return f"🌙 {TITLES[kind]}\n\nКак прошёл день? Расскажи по цифрам: звонки, встречи, КП, договоры, деньги."


def _clean_metrics(raw: dict) -> dict:
    """Только известные поля, только неотрицательные целые; остальное — None."""
    clean = {}
    for field in db.METRIC_FIELDS:
        value = raw.get(field)
        try:
            value = int(value) if value is not None else None
        except (TypeError, ValueError):
            value = None
        clean[field] = value if value is None or value >= 0 else None
    return clean


async def _with_crm_facts(employee, day: str, metrics: dict) -> dict:
    """Цифры, которые сотрудник не назвал (AI не спрашивает их, когда есть CRM), — из CRM."""
    fact = (await crm.facts_for_day(day)).get(employee["key"]) if crm.enabled() else None
    if not fact:
        return metrics
    return {f: (fact.get(f) if v is None and f in crm.FACT_FIELDS else v) for f, v in metrics.items()}


def build_report(full_name: str, kind: str, session_date: str, body: str) -> str:
    return f"📋 {TITLES[kind]} — {full_name} ({session_date})\n\n{body}"


def _transcript_report(turns: list) -> str:
    return "\n".join(
        f"{'—' if t['sender'] == 'employee' else '❓'} {t['text']}" for t in turns
    )


async def open_session(employee, kind: str, session_date: str, deadline_at):
    """Создаёт сессию и возвращает первое сообщение AI. None — если сессия уже шла
    (первое сообщение было отправлено раньше)."""
    db.start_checkin_session(employee["key"], session_date, kind, deadline_at)
    session = db.get_active_session(employee["key"], kind)
    if session is None or _turns(session):
        return None

    step = await asyncio.to_thread(
        ai.checkin_step,
        employee["full_name"], TITLES[kind], GOALS[kind], [], await _context(employee, session),
        False,
    )
    icon = "🌅" if kind == STANDUP else "🌙"
    text = f"{icon} {TITLES[kind]}\n\n{step['message']}" if step else _fallback_opening(kind)
    db.append_checkin_turn(session["id"], "bot", text)
    return text


async def handle_answer(session, employee, text: str):
    """Принимает ответ сотрудника. Возвращает (сообщение_сотруднику, отчёт_или_None):
    отчёт не None, когда AI решил, что всё выяснил, и сессия завершена."""
    turns = db.append_checkin_turn(session["id"], "employee", text)
    kind = session["kind"]
    employee_turns = sum(1 for t in turns if t["sender"] == "employee")
    must_finish = employee_turns >= MAX_EMPLOYEE_TURNS

    step = await asyncio.to_thread(
        ai.checkin_step,
        employee["full_name"], TITLES[kind], GOALS[kind], turns,
        await _context(employee, session, text), must_finish,
    )

    if step is None:
        if not must_finish:
            return "Не смог обработать ответ — напиши, пожалуйста, ещё раз.", None
        step = {"message": "Спасибо, принято ✅", "finished": True, "report": None}

    if not step.get("finished"):
        db.append_checkin_turn(session["id"], "bot", step["message"])
        return step["message"], None

    body = step.get("report") or _transcript_report(turns)
    db.complete_session(session["id"], body)
    if kind == EVENING and isinstance(step.get("metrics"), dict):
        metrics = await _with_crm_facts(employee, session["session_date"], _clean_metrics(step["metrics"]))
        db.save_daily_metrics(employee["key"], session["session_date"], metrics)
    report = build_report(employee["full_name"], kind, session["session_date"], body)
    return step["message"] or "Спасибо, принято ✅", report


# ---------- итоги дня формой (мини-апп) ----------

# Поля формы в порядке показа: (поле daily_metrics, подпись, деньги ли).
FORM_FIELDS = [
    ("calls", "📞 Звонков сделано", False),
    ("meetings_held", "🤝 Встреч проведено", False),
    ("meetings_new", "📅 Новых встреч назначено", False),
    ("kp_count", "📄 КП отправлено", False),
    ("kp_sum", "📄 КП на сумму", True),
    ("contracts_count", "✍️ Договоров подписано", False),
    ("contracts_sum", "✍️ Договоры на сумму", True),
    ("payments_sum", "💰 Поступило денег", True),
    ("new_connections", "🔌 Новых клиентов подключено", False),
]


def _form_text(metrics: dict, note: str) -> str:
    """Как форма выглядит в чате и в диалоге для AI."""
    lines = []
    for field, label, money in FORM_FIELDS:
        value = metrics.get(field) or 0
        lines.append(f"{label}: " + (f"{value:,}".replace(",", " ") + " сум" if money else str(value)))
    if note:
        lines += ["", note]
    return "\n".join(lines)


async def submit_form(employee, values: dict, note: str):
    """Итоги дня, заполненные формой: цифры сохраняются как есть (без разбора AI), AI
    пишет только фидбэк и отчёт руководителю. -> (текст в чат от сотрудника, ответ
    бота, отчёт руководителю) или None, если итоги за сегодня уже сданы."""
    today = date.today().isoformat()
    if EVENING in db.get_completed_checkin_kinds(employee["key"], today):
        return None
    deadline_at = datetime.now() + timedelta(minutes=SCHEDULE[EVENING]["deadline_minutes"])
    db.start_checkin_session(employee["key"], today, EVENING, deadline_at)
    session = db.get_active_session(employee["key"], EVENING)
    if session is None:
        return None

    # С CRM в форме только new_connections — остальные цифры берутся из GFSupport.
    metrics = await _with_crm_facts(employee, today, _clean_metrics(values))
    note = (note or "").strip()[:2000]
    employee_text = _form_text(metrics, note)
    turns = db.append_checkin_turn(session["id"], "employee", employee_text)

    step = await asyncio.to_thread(
        ai.checkin_step,
        employee["full_name"], TITLES[EVENING], GOALS[EVENING], turns,
        await _context(employee, session, note), True,
    )
    message = (step or {}).get("message") or "Спасибо, принято ✅"
    body = (step or {}).get("report") or employee_text
    db.complete_session(session["id"], body)
    db.save_daily_metrics(employee["key"], today, metrics)
    return employee_text, message, build_report(employee["full_name"], EVENING, today, body)
