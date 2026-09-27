from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from snuetl import canvas_api
from snuetl.config import load_config
from snuetl.errors import AuthenticationRequired, DiscoveryError


def _config_and_token(tmp_path: Path) -> tuple[object, canvas_api.CanvasToken]:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    token = canvas_api.CanvasToken(
        "1~exampletokenvalue123456789",
        "https://etl.example.test",
        "42",
        "77",
        (datetime.now(UTC) + timedelta(days=365)).isoformat(),
    )
    canvas_api.save_token(config, token)
    return config, token


def test_canvas_token_is_private_and_never_appears_in_status(tmp_path: Path) -> None:
    config, token = _config_and_token(tmp_path)
    assert canvas_api.load_token(config) == token
    if os.name != "nt":
        assert stat.S_IMODE(config.canvas_token_path.stat().st_mode) == 0o600
    status = canvas_api.token_status(config)
    assert status["configured"] is True
    assert token.value not in json.dumps(status)


def test_canvas_token_cannot_be_used_after_switching_etl_profile(tmp_path: Path) -> None:
    config, _ = _config_and_token(tmp_path)
    config.session_metadata_path.write_text('{"user_id":"another-user"}', encoding="utf-8")
    assert canvas_api.token_status(config)["ready"] is False
    with pytest.raises(AuthenticationRequired, match="another eTL profile"):
        canvas_api.CanvasClient(config)


def test_canvas_client_follows_same_origin_pages_and_rejects_cross_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, token = _config_and_token(tmp_path)
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {token.value}"
        requests.append(str(request.url))
        if request.url.path == "/api/v1/page2":
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": '<https://etl.example.test/api/v1/page2>; rel="next"'},
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        canvas_api.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with canvas_api.CanvasClient(config) as client:
        assert [item["id"] for item in client.pages("/api/v1/page1")] == [1, 2]
        with pytest.raises(DiscoveryError, match="origin"):
            client.pages("https://another.example.test/api/v1/users/self/profile")
    assert len(requests) == 2


def test_canvas_client_rejects_revoked_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _config_and_token(tmp_path)
    original_client = httpx.Client
    monkeypatch.setattr(
        canvas_api.httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(401)), **kwargs
        ),
    )
    with canvas_api.CanvasClient(config) as client, pytest.raises(AuthenticationRequired):
        client.request("GET", "/api/v1/users/self/profile")


def test_live_submission_and_grade_rows_handle_hidden_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _config_and_token(tmp_path)

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/courses":
            return httpx.Response(200, json=[{"id": 12, "name": "Biology"}])
        if path.endswith("/assignments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 8,
                        "name": "Essay",
                        "submission": {"workflow_state": "submitted", "score": None},
                    }
                ],
            )
        if path.endswith("/enrollments"):
            return httpx.Response(200, json=[{"grades": {"current_score": 82}}])
        raise AssertionError(path)

    original_client = httpx.Client
    monkeypatch.setattr(
        canvas_api.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    submissions = canvas_api.live_data(config, "submissions", course="Biology")
    assert submissions[0]["workflow_state"] == "submitted"
    assert submissions[0]["score"] is None
    grades = canvas_api.live_data(config, "grades", course="12")
    assert grades[0]["current_score"] == 82
    assert grades[0]["current_grade"] is None

@pytest.mark.parametrize("status", [200, 201])
def test_token_creation_accepts_guarded_canvas_json(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace

    token = "1~exampletokenvalue123456789"
    payload = {"visible_token": token, "id": 77, "expires_at": "2027-09-27T00:00:00Z"}

    def invalid_json() -> None:
        raise ValueError("Guarded JSON")

    response = SimpleNamespace(
        status=status,
        json=invalid_json,
        text=lambda: "while(1);" + json.dumps(payload),
    )
    control = SimpleNamespace(click=lambda: None, fill=lambda _value: None)
    page = SimpleNamespace(
        goto=lambda *_args, **_kwargs: None,
        expect_response=lambda _predicate: nullcontext(SimpleNamespace(value=response)),
    )
    monkeypatch.setattr(canvas_api, "_first_visible", lambda *_args: control)
    result = canvas_api._create_token_in_ui(page, "https://myetl.snu.ac.kr", datetime.now(UTC).date())
    assert result == (token, "77", payload["expires_at"])


def test_rate_limit_honors_retry_after_without_retry_storm(tmp_path, monkeypatch):
    config, token = _config_and_token(tmp_path)
    calls=[]; waits=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':'3'}) if len(calls)<3 else httpx.Response(200,json={'id':42})
    original=httpx.Client
    monkeypatch.setattr(canvas_api.httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(respond),**kwargs))
    monkeypatch.setattr(canvas_api.monotonic_time,'sleep',waits.append)
    monkeypatch.setattr(canvas_api.random,'uniform',lambda *_:0)
    with canvas_api.CanvasClient(config,token) as client:
        assert client.request('GET','/api/v1/users/self/profile')['id']==42
    assert waits==[3,3] and len(calls)==3
