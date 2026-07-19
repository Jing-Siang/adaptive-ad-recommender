import json
import logging
import sys
import time
from contextlib import contextmanager

from app.config import settings

logger = logging.getLogger("ad_recommender")
logger.setLevel(settings.log_level)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
if not logger.handlers:
    logger.addHandler(_handler)


def log_event(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, "ts": time.time(), **fields}))


@contextmanager
def log_duration(event: str, **fields):
    start = time.perf_counter()
    try:
        yield
    finally:
        log_event(event, duration_ms=round((time.perf_counter() - start) * 1000, 2), **fields)
