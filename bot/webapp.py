import os
from datetime import date, datetime, timedelta

from aiohttp import web

from bot import checkins, db, texts
from bot.config import ADMIN_CHAT_ID
from bot.telegram_auth import get_user, validate_init_data

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp_static")


def _authenticate(payload: dict):
    """Возвращает (employee_row, error_response) — один из двух всегда None."""
    pairs = validate_init_data(payload.get("initData", ""))
    if pairs is None:
        return None, web.json_response({"error": "invalid_init_data"}, status=401)

    user = get_user(pairs)
    if user is None or "id" not in user:
        return None, web.json_response({"error": "invalid_init_data"}, status=401)

    employee = db.get_employee_by_chat(user["id"])
    if employee is None:
        return None, web.json_response({"error": "not_registered"}, status=403)

    return employee, None


async def handle_index(request: web.Request):
    path = os.path.join(_STATIC_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def handle_state(request: web.Request):
    payload = await request.json()
    employee, err = _authenticate(payload)
    if err:
        return err

    today = date.today().isoformat()
    messages = db.get_messages_for_day(employee["key"], today)
    active = db.get_active_session(employee["key"])
    completed = db.get_completed_checkin_kinds(employee["key"], today)

    pending_action = None
    if active is None:
        if checkins.STANDUP not in completed:
            pending_action = "start_standup"
        elif checkins.EVENING not in completed:
            pending_action = "start_evening"

    return web.json_response(
        {
            "employee_name": employee["full_name"],
            "messages": [{"sender": m["sender"], "text": m["text"]} for m in messages],
            "awaiting_answer": active is not None,
            "pending_action": pending_action,
        }
    )


async def handle_start(request: web.Request):
    payload = await request.json()
    employee, err = _authenticate(payload)
    if err:
        return err

    today = date.today().isoformat()
    if db.get_active_session(employee["key"]) is not None:
        return web.json_response({"error": "already_active"}, status=409)

    completed = db.get_completed_checkin_kinds(employee["key"], today)
    if checkins.STANDUP not in completed:
        kind = checkins.STANDUP
    elif checkins.EVENING not in completed:
        kind = checkins.EVENING
    else:
        return web.json_response({"error": "nothing_pending"}, status=409)

    deadline_minutes = checkins.SCHEDULE[kind]["deadline_minutes"]
    deadline_at = datetime.now() + timedelta(minutes=deadline_minutes)
    db.start_checkin_session(employee["key"], today, kind, deadline_at)

    question = checkins.QUESTIONS[kind][0]
    bot_text = texts.checkin_start(checkins.TITLES[kind], question)
    db.log_message(employee["key"], today, "bot", bot_text)

    return web.json_response({"ok": True})


async def handle_message(request: web.Request):
    payload = await request.json()
    employee, err = _authenticate(payload)
    if err:
        return err

    text = (payload.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty"}, status=400)

    today = date.today().isoformat()
    db.log_message(employee["key"], today, "employee", text)

    bot = request.app["bot"]
    session = db.get_active_session(employee["key"])

    if session is not None:
        updated = db.record_answer(session["id"], text)
        kind = session["kind"]
        questions = checkins.QUESTIONS[kind]

        if updated["question_index"] < len(questions):
            next_question = questions[updated["question_index"]]
            db.log_message(employee["key"], today, "bot", next_question)
        else:
            db.complete_session(session["id"])
            db.log_message(employee["key"], today, "bot", texts.checkin_thanks())
            report = checkins.build_report(
                employee["full_name"], kind, session["session_date"], updated["answers"]
            )
            if ADMIN_CHAT_ID is not None:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
    else:
        if ADMIN_CHAT_ID is not None:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=f"💬 {employee['full_name']}: {text}"
            )

    return web.json_response({"ok": True})


def build_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/state", handle_state)
    app.router.add_post("/api/start", handle_start)
    app.router.add_post("/api/message", handle_message)
    return app


async def start_web_server(bot, port: int) -> web.AppRunner:
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
