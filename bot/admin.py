# Админка руководителя: планы сотрудников и статистика по их отчётам.
# Логина нет — доступ по секретному адресу /admin/<ADMIN_PANEL_TOKEN> (ссылку даёт /admin в боте).

import hmac
import os
from datetime import date, datetime, timedelta

from aiohttp import web

from bot import checkins, db, deals, digest, goals, stats, styles
from bot.config import ADMIN_PANEL_TOKEN, EMPLOYEES, WEBAPP_URL

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp_static", "admin.html")
_MAX_REPORTS = 30


def panel_url() -> str:
    return f"{WEBAPP_URL}/admin/{ADMIN_PANEL_TOKEN}" if WEBAPP_URL else ""


def _check_token(request: web.Request):
    # compare_digest — чтобы токен нельзя было подобрать по времени ответа.
    if not hmac.compare_digest(request.match_info.get("token", ""), ADMIN_PANEL_TOKEN):
        raise web.HTTPNotFound()


async def handle_page(request: web.Request):
    _check_token(request)
    with open(_STATIC, "r", encoding="utf-8") as f:
        return web.Response(
            text=f.read(),
            content_type="text/html",
            # Страницу с секретом в адресе не кэшируем и не отдаём адрес сторонним сайтам.
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                     "X-Robots-Tag": "noindex"},
        )


async def handle_data(request: web.Request):
    _check_token(request)
    period = request.query.get("period", "month")
    if period not in stats.PERIODS:
        period = "month"
    start, end = stats.period_range(period)

    plans = db.get_plans()
    facts = stats.facts_between(start, end)
    employees_rows = {r["key"]: r for r in db.all_employees()}
    reports = db.get_reports_between(start.isoformat(), end.isoformat())
    overdue = db.get_overdue_checkins_between(start.isoformat(), end.isoformat())

    today = date.today()
    today_str = today.isoformat()
    since = (today - timedelta(days=120)).isoformat()
    todays = db.get_reports_between(today_str, today_str)

    employees = []
    for e in EMPLOYEES:
        row = employees_rows.get(e.key)
        plan = plans.get(e.key) or {}
        done = [r for r in reports if r["employee_key"] == e.key]
        done_today = {r["kind"] for r in todays if r["employee_key"] == e.key}
        active = db.get_active_session(e.key)
        evening_dates = db.get_evening_dates(e.key, since)
        employees.append(
            {
                "key": e.key,
                "name": e.full_name,
                "connected": bool(row and row["chat_id"]),
                "style": styles.LABELS[styles.normalize(row["rop_style"] if row else None)],
                "plan": {f: plan.get(f) for f in db.PLAN_FIELDS},
                "plan_period": stats.plan_for_period(plan, period),
                "fact": facts.get(e.key, {}),
                "reports": {
                    "standup": sum(1 for r in done if r["kind"] == checkins.STANDUP),
                    "evening": sum(1 for r in done if r["kind"] == checkins.EVENING),
                    "late": sum(1 for r in overdue if r["employee_key"] == e.key),
                },
                "today": {
                    "standup": checkins.STANDUP in done_today,
                    "evening": checkins.EVENING in done_today,
                    "active": checkins.TITLES[active["kind"]] if active is not None else None,
                },
                "streak": digest.streak(evening_dates, today),
                "missed_days": digest.missed_days(evening_dates, today),
            }
        )

    goal_rows = list(db.get_open_goals()) + list(db.get_finished_goals(start.isoformat()))
    goal_list = []
    for task in goal_rows:
        p = goals.progress(task, today)
        p["employee_name"] = next((e.full_name for e in EMPLOYEES if e.key == task["employee_key"]),
                                  task["employee_key"])
        goal_list.append(p)

    names = {e.key: e.full_name for e in EMPLOYEES}
    return web.json_response(
        {
            "period": {"key": period, "label": stats.PERIODS[period],
                       "start": start.isoformat(), "end": end.isoformat(),
                       "workdays": stats.plan_for_period({}, period)["workdays"]},
            "periods": stats.PERIODS,
            "today": today_str,
            "is_work_day": today.weekday() in checkins.SCHEDULE[checkins.EVENING]["days"],
            "schedule": {k: checkins.SCHEDULE[k]["time"] for k in (checkins.STANDUP, checkins.EVENING)},
            "evening_deadline_passed": _deadline_passed(checkins.EVENING),
            "standup_deadline_passed": _deadline_passed(checkins.STANDUP),
            "employees": employees,
            "goals": goal_list,
            "deals": [dict(d, employee_name=names.get(d["employee_key"], d["employee_key"]))
                      for d in deals.board(today=today)],
            "deal_stages": [{"key": s, "label": deals.LABELS[s], "icon": deals.ICONS[s]}
                            for s in deals.STAGES + [deals.LOST]],
            "stale_days": deals.STALE_DAYS,
            "reports": [
                {
                    "name": names.get(r["employee_key"], r["employee_key"]),
                    "kind": checkins.TITLES.get(r["kind"], r["kind"]),
                    "date": r["session_date"],
                    "time": (r["completed_at"] or "")[11:16],
                    "text": r["report"] or "",
                }
                for r in reports[:_MAX_REPORTS]
            ],
        }
    )


