"""
ChatGPT Team 促销码扫描工具 — 配置模块

CLI 配置优先级: 环境变量 > config.toml > 默认值
面板任务使用启动时的配置快照，优先于环境变量与旧配置文件。

必要配置:
  - OPENAI_TOKEN: ChatGPT accessToken（从浏览器 F12 获取）

可选配置:
  - CLASH_SOCKET: Clash Verge Unix socket 路径
  - HTTP_PROXY: HTTP 代理地址
"""

import os
import tomllib
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.toml"

_config = {}
_runtime = None


def apply_runtime_settings(settings):
    """Apply an isolated worker's dashboard snapshot without changing CLI configuration."""
    global _runtime
    _runtime = dict(settings)


def get_runtime_setting(name, default=None):
    return _runtime.get(name, default) if _runtime is not None else default


def _load_config():
    """读取 config.toml（如果存在）"""
    global _config
    if _config:
        return _config
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "rb") as f:
                _config = tomllib.load(f)
        except Exception:
            _config = {}
    else:
        _config = {}
    return _config


def get_token() -> str:
    """获取 OpenAI accessToken"""
    if _runtime is not None:
        token = _runtime.get("token", "")
        if token:
            return token
        raise ValueError("请在面板的「连接设置」中保存 ChatGPT Access Token")
    # 1. 环境变量
    token = os.getenv("OPENAI_TOKEN")
    if token:
        return token

    # 2. config.toml
    cfg = _load_config()
    token = cfg.get("openai", {}).get("token")
    if token:
        return token

    raise ValueError(
        "未设置 OPENAI_TOKEN\n"
        "  方式一: export OPENAI_TOKEN='...'\n"
        "  方式二: 在 config.toml 中设置 [openai] token = '...'\n\n"
        "获取方法:\n"
        "  1. 浏览器登录 chatgpt.com\n"
        "  2. F12 → Console\n"
        "  3. 执行:\n"
        "     const s = await (await fetch('/api/auth/session')).json();\n"
        "     console.log(s.accessToken);\n"
        "     复制输出的字符串"
    )


def get_clash_socket() -> str:
    """获取 Clash Verge Unix socket 路径"""
    if _runtime is not None:
        return _runtime["clash_socket"]
    env = os.getenv("CLASH_SOCKET")
    if env:
        return env
    cfg = _load_config()
    return cfg.get("clash", {}).get("socket", "/tmp/verge/verge-mihomo.sock")


def get_proxy_url() -> str:
    """获取 HTTP 代理地址"""
    if _runtime is not None:
        return _runtime["proxy_url"]
    env = (
        os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
    )
    if env:
        return env
    cfg = _load_config()
    return cfg.get("proxy", {}).get("url", "http://127.0.0.1:7890")


def get_proxy_group(mode: str) -> str:
    """根据 Clash 模式返回对应的代理组名
    rule → 🤖 AI    global → GLOBAL
    """
    if _runtime is not None:
        return _runtime["global_group" if mode == "global" else "rule_group"]
    cfg = _load_config()
    groups = cfg.get("clash", {}).get("proxy_groups", {})
    if mode == "global":
        return groups.get("global", "GLOBAL")
    return groups.get("rule", "🤖 AI")


def get_output_dir():
    """获取输出目录（默认为脚本所在目录）"""
    return os.path.dirname(os.path.abspath(__file__))
