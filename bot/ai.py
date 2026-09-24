import json
from datetime import datetime

from openai import OpenAI

from bot import styles
from bot.config import (
    BOT_NAME,
    EMPLOYEES,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TRANSCRIBE_MODEL,
)

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


def transcribe(audio: bytes, filename: str):
    """Расшифровка голосового в текст. filename нужен OpenAI, чтобы понять формат
    (voice.ogg из Telegram, voice.webm/voice.mp4 из мини-аппа). None при ошибке."""
    if not OPENAI_API_KEY or not audio:
        return None
    try:
        resp = _get_client().audio.transcriptions.create(
            model=OPENAI_TRANSCRIBE_MODEL,
            file=(filename, audio),
            prompt="Разговор сотрудника отдела продаж: клиенты, звонки, встречи, КП, договоры.",
        )
    except Exception:
        return None
    return (resp.text or "").strip() or None


_HISTORY_LIMIT = 20


def _employee_system_prompt(full_name: str, tasks, style: str) -> str:
    if tasks:
        task_lines = "\n".join(f"- {t['title']} (статус: {t['status']})" for t in tasks)
    else:
        task_lines = "- задач на сегодня нет"
    return (
        f"Ты — {BOT_NAME}, AI-ассистент РОПа (руководителя отдела продаж). С тобой в рабочем "
        f"чате пишет сотрудник отдела продаж {full_name}. Помогай ему по работе: как вести "
        "переговоры и отрабатывать возражения, как написать сообщение или КП клиенту, как "
        "спланировать день, что делать с его задачами. Отвечай на русском, коротко и по делу, "
        "как руководитель — без воды. Если вопрос требует решения руководителя "
        "(деньги, скидки, увольнение, конфликт), скажи, что передал вопрос руководителю. "
        "Не выдумывай факты о клиентах, ценах и условиях компании.\n\n"
        f"{styles.PROMPTS[styles.normalize(style)]}\n\n"
        f"Задачи сотрудника на сегодня:\n{task_lines}"
    )


def employee_reply(full_name: str, history, tasks, style: str):
    """Ответ AI сотруднику. history — сообщения чата за сегодня (sender, text),
    последнее из них — новое сообщение сотрудника. None, если AI недоступен."""
    if not OPENAI_API_KEY:
        return None

    messages = [
        {"role": "system", "content": _employee_system_prompt(full_name, tasks, style)}
    ]
    for m in list(history)[-_HISTORY_LIMIT:]:
        role = "user" if m["sender"] == "employee" else "assistant"
        messages.append({"role": role, "content": m["text"]})

    try:
        resp = _get_client().chat.completions.create(model=OPENAI_MODEL, messages=messages)
    except Exception:
        return None

    return (resp.choices[0].message.content or "").strip() or None


_CHECKIN_TOOL = {
    "type": "function",
    "function": {
        "name": "checkin_step",
        "description": "Следующий шаг чек-ина: задать вопрос или завершить с отчётом.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Сообщение сотруднику: следующий вопрос, либо короткий "
                        "фидбэк по итогам в своём стиле, если finished=true"
                    ),
                },
                "finished": {
                    "type": "boolean",
                    "description": "true — всё нужное выяснено, чек-ин окончен",
                },
                "report": {
                    "type": "string",
                    "description": (
                        "Только при finished=true: отчёт для руководителя — сжато, по "
                        "пунктам, с цифрами и именами клиентов из ответов. Без выдумок; "
                        "если что-то сотрудник не сообщил — так и написать."
                    ),
                },
            },
            "required": ["message", "finished"],
        },
    },
}


