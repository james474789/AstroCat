"""QA-46: /search/coordinates rejects out-of-range input instead of querying with it."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import search
from app.database import get_db


@pytest.fixture
def client():
    app = FastAPI()
    app.state.limiter = search.limiter
    app.include_router(search.router, prefix="/search")

    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db
    return TestClient(app)


@pytest.mark.parametrize("params", [
    {"ra": 999, "dec": 0},
    {"ra": -1, "dec": 0},
    {"ra": 10, "dec": 95},
    {"ra": 10, "dec": -91},
    {"ra": 10, "dec": 10, "radius": -5},
    {"ra": 10, "dec": 10, "radius": 400},
])
def test_out_of_range_is_422(client, params):
    assert client.get("/search/coordinates", params=params).status_code == 422
