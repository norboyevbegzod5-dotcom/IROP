import json

from openai import OpenAI

from bot.config import BOT_NAME, EMPLOYEES, OPENAI_API_KEY, OPENAI_MODEL

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


_TOOL = {
    "type": "function",
    "function": {
        "name": "assign_task",
        "description": (
            "Извлечь структурированную задачу для сотрудника отдела продаж из текста руководителя."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "employee_key": {
                    "type": "string",
                    "enum": [e.key for e in EMPLOYEES] + ["all", "unclear"],
                    "description": "Кому назначена задача",
                },
                "title": {
                    "type": "string",
                    "description": "Короткое название задачи, 3-6 слов",
                },
                "description": {
                    "type": "string",
                    "description": "Формулировка задачи для сотрудника, как будто её пишет руководитель",
                },
                "deadline_days": {
                    "type": "integer",
                    "description": (
                        "Через сколько дней дедлайн. 'недельная'/'на неделю' = 7, "
                        "'месячная' = 30, 'сегодня' = 0, 'завтра' = 1, если не указано = 1"
                    ),
                },
            },
            "required": ["employee_key", "title", "description", "deadline_days"],
        },
    },
}


def _system_prompt() -> str:
    names = "\n".join(f"- {e.full_name} -> employee_key={e.key}" for e in EMPLOYEES)
    return (
        "Ты — ассистент РОПа (руководителя отдела продаж). Руководитель пишет тебе короткие "
        "поручения на русском в свободной форме, ты превращаешь их в структурированную задачу "
        "для конкретного сотрудника через функцию assign_task.\n\n"
        f"Сотрудники:\n{names}\n\n"
        "Если явно сказано 'все'/'всем', employee_key='all'. "
        "Если имя не распознано или неоднозначно, employee_key='unclear'. "
        "description должен быть готовой формулировкой задачи, которую сотрудник получит "
        "в Telegram от лица руководителя — по-деловому и конкретно."
    )


_HISTORY_LIMIT = 20


def _employee_system_prompt(full_name: str, tasks) -> str:
    if tasks:
        task_lines = "\n".join(f"- {t['title']} (статус: {t['status']})" for t in tasks)
    else:
        task_lines = "- задач на сегодня нет"
    return (
        f"Ты — {BOT_NAME}, AI-ассистент РОПа (руководителя отдела продаж). С тобой в рабочем "
        f"чате пишет сотрудник отдела продаж {full_name}. Помогай ему по работе: как вести "
        "переговоры и отрабатывать возражения, как написать сообщение или КП клиенту, как "
        "спланировать день, что делать с его задачами. Отвечай на русском, коротко и по делу, "
        "дружелюбно, но как руководитель — без воды. Если вопрос требует решения руководителя "
        "(деньги, скидки, увольнение, конфликт), скажи, что передал вопрос руководителю. "
        "Не выдумывай факты о клиентах, ценах и условиях компании.\n\n"
        f"Задачи сотрудника на сегодня:\n{task_lines}"
    )


def employee_reply(full_name: str, history, tasks):
    """Ответ AI сотруднику. history — сообщения чата за сегодня (sender, text),
    последнее из них — новое сообщение сотрудника. None, если AI недоступен."""
    if not OPENAI_API_KEY:
        return None

    messages = [{"role": "system", "content": _employee_system_prompt(full_name, tasks)}]
    for m in list(history)[-_HISTORY_LIMIT:]:
        role = "user" if m["sender"] == "employee" else "assistant"
        messages.append({"role": role, "content": m["text"]})

    try:
        resp = _get_client().chat.completions.create(model=OPENAI_MODEL, messages=messages)
    except Exception:
        return None

    return (resp.choices[0].message.content or "").strip() or None


def parse_task(admin_text: str):
    if not OPENAI_API_KEY:
        return None

    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": admin_text},
            ],
            tools=[_TOOL],
            tool_choice={"type": "function", "function": {"name": "assign_task"}},
        )
    except Exception:
        return None

    message = resp.choices[0].message
    if not message.tool_calls:
        return None

    try:
        return json.loads(message.tool_calls[0].function.arguments)
    except (ValueError, IndexError):
        return None
