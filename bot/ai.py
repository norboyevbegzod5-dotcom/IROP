import json
import logging
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

logger = logging.getLogger(__name__)

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
                "metric": {
                    "type": "string",
                    "enum": [
                        "calls", "meetings_held", "meetings_new", "kp_count", "contracts_count",
                        "contracts_sum", "payments_sum", "new_connections", "none",
                    ],
                    "description": (
                        "Если задача — набрать количество по цифрам вечернего отчёта, то какое: "
                        "calls — звонки; meetings_held — проведённые встречи; meetings_new — "
                        "назначенные встречи (просто «встречи» = meetings_new); kp_count — "
                        "отправленные КП; contracts_count — договоры (штук); contracts_sum — сумма "
                        "договоров; payments_sum — поступившие деньги / продажи / «собрать N млн»; "
                        "new_connections — новые клиенты/подключения. Иначе (презентация, съездить "
                        "к клиенту, позвонить конкретному клиенту) — none."
                    ),
                },
                "target": {
                    "type": "integer",
                    "description": (
                        "Сколько нужно набрать по metric за весь срок. Деньги — в сумах целым "
                        "числом ('20 млн' = 20000000). Поручение 'всем' — цель на каждого. "
                        "Если metric=none — 0."
                    ),
                },
            },
            "required": ["employee_key", "title", "description", "deadline_days", "metric", "target"],
        },
    },
}


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
# Метка в ответе AI сотруднику: вопрос нужно передать руководителю. Бот её вырезает.
ESCALATE_MARK = "[[РУКОВОДИТЕЛЮ]]"


def _with_knowledge(prompt: str, knowledge_block: str) -> str:
    return f"{prompt}\n\n{knowledge_block}" if knowledge_block else prompt


def _employee_system_prompt(full_name: str, tasks, style: str, knowledge_block: str) -> str:
    if tasks:
        task_lines = "\n".join(f"- {t['title']} (статус: {t['status']})" for t in tasks)
    else:
        task_lines = "- задач на сегодня нет"
    return _with_knowledge(
        f"Ты — {BOT_NAME}, AI-ассистент РОПа (руководителя отдела продаж). С тобой в рабочем "
        f"чате пишет сотрудник отдела продаж {full_name}. Помогай ему по работе: как вести "
        "переговоры и отрабатывать возражения, как написать сообщение или КП клиенту, как "
        "спланировать день, что делать с его задачами. Отвечай на русском, коротко и по делу, "
        "как руководитель — без воды. Если вопрос требует решения руководителя "
        "(деньги, скидки, увольнение, конфликт), скажи, что передал вопрос руководителю, и "
        f"добавь в самый конец ответа отдельной строкой метку {ESCALATE_MARK}. "
        "Не выдумывай факты о клиентах, ценах и условиях компании.\n\n"
        f"{styles.PROMPTS[styles.normalize(style)]}\n\n"
        f"Задачи сотрудника на сегодня:\n{task_lines}",
        knowledge_block,
    )


def employee_reply(full_name: str, history, tasks, style: str, knowledge_block: str = ""):
    """Ответ AI сотруднику. history — сообщения чата за сегодня (sender, text),
    последнее из них — новое сообщение сотрудника. knowledge_block — база знаний и
    образцы ответов руководителя (bot/knowledge.py). None, если AI недоступен."""
    if not OPENAI_API_KEY:
        return None

    system = _employee_system_prompt(full_name, tasks, style, knowledge_block)
    messages = [{"role": "system", "content": system}]
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
                "metrics": {
                    "type": "object",
                    "description": (
                        "Только для итогов дня при finished=true: цифры за день из ответов "
                        "сотрудника. Суммы — в сумах целым числом. Если сотрудник не назвал "
                        "цифру — null, не выдумывай."
                    ),
                    "properties": {
                        "calls": {"type": ["integer", "null"], "description": "Звонков сделано"},
                        "meetings_held": {"type": ["integer", "null"], "description": "Встреч проведено"},
                        "meetings_new": {
                            "type": ["integer", "null"],
                            "description": (
                                "Новых встреч назначено. Учитывай и встречи, о которых сотрудник "
                                "рассказал позже в разговоре («позвал на встречу завтра в 17:00», "
                                "«uchrashuvga chaqirdim») — даже если на прямой вопрос ответил 0"
                            ),
                        },
                        "kp_count": {"type": ["integer", "null"], "description": "КП отправлено"},
                        "kp_sum": {"type": ["integer", "null"], "description": "Общая сумма КП"},
                        "contracts_count": {"type": ["integer", "null"], "description": "Договоров подписано"},
                        "contracts_sum": {"type": ["integer", "null"], "description": "Сумма договоров"},
                        "payments_sum": {"type": ["integer", "null"], "description": "Фактически поступило денег"},
                        "new_connections": {"type": ["integer", "null"], "description": "Новых клиентов подключено"},
                    },
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
    if context.get("plan"):
        prompt += (
            f"\n\n{context['plan']}\nСравнивай факт с этим планом: называй план и спрашивай "
            "факт («План был 40 звонков. Сколько фактически?»), по недовыполнению — почему "
            "и что конкретно будет сделано, с датой и временем."
        )
    prompt = _with_knowledge(prompt, context.get("knowledge") or "")
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
                "deals": {
                    "type": "array",
                    "description": "Клиенты/бренды и этап работы с ними; пустой массив, если их нет",
                    "items": {
                        "type": "object",
                        "properties": {
                            "brand": {
                                "type": "string",
                                "description": "Название клиента/бренда, как в сообщении: «Evos», «Nur Textile»",
                            },
                            "stage": {
                                "type": "string",
                                "enum": ["talk", "meeting", "kp", "contract", "paid", "lost"],
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Короткая дословная цитата из сообщения сотрудника",
                            },
                        },
                        "required": ["brand", "stage", "evidence"],
                    },
                },
            },
            "required": ["new_tasks", "completed", "deals"],
        },
    },
}

