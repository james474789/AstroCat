"""QA-38: a rescan with no astrometry key is a clear refusal, not a 500."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import images as images_api
from app.models.image import FrameType


@pytest.mark.asyncio
async def test_rescan_without_key_is_409():
    image = SimpleNamespace(id=29, frame_type=FrameType.LIGHT, subtype=None, astrometry_status="NONE")
    result = MagicMock()
    result.scalar_one_or_none.return_value = image
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    with patch("app.config.settings.astrometry_api_key", ""), \
         patch("redis.from_url", side_effect=RuntimeError("no redis")):
        with pytest.raises(HTTPException) as exc:
            await images_api.rescan_image(29, False, db)

    assert exc.value.status_code == 409
    assert "API key" in exc.value.detail
