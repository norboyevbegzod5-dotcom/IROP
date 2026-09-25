import asyncio
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from bot import ai, checkins, db, knowledge, styles, texts
from bot.config import (
    ADMIN_CHAT_ID,
    AUTO_TASK_DEFAULT_DEADLINE,
    AUTO_TASK_DEFAULT_REMIND,
    AUTO_TASK_MAX_DAYS_AHEAD,
    AUTO_TASK_REMIND_BEFORE_MINUTES,
    BOT_NAME,
    EMPLOYEES,
    MAX_VOICE_SECONDS,
    WEBAPP_URL,
)

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
            f"Привет, руководитель. Я {BOT_NAME}. Команды:\n"
            f"/status — статус за сегодня\n"
            f"/team — кто из команды подключился\n"
            f"/style — характер AI-РОПа (строгий или мотиватор)\n"
            f"/learn — научить AI: факты, цены, скрипты, ответы на возражения\n"
            f"/knowledge — что AI уже знает; /forget номер — удалить запись\n\n"
            f"Чтобы AI отвечал как ты — отвечай на копии его диалогов своим вариантом."
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


def _style_menu():
    """Текст и кнопки выбора характера РОПа: по строке на сотрудника + «всем»."""
    rows = {r["key"]: r for r in db.all_employees()}
    lines = ["Характер AI-РОПа для каждого сотрудника:"]
    buttons = []
    for e in EMPLOYEES:
        current = styles.normalize(rows[e.key]["rop_style"] if e.key in rows else None)
        lines.append(f"• {e.full_name} — {styles.LABELS[current]}")
        buttons.append(
            [
                InlineKeyboardButton(
                    ("✓ " if s == current else "") + f"{e.full_name}: {label}",
                    callback_data=f"style:{e.key}:{s}",
                )
                for s, label in styles.LABELS.items()
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(f"Всем: {label}", callback_data=f"style:all:{s}")
            for s, label in styles.LABELS.items()
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def style_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text(texts.admin_only())
        return

    text, keyboard = _style_menu()
    await update.message.reply_text(text, reply_markup=keyboard)


async def on_style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.message.chat_id):
        await query.answer(texts.admin_only(), show_alert=True)
        return

    _, key, style = query.data.split(":", 2)
    if style not in styles.LABELS or (key != "all" and key not in _EMPLOYEE_KEYS):
        await query.answer()
        return

    for e in EMPLOYEES:
        if key in ("all", e.key):
            db.set_rop_style(e.key, style)

    await query.answer(f"Готово: {styles.LABELS[style]}")
    text, keyboard = _style_menu()
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest:
        pass  # ничего не изменилось — Telegram не даёт отредактировать тем же текстом


# ---------- обучение AI ----------

_AWAITING_KNOWLEDGE = "awaiting_knowledge"
_TELEGRAM_LIMIT = 4000  # с запасом до 4096


def _save_fact(text: str) -> str:
    knowledge_id = db.add_knowledge(knowledge.FACT, text.strip())
    total = sum(1 for r in db.all_knowledge() if r["kind"] == knowledge.FACT)
    return texts.learn_saved(knowledge_id, total)


async def learn_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text(texts.admin_only())
        return

    # /learn текст — сохранить сразу; просто /learn — ждём следующее сообщение.
    text = " ".join(context.args).strip() if context.args else ""
    if text:
        await update.message.reply_text(_save_fact(text))
        return
    context.user_data[_AWAITING_KNOWLEDGE] = True
    await update.message.reply_text(texts.learn_prompt())


async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        return
    context.user_data.pop(_AWAITING_KNOWLEDGE, None)
    await update.message.reply_text(texts.learn_cancelled())


def _short(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def knowledge_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text(texts.admin_only())
        return

    rows = db.all_knowledge()
    facts = [r for r in rows if r["kind"] == knowledge.FACT]
    examples = [r for r in rows if r["kind"] == knowledge.EXAMPLE]
    if not rows:
        await update.message.reply_text(texts.knowledge_empty())
        return

    lines = []
    if facts:
        lines.append(f"📚 База знаний ({len(facts)}):")
        lines += [f"#{r['id']} {_short(r['text'])}" for r in facts]
    if examples:
        lines.append(f"\n🎓 Твои образцы ответов ({len(examples)}):")
        lines += [
            f"#{r['id']} «{_short(r['question'], 70)}» → {_short(r['text'], 110)}"
            for r in examples
        ]
    lines.append("\nДобавить — /learn, удалить — /forget номер")

    # Длинный список режем на несколько сообщений по лимиту Telegram.
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > _TELEGRAM_LIMIT:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await update.message.reply_text(chunk)


async def forget_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text(texts.admin_only())
        return
    try:
        knowledge_id = int(context.args[0].lstrip("#"))
    except (IndexError, ValueError, AttributeError):
        await update.message.reply_text(texts.forget_usage())
        return
    if db.delete_knowledge(knowledge_id):
        await update.message.reply_text(texts.forget_done(knowledge_id))
    else:
        await update.message.reply_text(texts.forget_not_found(knowledge_id))


async def _save_correction(update: Update, dialog, text: str):
    """Руководитель ответил на копию диалога своим вариантом — сохраняем как образец."""
    knowledge_id = db.add_knowledge(knowledge.EXAMPLE, text.strip(), question=dialog["question"])
    employee = db.get_employee(dialog["employee_key"])
    keyboard = None
    if employee and employee["chat_id"]:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📨 Отправить сотруднику", callback_data=f"sendfix:{knowledge_id}:{employee['key']}")]]
        )
    await update.message.reply_text(
        texts.example_saved(knowledge_id, employee["full_name"] if employee else "сотруднику"),
        reply_markup=keyboard,
    )


async def on_sendfix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.message.chat_id):
        await query.answer(texts.admin_only(), show_alert=True)
        return

    _, knowledge_id, employee_key = query.data.split(":", 2)
    entry = db.get_knowledge(int(knowledge_id))
    employee = db.get_employee(employee_key)
    if entry is None or employee is None or not employee["chat_id"]:
        await query.answer("Не получилось отправить.", show_alert=True)
        return

    message = texts.correction_to_employee(entry["text"])
    db.log_message(employee["key"], date.today().isoformat(), "bot", message)
    try:
        await context.bot.send_message(chat_id=employee["chat_id"], text=message)
    except (Forbidden, BadRequest):
        await query.answer("Сотрудник недоступен.", show_alert=True)
        return

    await query.answer("Отправлено")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"📨 Отправил {employee['full_name']}.")


