import asyncio
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from bot import ai, checkins, db, texts
from bot.config import ADMIN_CHAT_ID, BOT_NAME, EMPLOYEES, MAX_VOICE_SECONDS, WEBAPP_URL

_EMPLOYEE_KEYS = {e.key for e in EMPLOYEES}


def _is_admin(chat_id: int) -> bool:
    return ADMIN_CHAT_ID is not None and chat_id == ADMIN_CHAT_ID


def _webapp_keyboard():
    if not WEBAPP_URL:
        return None
    from telegram import WebAppInfo

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Открыть чат", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if _is_admin(chat_id):
        await update.message.reply_text(
            f"Привет, руководитель. Я {BOT_NAME}. Команды: /status — статус за сегодня, "
            f"/team — кто из команды подключился."
        )
        return

    employee = db.get_employee_by_chat(chat_id)
    if employee is not None:
        await update.message.reply_text(
            texts.registered(employee["full_name"]), reply_markup=_webapp_keyboard()
        )
        return

    buttons = [
        [InlineKeyboardButton(e.full_name, callback_data=f"reg:{e.key}")] for e in EMPLOYEES
    ]
    await update.message.reply_text(
        texts.welcome_choose_name(BOT_NAME),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_register_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    chat_id = query.message.chat_id

    ok = db.register_employee_chat(key, chat_id)
    if not ok:
        await query.edit_message_text(texts.slot_taken())
        return

    employee = db.get_employee(key)
    await query.edit_message_text(
        texts.registered(employee["full_name"]), reply_markup=_webapp_keyboard()
    )


async def on_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id

    employee = db.get_employee_by_chat(chat_id)
    if employee is None:
        await query.answer(texts.not_registered(), show_alert=True)
        return

    instance_id = int(query.data.split(":", 1)[1])
    instance = db.get_instance(instance_id)
    if instance is None or instance["employee_key"] != employee["key"]:
        await query.answer("Это не твоя задача.", show_alert=True)
        return

    ok = db.mark_done(instance_id, employee["key"])
    if not ok:
        await query.answer("Уже отмечено.", show_alert=False)
        return

    await query.answer("Принято")
    now_hhmm = datetime.now().strftime("%H:%M")
    await query.edit_message_text(texts.task_done_confirm(now_hhmm))


async def tasks_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    employee = db.get_employee_by_chat(chat_id)
    if employee is None:
        await update.message.reply_text(texts.not_registered())
        return

    today_str = date.today().isoformat()
    rows = db.get_tasks_for_employee_on(employee["key"], today_str)
    if not rows:
        await update.message.reply_text(texts.today_tasks_empty())
        return

    lines = [texts.today_tasks_header(today_str)]
    lines += [texts.task_line(t) for t in rows]
    await update.message.reply_text("\n".join(lines))


async def status_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text(texts.admin_only())
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

    await update.message.reply_text("\n".join(lines))


async def team_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text(texts.admin_only())
        return

    rows = {r["key"]: r for r in db.all_employees()}
    lines = ["Команда:"]
    for e in EMPLOYEES:
        row = rows.get(e.key)
        lines.append(texts.team_line(e.full_name, bool(row and row["chat_id"])))

    await update.message.reply_text("\n".join(lines))


async def admin_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        return

    text = update.message.text
    if not text:
        return

    parsed = ai.parse_task(text)
    if parsed is None:
        await update.message.reply_text(texts.ai_error())
        return

    employee_key = parsed.get("employee_key")
    title = (parsed.get("title") or text)[:120]
    description = parsed.get("description") or text
    deadline_days = parsed.get("deadline_days")
    if not isinstance(deadline_days, int):
        deadline_days = 1

    if employee_key not in _EMPLOYEE_KEYS and employee_key != "all":
        await update.message.reply_text(texts.manual_task_unclear())
        return

    targets = EMPLOYEES if employee_key == "all" else [
        next(e for e in EMPLOYEES if e.key == employee_key)
    ]

    deadline_at = datetime.now() + timedelta(days=deadline_days)
    deadline_str = deadline_at.strftime("%d.%m %H:%M")
    today_str = date.today().isoformat()

    for emp in targets:
        instance_id = db.create_manual_task(emp.key, title, description, deadline_at, today_str)
        employee_row = db.get_employee(emp.key)

        if employee_row and employee_row["chat_id"]:
            task_text = texts.manual_task_message(emp.full_name, title, description, deadline_str)
            buttons = [[InlineKeyboardButton("🖐 Принять", callback_data=f"accept:{instance_id}")]]
            if WEBAPP_URL:
                from telegram import WebAppInfo

                buttons.append(
                    [
                        InlineKeyboardButton(
                            "📋 Все мои задачи",
                            web_app=WebAppInfo(url=f"{WEBAPP_URL}/?tab=tasks"),
                        )
                    ]
                )
            keyboard = InlineKeyboardMarkup(buttons)
            try:
                await context.bot.send_message(
                    chat_id=employee_row["chat_id"], text=task_text, reply_markup=keyboard
                )
            except (Forbidden, BadRequest):
                pass
            confirm_text = texts.manual_task_confirmation(emp.full_name, title, deadline_str)
            confirm_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🚫 Отменить", callback_data=f"cancel:{instance_id}")]]
            )
            await update.message.reply_text(confirm_text, reply_markup=confirm_keyboard)
        else:
            await update.message.reply_text(texts.manual_task_unregistered(emp.full_name))


