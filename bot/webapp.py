import asyncio
import os
from datetime import date, datetime, timedelta

from aiohttp import web

from bot import ai, checkins, db, handlers
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
    opening = await checkins.open_session(employee, kind, today, deadline_at)
    if opening is not None:
        db.log_message(employee["key"], today, "bot", opening)

    return web.json_response({"ok": True})


async def handle_message(request: web.Request):
    payload = await request.json()
    employee, err = _authenticate(payload)
    if err:
        return err

    text = (payload.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty"}, status=400)

    reply = await handlers.employee_message(request.app["bot"], employee, text)
    return web.json_response({"ok": True, "reply": reply})


_AUDIO_EXTENSIONS = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg",
                     "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-m4a": "m4a"}


async def handle_voice(request: web.Request):
    # multipart: initData (текст) + audio (файл записи из MediaRecorder)
    form = await request.post()
    employee, err = _authenticate({"initData": form.get("initData", "")})
    if err:
        return err

    audio = form.get("audio")
    if audio is None or not hasattr(audio, "file"):
        return web.json_response({"error": "no_audio"}, status=400)

    content_type = (audio.content_type or "").split(";")[0].strip()
    ext = _AUDIO_EXTENSIONS.get(content_type, "webm")
    data = audio.file.read()

    text = await asyncio.to_thread(ai.transcribe, data, f"voice.{ext}")
    if text is None:
        return web.json_response({"error": "not_recognized"}, status=422)

    reply = await handlers.employee_message(request.app["bot"], employee, text)
    return web.json_response({"ok": True, "text": text, "reply": reply})


def build_app(bot) -> web.Application:
    # Лимит тела запроса поднят ради голосовых (по умолчанию в aiohttp — 1 МБ).
    app = web.Application(client_max_size=25 * 1024 * 1024)
    app["bot"] = bot
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/state", handle_state)
    app.router.add_post("/api/start", handle_start)
    app.router.add_post("/api/message", handle_message)
    app.router.add_post("/api/voice", handle_voice)
    return app


async def start_web_server(bot, port: int) -> web.AppRunner:
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
