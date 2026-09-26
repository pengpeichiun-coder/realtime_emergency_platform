import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
QUEUE = "emergency-business"
MAX_ATTEMPTS = 3


def backoff(attempt):
    return min(60, 2**attempt)