_TASKS_PROMPT = (
    "Ты ведёшь задачи и сделки сотрудника отдела продаж. Сообщения бывают на русском и "
    "узбекском (в том числе латиницей). По его ПОСЛЕДНЕМУ сообщению сделай три вещи.\n\n"
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
    "- сообщение про другого клиента или другую работу;\n"
    "- ответ на вопрос опроса (стендап, итоги дня): число («5», «10») или перечисление "
    "цифр за день — это отчёт, а не выполнение задачи;\n"
    "- будущее время по-узбекски (gaplashaman, qilaman, boraman, yuboraman — «сделаю») — "
    "это намерение.\n"
    "evidence — дословная цитата из ПОСЛЕДНЕГО сообщения, где прямо назван результат по "
    "этой задаче. Предыдущие сообщения даны только для понимания, о чём речь. Если "
    "сомневаешься — не закрывай. Никогда не выдумывай task_id: только из списка.\n\n"
    "3) deals — клиенты/бренды, про которые сотрудник пишет, и до какого этапа дошло:\n"
    "talk — поговорил или собирается говорить; meeting — встреча назначена или проведена; "
    "kp — КП отправлено; contract — договор подписан; paid — деньги поступили; lost — "
    "клиент отказал окончательно. «Не ответил», «попросил перезвонить» — это talk. "
    "Только реальные названия клиентов/компаний из ПОСЛЕДНЕГО сообщения; люди без "
    "компании (например, «с Фаррухом») — тоже клиент, если речь о продаже. Не выдумывай."
)


_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _squash(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("«", "").replace("»", "").replace('"', "").split())


def _quoted_from(evidence: str, message: str) -> bool:
    """Цитата-основание действительно взята из сообщения и не пустая/не голое число —
    иначе AI закрывал задачи по ответам «5» и «10» на вопросы итогов дня."""
    ev = _squash(evidence)
    return bool(ev) and any(ch.isalpha() for ch in ev) and ev in _squash(message)


def analyze_tasks(message: str, history, tasks, now: datetime) -> dict:
    """{"completed": [{"task_id", "evidence"}], "new_tasks": [{"title", "description",
    "due_date", "due_time", "evidence"}], "deals": [{"brand", "stage", "evidence"}]} по
    последнему сообщению сотрудника. tasks — его открытые задачи, history — предыдущие
    сообщения для контекста. При ошибке — пустые списки."""
    empty = {"completed": [], "new_tasks": [], "deals": []}
    if not OPENAI_API_KEY:
        return empty
    # Ответ из одних цифр («5», «3 ta») — это ответ на вопрос опроса: ни задач, ни сделок.
    if sum(ch.isalpha() for ch in message) < 4:
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
        and _quoted_from(item.get("evidence"), message)
    ]
    new_tasks = [
        item for item in args.get("new_tasks") or []
        if isinstance(item, dict) and item.get("title") and item.get("due_date")
    ]
    deals = [
        {"brand": str(item["brand"]).strip(), "stage": item["stage"]}
        for item in args.get("deals") or []
        if isinstance(item, dict) and str(item.get("brand") or "").strip()
        and item.get("stage") in ("talk", "meeting", "kp", "contract", "paid", "lost")
        and _quoted_from(item.get("evidence"), message)
    ]
    return {"completed": completed, "new_tasks": new_tasks, "deals": deals}


