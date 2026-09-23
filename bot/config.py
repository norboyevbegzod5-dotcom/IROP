import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

_admin_chat_id = os.getenv("ADMIN_CHAT_ID")
ADMIN_CHAT_ID = int(_admin_chat_id) if _admin_chat_id else None

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
BOT_NAME = os.getenv("BOT_NAME", "Шеф")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
