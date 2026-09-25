import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from bot.config import DB_PATH, EMPLOYEES
from bot.templates import TASK_TEMPLATES

# Старые ежедневные шаблоны, заменённые диалоговыми чек-инами (bot/checkins.py).
_RETIRED_TEMPLATE_TITLES = (
    "Утренний план на день",
    "Промежуточный отчёт",
    "Итоговый отчёт за день",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    key TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    chat_id INTEGER UNIQUE,
    rop_style TEXT NOT NULL DEFAULT 'motivator'
);

CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    days_of_week TEXT NOT NULL,
    remind_time TEXT NOT NULL,
    deadline_minutes INTEGER NOT NULL DEFAULT 60,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    employee_key TEXT NOT NULL,
    task_date TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    remind_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TEXT,
    reminded INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'template',
    UNIQUE(template_id, employee_key, task_date)
);

CREATE TABLE IF NOT EXISTS checkin_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_key TEXT NOT NULL,
    session_date TEXT NOT NULL,
    kind TEXT NOT NULL,
    question_index INTEGER NOT NULL DEFAULT 0,
    answers TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'in_progress',
    started_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    nagged INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    report TEXT,
    UNIQUE(employee_key, session_date, kind)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_key TEXT NOT NULL,
    message_date TEXT NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Обучение AI руководителем: kind='fact' — запись базы знаний (text),
