"""Model-facing tool descriptions must be English when no bilingual branch exists."""

from jiuwenswarm.agents.harness.common.tools.channel_config_tools import (
    configure_channel,
    get_wechat_login_status,
)
from jiuwenswarm.agents.harness.common.tools.memory_tools import (
    edit_memory,
    memory_get,
    memory_search,
    read_memory,
    write_memory,
)


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def test_channel_configuration_tool_descriptions_are_english() -> None:
    for tool in (configure_channel, get_wechat_login_status):
        assert not _contains_cjk(tool.card.description)


def test_memory_tool_descriptions_are_english() -> None:
    for tool in (memory_search, memory_get, write_memory, edit_memory, read_memory):
        assert not _contains_cjk(tool.card.description)
