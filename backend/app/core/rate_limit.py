from fastapi import HTTPException

from app.core.queue import redis_conn


def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window rate limit backed by Redis (already running, for RQ) --
    INCR the window's counter, set it to expire on the first hit, raise 429
    once `key` has been hit more than `limit` times within the trailing
    window. A fixed window can allow a short burst right at the window
    boundary (e.g. one hit at 0:59 and the limit again at 1:00) -- an
    acceptable tradeoff for a cheap abuse guard, not a hard billing cap."""
    count = redis_conn.incr(key)
    if count == 1:
        redis_conn.expire(key, window_seconds)
    if count > limit:
        raise HTTPException(status_code=429, detail="Too many campaigns submitted recently -- try again later.")
