import asyncio
import json
from unittest.mock import patch

import pytest

from jiuwenswarm.gateway.cron.store import CronJobStore


def record(index, **extra):
    return {"id": str(index), "taskId": "job", "startedAt": index,
            "status": "success", "taskName": "历史提醒", **extra}


@pytest.mark.asyncio
async def test_migration_preserves_history_and_new_record_wins(tmp_path):
    legacy = tmp_path / "cron_desktop_runs.json"
    current = tmp_path / "cron_run_records.json"
    legacy.write_text(json.dumps([record(i) for i in range(600)] + [
        record(600, status="running", sessionId="desktop-session")]), encoding="utf-8")
    current.write_text(json.dumps([record(600, summary="新结果")]), encoding="utf-8")
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    rows = await store.list_run_records()
    assert len(rows) == 601
    assert rows[-1] == record(600, sessionId="desktop-session", summary="新结果")
    assert not legacy.exists()
    await store.save_run_record(record(601))
    assert len(await CronJobStore(path=store.path).list_run_records()) == 602


@pytest.mark.asyncio
async def test_failed_replace_keeps_both_files_then_retries(tmp_path):
    legacy = tmp_path / "cron_desktop_runs.json"
    current = tmp_path / "cron_run_records.json"
    legacy.write_text(json.dumps([record(1)]), encoding="utf-8")
    current.write_text(json.dumps([record(2)]), encoding="utf-8")
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    with patch("jiuwenswarm.gateway.cron.store.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await store.list_run_records()
    assert json.loads(legacy.read_text()) == [record(1)]
    assert json.loads(current.read_text()) == [record(2)]
    assert await store.list_run_records() == [record(1), record(2)]
    assert not legacy.exists()


@pytest.mark.asyncio
async def test_retry_after_interruption_does_not_duplicate_records(tmp_path):
    legacy = tmp_path / "cron_desktop_runs.json"
    legacy.write_text(json.dumps([record(1)]), encoding="utf-8")
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    with patch("pathlib.Path.unlink", side_effect=PermissionError("busy")):
        with pytest.raises(PermissionError):
            await store.list_run_records()
    assert legacy.exists()
    assert json.loads((tmp_path / "cron_run_records.json").read_text(encoding="utf-8")) == [record(1)]
    assert await store.list_run_records() == [record(1)]
    assert not legacy.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ['{', '{}', '[{"id":"bad"}]'])
async def test_invalid_legacy_file_is_not_deleted(tmp_path, invalid):
    legacy = tmp_path / "cron_desktop_runs.json"
    legacy.write_text(invalid, encoding="utf-8")
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    with pytest.raises(ValueError):
        await store.list_run_records()
    assert legacy.read_text() == invalid
    assert not (tmp_path / "cron_run_records.json").exists()


@pytest.mark.asyncio
async def test_concurrent_migration_and_save_keep_all_records(tmp_path):
    legacy = tmp_path / "cron_desktop_runs.json"
    legacy.write_text(json.dumps([record(1)]), encoding="utf-8")
    first = CronJobStore(path=tmp_path / "cron_jobs.json")
    second = CronJobStore(path=first.path)
    await asyncio.gather(first.list_run_records(), second.save_run_record(record(2)))
    assert await first.list_run_records() == [record(1), record(2)]
    assert not legacy.exists()


@pytest.mark.asyncio
async def test_no_legacy_file_needs_no_migration(tmp_path):
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    assert await store.list_run_records() == []
    assert not (tmp_path / "cron_run_records.json").exists()