_ADMIN_HISTORY_LIMIT = 12


def _admin_chat_prompt(snapshot: str) -> str:
    names = "\n".join(f"- {e.full_name} -> employee_key={e.key}" for e in EMPLOYEES)
    return (
        f"Ты — {BOT_NAME}, AI-помощник руководителя отдела продаж. Руководитель пишет тебе "
        "в чат. Возможны два случая:\n"
        "1) Вопрос о работе команды («сколько встреч сегодня сделали ребята?», «кто не сдал "
        "итоги дня?», «как Аслбек по плану?»). Отвечай по ДАННЫМ ниже: коротко, по-русски, "
        "по каждому сотруднику отдельной строкой, с цифрами, клиентами и временем, если они "
        "есть. Если нужных данных нет — прямо скажи, чего нет и почему (например, итоги дня "
        "ещё не сданы, есть только утренний план). Никогда не выдумывай цифры.\n"
        "2) Поручение сотруднику («Аслбеку 150 звонков за неделю», «всем отправить отчёт») — "
        "вызови функцию assign_task. Вопрос — это не поручение.\n\n"
        f"Сотрудники:\n{names}\n\n"
        "Если явно сказано 'все'/'всем', employee_key='all'. Если имя не распознано, "
        "employee_key='unclear'. description в assign_task — готовая формулировка задачи от "
        "лица руководителя.\n\n"
        f"ДАННЫЕ:\n{snapshot}"
    )


def admin_chat(message: str, history, snapshot: str):
    """Ответ AI руководителю. Возвращает ("task", args) — если это поручение
    (args: employee_key, title, description, deadline_days), ("answer", текст) — если вопрос, None — при ошибке.
    history — предыдущие сообщения этого чата (sender 'admin'/'bot', text)."""
    if not OPENAI_API_KEY:
        return None

    messages = [{"role": "system", "content": _admin_chat_prompt(snapshot)}]
    for m in list(history)[-_ADMIN_HISTORY_LIMIT:]:
        messages.append({"role": "user" if m["sender"] == "admin" else "assistant", "content": m["text"]})
    messages.append({"role": "user", "content": message})

    try:
        resp = _get_client().chat.completions.create(
            model=OPENAI_MODEL, messages=messages, tools=[_TOOL], tool_choice="auto"
        )
    except Exception:
        return None

    reply = resp.choices[0].message
    if reply.tool_calls:
        try:
            return "task", json.loads(reply.tool_calls[0].function.arguments)
        except (ValueError, IndexError):
            return None
    text = (reply.content or "").strip()
    return ("answer", text) if text else None




# ---------- вечерняя сводка руководителю ----------

_DIGEST_PROMPT = (
    "Ты — опытный руководитель отдела продаж и помогаешь владельцу бизнеса разобрать день "
    "команды. На входе JSON с фактами за сегодня по каждому сотруднику: отчёт стендапа и "
    "отчёт итогов дня (тексты), цифры дня, средние цифры его обычного дня, план руководителя, "
    "серия сданных отчётов, пропуски, цели и зависшие сделки.\n\n"
    "Напиши выводы обычным текстом без Markdown (без *, #, _), строго в таком виде:\n"
    "🏆 Лучший день: одна строка — кто и чем отличился.\n\n"
    "⚠️ Обратить внимание:\n• до 4 пунктов\n\n"
    "💡 Что сделать завтра:\n• до 3 конкретных действий для руководителя\n\n"
    "Что искать: расхождение утреннего плана (с кем собирался говорить) и вечернего факта; "
    "заметную просадку против своего обычного дня; много звонков без встреч; отставание от "
    "плана и целей; сделки без движения; отсутствие отчёта. Опирайся только на данные, имена и цифры бери из них, "
    "не выдумывай. Общие итоги не повторяй — они уже есть в сообщении выше. Пиши коротко, "
    "по-деловому, по-русски. Если данных почти нет — скажи это одной строкой."
)


def daily_insights(facts: dict):
    """Выводы для вечерней сводки. None — если AI недоступен или ответил пусто."""
    if not OPENAI_API_KEY:
        return None
    try:
        resp = _get_client().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _DIGEST_PROMPT},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            max_tokens=700,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Не удалось получить выводы для сводки")
        return None
    return text or None
