import json
import logging
import logging.handlers
import queue
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry)


class _PassThroughQueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record):
        return record

def setup_logging(log_path="kalshi.log", level=logging.INFO, max_bytes=10_000_000, backups=5):
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backups)
    file_handler.setFormatter(JsonFormatter())

    log_queue = queue.Queue(-1)
    queue_handler = _PassThroughQueueHandler(log_queue)
    listener = logging.handlers.QueueListener(log_queue, file_handler, respect_handler_level=True)
    listener.start()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(queue_handler)
    return listener