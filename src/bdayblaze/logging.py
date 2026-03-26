from __future__ import annotations

import hashlib
import logging
from typing import Any, cast

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(**fields: Any) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger("bdayblaze").bind(**fields)
    return cast(structlog.stdlib.BoundLogger, logger)


def redact_identifier(value: int | str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[:12]
