# Диалоговые чек-ины: РОП задаёт вопросы по одному, сотрудник отвечает текстом,
# в конце готовый отчёт уходит руководителю.

STANDUP = "standup"
EVENING = "evening"

TITLES = {
    STANDUP: "Стендап",
    EVENING: "Итоги дня",
}

QUESTIONS = {
    STANDUP: [
        "С какими клиентами ты вчера поговорил?",
        "Что они ответили?",
        "С какими брендами будешь разговаривать сегодня?",
    ],
    EVENING: [
        "Сколько звонков ты сделал и кому?",
        "Сколько встреч назначил?",
        "Сколько КП отправил?",
        "Сколько договоров отправил?",
        "Сколько денег поступило от клиента?",
    ],
}

# days_of_week: 0=Пн ... 6=Вс
SCHEDULE = {
    STANDUP: {"days": {0, 1, 2, 3, 4, 5}, "time": "09:00", "deadline_minutes": 30},
    EVENING: {"days": {0, 1, 2, 3, 4, 5}, "time": "18:30", "deadline_minutes": 90},
}


def build_report(full_name: str, kind: str, session_date: str, answers: list) -> str:
    lines = [f"📋 {TITLES[kind]} — {full_name} ({session_date})"]
    for i, (question, answer) in enumerate(zip(QUESTIONS[kind], answers), start=1):
        lines.append(f"{i}) {question}\n— {answer}")
    return "\n".join(lines)