async def admin_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        return

    text = update.message.text
    if not text:
        return

    # 1) Ждём запись для базы знаний после /learn.
    if context.user_data.pop(_AWAITING_KNOWLEDGE, False):
        await update.message.reply_text(_save_fact(text))
        return

    # 2) Ответ на копию диалога «сотрудник → AI» — это исправление ответа AI.
    reply_to = update.message.reply_to_message
    if reply_to is not None:
        dialog = db.get_ai_dialog(reply_to.message_id)
        if dialog is not None:
            await _save_correction(update, dialog, text)
            return

    # 3) Иначе — поручение сотруднику.

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

    knowledge_block = knowledge.prompt_block(text)
    reply = await asyncio.to_thread(
        ai.employee_reply,
        employee["full_name"], history, tasks, employee["rop_style"], knowledge_block,
    )
    ai_answered = reply is not None
    if not ai_answered:
        reply = texts.employee_ai_unavailable()
    db.log_message(employee["key"], today_str, "bot", reply)

    if ADMIN_CHAT_ID is not None:
        try:
            copy = await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=texts.employee_ai_dialog_admin(
                    employee["full_name"], text, reply, can_correct=ai_answered
                ),
            )
            if ai_answered:
                # Руководитель может ответить на эту копию своим вариантом — так AI учится.
                db.save_ai_dialog(copy.message_id, employee["key"], text, reply)
        except (Forbidden, BadRequest):
            pass

    return reply


async def _answer_employee(bot, employee, text: str) -> str:
    """Ответ сотруднику: следующий шаг чек-ина либо свободный AI-чат."""
    session = db.get_active_session(employee["key"])
    if session is None:
        return await ai_chat_reply(bot, employee, text)

    reply, report = await checkins.handle_answer(session, employee, text)
    db.log_message(employee["key"], date.today().isoformat(), "bot", reply)

    if report is not None and ADMIN_CHAT_ID is not None:
        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
        except (Forbidden, BadRequest):
            pass

    return reply


async def _notify_admin(bot, text: str, keyboard=None):
    if ADMIN_CHAT_ID is None:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=keyboard)
    except (Forbidden, BadRequest):
        pass


