"""Structured logging with rotation for NITS Arena."""

import logging
import logging.handlers
import os
from typing import Optional


def get_logger(
    name: str,
    log_dir: str = "logs",
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,  # 5 MB
    backup_count: int = 5,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """Create and return a named logger with console and rotating-file handlers.

    Args:
        name: Logger name (typically ``__name__`` of the calling module).
        log_dir: Directory in which to store log files.
        level: Minimum logging level.
        max_bytes: Maximum size of a single log file before rotation.
        backup_count: Number of rotated log files to keep.
        log_format: Custom log format string; defaults to a structured format.

    Returns:
        A configured :class:`logging.Logger` instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers when the logger is fetched multiple times
    if logger.handlers:
        return logger

    logger.setLevel(level)

    if log_format is None:
        log_format = (
            "%(asctime)s | %(levelname)-8s | %(name)s | "
            "%(filename)s:%(lineno)d | %(message)s"
        )

    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%dT%H:%M:%S")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{name.replace('.', '_')}.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
