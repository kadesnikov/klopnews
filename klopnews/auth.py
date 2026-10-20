"""Подписанные токены «id.срок.подпись»: ссылка входа из бота и cookie сессии."""
import hashlib
import hmac
import time


def sign(uid: int, ttl: int, secret: str) -> str:
    body = f"{uid}.{int(time.time()) + ttl}"
    return f"{body}.{hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]}"


def verify(token: str | None, secret: str) -> int | None:
    try:
        uid, exp, sig = (token or "").split(".")
    except ValueError:
        return None
    good = hmac.new(secret.encode(), f"{uid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return int(uid)