def _checkin_system_prompt(full_name, title, goal, context, must_finish) -> str:
    tasks = context.get("tasks") or []
    task_lines = "\n".join(f"- {t['title']} (статус: {t['status']})" for t in tasks) or "- нет"
    previous = context.get("previous_report") or "нет"
    prompt = (
        f"Ты — {BOT_NAME}, РОП (руководитель отдела продаж). Ты проводишь «{title}» с "
        f"сотрудником {full_name} в чате.\n\n"
        f"Цель разговора: {goal}\n\n"
        "Правила:\n"
        "- Задавай ровно один вопрос за сообщение, коротко, по-русски, на «ты», в своём стиле.\n"
        "- Каждый следующий вопрос строй из предыдущих ответов: уточняй расплывчатое "
        "(«несколько» — сколько? «клиент» — какой?), не спрашивай то, что уже сказано.\n"
        "- Если сотрудник сам ответил на несколько пунктов сразу — не переспрашивай их.\n"
        "- Первое сообщение начни с короткого приветствия и сразу первого вопроса.\n"
        "- Не давай развёрнутых оценок посреди опроса, сначала собери информацию.\n"
        "- Когда всё из цели выяснено — finished=true и заполни report. В message дай "
        "короткий фидбэк (2-4 предложения) по цифрам и фактам из разговора в своём стиле и "
        "один конкретный ориентир на следующий шаг.\n\n"
        f"{styles.PROMPTS[styles.normalize(context.get('style'))]}\n\n"
        f"Предыдущий отчёт сотрудника:\n{previous}\n\n"
        f"Задачи сотрудника на сегодня:\n{task_lines}"
    )
    if must_finish:
        prompt += "\n\nВопросов уже достаточно: завершай сейчас (finished=true) с отчётом."
    return prompt


def checkin_step(full_name, title, goal, turns, context, must_finish):
    """Следующий шаг чек-ина от AI: {"message", "finished", "report"}. turns — диалог
    сессии (sender, text); пустой список — нужно первое сообщение. None при ошибке."""
    if not OPENAI_API_KEY:
        return None

    messages = [
        {
            "role": "system",
            "content": _checkin_system_prompt(full_name, title, goal, context, must_finish),
        }
    ]
    for t in turns:
        role = "user" if t["sender"] == "employee" else "assistant"
        messages.append({"role": role, "content": t["text"]})
    if not turns:
        messages.append({"role": "user", "content": "(сотрудник открыл чат, начинай)"})

    try:
        resp = _get_client().chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=[_CHECKIN_TOOL],
            tool_choice={"type": "function", "function": {"name": "checkin_step"}},
        )
        call = resp.choices[0].message.tool_calls[0]
        step = json.loads(call.function.arguments)
    except Exception:
        return None

    if not isinstance(step, dict) or not (step.get("message") or "").strip():
        return None
    step["finished"] = bool(step.get("finished"))
    return step


_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "update_tasks",
        "description": (
            "По последнему сообщению сотрудника: какие открытые задачи он выполнил и какие "
            "новые задачи с конкретным сроком из него следуют."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "new_tasks": {
                    "type": "array",
                    "description": "Новые задачи; пустой массив, если таких нет",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Коротко, начиная с глагола: «Перезвонить Evos»",
                            },
                            "description": {
                                "type": "string",
                                "description": "Контекст: что сказал клиент и о чём говорить",
                            },
                            "due_date": {
                                "type": "string",
                                "description": "Дата выполнения, YYYY-MM-DD",
                            },
                            "due_time": {
                                "type": ["string", "null"],
                                "description": "Время HH:MM, если названо или однозначно следует "
                                "из текста («утром» = 10:00, «после обеда» = 14:00), иначе null",
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Короткая дословная цитата из сообщения сотрудника",
                            },
                        },
                        "required": ["title", "description", "due_date", "due_time", "evidence"],
                    },
                },
                "completed": {
                    "type": "array",
                    "description": "Выполненные задачи; пустой массив, если таких нет",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "integer"},
                            "evidence": {
                                "type": "string",
                                "description": "Короткая дословная цитата из сообщения сотрудника",
                            },
                        },
                        "required": ["task_id", "evidence"],
                    },
                },
            },
            "required": ["new_tasks", "completed"],
        },
    },
}

