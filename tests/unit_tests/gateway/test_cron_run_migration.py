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


# ── 手机建 PC 任务 targets=xiaoyi → web 归一 ────────────────────────────────


async def _create_job(store, **overrides):
    params = dict(
        name="手机建的提醒",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="reminder",
        targets="xiaoyi",
    )
    params.update(overrides)
    return await store.create_job(**params)


@pytest.mark.asyncio
async def test_phone_created_job_targets_normalized_to_web(tmp_path):
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    job = await _create_job(store)
    assert job.targets == "web"


@pytest.mark.asyncio
async def test_device_job_keeps_xiaoyi_targets(tmp_path):
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    job = await _create_job(
        store,
        required_device_intents=["alarm"],
        xiaoyi_push_id="push-1",
    )
    assert job.targets == "xiaoyi"


@pytest.mark.asyncio
async def test_update_patch_xiaoyi_targets_normalized(tmp_path):
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    job = await _create_job(store)
    updated = await store.update_job(job.id, {"targets": "xiaoyi"})
    assert updated.targets == "web"
    # 设备任务 update targets 不归一（结果必须回推手机）
    device = await _create_job(
        store,
        required_device_intents=["alarm"],
        xiaoyi_push_id="push-2",
    )
    device_updated = await store.update_job(device.id, {"targets": "web"})
    assert device_updated.targets == "web"


@pytest.mark.asyncio
async def test_existing_xiaoyi_job_migrated_on_list(tmp_path):
    """存量 targets=xiaoyi 任务：list_jobs 读时归一为 web 并写回文件。"""
    path = tmp_path / "cron_jobs.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "legacy-1",
                        "name": "旧手机任务",
                        "enabled": True,
                        "cron_expr": "0 9 * * *",
                        "timezone": "Asia/Shanghai",
                        "description": "legacy",
                        "targets": "xiaoyi",
                        "work_mode": "work",
                    },
                    {
                        "id": "device-1",
                        "name": "设备任务",
                        "enabled": True,
                        "cron_expr": "0 9 * * *",
                        "timezone": "Asia/Shanghai",
                        "description": "device",
                        "targets": "xiaoyi",
                        "work_mode": "work",
                        "required_device_intents": ["alarm"],
                        "xiaoyi_push_id": "push-3",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    store = CronJobStore(path=path)
    jobs = {j.id: j for j in await store.list_jobs()}
    assert jobs["legacy-1"].targets == "web"
    assert jobs["device-1"].targets == "xiaoyi"
    # 写回持久化：新实例读到的已是归一后的值
    reloaded = {j.id: j for j in await CronJobStore(path=path).list_jobs()}
    assert reloaded["legacy-1"].targets == "web"
    assert reloaded["device-1"].targets == "xiaoyi"
