import os
import tempfile

# isolated data dir BEFORE app imports
_tmp = tempfile.mkdtemp(prefix="agentalgo_test_")
os.environ["AGENTALGO_DATA_DIR"] = _tmp
os.environ["AGENTALGO_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app


@pytest.fixture(scope="session")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "tester@agentalgo.dev", "password": "secret123"},
    )
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
