# Обучение AI руководителем: база знаний (факты о компании, продуктах, ценах, скриптах)
# и образцы ответов руководителя. Перед ответом сотруднику сюда подбираются записи,
# относящиеся к его вопросу, и подставляются в промпт AI.

import re

from bot import db

FACT = "fact"
EXAMPLE = "example"

# Сколько текста базы знаний и сколько образцов отправлять в AI за раз.
MAX_FACTS_CHARS = 6000
MAX_EXAMPLES = 5

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _stems(text: str) -> set:
    # Грубая «основа» слова — первые 5 букв: «клиенту»/«клиентов» → «клиен».
    # Короткие слова (предлоги, «да», «не») не учитываем.
    return {w[:5] for w in _WORD.findall((text or "").lower()) if len(w) >= 4}


def _score(query_stems: set, text: str) -> int:
    return len(query_stems & _stems(text))


def select(query: str):
    """(факты, образцы) для промпта. Если база маленькая — отдаём все факты, иначе
    самые близкие к query; образцы — до MAX_EXAMPLES самых похожих на вопрос."""
    rows = db.all_knowledge()
    facts = [r for r in rows if r["kind"] == FACT]
    examples = [r for r in rows if r["kind"] == EXAMPLE]
    q = _stems(query)

    if sum(len(f["text"]) for f in facts) > MAX_FACTS_CHARS:
        # Сначала релевантные, при равенстве — свежие.
        facts = sorted(facts, key=lambda f: (_score(q, f["text"]), f["id"]), reverse=True)
    picked_facts, used = [], 0
    for f in facts:
        if used + len(f["text"]) > MAX_FACTS_CHARS:
            continue
        picked_facts.append(f["text"])
        used += len(f["text"])

    ranked = sorted(
        examples,
        key=lambda e: (_score(q, (e["question"] or "") + " " + e["text"]), e["id"]),
        reverse=True,
    )
    picked_examples = [(e["question"] or "", e["text"]) for e in ranked[:MAX_EXAMPLES]]
    return picked_facts, picked_examples


def prompt_block(query: str) -> str:
    """Готовый блок для системного промпта AI; пустая строка, если обучать ещё нечем."""
    facts, examples = select(query)
    parts = []
    if facts:
        parts.append(
            "База знаний компании от руководителя. Это факты — опирайся на них, не "
            "противоречь им и не выдумывай того, чего здесь нет:\n"
            + "\n".join(f"- {f}" for f in facts)
        )
    if examples:
        parts.append(
            "Так отвечает сам руководитель. Перенимай его манеру, формулировки и позицию; "
            "в похожей ситуации отвечай так же по сути:\n"
            + "\n\n".join(f"Сотрудник: {q}\nРуководитель: {a}" for q, a in examples)
        )
    return "\n\n".join(parts)
