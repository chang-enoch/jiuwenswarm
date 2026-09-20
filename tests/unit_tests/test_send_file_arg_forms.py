"""send_file_to_user 入参形态：数组必须能进来，四种形态都要能归一化。

背景：工具描述写了「accepts a single path string or an array of paths」、示例却是
Python 字面量 ``['a', 'b']``，而 schema 声明 ``type: string`` —— 模型传真数组时会在
参数校验层直接 `[189001] validate data with schema failed` 失败（工具根本没执行）；
照抄示例传单引号串时又会因 ``json.loads`` 失败被当成一个路径。这里把两种行为都锁住。
"""
import asyncio

import pytest

from jiuwenswarm.agents.harness.common.tools import send_file_to_user as sfu


def _real_input_params() -> dict:
    toolkit = sfu.SendFileToolkit(
        request_id="req-1", session_id="sess-1", channel_id="desktop"
    )
    tools = toolkit.get_tools()
    assert len(tools) == 1
    return tools[0].card.input_params


def test_schema_allows_both_string_and_array() -> None:
    props = _real_input_params()["properties"]["abs_file_path_list"]
    assert "anyOf" in props, "abs_file_path_list 必须同时接受字符串与数组"
    types = [branch.get("type") for branch in props["anyOf"]]
    assert types == ["string", "array"]
    assert props["anyOf"][1]["items"] == {"type": "string"}


def test_tool_description_has_no_python_literal_example() -> None:
    toolkit = sfu.SendFileToolkit(
        request_id="req-1", session_id="sess-1", channel_id="desktop"
    )
    description = toolkit.get_tools()[0].card.description
    # 描述里不能再出现 ['a', 'b'] 这种 Python 字面量示例（非法 JSON，模型照抄就出错）
    assert "['" not in description
    assert '["/tmp/file1.csv", "/tmp/file2.xlsx"]' in description


@pytest.mark.parametrize(
    "raw, expected",
    [
        (["C:/a.txt", "C:/b.txt"], ["C:/a.txt", "C:/b.txt"]),          # 原生数组
        ('["C:/a.txt", "C:/b.txt"]', ["C:/a.txt", "C:/b.txt"]),        # 双引号 JSON 串
        ("['C:/a.txt', 'C:/b.txt']", ["C:/a.txt", "C:/b.txt"]),        # 单引号字面量串
        ("C:/a.txt", ["C:/a.txt"]),                                    # 单路径字符串
        (None, []),                                                     # 空
    ],
)
def test_normalize_abs_file_path_list(raw, expected) -> None:
    assert sfu._normalize_abs_file_path_list(raw) == expected


def _invoke_schema(params: dict, inputs: dict) -> str:
    """走真实的 LocalFunction 校验/格式化链路，返回工具函数收到的值。"""
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

    async def fake_send_file(abs_file_path_list=None, target_channels=None, **_ignored):
        return f"{type(abs_file_path_list).__name__}:{abs_file_path_list!r}"

    tool = LocalFunction(
        card=ToolCard(name="send_file_to_user", description="d", input_params=params),
        func=fake_send_file,
    )
    return asyncio.run(tool.invoke(inputs))


def test_array_argument_passes_tool_validation() -> None:
    params = _real_input_params()
    got = _invoke_schema(params, {"abs_file_path_list": ["C:/a.txt", "C:/b.txt"]})
    assert got == "list:['C:/a.txt', 'C:/b.txt']"
    got_single = _invoke_schema(params, {"abs_file_path_list": "C:/a.txt"})
    assert got_single == "str:'C:/a.txt'"
