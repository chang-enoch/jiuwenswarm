# -*- coding: utf-8 -*-
"""
记忆数据采集器

功能：
- 读取今日记忆文件
- 读取长期记忆
- 提取工作摘要
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_REPORT_TZ = ZoneInfo("Asia/Shanghai")


def legacy_memory_enabled(workspace_dir: str | Path) -> bool:
    """Use Swarm's legacy switch; standalone skill processes read its YAML."""
    try:
        from jiuwenswarm.common.config import get_config
        from jiuwenswarm.agents.harness.common.memory.external_memory_config import is_legacy_workspace_memory_enabled
    except ImportError:
        pass
    else:
        return is_legacy_workspace_memory_enabled(get_config())
    try:
        import yaml
    except ImportError:
        return False
    try:
        roots = ([Path(os.environ["JIUWENSWARM_DATA_DIR"])]
                 if os.environ.get("JIUWENSWARM_DATA_DIR") else list(Path(workspace_dir).resolve().parents))
        config_file = next(root / "config/config.yaml" for root in roots
                           if (root / "config/config.yaml").is_file())
        config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        def resolve(value):
            return re.sub(r"\$\{([^:}]+)(?::-([^}]*))?\}",
                          lambda m: os.environ.get(m[1], m[2] or ""), str(value))
        memory = config.get("memory", {})
        engine = resolve(memory.get("engine", "builtin")).strip().lower()
        provider = resolve(memory.get("external", {}).get("provider", "")).strip().lower()
        enabled = config.get("modes", {}).get("agent", {}).get("memory", {}).get("enabled", False)
        return ((engine in {"external", "both"} and provider == "old-celia")
                or (engine in {"builtin", "both"} and resolve(memory.get("mode", "local")).strip().lower() == "local"
                    and resolve(enabled).strip().lower() == "true"))
    except (OSError, ValueError, TypeError, AttributeError, StopIteration, yaml.YAMLError):
        return False


@dataclass
class MemoryData:
    """记忆数据"""

    today_content: str = ""  # 今日记忆内容
    long_term_content: str = ""  # 长期记忆内容
    work_summaries: list[str] = field(default_factory=list)  # 工作摘要列表
    key_decisions: list[str] = field(default_factory=list)  # 关键决策

    def to_dict(self) -> dict:
        return {
            "today_content": self.today_content[:500] if self.today_content else "",
            "work_summaries": self.work_summaries,
            "key_decisions": self.key_decisions,
        }


class MemoryCollector:
    """记忆数据采集器"""

    def __init__(self, workspace_dir: str | Path):
        """
        初始化记忆采集器

        Args:
            workspace_dir: Agent 根目录（如 ~/.jiuwenswarm/agent）
        """
        self.workspace_dir = Path(workspace_dir)
        self.memory_dir = self.workspace_dir / "memory"

    @staticmethod
    def _read_file_safe(file_path: Path) -> str:
        """安全读取文件"""
        if not file_path.exists():
            return ""
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _extract_list_items(content: str) -> list[str]:
        """提取列表项（以 - 或 * 开头的行）"""
        items = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                item = stripped.lstrip("-* ").strip()
                # 跳过注释和空项
                if item and not item.startswith("<!--"):
                    items.append(item)
        return items

    @staticmethod
    def _extract_sections(content: str, section_title: str) -> list[str]:
        """提取指定标题下的内容"""
        items = []
        in_section = False

        for line in content.split("\n"):
            stripped = line.strip()

            # 检测标题
            if stripped.startswith("##"):
                if section_title.lower() in stripped.lower():
                    in_section = True
                else:
                    in_section = False
                continue

            if in_section:
                if stripped.startswith("-") or stripped.startswith("*"):
                    item = stripped.lstrip("-* ").strip()
                    if item and not item.startswith("<!--"):
                        items.append(item)

        return items

    def collect(self, date: Optional[str] = None) -> MemoryData:
        """
        采集记忆数据

        Args:
            date: 日期字符串 (YYYY-MM-DD)，默认今天

        Returns:
            MemoryData: 记忆数据
        """
        if date is None:
            date = datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")

        data = MemoryData()
        if not legacy_memory_enabled(self.workspace_dir):
            return data

        # 读取今日记忆
        today_file = self.memory_dir / f"{date}.md"
        data.today_content = self._read_file_safe(today_file)

        # 读取长期记忆
        memory_file = self.memory_dir / "MEMORY.md"
        data.long_term_content = self._read_file_safe(memory_file)

        # 提取工作摘要
        data.work_summaries = self._extract_list_items(data.today_content)

        # 提取关键决策（从长期记忆中）
        data.key_decisions = self._extract_sections(
            data.long_term_content, "决策"
        ) or self._extract_sections(data.long_term_content, "偏好")

        return data

    def get_week_memories(self, end_date: Optional[str] = None) -> dict[str, MemoryData]:
        """获取一周的记忆数据"""
        if end_date is None:
            end_date = datetime.now(_REPORT_TZ)
        else:
            end_date = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=_REPORT_TZ)

        result = {}
        for i in range(7):
            date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            result[date] = self.collect(date)

        return result

    def get_month_memories(self, year: int, month: int) -> dict[str, MemoryData]:
        """获取一月的记忆数据"""
        import calendar

        _, days_in_month = calendar.monthrange(year, month)
        result = {}

        for day in range(1, days_in_month + 1):
            date = f"{year:04d}-{month:02d}-{day:02d}"
            result[date] = self.collect(date)

        return result