_TASKS_PROMPT = (
    "Ты ведёшь задачи сотрудника отдела продаж. По его ПОСЛЕДНЕМУ сообщению сделай две вещи.\n\n"
    "1) new_tasks — поставь задачу, если из сообщения следует конкретное действие "
    "сотрудника с конкретным сроком: клиент попросил перезвонить/написать/прислать КП "
    "к определённому времени, договорились о встрече, сотрудник сам обещает что-то к "
    "определённому дню («Evos сказал перезвонить через 2 дня в 18:00» → «Перезвонить Evos», "
    "дата = сегодня + 2 дня, 18:00).\n"
    "- Относительные сроки («завтра», «через 2 дня», «в пятницу», «на следующей неделе» = "
    "понедельник) считай от текущей даты и времени ниже. Срок должен быть в будущем.\n"
    "- Без срока («надо бы позвонить», «как-нибудь») — задачу НЕ ставь.\n"
    "- Не дублируй: если такая задача уже есть в открытых — не ставь снова.\n"
    "- Уже сделанное («перезвонил», «отправил») — это не новая задача.\n\n"
    "2) completed — какие из открытых задач он выполнил ПОЛНОСТЬЮ.\n"
    "Закрывай задачу, только если сотрудник прямо сообщает о свершившемся результате, "
    "который целиком покрывает задачу («отправил КП в Makro», «договор с Evos подписан», "
    "«отчёт скинул»).\n"
    "НЕ закрывай, если это:\n"
    "- план или намерение («сегодня отправлю», «буду звонить»);\n"
    "- частичный прогресс («сделал 40 звонков» при задаче на 150; «начал готовить КП»);\n"
    "- упоминание задачи без результата, вопрос о ней или отказ клиента, если задача была "
    "добиться результата;\n"
    "- сообщение про другого клиента или другую работу.\n"
    "Предыдущие сообщения даны только для понимания, о чём речь. Если сомневаешься — "
    "не закрывай. Никогда не выдумывай task_id: только из списка."
)


_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def analyze_tasks(message: str, history, tasks, now: datetime) -> dict:
    """{"completed": [{"task_id", "evidence"}], "new_tasks": [{"title", "description",
    "due_date", "due_time", "evidence"}]} по последнему сообщению сотрудника.
    tasks — его открытые задачи, history — предыдущие сообщения для контекста.
    При ошибке — пустые списки."""
    empty = {"completed": [], "new_tasks": []}
    if not OPENAI_API_KEY:
        return empty

    task_lines = "\n".join(
        f"- task_id={t['id']}: {t['title']} (срок {t['deadline_at'][:16].replace('T', ' ')})"
        + (f" — {t['description']}" if t["description"] and t["description"] != t["title"] else "")
        for t in tasks
    ) or "— нет"
    context_lines = "\n".join(
        f"{'Сотрудник' if m['sender'] == 'employee' else 'РОП'}: {m['text']}" for m in history
    ) or "—"

    try:
        resp = _get_client().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _TASKS_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Сейчас: {now:%Y-%m-%d %H:%M}, {_WEEKDAYS[now.weekday()]}.\n\n"
                        f"Открытые задачи:\n{task_lines}\n\n"
                        f"Предыдущие сообщения:\n{context_lines}\n\n"
                        f"ПОСЛЕДНЕЕ сообщение сотрудника:\n{message}"
                    ),
                },
            ],
            tools=[_TASKS_TOOL],
            tool_choice={"type": "function", "function": {"name": "update_tasks"}},
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
    except Exception:
        return empty

    valid_ids = {t["id"] for t in tasks}
    completed = [
        {"task_id": item["task_id"], "evidence": str(item.get("evidence", ""))}
        for item in args.get("completed") or []
        if isinstance(item, dict) and item.get("task_id") in valid_ids
    ]
    new_tasks = [
        item for item in args.get("new_tasks") or []
        if isinstance(item, dict) and item.get("title") and item.get("due_date")
    ]
    return {"completed": completed, "new_tasks": new_tasks}


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
