import logging
import sys
from datetime import datetime, timezone

from rich.logging import RichHandler

from .console import console

# attributes every LogRecord has; anything else came from `extra=`
_RESERVED: set[str] = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def _logfmt_value(value: object) -> str:
    text = str(value)
    if text == "" or any(c in text for c in ' ="\\\n\t'):
        text = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{text}"'
    return text


class LogfmtFormatter(logging.Formatter):
    """Format records as logfmt: time=... level=INFO logger=... msg="..." key=value"""

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                fields[key] = value
        if record.exc_info:
            fields["error"] = self.formatException(record.exc_info)
        if record.stack_info:
            fields["stack"] = self.formatStack(record.stack_info)

        return " ".join(
            f"{key}={_logfmt_value(value)}" for key, value in fields.items()
        )


def setup_logging(level: int | str = logging.INFO) -> None:
    """
    Configure logging: Rich output in a terminal, logfmt otherwise.

    Args:
        level (int | str): The logging level to set. Default is logging.INFO.
    """

    handler: logging.Handler
    if console.is_terminal:
        handler = RichHandler(console=console, rich_tracebacks=True, markup=False)
        handler.setFormatter(
            logging.Formatter("[%(name)s] %(message)s", datefmt="[%X]")
        )
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(LogfmtFormatter())

    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )
