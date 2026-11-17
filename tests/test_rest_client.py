import io
import urllib.error
from typing import Any

import pytest

from mk96_app_pc_rocket_tool.models import RoomInfo
from mk96_app_pc_rocket_tool.rest_client import (
    AuthError,
    Presence,
    RestApiError,
    RestClient,
    check_connection,
    collect_pages,
    describe_connection_error,
    is_insecure_url,
    parse_history_messages,
    resolve_room_name,
    validate_server_url,
)


class _RecordingClient(RestClient):
    """Captures each request instead of hitting the network."""

    def __init__(self, response: dict[str, Any] | None = None):
        super().__init__("https://chat.example.com/", "uid", "tok")
        self.calls: list[tuple[str, str, dict[str, Any], Any]] = []
        self.response = response or {}

    def _request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params or {}, body))
        return self.response


@pytest.mark.parametrize(
    ("room", "own", "expected"),
    [
        ({"t": "c", "fname": "General", "name": "general"}, None, "General"),
        ({"t": "c", "name": "general"}, None, "general"),
        ({"t": "c", "_id": "room123"}, None, "room123"),
        ({"t": "d", "usernames": ["yo", "ana", "luis"]}, "yo", "ana, luis"),
        ({"t": "d", "usernames": ["yo", "colega"]}, None, "yo, colega"),
        ({"t": "d", "usernames": ["yo"]}, "yo", "yo (tú)"),
        ({"t": "d", "_id": "dm1"}, "yo", "dm1"),
    ],
)
def test_resolve_room_name(room, own, expected):
    assert resolve_room_name(room, own) == expected


def test_room_description_labels_known_and_unknown_types():
    assert RoomInfo("1", "General", "c").description == "Canal · General"
    assert RoomInfo("1", "ana", "d").description == "DM · ana"
    assert RoomInfo("1", "x", "p").description == "Privado · x"
    assert RoomInfo("1", "x", None).description == "? · x"


def test_list_subscriptions_sends_updated_since_only_when_given():
    client = _RecordingClient()
    client.list_subscriptions()
    client.list_subscriptions("2026-08-14T10:00:00.000Z")
    assert client.calls[0][2] == {}
    assert client.calls[1][2] == {"updatedSince": "2026-08-14T10:00:00.000Z"}


@pytest.mark.parametrize(
    ("room_type", "endpoint"),
    [("c", "channels.history"), ("p", "groups.history"), ("d", "im.history")],
)
def test_room_history_uses_the_endpoint_of_each_room_type(room_type, endpoint):
    client = _RecordingClient()
    client.room_history("room1", room_type, oldest="2026-08-14T10:00:00.000Z")
    _method, path, params, _body = client.calls[0]
    assert path == endpoint and params["oldest"] == "2026-08-14T10:00:00.000Z"


def test_list_rooms_resolves_names_with_own_username():
    client = _RecordingClient(
        {"username": "yo", "update": [{"_id": "d1", "t": "d", "usernames": ["yo", "ana"]}]}
    )
    assert client.list_rooms() == [RoomInfo("d1", "ana", "d")]


def test_set_presence_posts_the_status():
    client = _RecordingClient()
    client.set_presence(Presence.BUSY)
    assert client.calls == [("POST", "users.setStatus", {}, {"status": "busy"})]
    with pytest.raises(ValueError):
        client.set_presence("dormido")  # type: ignore[arg-type]


def test_collect_pages_stops_on_short_page_or_max_pages():
    pages = {0: [1, 2], 2: [3]}
    assert collect_pages(lambda offset, count: pages.get(offset, []), 2, 10) == [1, 2, 3]
    offsets: list[int] = []

    def full_page(offset: int, count: int) -> list[int]:
        offsets.append(offset)
        return [0] * count

    assert len(collect_pages(full_page, 3, 4)) == 12 and offsets == [0, 3, 6, 9]


def test_parse_history_messages_is_chronological_and_keeps_mentions():
    raw = [
        {"u": {"username": "b"}, "msg": "dos", "ts": "2", "mentions": [{"username": "all"}]},
        {"u": {"username": "a"}, "msg": "uno", "ts": "1"},
    ]
    parsed = parse_history_messages(raw)
    assert [m.text for m in parsed] == ["uno", "dos"] and parsed[1].mentions == ("all",)


class _PagedClient(RestClient):
    def __init__(self, total: int):
        super().__init__("https://chat.example.com", "uid", "tok")
        self.total, self.offsets = total, []

    def _request(self, method, path, params=None, body=None):
        offset, count = params.get("offset", 0), params["count"]
        self.offsets.append(offset)
        newest_first = list(range(self.total, 0, -1))[offset : offset + count]
        return {
            "messages": [
                {"u": {"username": "x"}, "msg": str(n), "ts": str(n)} for n in newest_first
            ]
        }


def test_room_history_since_paginates_bursts_and_without_cursor_reads_one_page():
    client = _PagedClient(total=250)
    messages = client.room_history_since("r", "c", "0")
    assert len(messages) == 250 and messages[0].text == "1" and client.offsets == [0, 100, 200]
    single = _PagedClient(total=250)
    assert len(single.room_history_since("r", "c", None)) == 50 and single.offsets == [0]


def _raise_http(code: int):
    def fake_urlopen(*_args, **_kwargs):
        raise urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    return fake_urlopen


def test_request_maps_401_to_auth_error_and_other_codes_to_rest_error(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise_http(401))
    with pytest.raises(AuthError) as auth:
        RestClient("https://x", "u", "t").get_own_username()
    assert auth.value.status == 401
    monkeypatch.setattr("urllib.request.urlopen", _raise_http(500))
    with pytest.raises(RestApiError) as other:
        RestClient("https://x", "u", "t").get_own_username()
    assert not isinstance(other.value, AuthError) and other.value.status == 500


def test_request_timeout_becomes_rest_error(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    with pytest.raises(RestApiError, match="timed out"):
        RestClient("https://x", "u", "t").get_own_username()


@pytest.mark.parametrize(
    ("url", "insecure"),
    [
        ("http://chat.example.com", True),
        ("https://chat.example.com", False),
        ("http://localhost:3000", False),
        ("http://127.0.0.1:3000/x", False),
    ],
)
def test_is_insecure_url_flags_cleartext_to_remote_hosts(url, insecure):
    assert is_insecure_url(url) is insecure


def test_validate_server_url_requires_scheme_and_host():
    assert validate_server_url("https://chat.example.com") is None
    assert validate_server_url("chat.example.com") is not None
    assert validate_server_url("https://") is not None


def test_describe_connection_error_messages():
    assert "rechazadas" in describe_connection_error(AuthError("401", status=401))
    assert "404" in describe_connection_error(RestApiError("x", status=404))
    assert "Name or service" in describe_connection_error(RestApiError("Name or service not known"))


def test_check_connection_returns_username_or_fails_without_one(monkeypatch):
    monkeypatch.setattr(RestClient, "_request", lambda *_a, **_k: {"username": "ana"})
    assert check_connection("https://x", "u", "t") == "ana"
    monkeypatch.setattr(RestClient, "_request", lambda *_a, **_k: {})
    with pytest.raises(RestApiError):
        check_connection("https://x", "u", "t")
