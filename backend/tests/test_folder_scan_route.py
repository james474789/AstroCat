import asyncio


def test_trigger_folder_scan_queues_selected_path(monkeypatch):
    from app.api.indexer import trigger_folder_scan

    calls = {}

    class DummyTask:
        id = "task-123"

    def fake_delay(path):
        calls["path"] = path
        return DummyTask()

    import app.tasks.indexer as indexer_tasks
    monkeypatch.setattr(indexer_tasks.scan_directory, "delay", fake_delay)

    result = asyncio.run(trigger_folder_scan({"path": "/data/test/folder"}, None))

    assert result == {
        "message": "Folder scan started",
        "task_id": "task-123",
        "path": "/data/test/folder",
    }
    assert calls["path"] == "/data/test/folder"


def test_trigger_folder_scan_rejects_invalid_path():
    from app.api.indexer import trigger_folder_scan

    result = asyncio.run(trigger_folder_scan({"path": "/tmp/not-allowed"}, None))

    assert result == {"error": "Invalid path format. Must start with /data/"}
