"""Service settings : lecture/écriture des ``system_settings`` (Module 7).

Les secrets sont chiffrés AES-256-GCM (``is_secret=True``) ; les autres valeurs
sont stockées en clair UTF-8. ``get_all`` masque les secrets définis (``MASK``)
et renvoie ``None`` pour les clés non définies.
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
    """F1 : force project_id/user_id à None selon le scope (pas d'ID superflu)."""
    if scope is SettingScope.GLOBAL:
        return None, None
    if scope is SettingScope.PROJECT:
        return project_id, None
    if scope is SettingScope.USER:
        return None, user_id
    raise ValueError(f"unknown scope: {scope}")


def _scope_filter(scope: SettingScope, project_id, user_id) -> list:
    """Clauses WHERE isolant un scope (ferme les trous NULL des index partiels)."""
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
    """Valeur effective d'un setting (démasquée), avec default si absent.

    ``None`` si la clé n'est pas définie et n'a pas de default.
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
    """Upsert d'un setting (respecte les index uniques partiels du lot 1).

    Chiffre si secret, stocke en clair UTF-8 sinon.

    ``commit=False`` : ne pas committer (le commit est fait par l'appelant, pour
    grouper plusieurs set/delete dans une seule transaction — voir
    ``admin.update_settings``). Le comportement par défaut (``commit=True``) est
    inchangé pour les autres appelants.
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
    """Supprime la ligne (revert au default si la clé en a un).

    ``commit=False`` : ne pas committer (commit par l'appelant, cf. ``set_setting``).
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
    """Toutes les valeurs d'un scope : secrets définis → ``MASK``, non définis → ``None``."""
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
