from celery import Celery, signals
from app.observability import configure_logging
from app.workers.config import BROKER_URL, RESULT_BACKEND, QUEUE

celery_app = Celery(
    "emergency",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.workers.tasks.jobs", "app.workers.tasks.webhooks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue=QUEUE,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={
        "visibility_timeout": 300,
        "socket_timeout": 3,
        "socket_connect_timeout": 3,
    },
    result_expires=3600,
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    task_publish_retry=False,
)


@signals.setup_logging.connect
def worker_logging(**kwargs):
    configure_logging()
