"""Mojing 应用配置。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from simpleclaw.llm.config import VolcengineConfig

load_dotenv()

_WORKSPACE = Path(__file__).parent / "workspace"

# App 与 Device 使用同一个主 Agent/loop，但静态 prompt bundle 分开装配。
# compliance.md 是跨主 Agent + 子 Agent 共用的合规约束；同样通过 load_compliance() 注入到子 Agent。
_APP_PROMPT_FILES = ("Agent.md", "SOUL.md", "TOOL.md")
_DEVICE_PROMPT_FILES = ("Agent.md", "SOUL.md", "TOOL.md")
_STABLE_FILES = (*_APP_PROMPT_FILES, "compliance.md")  # backwards-compatible alias for tooling
_JOURNEY_DIR = _WORKSPACE / " journey"   # App 阶段策略目录（注意前置空格，历史遗留）
_COMPLIANCE_FILE = "compliance.md"
_DEVICE_PROMPT_DIR = _WORKSPACE / "device"
_DEVICE_JOURNEY_DIR = _DEVICE_PROMPT_DIR / "journey"


def make_image_analysis_url() -> str:
    return os.environ["MOJING_IMAGE_ANALYSIS_URL"]


def make_deep_research_url() -> str:
    return os.environ["MOJING_DEEP_RESEARCH_URL"]


def make_tos_config() -> dict[str, str]:
    """火山 TOS 直传配置（/admin/lab 历史照片回填用）。

    AK/SK 缺失时返回空串，由调用方（lab 路由）给出友好报错，不在启动期 fail fast——
    该配置仅测试工具使用，不应阻塞主服务启动。
    """
    return {
        "access_key": os.getenv("TOS_ACCESS_KEY", "").strip(),
        "secret_key": os.getenv("TOS_SECRET_KEY", "").strip(),
        "bucket": os.getenv("TOS_BUCKET", "mojing-photo-cat").strip(),
        "region": os.getenv("TOS_REGION", "tos-cn-guangzhou").strip(),
        "endpoint": os.getenv("TOS_ENDPOINT", "tos-cn-guangzhou.volces.com").strip(),
    }


def make_lab_backfill_profile_timeout_s() -> int:
    """/admin/lab 回填时单张照片等待外部分析画像落库的超时秒数。"""
    return max(10, int(os.getenv("LAB_BACKFILL_PROFILE_TIMEOUT_S", "120")))


def make_cabinet_import_url() -> str:
    return os.getenv(
        "MOJING_CABINET_IMPORT_URL",
        "http://118.145.101.96:3000/api/cabinet/products/import-by-name",
    ).strip()


def make_skin_diary_crop_url() -> str:
    return os.getenv("SKIN_DIARY_CROP_ENDPOINT_URL", "").strip()


def make_skin_diary_crop_timeout_s() -> int:
    return max(1, int(os.getenv("SKIN_DIARY_CROP_TIMEOUT_S", "20")))


def make_cabinet_product_research_timeout_min() -> int:
    return max(1, int(os.getenv("CABINET_PRODUCT_RESEARCH_TIMEOUT_MIN", "3")))


def make_deep_research_timeout_min() -> int:
    return max(1, int(os.getenv("DEEP_RESEARCH_TIMEOUT_MIN", "30")))


def make_device_command_url() -> str:
    return os.getenv(
        "DEVICE_COMMAND_API_URL",
        "https://test.onrunlab.com/mojing/app-api/agent/device/command",
    )


def make_device_command_timeout_s() -> float:
    return float(os.getenv("DEVICE_COMMAND_TIMEOUT_S", "90"))


def make_photo_capture_wait_timeout_s() -> float:
    return max(1.0, float(os.getenv("PHOTO_CAPTURE_WAIT_TIMEOUT_S", "15")))


def make_device_status_url() -> str:
    return os.getenv(
        "DEVICE_STATUS_API_URL",
        "https://test.onrunlab.com/mojing/app-api/agent/device/status",
    )


def make_device_status_timeout_s() -> float:
    return float(os.getenv("DEVICE_STATUS_TIMEOUT_S", "10"))


def make_device_dismiss_url() -> str:
    return os.getenv(
        "DEVICE_DISMISS_API_URL",
        "https://test.onrunlab.com/mojing/app-api/agent/session/dismiss",
    )


def make_device_dismiss_timeout_s() -> float:
    return float(os.getenv("DEVICE_DISMISS_TIMEOUT_S", "10"))


def make_baidu_weather_url() -> str:
    return os.getenv("BAIDU_WEATHER_URL", "https://api.map.baidu.com/weather/v1/").strip()


def make_baidu_map_ak() -> str:
    return os.getenv("BAIDU_MAP_AK", "").strip()


def make_weather_timeout_s() -> float:
    return float(os.getenv("WEATHER_TIMEOUT_S", "3"))


def make_llm_config() -> VolcengineConfig:
    """主 Agent / 子 Agent 用的 pro 模型，启用 prefix cache。
    """
    return VolcengineConfig(
        api_key=os.environ["VOLCENGINE_API_KEY"],
        api_base=os.getenv("VOLCENGINE_API_BASE"),
        model=os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215"),
        thinking=False,
    )


def make_hook_llm_config() -> VolcengineConfig:
    """后台异步任务（postprocess / cold path）用的 lite 模型，独立实例。

    """
    return VolcengineConfig(
        api_key=os.environ["VOLCENGINE_API_KEY"],
        api_base=os.getenv("VOLCENGINE_API_BASE"),
        model=os.getenv("VOLCENGINE_HOOK_MODEL", "doubao-seed-2-0-lite-260428"),
        thinking=False,
    )


def make_first_token_llm_config() -> VolcengineConfig:
    """first_token_llm 用的无工具 mini 模型，走 Responses prefix cache。"""
    api_key = os.getenv("VOLCENGINE_FIRST_TOKEN_API_KEY", "").strip() or os.environ["VOLCENGINE_API_KEY"]
    return VolcengineConfig(
        api_key=api_key,
        api_base=os.getenv("VOLCENGINE_API_BASE"),
        model=os.getenv("VOLCENGINE_FIRST_TOKEN_MODEL", "doubao-seed-2-0-mini-260428"),
        temperature=float(os.getenv("FIRST_TOKEN_TEMPERATURE", "0.6")),
        max_tokens=int(os.getenv("FIRST_TOKEN_MAX_TOKENS", "48")),
        thinking=False,
        prefix_cache=True,
    )


def make_first_token_enabled() -> bool:
    value = os.getenv("FIRST_TOKEN_LLM_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def make_first_token_timeout_s() -> float:
    return max(float(os.getenv("FIRST_TOKEN_TIMEOUT_MS", "2500")) / 1000.0, 0.05)


def make_task_queue():
    """返回 InMemoryTaskQueue 或 RedisTaskQueue，取决于 REDIS_URL 是否配置。

    本地开发无需 Redis，留空 REDIS_URL 即可用内存队列。
    """
    from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue

    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        return RedisTaskQueue(url=redis_url, stream_prefix=make_task_stream_prefix())
    return InMemoryTaskQueue()


def make_task_stream_prefix() -> str:
    return os.getenv("MOJING_TASK_STREAM_PREFIX", "mojing:tasks").strip() or "mojing:tasks"


def make_task_consumer_group() -> str:
    return os.getenv("MOJING_TASK_CONSUMER_GROUP", "mojing").strip() or "mojing"


def make_dream_mutation_enabled(tenant_key: str = "") -> bool:
    """Whether DreamSubagent may apply memory/document changes directly.

    Default is test-only: tenants created by scenario tests (`test_*`) can
    exercise mutation tools, while production tenants remain artifact-only.
    """

    value = os.getenv("MOJING_DREAM_MUTATION_ENABLED", "test").strip().lower()
    if value in {"1", "true", "yes", "on", "all"}:
        return True
    if value in {"0", "false", "no", "off", "none"}:
        return False
    return str(tenant_key or "").startswith("test_")


def make_dream_idle_threshold_s() -> int:
    """session 静默多久后才触发 dream（秒）。默认 1 小时。"""
    return max(60, int(os.getenv("MOJING_DREAM_IDLE_THRESHOLD_S", "3600")))


def make_db_kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
        minsize=int(os.getenv("MYSQL_POOL_MIN", "2")),
        maxsize=int(os.getenv("MYSQL_POOL_MAX", "30")),
        pool_recycle=int(os.getenv("MYSQL_POOL_RECYCLE", "3600")),
    )


def load_compliance() -> str:
    """读取共享合规约束文本（主 Agent + 子 Agent 共用）。

    主 Agent 通过 load_stable_sections() 自动包含；子 Agent 在 make_context_builder()
    里显式调用此函数追加到 stable_sections。
    """
    p = _WORKSPACE / _COMPLIANCE_FILE
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def load_workspace_section(name: str) -> str:
    """读取 workspace 下的单个稳定提示词文件。"""
    filename = str(name or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        return ""
    p = _WORKSPACE / filename
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def load_stable_sections(stage: str = "novice", prompt_surface: str = "app") -> list[str]:
    """从 workspace 目录读取稳定段落，按顺序返回字符串列表。

    App surface:
        workspace/Agent.md → SOUL.md → TOOL.md → compliance.md → workspace/ journey/{stage}.md

    Device surface:
        workspace/device/Agent.md → SOUL.md → TOOL.md → compliance.md
        → workspace/device/journey/{stage}.md（缺失时只回退到 device/journey/novice.md）

    Device 是独立 prompt bundle，不再继承 App 的 Agent/SOUL/TOOL/journey。
    """
    surface_key = _normalize_prompt_surface(prompt_surface)
    if surface_key == "device":
        return _load_device_stable_sections(stage)
    return _load_app_stable_sections(stage)


def _load_app_stable_sections(stage: str = "novice") -> list[str]:
    sections = _load_existing_files(_WORKSPACE / name for name in _APP_PROMPT_FILES)
    compliance = load_compliance()
    if compliance:
        sections.append(compliance)
    journey_content = _read_prompt_file(_JOURNEY_DIR / f"{_safe_stage_name(stage)}.md")
    if journey_content:
        sections.append(journey_content)
    return sections


def _load_device_stable_sections(stage: str = "novice") -> list[str]:
    sections = _load_existing_files(_DEVICE_PROMPT_DIR / name for name in _DEVICE_PROMPT_FILES)
    compliance = load_compliance()
    if compliance:
        sections.append(compliance)
    journey_path = _resolve_device_journey_path(stage)
    if journey_path is not None:
        journey_content = _read_prompt_file(journey_path)
        if journey_content:
            sections.append(journey_content)
    return sections


def _load_existing_files(paths) -> list[str]:
    sections: list[str] = []
    for path in paths:
        content = _read_prompt_file(path)
        if content:
            sections.append(content)
    return sections


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _resolve_device_journey_path(stage: str = "novice") -> Path | None:
    stage_name = _safe_stage_name(stage)
    requested = _DEVICE_JOURNEY_DIR / f"{stage_name}.md"
    if requested.exists():
        return requested
    fallback = _DEVICE_JOURNEY_DIR / "novice.md"
    if fallback.exists():
        return fallback
    return None


def _safe_stage_name(stage: str = "novice") -> str:
    name = str(stage or "novice").strip() or "novice"
    if "/" in name or "\\" in name:
        return "novice"
    return name


def _normalize_prompt_surface(prompt_surface: str = "app") -> str:
    surface = str(prompt_surface or "app").strip().lower()
    return surface if surface in {"app", "device"} else "app"


# ---------------------------------------------------------------------------
# 历史兼容辅助函数（保留以兼容旧接口）
# ---------------------------------------------------------------------------

def load_system_prompt(tenant_key: str = "__default__") -> str:
    """返回单一字符串格式的系统提示词（旧版接口，不含记忆/动态尾部）。"""
    sections = load_stable_sections()
    return "\n\n---\n\n".join(sections)
