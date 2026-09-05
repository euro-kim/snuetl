from __future__ import annotations

import argparse
import json

import pytest

from snuetl.cli_runtime import handle_cli_exception
from snuetl.config import ConfigError
from snuetl.errors import AuthenticationRequired, DiscoveryError


@pytest.mark.parametrize(
    ("error", "exit_code", "error_code"),
    [
        (AuthenticationRequired("expired"), 2, "AUTHENTICATION_REQUIRED"),
        (ConfigError("invalid"), 3, "LOCAL_STATE_ERROR"),
        (DiscoveryError("changed"), 1, "COMMAND_FAILED"),
        (KeyboardInterrupt(), 130, "CANCELLED"),
        (RuntimeError("broken"), 1, "UNEXPECTED_FAILURE"),
    ],
)
def test_json_exception_policy_preserves_exit_and_error_contract(
    error, exit_code, error_code, capsys
) -> None:
    args = argparse.Namespace(command="files", json=True)

    assert handle_cli_exception(args, error) == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "files"
    assert payload["error"]["code"] == error_code
