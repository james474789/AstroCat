import asyncio

import app.database as database


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.commits += 1
        raise RuntimeError("underlying connection is closed")

    async def rollback(self):
        self.rollbacks += 1

    async def close(self):
        pass


async def _drive(session, restore_mid_request):
    gen = database.get_db()
    await gen.__anext__()
    if restore_mid_request:
        database.mark_database_restored()
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass


def test_commit_skipped_when_restore_killed_the_connection(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)
    asyncio.run(_drive(session, restore_mid_request=True))
    assert session.commits == 0
    assert session.rollbacks == 1


def test_normal_request_still_commits(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)
    try:
        asyncio.run(_drive(session, restore_mid_request=False))
    except RuntimeError:
        pass
    assert session.commits == 1