def _deadline_passed(kind: str) -> bool:
    cfg = checkins.SCHEDULE[kind]
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    now = datetime.now()
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= deadline + timedelta(minutes=cfg["deadline_minutes"])


def _plan_value(raw):
    if raw in (None, ""):
        return None
    try:
        value = int(str(raw).replace(" ", "").replace(" ", ""))
    except ValueError:
        raise web.HTTPBadRequest(text="bad number")
    if value < 0:
        raise web.HTTPBadRequest(text="negative")
    return value


async def handle_save_plans(request: web.Request):
    _check_token(request)
    payload = await request.json()
    keys = {e.key for e in EMPLOYEES}
    for item in payload.get("plans") or []:
        if item.get("key") not in keys:
            continue
        db.save_plan(item["key"], {f: _plan_value(item.get(f)) for f in db.PLAN_FIELDS})
    return web.json_response({"ok": True})


# ---------- настройки, база знаний, AI-чат (всё, что раньше было командами бота) ----------

def _settings_json() -> dict:
    from bot import handlers  # локально: handlers тяжёлый, а админке он нужен только тут

    rows = {r["key"]: r for r in db.all_employees()}
    return {
        "styles": styles.LABELS,
        "employees": [
            {"key": e.key, "name": e.full_name,
             "style": styles.normalize(rows[e.key]["rop_style"] if e.key in rows else None),
             "connected": bool(e.key in rows and rows[e.key]["chat_id"])}
            for e in EMPLOYEES
        ],
        "copies": handlers.dialog_copies_on(),
        "knowledge": [
            {"id": r["id"], "kind": r["kind"], "question": r["question"] or "", "text": r["text"]}
            for r in db.all_knowledge()
        ],
        "chat": handlers.admin_history(date.today().isoformat()),
    }


async def handle_settings(request: web.Request):
    _check_token(request)
    return web.json_response(_settings_json())


async def handle_style(request: web.Request):
    _check_token(request)
    payload = await request.json()
    key, style = payload.get("key"), payload.get("style")
    if style not in styles.LABELS or (key != "all" and key not in {e.key for e in EMPLOYEES}):
        raise web.HTTPBadRequest(text="bad style")
    for e in EMPLOYEES:
        if key in ("all", e.key):
            db.set_rop_style(e.key, style)
    return web.json_response(_settings_json())


async def handle_copies(request: web.Request):
    _check_token(request)
    from bot import handlers

    payload = await request.json()
    handlers.set_dialog_copies(bool(payload.get("on")))
    return web.json_response(_settings_json())


async def handle_knowledge_add(request: web.Request):
    _check_token(request)
    from bot import knowledge

    text = str((await request.json()).get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="empty")
    db.add_knowledge(knowledge.FACT, text[:4000])
    return web.json_response(_settings_json())


async def handle_knowledge_delete(request: web.Request):
    _check_token(request)
    try:
        knowledge_id = int((await request.json()).get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="bad id")
    db.delete_knowledge(knowledge_id)
    return web.json_response(_settings_json())


async def handle_chat(request: web.Request):
    _check_token(request)
    from bot import handlers

    text = str((await request.json()).get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="empty")
    replies = await handlers.admin_request(request.app["bot"], text[:4000])
    return web.json_response({"replies": replies})


async def handle_task_cancel(request: web.Request):
    _check_token(request)
    from bot import handlers

    try:
        task_id = int((await request.json()).get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="bad id")
    ok = await handlers.cancel_assigned_task(request.app["bot"], task_id)
    return web.json_response({"ok": ok})


def register(app: web.Application):
    app.router.add_get("/admin/{token}", handle_page)
    app.router.add_get("/admin/{token}/api/data", handle_data)
    app.router.add_post("/admin/{token}/api/plans", handle_save_plans)
    app.router.add_get("/admin/{token}/api/settings", handle_settings)
    app.router.add_post("/admin/{token}/api/style", handle_style)
    app.router.add_post("/admin/{token}/api/copies", handle_copies)
    app.router.add_post("/admin/{token}/api/knowledge", handle_knowledge_add)
    app.router.add_post("/admin/{token}/api/knowledge/delete", handle_knowledge_delete)
    app.router.add_post("/admin/{token}/api/chat", handle_chat)
    app.router.add_post("/admin/{token}/api/task/cancel", handle_task_cancel)
