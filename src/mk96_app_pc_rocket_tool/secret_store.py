"""Server URL, user id and Personal Access Token of each server, in Windows Credential
Manager (through `keyring`), one generic credential per server id.

Never in config.yaml or plain text: if Credential Manager is not the active backend, every
call fails with SecretStoreUnavailable instead of falling back to something insecure.
"""

import json
from dataclasses import dataclass, field

import keyring
import keyring.errors
from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring

from mk96_app_pc_rocket_tool import APP_ID

SERVICE_NAME = APP_ID


class SecretStoreUnavailable(Exception):
    """Windows Credential Manager is not available or refused the operation."""


@dataclass(frozen=True, slots=True)
class ServerCredentials:
    server_url: str
    user_id: str
    token: str = field(repr=False)  # never printed in logs or tracebacks


def _backend() -> KeyringBackend:
    backend = keyring.get_keyring()
    if not isinstance(backend, WinVaultKeyring):
        raise SecretStoreUnavailable(
            f"El almacén de credenciales de Windows no está disponible ({type(backend).__name__})."
        )
    return backend


def save_server_credentials(server_id: str, credentials: ServerCredentials) -> None:
    secret = json.dumps(
        {
            "server_url": credentials.server_url,
            "user_id": credentials.user_id,
            "token": credentials.token,
        }
    )
    try:
        _backend().set_password(SERVICE_NAME, server_id, secret)
    except keyring.errors.KeyringError as exc:
        raise SecretStoreUnavailable(f"No se pudo guardar la credencial: {exc}") from exc


def load_server_credentials(server_id: str) -> ServerCredentials | None:
    """None when nothing is stored for that server."""
    try:
        secret = _backend().get_password(SERVICE_NAME, server_id)
    except keyring.errors.KeyringError as exc:
        raise SecretStoreUnavailable(f"No se pudo leer la credencial: {exc}") from exc
    if secret is None:
        return None
    try:
        values = json.loads(secret)
        return ServerCredentials(values["server_url"], values["user_id"], values["token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SecretStoreUnavailable(
            "La credencial guardada está dañada; vuelve a guardarla."
        ) from exc


def clear_server_credentials(server_id: str) -> None:
    backend = _backend()
    try:
        backend.delete_password(SERVICE_NAME, server_id)
    except keyring.errors.PasswordDeleteError:
        return  # nothing stored: already in the wanted state
    except keyring.errors.KeyringError as exc:
        raise SecretStoreUnavailable(f"No se pudo borrar la credencial: {exc}") from exc
