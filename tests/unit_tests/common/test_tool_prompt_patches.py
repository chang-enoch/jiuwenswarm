from __future__ import annotations

import pytest

from jiuwenswarm.common.tool_prompt_patches import (
    apply_tool_prompt_patches,
    patch_async_dict_content,
)


@pytest.mark.asyncio
async def test_patch_async_dict_content_replaces_prompt_and_preserves_result() -> None:
    class FakeTool:
        async def read(self) -> dict[str, object]:
            return {"content": "before OLD after", "multimodal": []}

    applied = patch_async_dict_content(
        FakeTool,
        "read",
        patch_id="fake-tool-prompt",
        replacements={"OLD": "NEW"},
    )

    assert applied is True
    assert await FakeTool().read() == {
        "content": "before NEW after",
        "multimodal": [],
    }


@pytest.mark.asyncio
async def test_patch_async_dict_content_is_idempotent() -> None:
    calls = 0

    class FakeTool:
        async def read(self) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"content": "OLD"}

    first = patch_async_dict_content(
        FakeTool,
        "read",
        patch_id="fake-tool-prompt",
        replacements={"OLD": "NEW"},
    )
    second = patch_async_dict_content(
        FakeTool,
        "read",
        patch_id="fake-tool-prompt",
        replacements={"OLD": "NEW"},
    )

    assert first is True
    assert second is False
    assert await FakeTool().read() == {"content": "NEW"}
    assert calls == 1


@pytest.mark.asyncio
async def test_patch_async_dict_content_leaves_other_results_unchanged() -> None:
    class FakeTool:
        async def read(self, result: object) -> object:
            return result

    patch_async_dict_content(
        FakeTool,
        "read",
        patch_id="fake-tool-prompt",
        replacements={"OLD": "NEW"},
    )

    assert await FakeTool().read("OLD") == "OLD"
    assert await FakeTool().read({"content": 123}) == {"content": 123}
    assert await FakeTool().read({"content": "unrelated"}) == {"content": "unrelated"}


@pytest.mark.asyncio
async def test_default_patch_routes_disabled_image_read_to_xiaoyi_skill(monkeypatch) -> None:
    from openjiuwen.harness.tools.filesystem import ReadFileTool

    async def fake_read_image(self, file_path: str, model_name: str) -> dict[str, object]:
        return {
            "content": (
                "Image bytes are not attached.\n"
                "If a vision tool is configured, call image_ocr or "
                "visual_question_answering with this file path."
            ),
            "multimodal": [],
        }

    monkeypatch.setattr(ReadFileTool, "_read_image", fake_read_image)

    apply_tool_prompt_patches()
    result = await ReadFileTool._read_image(object(), "C:/image.png", "model")

    assert "xiaoyi-image-understanding-win" in result["content"]
    assert "image_ocr" not in result["content"]
    assert "visual_question_answering" not in result["content"]
    assert result["multimodal"] == []
