"""Rocket.Chat REST calls: own user, rooms, subscriptions, history, read marks, presence and
messages. The Personal Access Token goes straight into X-Auth-Token with the user id in
X-User-Id, so no login call (and no 2FA dance) is needed.

Blocking by design: callers run it off the GUI thread (workers.run_in_thread).
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from mk96_app_pc_rocket_tool.models import HistoryMessage, RoomInfo

REQUEST_TIMEOUT_S = 10
HISTORY_PAGE_SIZE = 100
HISTORY_MAX_PAGES = 10
DEFAULT_HISTORY_COUNT = 50
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class Presence(StrEnum):
    ONLINE = "online"
    AWAY = "away"
    BUSY = "busy"
    OFFLINE = "offline"


PRESENCE_LABELS = {
    Presence.ONLINE: "Disponible",
    Presence.AWAY: "Ausente",
    Presence.BUSY: "Ocupado",
    Presence.OFFLINE: "Invisible",
}

# Public channels use channels.history, private groups groups.history and DMs im.history:
# channels.history on a private group answers 400 instead of history.
_HISTORY_ENDPOINTS = {"d": "im.history", "p": "groups.history"}


class RestApiError(Exception):
    """Rocket.Chat answered with an error or could not be reached."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(RestApiError):
    """The server rejected the credentials (401): retrying will not help."""


def collect_pages(
    fetch_page: Callable[[int, int], list[Any]], page_size: int, max_pages: int
) -> list[Any]:
    """Concatenates (offset, count) pages until one comes back short or max_pages is hit."""
    result: list[Any] = []
    for page in range(max_pages):
        batch = fetch_page(page * page_size, page_size)
        result.extend(batch)
        if len(batch) < page_size:
            break
    return result


def parse_history_messages(raw_messages: list[dict[str, Any]]) -> list[HistoryMessage]:
    """API messages come newest first; this returns them in chronological order."""
    return [
        HistoryMessage(
            sender=(message.get("u") or {}).get("username"),
            text=message.get("msg") or "",
            ts=message.get("ts"),
            mentions=tuple(
                m["username"] for m in message.get("mentions") or [] if m.get("username")
            ),
        )
        for message in reversed(raw_messages)
    ]


def validate_server_url(server_url: str) -> str | None:
    """Error message if the URL cannot work, or None."""
    scheme, _, rest = server_url.strip().partition("://")
    if scheme.lower() not in ("http", "https") or not rest.strip("/"):
        return "La URL debe empezar por http:// o https:// y llevar el nombre del servidor."
    return None


def is_insecure_url(server_url: str) -> bool:
    """http:// to a non-local host: the token would travel in clear text."""
    url = server_url.strip().lower()
    if not url.startswith("http://"):
        return False
    host = url.removeprefix("http://").split("/")[0].rsplit(":", 1)[0]
    return host not in LOCAL_HOSTS


def describe_connection_error(exc: RestApiError) -> str:
    """Readable Spanish message for a failed connection."""
    if isinstance(exc, AuthError):
        return "Credenciales rechazadas: revisa el User Id y el token."
    if exc.status is not None:
        return f"El servidor respondió con el error {exc.status}: revisa la URL."
    return f"No se pudo contactar con el servidor: {exc}"


def resolve_room_name(room: dict[str, Any], own_username: str | None = None) -> str:
    """Recognizable name of a room. DMs have no useful name, so their participants are used,
    minus the own user when known."""
    if room.get("t") == "d":
        usernames = room.get("usernames") or []
        others = [u for u in usernames if u != own_username]
        if others:
            return ", ".join(others)
        if usernames:  # DM with yourself: you are the only participant
            return f"{usernames[0]} (tú)"
        return str(room.get("_id", ""))
    return str(room.get("fname") or room.get("name") or room.get("_id", ""))


class RestClient:
    def __init__(self, server_url: str, user_id: str, token: str):
        self._base_url = server_url.strip().rstrip("/")
        self._headers = {
            "X-Auth-Token": token,
            "X-User-Id": user_id,
            "Content-Type": "application/json",
        }
        self._own_username: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/api/v1/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                result: dict[str, Any] = json.loads(response.read())
                return result
        except urllib.error.HTTPError as exc:
            error_class = AuthError if exc.code == 401 else RestApiError
            raise error_class(str(exc), status=exc.code) from exc
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            # OSError covers URLError and socket timeouts while reading the body.
            raise RestApiError(str(exc)) from exc

    def get_own_username(self) -> str | None:
        if self._own_username is None:
            self._own_username = self._request("GET", "me").get("username")
        return self._own_username

    def list_rooms(self) -> list[RoomInfo]:
        """Rooms and DMs visible to the user, for the rule room search."""
        response = self._request("GET", "rooms.get")
        rooms = (response.get("update") or []) + (response.get("remove") or [])
        own_username = self.get_own_username()
        return [
            RoomInfo(room["_id"], resolve_room_name(room, own_username), room.get("t"))
            for room in rooms
        ]

    def list_subscriptions(self, updated_since: str | None = None) -> list[dict[str, Any]]:
        """One subscription per room, with its `ls` (last seen) and unread counters. With
        `updated_since`, only what changed since then (incremental polling)."""
        params = {"updatedSince": updated_since} if updated_since else None
        response = self._request("GET", "subscriptions.get", params=params)
        return list((response.get("update") or []) + (response.get("remove") or []))

    def _history_page(
        self, room_id: str, room_type: str, count: int, oldest: str | None, offset: int = 0
    ) -> list[dict[str, Any]]:
        endpoint = _HISTORY_ENDPOINTS.get(room_type, "channels.history")
        params: dict[str, Any] = {"roomId": room_id, "count": count}
        if offset:
            params["offset"] = offset
        if oldest:
            params["oldest"] = oldest
        return list(self._request("GET", endpoint, params=params).get("messages") or [])

    def room_history(
        self,
        room_id: str,
        room_type: str,
        count: int = DEFAULT_HISTORY_COUNT,
        oldest: str | None = None,
    ) -> list[HistoryMessage]:
        return parse_history_messages(self._history_page(room_id, room_type, count, oldest))

    def room_history_since(
        self, room_id: str, room_type: str, oldest: str | None
    ) -> list[HistoryMessage]:
        """Every message after `oldest`, paginating, so a burst between two polls is never
        lost. Without `oldest` only the last page (no point in dumping the whole history)."""
        if not oldest:
            return self.room_history(room_id, room_type)
        raw = collect_pages(
            lambda offset, count: self._history_page(room_id, room_type, count, oldest, offset),
            HISTORY_PAGE_SIZE,
            HISTORY_MAX_PAGES,
        )
        return parse_history_messages(raw)

    def mark_room_read(self, room_id: str) -> None:
        self._request("POST", "subscriptions.read", body={"rid": room_id})

    def set_presence(self, status: Presence) -> None:
        self._request("POST", "users.setStatus", body={"status": Presence(status).value})

    def send_message(self, room_id: str, text: str) -> None:
        self._request("POST", "chat.postMessage", body={"roomId": room_id, "text": text})


def check_connection(server_url: str, user_id: str, token: str) -> str:
    """Tries credentials without saving them: the username if the server accepts them;
    raises RestApiError (AuthError when rejected) otherwise."""
    username = RestClient(server_url, user_id, token).get_own_username()
    if not username:
        raise RestApiError("el servidor no devolvió un usuario")
    return username
