"""Console logs: one `key=value` line per event, tied together by a request id.

Lines carry ids, field names and counts only, never names, emails, tokens, request bodies or document
contents, so a log can be shared safely. LOG_LEVEL=DEBUG adds every graph query.
"""
import contextvars
import logging
import os
import re
import sys
import time
import uuid

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE = re.compile(r"^[\w.:/@+-]*$")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = request_id.get()
        return True


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s rid=%(rid)s %(message)s", "%H:%M:%S"))
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger("azentio")
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    root.propagate = False


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"azentio.{name}")


def _fmt(value) -> str:
    s = "-" if value is None else str(value)
    return s if _SAFE.match(s) else '"' + s.replace('"', "'")[:200] + '"'


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    """log_event(log, "run.finished", run=rid, status="succeeded", advanced=3)
    → `run.finished run=run_ab12 status=succeeded advanced=3`"""
    logger.log(level, " ".join([event, *(f"{k}={_fmt(v)}" for k, v in fields.items())]))


_TOKEN_PATH = re.compile(r"(/portal/)[^/]+")


def safe_path(path: str) -> str:
    """Portal tokens are credentials; never print them."""
    return _TOKEN_PATH.sub(r"\1***", path)


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    @property
    def ms(self) -> int:
        return round((time.perf_counter() - self.start) * 1000)

    def __exit__(self, *exc):
        return False