def _auto_task_schedule(item, now: datetime):
    """(remind_at, deadline_at, текст срока, текст напоминания) для задачи из чата;
    None, если дата/время не разобрались или срок уже прошёл."""
    try:
        due_day = date.fromisoformat(item["due_date"])
        due_time = item.get("due_time")
        hh, mm = (int(x) for x in (due_time or AUTO_TASK_DEFAULT_DEADLINE).split(":"))
        deadline_at = datetime.combine(due_day, datetime.min.time()).replace(hour=hh, minute=mm)
    except (ValueError, TypeError, AttributeError):
        return None

    if deadline_at <= now or deadline_at > now + timedelta(days=AUTO_TASK_MAX_DAYS_AHEAD):
        return None

    if due_time:
        remind_at = deadline_at - timedelta(minutes=AUTO_TASK_REMIND_BEFORE_MINUTES)
        due_text = f"{deadline_at:%d.%m} в {deadline_at:%H:%M}"
        remind_text = f"за {AUTO_TASK_REMIND_BEFORE_MINUTES} минут"
    else:
        hh, mm = (int(x) for x in AUTO_TASK_DEFAULT_REMIND.split(":"))
        remind_at = deadline_at.replace(hour=hh, minute=mm)
        due_text = f"{deadline_at:%d.%m}, до конца дня"
        remind_text = f"{remind_at:%d.%m} в {remind_at:%H:%M}"
    if remind_at <= now:
        remind_at = now  # срок совсем скоро — напомним сразу при следующей проверке
        remind_text = "сразу"
    return remind_at, deadline_at, due_text, remind_text


async def process_tasks(bot, employee, text: str) -> list:
    """AI по сообщению сотрудника: закрывает выполненные задачи и ставит новые с
    конкретным сроком («Evos сказал перезвонить через 2 дня в 18:00»). Возвращает
    уведомления для сотрудника; руководителю уходят уведомления с кнопками отмены."""
    today_str = date.today().isoformat()
    now = datetime.now()
    open_tasks = db.get_open_tasks(employee["key"], today_str)

    # Контекст — несколько сообщений до текущего (само текущее уже записано последним).
    history = db.get_messages_for_day(employee["key"], today_str)[-7:-1]
    result = await asyncio.to_thread(ai.analyze_tasks, text, history, open_tasks, now)
    by_id = {t["id"]: t for t in open_tasks}

    notices = []
    for item in result["new_tasks"]:
        schedule = _auto_task_schedule(item, now)
        if schedule is None:
            continue
        remind_at, deadline_at, due_text, remind_text = schedule
        title = item["title"][:120]
        task_id = db.create_auto_task(
            employee["key"], title, item.get("description") or "", remind_at, deadline_at
        )
        notices.append(texts.auto_task_created_employee(title, due_text, remind_text))
        await _notify_admin(
            bot,
            texts.auto_task_created_admin(
                employee["full_name"], title, due_text, item.get("evidence", "")
            ),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("🚫 Отменить", callback_data=f"cancel:{task_id}")]]
            ),
        )

    for item in result["completed"]:
        task = by_id[item["task_id"]]
        if not db.close_task_by_ai(task["id"], employee["key"], item["evidence"]):
            continue
        notices.append(texts.task_autoclosed_employee(task["title"]))
        await _notify_admin(
            bot,
            texts.task_autoclosed_admin(employee["full_name"], task["title"], item["evidence"]),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("↩️ Вернуть в работу", callback_data=f"reopen:{task['id']}")]]
            ),
        )
    return notices


async def employee_message(bot, employee, text: str) -> list:
    """Общая обработка сообщения сотрудника (текст или расшифровка голосового) — и для
    обычного чата, и для мини-аппа. Возвращает сообщения бота по порядку: ответ AI и
    уведомления о задачах, которые AI закрыл по этому тексту. История пишется в
    chat_messages, поэтому оба канала видят одну и ту же переписку."""
    today_str = date.today().isoformat()
    db.log_message(employee["key"], today_str, "employee", text)

    # Ответ и проверка задач — параллельно, чтобы не удваивать ожидание.
    reply, notices = await asyncio.gather(
        _answer_employee(bot, employee, text), process_tasks(bot, employee, text)
    )
    for notice in notices:
        db.log_message(employee["key"], today_str, "bot", notice)
    return [reply] + notices


async def on_reopen_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.message.chat_id):
        await query.answer(texts.admin_only(), show_alert=True)
        return

    instance_id = int(query.data.split(":", 1)[1])
    instance = db.get_instance(instance_id)
    if instance is None or not db.reopen_task(instance_id):
        await query.answer("Задачу уже нельзя вернуть.", show_alert=True)
        return

    employee = db.get_employee(instance["employee_key"])
    if employee and employee["chat_id"]:
        notice = texts.task_reopened_employee(instance["title"])
        db.log_message(employee["key"], date.today().isoformat(), "bot", notice)
        try:
            await context.bot.send_message(chat_id=employee["chat_id"], text=notice)
        except (Forbidden, BadRequest):
            pass

    await query.answer("Возвращено в работу")
    await query.edit_message_text(f"↩️ Возвращено в работу: {instance['title']}")


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
    for message in await employee_message(context.bot, employee, text):
        await update.message.reply_text(message)


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
    for message in await employee_message(context.bot, employee, text):
        await update.message.reply_text(message)
