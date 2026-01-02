import re
import shutil
from enum import Enum
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image
from astrbot.core.platform import AstrMessageEvent


# =========================
# 日志等级枚举
# =========================
class LogLevel(Enum):
    ALL = "ALL"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @classmethod
    def from_input(cls, value: str):
        if not value:
            return cls.ALL

        value = value.upper()
        for item in cls:
            if item.value == value:
                return item
        return None

    @classmethod
    def choices(cls) -> str:
        return " / ".join(i.value for i in cls)


# =========================
# 插件主体
# =========================
class LoggerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_logger")
        self.image_cache_dir = self.data_dir / "image_cache"
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)
        self.style = None

    async def initialize(self):
        try:
            import pillowmd

            style_path = Path(self.conf["pillowmd_style_dir"]).resolve()
            self.style = pillowmd.LoadMarkdownStyles(style_path)
        except Exception as e:
            logger.error(f"加载 pillowmd 失败: {e}")

    async def terminate(self):
        if self.conf.get("clean_cache") and self.image_cache_dir.exists():
            try:
                shutil.rmtree(self.image_cache_dir)
            except Exception as e:
                logger.error(f"清理缓存失败: {e}")
            self.image_cache_dir.mkdir(parents=True, exist_ok=True)

    # =========================
    # 工具方法
    # =========================

    async def _t2i(self, text: str) -> Path:
        if not self.style:
            raise RuntimeError("pillowmd 未初始化")

        img = await self.style.AioRender(
            text=text,
            useImageUrl=True,
        )
        return img.Save(self.image_cache_dir)

    def _safe_get_logs(self) -> list:
        try:
            return self.context.get_logs()
        except Exception as e:
            logger.error(f"获取日志失败: {e}")
            return []

    @staticmethod
    def _slice_logs(logs: list, limit: str, default_limit: int):
        total = len(logs)

        if not limit:
            return logs[-default_limit:]

        limit = limit.strip()

        # 最近 N 条
        if limit.isdigit():
            return logs[-int(limit) :]

        # 范围 a-b
        if "-" in limit:
            left, right = limit.split("-", 1)

            start = int(left) - 1 if left.isdigit() else 0
            end = int(right) if right.isdigit() else total

            start = max(start, 0)
            end = min(end, total)

            return logs[start:end]

        return logs[-default_limit:]

    @staticmethod
    def _format_log(log: dict) -> str:
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        text = ansi.sub("", log.get("data", "")).rstrip()
        level = log.get("level", "UNKNOWN").upper()

        match level:
            case LogLevel.ERROR.value:
                return f"```\n{text}\n```"
            case LogLevel.WARNING.value:
                return f"**{text}**"
            case LogLevel.DEBUG.value:
                return text
            case _:
                return f"> {text}"

    def _filter_logs_by_level(self, logs: list, level: LogLevel) -> list:
        if level == LogLevel.ALL:
            return logs
        return [log for log in logs if log.get("level") == level.value]

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("log", alias={"logger", "日志", "查看日志"})
    async def on_log(
        self, event: AstrMessageEvent, level: str = "ALL", limit: str = ""
    ):
        """
        log <debug|info|warning|error|all> <start-end>
        """

        logs = self._safe_get_logs()

        log_level = LogLevel.from_input(level)
        if not log_level:
            yield event.plain_result(
                f"仅支持等级：{LogLevel.choices()}"
            )
            return

        logs = self._filter_logs_by_level(logs, log_level)
        logs = self._slice_logs(logs, limit, self.conf["log_limit"])

        if not logs:
            yield event.plain_result("暂无日志")
            return

        formatted = [self._format_log(log) for log in logs]
        img_path = await self._t2i("\n".join(formatted))
        yield event.chain_result([Image.fromFileSystem(str(img_path))])

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("logfind", alias={"搜索日志", "日志搜索"})
    async def on_log_search(self, event: AstrMessageEvent, keyword: str):
        """搜索日志 <关键字>"""
        logs = self._safe_get_logs()

        keyword_lower = keyword.lower()
        matched = [log for log in logs if keyword_lower in log.get("data", "").lower()]

        if not matched:
            yield event.plain_result(f"未找到包含 `{keyword}` 的日志")
            return

        formatted = [self._format_log(log) for log in matched]
        img_path = await self._t2i("\n".join(formatted))
        yield event.chain_result([Image.fromFileSystem(str(img_path))])
