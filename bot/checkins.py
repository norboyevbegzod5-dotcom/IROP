# Диалоговые чек-ины: AI сам ведёт разговор с сотрудником — задаёт вопросы по одному,
# исходя из цели чек-ина и предыдущих ответов, а в конце готовый отчёт уходит руководителю.

import asyncio
import json

from bot import ai, db, knowledge, stats

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


def _context(employee, session, query: str = "") -> dict:
    return {
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
        employee["full_name"], TITLES[kind], GOALS[kind], [], _context(employee, session),
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
        _context(employee, session, text), must_finish,
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
        db.save_daily_metrics(employee["key"], session["session_date"], _clean_metrics(step["metrics"]))
    report = build_report(employee["full_name"], kind, session["session_date"], body)
    return step["message"] or "Спасибо, принято ✅", report
