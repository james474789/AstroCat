"""QA-48: /catalogs/messier/{designation} reports the same image stats as the list."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import catalogs


@pytest.mark.asyncio
async def test_detail_includes_image_stats():
    item = SimpleNamespace(designation="M31")
    first = MagicMock()
    first.scalar_one_or_none.return_value = item
    second = MagicMock()
    second.one.return_value = (120.0, 2, 0.4)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[first, second])

    result = await catalogs.get_messier("M31", db)

    assert result.image_count == 2
    assert result.cumulative_exposure_seconds == 120.0
    assert result.max_separation_degrees == 0.4
