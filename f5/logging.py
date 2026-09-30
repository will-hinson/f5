import logging
from rich.logging import RichHandler


def setup_logging(level: logging._Level = logging.INFO) -> None:
    """
    Setup logging configuration with RichHandler for better console output.

    Args:
        level (logging._Level): The logging level to set. Default is logging.INFO.
    """

    logging.basicConfig(
        level=level,
        format="[%(name)s] %(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                markup=False,
            )
        ],
    )
