def welcome_choose_name(bot_name: str) -> str:
    return (
        f"Привет, я {bot_name} — РОП. Буду ставить задачи, напоминать про дедлайны "
        f"и спрашивать результат.\n\nВыбери, кто ты:"
    )


def registered(full_name: str) -> str:
    return (
        f"Готово, {full_name}. С этого момента я слежу за твоими задачами и дедлайнами.\n"
        f"Команда /tasks — посмотреть задачи на сегодня."
    )


def slot_taken() -> str:
    return "Этот сотрудник уже зарегистрирован с другого аккаунта. Обратись к руководителю."


def not_registered() -> str:
    return "Ты ещё не зарегистрирован. Нажми /start и выбери своё имя."


def task_reminder(full_name: str, title: str, description: str, deadline_hhmm: str) -> str:
    text = f"📋 {full_name}, задача: {title}"
    if description:
        text += f"\n{description}"
    text += f"\n⏰ Дедлайн: {deadline_hhmm}\nОтметь выполнение кнопкой ниже."
    return text


def task_done_confirm(hhmm: str) -> str:
    return f"✅ Выполнено в {hhmm}"


def task_overdue_employee(title: str) -> str:
    return (
        f"🔴 Дедлайн по задаче «{title}» прошёл, отметки о выполнении нет. "
        f"Выполни и отпишись немедленно."
    )


def task_overdue_admin(full_name: str, title: str) -> str:
    return f"⚠️ {full_name} не выполнил в срок: «{title}»"


def today_tasks_header(date_str: str) -> str:
    return f"Задачи на {date_str}:"


def today_tasks_empty() -> str:
    return "На сегодня задач нет."


_STATUS_ICON = {"pending": "⏳", "done": "✅", "accepted": "🖐", "overdue": "🔴", "cancelled": "🚫"}


def task_line(t) -> str:
    icon = _STATUS_ICON.get(t["status"], "•")
    hhmm = t["remind_at"][11:16]
    return f"{icon} {hhmm} — {t['title']}"


def admin_summary_header(date_str: str) -> str:
    return f"📊 Итоги дня ({date_str}):"


def admin_summary_line(full_name: str, done: int, overdue: int, total: int) -> str:
    if total == 0:
        return f"— {full_name}: задач не было"
    icon = "✅" if done == total else ("🔴" if overdue else "⏳")
    line = f"{icon} {full_name}: {done}/{total} выполнено"
    if overdue:
        line += f", просрочено: {overdue}"
    return line


def team_line(full_name: str, registered_flag: bool) -> str:
    status = "зарегистрирован" if registered_flag else "ещё не подключился"
    return f"— {full_name}: {status}"


def admin_only() -> str:
    return "Эта команда доступна только руководителю."


def manual_task_message(full_name: str, title: str, description: str, deadline_hhmm: str) -> str:
    text = f"📋 {full_name}, новая задача: {title}"
    if description:
        text += f"\n{description}"
    text += f"\n⏰ Срок: {deadline_hhmm}\nПодтверди, что принял задачу, кнопкой ниже."
    return text


def manual_task_confirmation(full_name: str, title: str, deadline_str: str) -> str:
    return f"✅ Отправил {full_name}: «{title}»\nСрок: {deadline_str}"


def accept_confirm_employee(title: str) -> str:
    return f"🖐 Принято: {title}"


def accept_confirm_admin(full_name: str, title: str) -> str:
    return f"🖐 {full_name} принял задачу: «{title}»"


def already_accepted() -> str:
    return "Уже принято."


def manual_task_unregistered(full_name: str) -> str:
    return (
        f"⚠️ {full_name} ещё не подключился к боту (не жал /start) — "
        f"задача создана, придёт как только он зарегистрируется."
    )


def manual_task_unclear() -> str:
    return "Не понял, кому назначить задачу. Укажи явно: Саидмурод, Аслбек или Муроджон."


def ai_error() -> str:
    return "Не смог обработать через AI. Проверь OPENAI_API_KEY или переформулируй задачу."


def auto_task_reminder(title: str, description: str, due_hhmm: str) -> str:
    text = f"⏰ Напоминаю: {title} — в {due_hhmm}"
    if description:
        text += f"\n{description}"
    text += "\nКогда сделаешь — напиши в чат, как прошло, или отметь кнопкой."
    return text


def auto_task_created_employee(title: str, due: str, remind: str) -> str:
    return f"📅 Поставил задачу: {title} — {due}. Напомню {remind}."


def auto_task_created_admin(full_name: str, title: str, due: str, evidence: str) -> str:
    text = f"📅 AI поставил задачу {full_name}: «{title}» — {due}"
    if evidence:
        text += f"\nПо сообщению: «{evidence}»"
    return text


def task_autoclosed_employee(title: str) -> str:
    return f"✅ Закрыл задачу «{title}» — по твоему сообщению. Если рано — скажи руководителю."


def task_autoclosed_admin(full_name: str, title: str, evidence: str) -> str:
    text = f"✅ AI закрыл задачу у {full_name}: «{title}»"
    if evidence:
        text += f"\nОснование: «{evidence}»"
    return text


def task_reopened_employee(title: str) -> str:
    return f"↩️ Руководитель вернул задачу «{title}» в работу."


def voice_transcript(text: str) -> str:
    return f"🎤 {text}"


def voice_not_recognized() -> str:
    return "Не смог разобрать голосовое 😕 Попробуй ещё раз или напиши текстом."


def voice_too_long(minutes: int) -> str:
    return f"Голосовое слишком длинное — уложись, пожалуйста, в {minutes} минут."


def employee_ai_unavailable() -> str:
    return "Сейчас не могу ответить — передал сообщение руководителю, он ответит сам."


def employee_ai_dialog_admin(full_name: str, question: str, answer: str, can_correct: bool = False) -> str:
    text = f"💬 {full_name}: {question}\n\n🤖 Ответ AI: {answer}"
    if can_correct:
        text += "\n\n✍️ Ответь на это сообщение своим вариантом — AI запомнит, как отвечаешь ты."
    return text


def admin_panel_link(url: str) -> str:
    return (
        "📊 Админка: планы сотрудников и статистика по их отчётам.\n\n"
        f"{url}\n\n"
        "Это личная ссылка без пароля — не пересылай её сотрудникам."
    )


def admin_panel_no_url() -> str:
    return "Админка доступна, когда у бота есть публичный адрес (WEBAPP_URL / Render)."


# ---------- обучение AI ----------

def learn_prompt() -> str:
    return (
        "📚 Пришли одним сообщением то, что AI должен знать: факт о компании, цены, "
        "условия, скрипт или как отвечать на возражение. Можно переслать готовый текст.\n"
        "Отмена — /cancel"
    )


def learn_saved(knowledge_id: int, total: int) -> str:
    return f"📚 Запомнил (#{knowledge_id}). Записей в базе знаний: {total}. Список — /knowledge"


def learn_cancelled() -> str:
    return "Ок, ничего не сохранил."


def example_saved(knowledge_id: int, full_name: str) -> str:
    return (
        f"🎓 Запомнил твой ответ как образец (#{knowledge_id}). В похожих ситуациях AI будет "
        f"отвечать так же. Отправить этот ответ {full_name}?"
    )


def correction_to_employee(text: str) -> str:
    return f"💬 Руководитель: {text}"


def forget_usage() -> str:
    return "Напиши номер записи: /forget 12. Номера — в /knowledge."


def forget_done(knowledge_id: int) -> str:
    return f"🗑 Удалил запись #{knowledge_id}."


def forget_not_found(knowledge_id: int) -> str:
    return f"Записи #{knowledge_id} нет. Номера — в /knowledge."


def knowledge_empty() -> str:
    return (
        "📚 База знаний пока пустая.\n\n"
        "Как обучать AI:\n"
        "• /learn — добавить факт, цены, скрипт, ответ на возражение;\n"
        "• ответь на копию диалога «💬 сотрудник → 🤖 ответ AI» своим вариантом — AI "
        "запомнит его как образец;\n"
        "• /forget 12 — удалить запись."
    )


def task_cancelled_employee(title: str) -> str:
    return f"🚫 Задача «{title}» отменена руководителем, можешь не выполнять."


def task_already_uncancellable() -> str:
    return "Эту задачу уже нельзя отменить (выполнена или уже отменена)."


def checkin_overdue_employee(title: str) -> str:
    return f"🔴 Не закончил «{title}» — ответь на вопрос, чтобы продолжить."


def checkin_overdue_admin(full_name: str, title: str) -> str:
    return f"⚠️ {full_name} не завершил «{title}» вовремя."
