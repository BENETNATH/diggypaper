"""Settings service: reading/writing ``system_settings`` (Module 7).

Secrets are encrypted AES-256-GCM (``is_secret=True``); the other values are
stored as plain UTF-8. ``get_all`` masks the defined secrets (``MASK``) and
returns ``None`` for undefined keys.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import MASK, decrypt_secret, encrypt_secret
from app.db.models import SettingScope, SystemSettings
from app.settings.keys import DEFAULTS, KNOWN_KEYS, is_secret


def _check_key(key: str) -> None:
    if key not in KNOWN_KEYS:
        raise ValueError(f"unknown setting key: {key}")


def _normalize_ids(scope: SettingScope, project_id, user_id) -> tuple:
    """F1: forces project_id/user_id to None according to the scope (no surplus IDs)."""
    if scope is SettingScope.GLOBAL:
        return None, None
    if scope is SettingScope.PROJECT:
        return project_id, None
    if scope is SettingScope.USER:
        return None, user_id
    raise ValueError(f"unknown scope: {scope}")


def _scope_filter(scope: SettingScope, project_id, user_id) -> list:
    """WHERE clauses isolating a scope (closes the NULL holes of the partial indexes)."""
    if scope is SettingScope.GLOBAL:
        return [
            SystemSettings.scope == SettingScope.GLOBAL,
            SystemSettings.project_id.is_(None),
            SystemSettings.user_id.is_(None),
        ]
    if scope is SettingScope.PROJECT:
        if project_id is None:
            raise ValueError("project_id is required for PROJECT scope")
        return [
            SystemSettings.scope == SettingScope.PROJECT,
            SystemSettings.project_id == project_id,
            SystemSettings.user_id.is_(None),
        ]
    if scope is SettingScope.USER:
        if user_id is None:
            raise ValueError("user_id is required for USER scope")
        return [
            SystemSettings.scope == SettingScope.USER,
            SystemSettings.user_id == user_id,
            SystemSettings.project_id.is_(None),
        ]
    raise ValueError(f"unknown scope: {scope}")


async def _get_row(
    db: AsyncSession, key: str, scope: SettingScope, project_id, user_id
) -> SystemSettings | None:
    stmt = select(SystemSettings).where(
        *_scope_filter(scope, project_id, user_id), SystemSettings.key_name == key
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_setting(
    db: AsyncSession,
    key: str,
    scope: SettingScope = SettingScope.GLOBAL,
    project_id=None,
    user_id=None,
) -> str | None:
    """Effective value of a setting (unmasked), with the default if absent.

    ``None`` if the key is not defined and has no default.
    """
    project_id, user_id = _normalize_ids(scope, project_id, user_id)
    row = await _get_row(db, key, scope, project_id, user_id)
    if row is None or row.value_encrypted is None:
        return DEFAULTS.get(key)
    if row.is_secret:
        return decrypt_secret(row.value_encrypted)
    return row.value_encrypted.decode("utf-8")


async def set_setting(
    db: AsyncSession,
    key: str,
    value: str,
    scope: SettingScope = SettingScope.GLOBAL,
    project_id=None,
    user_id=None,
    *,
    commit: bool = True,
) -> None:
    """Upsert of a setting (respects the partial unique indexes of lot 1).

    Encrypts if secret, stores plain UTF-8 otherwise.

    ``commit=False``: do not commit (the commit is done by the caller, to group
    several set/delete in a single transaction — see ``admin.update_settings``).
    The default behavior (``commit=True``) is unchanged for the other callers.
    """
    _check_key(key)
    project_id, user_id = _normalize_ids(scope, project_id, user_id)
    row = await _get_row(db, key, scope, project_id, user_id)
    if row is None:
        row = SystemSettings(scope=scope, project_id=project_id, user_id=user_id, key_name=key)
        db.add(row)
    secret = is_secret(key)
    row.value_encrypted = encrypt_secret(value) if secret else value.encode("utf-8")
    row.is_secret = secret
    row.updated_at = datetime.now(UTC)
    if commit:
        await db.commit()


async def delete_setting(
    db: AsyncSession,
    key: str,
    scope: SettingScope = SettingScope.GLOBAL,
    project_id=None,
    user_id=None,
    *,
    commit: bool = True,
) -> None:
    """Deletes the row (revert to the default if the key has one).

    ``commit=False``: do not commit (commit by the caller, cf. ``set_setting``).
    """
    _check_key(key)
    project_id, user_id = _normalize_ids(scope, project_id, user_id)
    row = await _get_row(db, key, scope, project_id, user_id)
    if row is not None:
        await db.delete(row)
        if commit:
            await db.commit()


async def get_all(
    db: AsyncSession,
    scope: SettingScope = SettingScope.GLOBAL,
    project_id=None,
    user_id=None,
) -> dict[str, str | None]:
    """All values of a scope: defined secrets → ``MASK``, undefined → ``None``."""
    project_id, user_id = _normalize_ids(scope, project_id, user_id)
    stmt = select(SystemSettings).where(*_scope_filter(scope, project_id, user_id))
    rows = (await db.execute(stmt)).scalars().all()
    by_key = {r.key_name: r for r in rows}
    result: dict[str, str | None] = {}
    for key in KNOWN_KEYS:
        row = by_key.get(key)
        if row is None or row.value_encrypted is None:
            result[key] = None
        elif row.is_secret:
            result[key] = MASK
        else:
            result[key] = row.value_encrypted.decode("utf-8")
    return result
