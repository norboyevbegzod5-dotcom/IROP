from datetime import date, datetime, time as dt_time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, BadRequest
from telegram.ext import ContextTypes

from bot import checkins, db, texts
from bot.config import ADMIN_CHAT_ID, EMPLOYEES, WEBAPP_URL


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def generate_daily_tasks(context: ContextTypes.DEFAULT_TYPE):
    db.generate_instances_for_date(date.today())


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    due = db.get_due_reminders(_now_iso())
    for t in due:
        employee = db.get_employee(t["employee_key"])
        db.mark_reminded(t["id"])
        if employee is None or employee["chat_id"] is None:
            continue

        deadline_hhmm = t["deadline_at"][11:16]
        text = texts.task_reminder(
            employee["full_name"], t["title"], t["description"], deadline_hhmm
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Выполнено", callback_data=f"done:{t['id']}")]]
        )
        try:
            await context.bot.send_message(
                chat_id=employee["chat_id"], text=text, reply_markup=keyboard
            )
        except (Forbidden, BadRequest):
            pass


async def check_deadlines(context: ContextTypes.DEFAULT_TYPE):
    overdue = db.get_newly_overdue(_now_iso())
    for t in overdue:
        db.mark_overdue(t["id"])
        employee = db.get_employee(t["employee_key"])
        if employee is None:
            continue

        if employee["chat_id"] is not None:
            try:
                await context.bot.send_message(
                    chat_id=employee["chat_id"],
                    text=texts.task_overdue_employee(t["title"]),
                )
            except (Forbidden, BadRequest):
                pass

        if ADMIN_CHAT_ID is not None:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=texts.task_overdue_admin(employee["full_name"], t["title"]),
                )
            except (Forbidden, BadRequest):
                pass


async def send_admin_summary(context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_CHAT_ID is None:
        return

    today_str = date.today().isoformat()
    summary = {row["employee_key"]: row for row in db.get_status_summary(today_str)}

    lines = [texts.admin_summary_header(today_str)]
    for e in EMPLOYEES:
        row = summary.get(e.key)
        done = row["done"] or 0 if row else 0
        overdue = row["overdue"] or 0 if row else 0
        total = row["total"] or 0 if row else 0
        lines.append(texts.admin_summary_line(e.full_name, done, overdue, total))

    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="\n".join(lines))
    except (Forbidden, BadRequest):
        pass


async def _nudge_webapp(context: ContextTypes.DEFAULT_TYPE, employee, kind: str):
    from telegram import WebAppInfo

    label = "🚀 Начать рабочий день" if kind == checkins.STANDUP else "🌙 Подвести итоги дня"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    try:
        await context.bot.send_message(
            chat_id=employee["chat_id"],
            text=f"{checkins.TITLES[kind]}: открой чат и продолжим.",
            reply_markup=keyboard,
        )
    except (Forbidden, BadRequest):
        pass


async def _start_checkins(context: ContextTypes.DEFAULT_TYPE, kind: str):
    today = date.today()
    if today.weekday() not in checkins.SCHEDULE[kind]["days"]:
        return

    date_str = today.isoformat()

    for e in EMPLOYEES:
        employee = db.get_employee(e.key)
        if employee is None or employee["chat_id"] is None:
            continue

        completed = db.get_completed_checkin_kinds(e.key, date_str)
        if kind in completed:
            continue

        if WEBAPP_URL:
            await _nudge_webapp(context, employee, kind)
            continue

        hh, mm = (int(x) for x in checkins.SCHEDULE[kind]["time"].split(":"))
        deadline_at = datetime.combine(today, datetime.min.time()).replace(hour=hh, minute=mm)
        deadline_at += timedelta(minutes=checkins.SCHEDULE[kind]["deadline_minutes"])

        text = await checkins.open_session(employee, kind, date_str, deadline_at)
        if text is None:
            continue

        try:
            await context.bot.send_message(chat_id=employee["chat_id"], text=text)
        except (Forbidden, BadRequest):
            pass


async def start_standup(context: ContextTypes.DEFAULT_TYPE):
    await _start_checkins(context, checkins.STANDUP)


async def start_evening(context: ContextTypes.DEFAULT_TYPE):
    await _start_checkins(context, checkins.EVENING)


async def check_checkin_deadlines(context: ContextTypes.DEFAULT_TYPE):
    overdue = db.get_overdue_checkins(_now_iso())
    for s in overdue:
        db.mark_checkin_nagged(s["id"])
        employee = db.get_employee(s["employee_key"])
        if employee is None:
            continue

        title = checkins.TITLES[s["kind"]]

        if employee["chat_id"] is not None:
            try:
                await context.bot.send_message(
                    chat_id=employee["chat_id"], text=texts.checkin_overdue_employee(title)
                )
            except (Forbidden, BadRequest):
                pass

        if ADMIN_CHAT_ID is not None:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=texts.checkin_overdue_admin(employee["full_name"], title),
                )
            except (Forbidden, BadRequest):
                pass


def register_jobs(job_queue, tzinfo):
    job_queue.run_daily(generate_daily_tasks, time=dt_time(0, 5, tzinfo=tzinfo), name="generate_daily")
    job_queue.run_repeating(check_reminders, interval=60, first=10, name="check_reminders")
    job_queue.run_repeating(check_deadlines, interval=60, first=15, name="check_deadlines")
    job_queue.run_daily(send_admin_summary, time=dt_time(20, 0, tzinfo=tzinfo), name="admin_summary")

    standup_hh, standup_mm = (int(x) for x in checkins.SCHEDULE[checkins.STANDUP]["time"].split(":"))
    evening_hh, evening_mm = (int(x) for x in checkins.SCHEDULE[checkins.EVENING]["time"].split(":"))
    job_queue.run_daily(
        start_standup, time=dt_time(standup_hh, standup_mm, tzinfo=tzinfo), name="start_standup"
    )
    job_queue.run_daily(
        start_evening, time=dt_time(evening_hh, evening_mm, tzinfo=tzinfo), name="start_evening"
    )
    job_queue.run_repeating(
        check_checkin_deadlines, interval=60, first=20, name="check_checkin_deadlines"
    )