async def on_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    if not _is_admin(chat_id):
        await query.answer(texts.admin_only(), show_alert=True)
        return

    instance_id = int(query.data.split(":", 1)[1])
    instance = db.get_instance(instance_id)
    if instance is None:
        await query.answer("Задача не найдена.", show_alert=True)
        return

    ok = db.cancel_task(instance_id)
    if not ok:
        await query.answer(texts.task_already_uncancellable(), show_alert=True)
        return

    employee = db.get_employee(instance["employee_key"])
    if employee and employee["chat_id"]:
        try:
            await context.bot.send_message(
                chat_id=employee["chat_id"],
                text=texts.task_cancelled_employee(instance["title"]),
            )
        except (Forbidden, BadRequest):
            pass

    await query.answer("Отменено")
    await query.edit_message_text(f"🚫 Отменено: {instance['title']}")


async def on_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id

    employee = db.get_employee_by_chat(chat_id)
    if employee is None:
        await query.answer(texts.not_registered(), show_alert=True)
        return

    instance_id = int(query.data.split(":", 1)[1])
    instance = db.get_instance(instance_id)
    if instance is None or instance["employee_key"] != employee["key"]:
        await query.answer("Это не твоя задача.", show_alert=True)
        return

    ok = db.mark_accepted(instance_id, employee["key"])
    if not ok:
        await query.answer(texts.already_accepted(), show_alert=False)
        return

    await query.answer("Принято")
    await query.edit_message_text(texts.accept_confirm_employee(instance["title"]))

    if ADMIN_CHAT_ID is not None:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=texts.accept_confirm_admin(employee["full_name"], instance["title"]),
            )
        except (Forbidden, BadRequest):
            pass


async def ai_chat_reply(bot, employee, text: str) -> str:
    """Отвечает сотруднику через AI вне чек-ина. Сообщение сотрудника уже должно быть
    записано в chat_messages. Логирует ответ и пересылает вопрос+ответ руководителю."""
    today_str = date.today().isoformat()
    history = db.get_messages_for_day(employee["key"], today_str)
    tasks = db.get_tasks_for_employee_on(employee["key"], today_str)

    reply = await asyncio.to_thread(ai.employee_reply, employee["full_name"], history, tasks)
    if reply is None:
        reply = texts.employee_ai_unavailable()
    db.log_message(employee["key"], today_str, "bot", reply)

    if ADMIN_CHAT_ID is not None:
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=texts.employee_ai_dialog_admin(employee["full_name"], text, reply),
            )
        except (Forbidden, BadRequest):
            pass

    return reply


async def employee_message(bot, employee, text: str) -> str:
    """Общая обработка сообщения сотрудника (текст или расшифровка голосового) — и для
    обычного чата, и для мини-аппа: ответ в чек-ине либо свободный AI-чат. История
    пишется в chat_messages, поэтому оба канала видят одну и ту же переписку."""
    today_str = date.today().isoformat()
    db.log_message(employee["key"], today_str, "employee", text)

    session = db.get_active_session(employee["key"])
    if session is None:
        return await ai_chat_reply(bot, employee, text)

    reply, report = await checkins.handle_answer(session, employee, text)
    db.log_message(employee["key"], today_str, "bot", reply)

    if report is not None and ADMIN_CHAT_ID is not None:
        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
        except (Forbidden, BadRequest):
            pass

    return reply


async def employee_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if _is_admin(chat_id):
        return

    text = update.message.text
    if not text:
        return

    employee = db.get_employee_by_chat(chat_id)
    if employee is None:
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await employee_message(context.bot, employee, text)
    await update.message.reply_text(reply)


async def employee_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if _is_admin(chat_id):
        return

    employee = db.get_employee_by_chat(chat_id)
    if employee is None:
        await update.message.reply_text(texts.not_registered())
        return

    voice = update.message.voice
    if voice.duration and voice.duration > MAX_VOICE_SECONDS:
        await update.message.reply_text(texts.voice_too_long(MAX_VOICE_SECONDS // 60))
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    tg_file = await voice.get_file()
    audio = bytes(await tg_file.download_as_bytearray())
    text = await asyncio.to_thread(ai.transcribe, audio, "voice.ogg")
    if text is None:
        await update.message.reply_text(texts.voice_not_recognized())
        return

    await update.message.reply_text(texts.voice_transcript(text))
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await employee_message(context.bot, employee, text)
    await update.message.reply_text(reply)
