import hashlib
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Бот везде работает с «наивными» datetime.now()/date.today(). Переводим весь процесс в
# часовой пояс компании: на Render сервер по умолчанию в UTC, и без этого «18:00»
# в задачах и «сегодня» в отчётах сдвигались бы на 5 часов. (На Windows tzset нет —
# локально используется часовой пояс машины.)
os.environ["TZ"] = os.getenv("TIMEZONE", "Asia/Tashkent")
if hasattr(time, "tzset"):
    time.tzset()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Секрет в адресе админки (/admin/<token>): логина нет, но адрес не угадать.
# По умолчанию выводится из BOT_TOKEN, так что настраивать ничего не нужно;
# задайте ADMIN_PANEL_TOKEN, чтобы сменить ссылку (старая перестанет работать).
ADMIN_PANEL_TOKEN = os.getenv("ADMIN_PANEL_TOKEN") or hashlib.sha256(
    f"{BOT_TOKEN}:admin-panel".encode()
).hexdigest()[:24]

_admin_chat_id = os.getenv("ADMIN_CHAT_ID")
ADMIN_CHAT_ID = int(_admin_chat_id) if _admin_chat_id else None

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
BOT_NAME = os.getenv("BOT_NAME", "Шеф")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Модель распознавания голосовых сообщений сотрудников.
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
# Задачи, которые AI ставит по чату сотрудника: напоминание за столько минут до срока
# (если время названо; иначе — в AUTO_TASK_DEFAULT_REMIND утром того дня), срок без
# времени — конец рабочего дня, и сколько часов после срока ждать отчёта, прежде чем
# считать задачу просроченной.
AUTO_TASK_REMIND_BEFORE_MINUTES = 15
AUTO_TASK_DEFAULT_REMIND = "10:00"
AUTO_TASK_DEFAULT_DEADLINE = "19:00"
AUTO_TASK_OVERDUE_GRACE_HOURS = 2
# Дальше этого срока AI задачи не ставит (защита от ошибок в дате).
AUTO_TASK_MAX_DAYS_AHEAD = 90
# Голосовые длиннее этого не распознаём (секунды).
MAX_VOICE_SECONDS = 300

# Render сам прокидывает RENDER_EXTERNAL_URL с публичным адресом сервиса —
# используем его, если WEBAPP_URL не задан явно (для локального туннеля).
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
# Render (и большинство PaaS) сами назначают порт через $PORT — уважаем его,
# если он задан, иначе используем WEBAPP_PORT/8080 для локального запуска.
WEBAPP_PORT = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT", "8080"))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DATA_DIR указывает на постоянный диск (Render Persistent Disk и т.п.),
# чтобы SQLite не терялась при передеплое. Локально — обычная папка data/.
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "irop.db")


@dataclass(frozen=True)
class Employee:
    key: str
    full_name: str


EMPLOYEES = [
    Employee("saidmurod", "Саидмурод"),
    Employee("aslbek", "Аслбек"),
    Employee("murodjon", "Муроджон"),
]

EMPLOYEE_BY_KEY = {e.key: e for e in EMPLOYEES}
