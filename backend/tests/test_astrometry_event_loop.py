"""QA-31: astrometry tasks must run in worker threads that have no event loop."""
import threading

from app.tasks import astrometry


def test_run_async_works_in_thread_without_event_loop():
    async def coro():
        return 42

    out = {}

    def target():
        try:
            out["v"] = astrometry._run_async(coro)
        except Exception as e:  # pragma: no cover
            out["e"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    assert out == {"v": 42}
