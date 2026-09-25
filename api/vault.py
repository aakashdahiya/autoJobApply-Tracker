"""Credentials for per-tenant ATS accounts.

Workday gives every employer its own tenant and its own login, which is the
worst friction in applying to Canadian enterprises and therefore the biggest
win available. Passwords live in the OS keychain and are referenced by
`secret_ref`; there is no password column, nothing in the extension's storage,
and nothing in the repository.

If no keychain backend is available (a bare Linux container, say), storing
fails loudly rather than silently falling back to a plaintext file.
"""

from __future__ import annotations

SERVICE = "autoJobApply-Tracker"


class VaultUnavailable(RuntimeError):
    pass


def _backend():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise VaultUnavailable("keyring is not installed") from exc

    if isinstance(keyring.get_keyring(), FailKeyring):
        raise VaultUnavailable(
            "no OS keychain is available here; run the API on your own machine, "
            "or set up a keyring backend before storing credentials"
        )
    return keyring


def secret_ref(ats: str, tenant: str, username: str) -> str:
    return f"{ats}:{tenant}:{username}"


def store(ref: str, password: str) -> None:
    _backend().set_password(SERVICE, ref, password)


def fetch(ref: str) -> str | None:
    return _backend().get_password(SERVICE, ref)


def delete(ref: str) -> None:
    keyring = _backend()
    try:
        keyring.delete_password(SERVICE, ref)
    except Exception:  # deleting something already gone is not an error
        pass


def available() -> bool:
    try:
        _backend()
        return True
    except VaultUnavailable:
        return False
