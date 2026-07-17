"""Flowcut 浏览器自动化封装层。

基于 Playwright 通过 CDP 连接到一个长驻 Chrome 实例（由 scripts/start-chrome-qianchuan.sh 启动）。
登录态持久化在 user-data-dir 中，跨任务保留。

不依赖 simpleclaw 上游代码 —— 完全是 Flowcut 业务模块内的工具。
"""
from .client import BrowserClient
from .network import NetworkRecorder

__all__ = ["BrowserClient", "NetworkRecorder"]
