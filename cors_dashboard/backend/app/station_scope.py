"""Station access scope shared by control and read APIs."""

from sqlalchemy.ext.asyncio import AsyncSession

from . import auth, crud, models


async def _rtcm_visible_serials(auth_db: AsyncSession, devices_db: AsyncSession, user: models.User) -> set[str] | None:
    if user.role == auth.Role.ADMIN:
        return None
    if user.role == auth.Role.COORDINATOR:
        return await crud.get_coordinator_visible_base_serials(devices_db, user)
    return await crud.get_viewer_visible_base_serials(auth_db, devices_db, user)

