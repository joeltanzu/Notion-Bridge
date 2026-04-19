from typing import Optional
import keyring

SERVICE = "notion-bridge"
SECRET_SERVICE = "notion-bridge-secrets"


def store_token(vault_id: str, token: str) -> None:
    keyring.set_password(SERVICE, vault_id, token)


def get_token(vault_id: str) -> Optional[str]:
    return keyring.get_password(SERVICE, vault_id)


def delete_token(vault_id: str) -> None:
    try:
        keyring.delete_password(SERVICE, vault_id)
    except keyring.errors.PasswordDeleteError:
        pass


def store_secret(vault_id: str, secret: str) -> None:
    keyring.set_password(SECRET_SERVICE, vault_id, secret)


def get_secret(vault_id: str) -> Optional[str]:
    return keyring.get_password(SECRET_SERVICE, vault_id)


def delete_secret(vault_id: str) -> None:
    try:
        keyring.delete_password(SECRET_SERVICE, vault_id)
    except keyring.errors.PasswordDeleteError:
        pass
