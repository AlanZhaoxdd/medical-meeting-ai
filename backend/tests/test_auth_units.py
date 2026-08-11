from types import SimpleNamespace

import pytest

from app.core.auth import require_exact_role
from app.core.exceptions import ForbiddenError
from app.schemas.kb import Role


@pytest.mark.asyncio
async def test_settings_require_exact_it_admin_role() -> None:
    dependency = require_exact_role(Role.ADMIN)

    assert await dependency(SimpleNamespace(role=Role.ADMIN)) == SimpleNamespace(role=Role.ADMIN)
    with pytest.raises(ForbiddenError):
        await dependency(SimpleNamespace(role=Role.OWNER))
