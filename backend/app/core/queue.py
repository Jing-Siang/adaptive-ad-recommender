from redis import Redis
from rq import Queue

from app.core.config import settings

redis_conn = Redis.from_url(settings.redis_url)
campaign_review_queue = Queue("campaign_review", connection=redis_conn)
