from __future__ import annotations

import argparse
import logging

from .agent import emit_error
from .config import ConfigError
from .errors import AuthenticationRequired, SnuetlError
from .logging_utils import redact

LOGGER = logging.getLogger("snuetl.cli")


def handle_cli_exception(args: argparse.Namespace, exc: BaseException) -> int:
    """Apply the stable JSON error and exit-code contract in one place."""
    command = getattr(args, "command", None) or "snuetl"
    json_output = bool(getattr(args, "json", False))

    if isinstance(exc, AuthenticationRequired):
        if json_output:
            emit_error(
                command,
                "AUTHENTICATION_REQUIRED",
                str(redact(exc)),
                "Run snuetl login interactively.",
            )
        else:
            LOGGER.error("authentication required: %s", exc)
        return 2

    if isinstance(exc, (ConfigError, OSError, ValueError)):
        if json_output:
            emit_error(
                command,
                "LOCAL_STATE_ERROR",
                str(redact(exc)),
                "Check the command arguments and run snuetl doctor.",
            )
        else:
            LOGGER.error("configuration or local-state error: %s", exc)
        return 3

    if isinstance(exc, SnuetlError):
        if json_output:
            emit_error(
                command,
                "COMMAND_FAILED",
                str(redact(exc)),
                "Retry with --verbose or run snuetl doctor.",
            )
        else:
            LOGGER.error("command failed: %s", exc)
        return 1

    if isinstance(exc, (KeyboardInterrupt, EOFError)):
        if json_output:
            emit_error(
                command,
                "CANCELLED",
                "Cancelled by user.",
                "Run the command again when ready.",
            )
        else:
            LOGGER.error("cancelled")
        return 130

    if json_output:
        emit_error(
            command,
            "UNEXPECTED_FAILURE",
            str(redact(exc)),
            "Retry with --verbose and report the failure if it persists.",
        )
    else:
        LOGGER.error("unexpected failure: %s", exc)
    return 1
