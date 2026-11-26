import sys
from loguru import logger

CONSOLE_FORMAT = "{time:HH:mm:ss} | {function} | <level>{message}</level>"
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {function} | {message}"
LOG_FILE = "data/logger.log"

configured = False


def get_logger():
    global configured
    if not configured:
        logger.remove()
        logger.add(sys.stderr, format=CONSOLE_FORMAT, enqueue=True)
        logger.add(LOG_FILE, format=FILE_FORMAT, rotation="10 MB", retention="7 days", encoding="utf-8", enqueue=True)
        configured = True
    return logger
