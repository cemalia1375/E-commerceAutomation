"""提供方配置数据类。

每个提供方拥有独立的 Config，包含连接参数
（api_key、api_base）和生成默认值（model、temperature、max_tokens）。

值应在启动时来自环境变量 — 切勿将密钥硬编码在源码中。
请参阅 .env.example 了解所需的变量名。

用法：
    import os
    from dotenv import load_dotenv
    from simpleclaw.llm.config import VolcengineConfig

    load_dotenv()
    config = VolcengineConfig(
        api_key=os.environ["VOLCENGINE_API_KEY"],
        api_base=os.getenv("VOLCENGINE_API_BASE"),
        model=os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GeminiConfig:
    """Google Gemini Interactions API 的连接与生成配置。"""

    # --- 连接 ---
    api_key: str

    # --- 模型 ---
    model: str = "gemini-3.1-flash-lite"

    # --- 生成默认值 ---
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class VolcengineConfig:
    """火山引擎 / 豆包 API 的连接与生成配置。"""

    # --- 连接 ---
    api_key: str
    api_base: str | None = None

    # --- 模型 ---
    model: str = "doubao-seed-2-0-pro-260215"

    # --- 生成默认值 ---
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking: bool = False          # 启用扩展思考（R1 风格模型）
    prefix_cache: bool = True       # 启用 Responses API 显式前缀缓存

    # --- 缓存 ---
    # 稳定的进程级亲和性令牌，使本实例所有请求都命中同一后端节点，
    # 从而最大化前缀缓存复用率。
    session_affinity_id: str | None = None

    # 转发给 AsyncOpenAI 客户端的额外 HTTP 头
    extra_headers: dict[str, str] = field(default_factory=dict)
