# Мини-CRM: сделки по клиентам/брендам. AI вытаскивает клиентов и этап из сообщений
# сотрудника (стендап, итоги дня, обычный чат) — так доска собирается сама. Сотрудник
# может двигать сделку и вручную в мини-аппе. Зависшие сделки попадают в сводку.

from datetime import date, datetime

from bot import db

# Этапы по порядку; lost — отдельно, вне воронки.
STAGES = ["talk", "meeting", "kp", "contract", "paid"]
LOST = "lost"
LABELS = {
    "talk": "Разговор",
    "meeting": "Встреча",
    "kp": "КП",
    "contract": "Договор",
    "paid": "Оплата",
    LOST: "Отказ",
}
ICONS = {"talk": "📞", "meeting": "🤝", "kp": "📄", "contract": "✍️", "paid": "💰", LOST: "✖️"}
# Без движения дольше — сделка «зависла» (подсветка в мини-аппе, админке и сводке).
STALE_DAYS = 5
# Закрытые сделки (оплата/отказ) показываем на доске ещё столько дней.
CLOSED_VISIBLE_DAYS = 14


def brand_key(brand: str) -> str:
    return " ".join(brand.casefold().split())


def is_open(stage: str) -> bool:
    return stage not in ("paid", LOST)


def days_on_stage(deal, today: date = None) -> int:
    today = today or date.today()
    return (today - datetime.fromisoformat(deal["stage_changed_at"]).date()).days


def is_stale(deal, today: date = None) -> bool:
    return is_open(deal["stage"]) and days_on_stage(deal, today) >= STALE_DAYS


def apply_mentions(employee_key: str, mentions: list) -> list:
    """Обновляет доску по упоминаниям из сообщения ({brand, stage}). Сделка двигается
    только вперёд (отказ — с любого этапа; новый интерес после отказа — снова в работу).
    -> [(deal_id, brand, stage, created)] — что изменилось."""
    changes = []
    for m in mentions:
        brand = " ".join(m["brand"].split())[:80]
        key, stage = brand_key(brand), m["stage"]
        deal = db.find_deal(employee_key, key)
        if deal is None:
            deal_id = db.create_deal(employee_key, brand, key, stage)
            if deal_id:
                changes.append((deal_id, brand, stage, True))
            continue
        current = deal["stage"]
        if stage == current:
            continue
        forward = (
            stage == LOST and current != "paid"
            or current == LOST and stage != LOST
            or stage in STAGES and current in STAGES and STAGES.index(stage) > STAGES.index(current)
        )
        if forward:
            db.set_deal_stage(deal["id"], stage)
            changes.append((deal["id"], deal["brand"], stage, False))
    return changes


def change_notice(changes: list) -> str:
    """Одно сообщение сотруднику обо всех изменениях на доске."""
    parts = [f"{b} → {LABELS[s]}" + (" (новая)" if created else "") for _, b, s, created in changes]
    return "📇 Сделки: " + "; ".join(parts)


def move(deal_id: int, employee_key: str, stage: str):
    """Ручное перемещение из мини-аппа (на любой этап). -> строка сделки или None."""
    deal = db.get_deal(deal_id)
    if deal is None or deal["employee_key"] != employee_key or stage not in LABELS:
        return None
    if stage != deal["stage"]:
        db.set_deal_stage(deal_id, stage)
    return db.get_deal(deal_id)


def board(employee_key: str = None, today: date = None) -> list:
    """Сделки для доски: открытые + недавно закрытые."""
    today = today or date.today()
    result = []
    for d in db.get_deals(employee_key):
        days = days_on_stage(d, today)
        if not is_open(d["stage"]) and days > CLOSED_VISIBLE_DAYS:
            continue
        result.append({
            "id": d["id"],
            "employee_key": d["employee_key"],
            "brand": d["brand"],
            "stage": d["stage"],
            "stage_label": LABELS[d["stage"]],
            "days": days,
            "stale": is_stale(d, today),
        })
    return result


def stale(today: date = None) -> list:
    today = today or date.today()
    return [d for d in board(today=today) if d["stale"]]
