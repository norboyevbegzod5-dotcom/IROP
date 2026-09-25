# Админка руководителя: планы сотрудников и статистика по их отчётам.
# Логина нет — доступ по секретному адресу /admin/<ADMIN_PANEL_TOKEN> (ссылку даёт /admin в боте).

import hmac
import os
from datetime import date

from aiohttp import web

from bot import checkins, db, stats, styles
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

    employees = []
    for e in EMPLOYEES:
        row = employees_rows.get(e.key)
        plan = plans.get(e.key) or {}
        done = [r for r in reports if r["employee_key"] == e.key]
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
            }
        )

    names = {e.key: e.full_name for e in EMPLOYEES}
    return web.json_response(
        {
            "period": {"key": period, "label": stats.PERIODS[period],
                       "start": start.isoformat(), "end": end.isoformat(),
                       "workdays": stats.plan_for_period({}, period)["workdays"]},
            "periods": stats.PERIODS,
            "today": date.today().isoformat(),
            "employees": employees,
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


def register(app: web.Application):
    app.router.add_get("/admin/{token}", handle_page)
    app.router.add_get("/admin/{token}/api/data", handle_data)
    app.router.add_post("/admin/{token}/api/plans", handle_save_plans)
