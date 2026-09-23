import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from bot.config import BOT_TOKEN

_MAX_AGE_SECONDS = 24 * 60 * 60


def validate_init_data(init_data: str):
    """Проверяет подпись Telegram WebApp initData и возвращает распарсенные поля,
    либо None, если подпись неверна/данные пустые/просрочены."""
    if not init_data:
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if auth_date and time.time() - int(auth_date) > _MAX_AGE_SECONDS:
        return None

    return pairs


def get_user(pairs: dict):
    raw_user = pairs.get("user")
    if not raw_user:
        return None
    try:
        return json.loads(raw_user)
    except ValueError:
        return None