-- kind='example' — образец ответа руководителя (question -> text).
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    question TEXT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Копии диалогов «вопрос сотрудника → ответ AI», отправленные руководителю:
-- по admin_message_id узнаём, на какой диалог руководитель ответил исправлением.
CREATE TABLE IF NOT EXISTS ai_dialogs (
    admin_message_id INTEGER PRIMARY KEY,
    employee_key TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_TASK_INSTANCES_FRESH_SQL = """
CREATE TABLE task_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    employee_key TEXT NOT NULL,
    task_date TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    remind_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TEXT,
    reminded INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'template',
    UNIQUE(template_id, employee_key, task_date)
);
"""


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate_task_instances(conn)
        _migrate_task_autoclose(conn)
        _migrate_checkin_sessions(conn)
        _migrate_employees(conn)
        _seed_employees(conn)
        _seed_templates(conn)
        _retire_old_templates(conn)


def _retire_old_templates(conn):
    placeholders = ",".join("?" * len(_RETIRED_TEMPLATE_TITLES))
    conn.execute(
        f"UPDATE task_templates SET active = 0 WHERE title IN ({placeholders}) AND active = 1",
        _RETIRED_TEMPLATE_TITLES,
    )


def _migrate_task_instances(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_instances)").fetchall()}
    if "source" in cols:
        return
    conn.execute("ALTER TABLE task_instances RENAME TO task_instances_old")
    conn.execute(_TASK_INSTANCES_FRESH_SQL)
    conn.execute(
        """INSERT INTO task_instances
           (id, template_id, employee_key, task_date, title, description,
            remind_at, deadline_at, status, completed_at, reminded, source)
           SELECT id, template_id, employee_key, task_date, title, description,
                  remind_at, deadline_at, status, completed_at, reminded, 'template'
           FROM task_instances_old"""
    )
    conn.execute("DROP TABLE task_instances_old")


def _migrate_task_autoclose(conn):
    # closed_by: 'ai', если задачу закрыл AI по тексту сотрудника; close_note — цитата-
    # основание; prev_status — статус до закрытия, чтобы руководитель мог вернуть задачу.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_instances)").fetchall()}
    for col in ("closed_by", "close_note", "prev_status"):
        if col not in cols:
            conn.execute(f"ALTER TABLE task_instances ADD COLUMN {col} TEXT")


def _migrate_employees(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(employees)").fetchall()}
    if "rop_style" not in cols:
        conn.execute(
            "ALTER TABLE employees ADD COLUMN rop_style TEXT NOT NULL DEFAULT 'motivator'"
        )


def _migrate_checkin_sessions(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checkin_sessions)").fetchall()}
    if "report" not in cols:
        conn.execute("ALTER TABLE checkin_sessions ADD COLUMN report TEXT")


def _seed_employees(conn):
    for e in EMPLOYEES:
        conn.execute(
            "INSERT OR IGNORE INTO employees (key, full_name, chat_id) VALUES (?, ?, NULL)",
            (e.key, e.full_name),
        )


def _seed_templates(conn):
    existing = conn.execute("SELECT COUNT(*) AS c FROM task_templates").fetchone()["c"]
    if existing:
        return
    for t in TASK_TEMPLATES:
        conn.execute(
            """INSERT INTO task_templates
               (employee_key, title, description, days_of_week, remind_time, deadline_minutes, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (
                t["employee_key"],
                t["title"],
                t.get("description", ""),
                ",".join(str(d) for d in t["days_of_week"]),
                t["remind_time"],
                t["deadline_minutes"],
            ),
        )


# ---------- employees ----------

def register_employee_chat(key: str, chat_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT key, chat_id FROM employees WHERE key = ?", (key,)).fetchone()
        if row is None:
            return False
        if row["chat_id"] is not None and row["chat_id"] != chat_id:
            return False
        conn.execute("UPDATE employees SET chat_id = ? WHERE key = ?", (chat_id, key))
        return True


def get_employee_by_chat(chat_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM employees WHERE chat_id = ?", (chat_id,)).fetchone()


def get_employee(key: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM employees WHERE key = ?", (key,)).fetchone()


def all_employees():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM employees ORDER BY key").fetchall()


def set_rop_style(key: str, style: str):
    with get_conn() as conn:
        conn.execute("UPDATE employees SET rop_style = ? WHERE key = ?", (style, key))


# ---------- task generation ----------

def generate_instances_for_date(target_date: datetime.date):
    weekday = target_date.weekday()
    date_str = target_date.isoformat()
    with get_conn() as conn:
        templates = conn.execute(
            "SELECT * FROM task_templates WHERE active = 1"
        ).fetchall()
        for tpl in templates:
            days = {int(d) for d in tpl["days_of_week"].split(",") if d != ""}
            if weekday not in days:
                continue

            if tpl["employee_key"] == "all":
                keys = [e.key for e in EMPLOYEES]
            else:
                keys = [tpl["employee_key"]]

            hh, mm = (int(x) for x in tpl["remind_time"].split(":"))
            remind_at = datetime.combine(target_date, datetime.min.time()).replace(hour=hh, minute=mm)
            deadline_at = remind_at + timedelta(minutes=tpl["deadline_minutes"])

            for key in keys:
                conn.execute(
                    """INSERT OR IGNORE INTO task_instances
                       (template_id, employee_key, task_date, title, description,
                        remind_at, deadline_at, status, reminded)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0)""",
                    (
                        tpl["id"],
                        key,
                        date_str,
                        tpl["title"],
                        tpl["description"],
                        remind_at.isoformat(timespec="seconds"),
                        deadline_at.isoformat(timespec="seconds"),
                    ),
                )


# ---------- task queries / updates ----------

def get_due_reminders(now_iso: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM task_instances
               WHERE status = 'pending' AND reminded = 0 AND remind_at <= ?""",
            (now_iso,),
        ).fetchall()


def mark_reminded(instance_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE task_instances SET reminded = 1 WHERE id = ?", (instance_id,))


def get_newly_overdue(now_iso: str, auto_cutoff_iso: str):
    """auto_cutoff_iso — для задач из чата (source='auto') просрочка считается позже,
    с запасом после срока: сотрудник обычно отчитывается уже после звонка."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM task_instances
               WHERE status = 'pending'
                 AND ((source != 'auto' AND deadline_at <= ?)
                      OR (source = 'auto' AND deadline_at <= ?))""",
            (now_iso, auto_cutoff_iso),
        ).fetchall()


def mark_overdue(instance_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE task_instances SET status = 'overdue' WHERE id = ?", (instance_id,))


def mark_done(instance_id: int, employee_key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM task_instances WHERE id = ? AND employee_key = ?",
            (instance_id, employee_key),
        ).fetchone()
        if row is None or row["status"] == "done":
            return False
        conn.execute(
            "UPDATE task_instances SET status = 'done', completed_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), instance_id),
        )
        return True


_OPEN_STATUSES = ("pending", "accepted", "overdue")


def get_open_tasks(employee_key: str, today_str: str):
    """Незакрытые задачи сотрудника, которые AI может закрыть по тексту: все задачи
    от руководителя и поставленные AI + сегодняшние задачи по шаблонам."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM task_instances
               WHERE employee_key = ? AND status IN ('pending', 'accepted', 'overdue')
                 AND (source IN ('manual', 'auto') OR task_date = ?)
               ORDER BY deadline_at""",
            (employee_key, today_str),
        ).fetchall()


def close_task_by_ai(instance_id: int, employee_key: str, note: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM task_instances WHERE id = ? AND employee_key = ?",
            (instance_id, employee_key),
        ).fetchone()
        if row is None or row["status"] not in _OPEN_STATUSES:
            return False
        conn.execute(
            """UPDATE task_instances
               SET status = 'done', completed_at = ?, closed_by = 'ai', close_note = ?,
                   prev_status = ?
               WHERE id = ?""",
            (datetime.now().isoformat(timespec="seconds"), note, row["status"], instance_id),
        )
        return True


def reopen_task(instance_id: int) -> bool:
    """Отменяет закрытие AI: возвращает статус, который был до закрытия."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, closed_by, prev_status FROM task_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if row is None or row["status"] != "done" or row["closed_by"] != "ai":
            return False
        conn.execute(
            """UPDATE task_instances
               SET status = ?, completed_at = NULL, closed_by = NULL, close_note = NULL,
                   prev_status = NULL
               WHERE id = ?""",
            (row["prev_status"] or "pending", instance_id),
        )
        return True


def create_manual_task(employee_key: str, title: str, description: str, deadline_at, task_date: str) -> int:
    now_iso = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO task_instances
               (template_id, employee_key, task_date, title, description,
                remind_at, deadline_at, status, reminded, source)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, 'pending', 1, 'manual')""",
            (
                employee_key,
                task_date,
                title,
                description,
                now_iso,
                deadline_at.isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def mark_accepted(instance_id: int, employee_key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM task_instances WHERE id = ? AND employee_key = ?",
            (instance_id, employee_key),
        ).fetchone()
        # Просроченную задачу тоже можно принять — с опозданием.
        if row is None or row["status"] not in ("pending", "overdue"):
            return False
        conn.execute(
            "UPDATE task_instances SET status = 'accepted', completed_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), instance_id),
        )
        return True


def cancel_task(instance_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM task_instances WHERE id = ?", (instance_id,)).fetchone()
        if row is None or row["status"] in ("done", "cancelled"):
            return False
        conn.execute("UPDATE task_instances SET status = 'cancelled' WHERE id = ?", (instance_id,))
        return True


def get_employee_tasks(employee_key: str, since_date: str):
    """Задачи от руководителя (source='manual') и поставленные AI по чату (source='auto')
    с since_date, кроме отменённых — для раздела «Задачи» мини-аппа."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM task_instances
               WHERE employee_key = ? AND source IN ('manual', 'auto')
                 AND status != 'cancelled' AND task_date >= ?
               ORDER BY deadline_at""",
            (employee_key, since_date),
        ).fetchall()


def create_auto_task(employee_key: str, title: str, description: str, remind_at, deadline_at) -> int:
    """Задача, которую AI поставил по сообщению сотрудника. reminded=0 — обычная джоба
    напоминаний пришлёт её в remind_at с кнопкой «Выполнено»."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO task_instances
               (template_id, employee_key, task_date, title, description,
                remind_at, deadline_at, status, reminded, source)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, 'pending', 0, 'auto')""",
            (
                employee_key,
                deadline_at.date().isoformat(),
                title,
                description,
                remind_at.isoformat(timespec="seconds"),
                deadline_at.isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def get_instance(instance_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM task_instances WHERE id = ?", (instance_id,)).fetchone()


def get_tasks_for_employee_on(employee_key: str, date_str: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM task_instances
               WHERE employee_key = ? AND task_date = ?
               ORDER BY remind_at""",
            (employee_key, date_str),
        ).fetchall()


def get_status_summary(date_str: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT employee_key,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status IN ('done', 'accepted') THEN 1 ELSE 0 END) AS done,
                      SUM(CASE WHEN status = 'overdue' THEN 1 ELSE 0 END) AS overdue
               FROM task_instances
               WHERE task_date = ?
               GROUP BY employee_key""",
            (date_str,),
        ).fetchall()


# ---------- checkin sessions (стендап / итоги дня) ----------

def start_checkin_session(employee_key: str, session_date: str, kind: str, deadline_at) -> int:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM checkin_sessions WHERE employee_key = ? AND session_date = ? AND kind = ?",
            (employee_key, session_date, kind),
        ).fetchone()
        if existing:
            return existing["id"]
        now_iso = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            """INSERT INTO checkin_sessions
               (employee_key, session_date, kind, question_index, answers,
                status, started_at, deadline_at, nagged)
               VALUES (?, ?, ?, 0, '[]', 'in_progress', ?, ?, 0)""",
            (employee_key, session_date, kind, now_iso, deadline_at.isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def get_active_session(employee_key: str, kind: str = None):
    with get_conn() as conn:
        if kind:
            return conn.execute(
                """SELECT * FROM checkin_sessions
                   WHERE employee_key = ? AND kind = ? AND status = 'in_progress'
                   ORDER BY id DESC LIMIT 1""",
                (employee_key, kind),
            ).fetchone()
        return conn.execute(
            """SELECT * FROM checkin_sessions
               WHERE employee_key = ? AND status = 'in_progress'
               ORDER BY id DESC LIMIT 1""",
            (employee_key,),
        ).fetchone()


def append_checkin_turn(session_id: int, sender: str, text: str) -> list:
    """Добавляет реплику в диалог сессии (answers — JSON-список {sender, text})
    и возвращает весь диалог."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM checkin_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return []
        turns = [
            t if isinstance(t, dict) else {"sender": "employee", "text": t}
            for t in json.loads(row["answers"])
        ]
        turns.append({"sender": sender, "text": text})
        conn.execute(
            "UPDATE checkin_sessions SET answers = ?, question_index = ? WHERE id = ?",
            (json.dumps(turns, ensure_ascii=False), len(turns), session_id),
        )
        return turns


def complete_session(session_id: int, report: str = None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE checkin_sessions SET status = 'completed', completed_at = ?, report = ?
               WHERE id = ?""",
            (datetime.now().isoformat(timespec="seconds"), report, session_id),
        )


def get_previous_report(employee_key: str, session_id: int):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT report FROM checkin_sessions
               WHERE employee_key = ? AND id < ? AND status = 'completed'
                 AND report IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (employee_key, session_id),
        ).fetchone()
        return row["report"] if row else None


def get_overdue_checkins(now_iso: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM checkin_sessions
               WHERE status = 'in_progress' AND nagged = 0 AND deadline_at <= ?""",
            (now_iso,),
        ).fetchall()


def mark_checkin_nagged(session_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE checkin_sessions SET nagged = 1 WHERE id = ?", (session_id,))


def get_completed_checkin_kinds(employee_key: str, session_date: str) -> set:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT kind FROM checkin_sessions
               WHERE employee_key = ? AND session_date = ? AND status = 'completed'""",
            (employee_key, session_date),
        ).fetchall()
        return {r["kind"] for r in rows}


# ---------- чат мини-аппа ----------

def log_message(employee_key: str, message_date: str, sender: str, text: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO chat_messages (employee_key, message_date, sender, text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (employee_key, message_date, sender, text, datetime.now().isoformat(timespec="seconds")),
        )


def get_messages_for_day(employee_key: str, message_date: str):
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM chat_messages
               WHERE employee_key = ? AND message_date = ?
               ORDER BY id""",
            (employee_key, message_date),
        ).fetchall()


# ---------- обучение AI (база знаний и образцы ответов) ----------

def add_knowledge(kind: str, text: str, question: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge (kind, question, text, created_at) VALUES (?, ?, ?, ?)",
            (kind, question, text, datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def all_knowledge():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM knowledge ORDER BY id").fetchall()


def get_knowledge(knowledge_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,)).fetchone()


def delete_knowledge(knowledge_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
        return cur.rowcount > 0


def save_ai_dialog(admin_message_id: int, employee_key: str, question: str, answer: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO ai_dialogs
               (admin_message_id, employee_key, question, answer, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (admin_message_id, employee_key, question, answer,
             datetime.now().isoformat(timespec="seconds")),
        )


def get_ai_dialog(admin_message_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ai_dialogs WHERE admin_message_id = ?", (admin_message_id,)
        ).fetchone()
