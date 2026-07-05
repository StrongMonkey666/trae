"""配置加载。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .exceptions import ConfigError


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "settings.yaml"


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """加载 YAML 配置文件；未传路径则使用默认 config/settings.yaml。"""
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not p.is_file():
        raise ConfigError(f"配置文件不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ConfigError("配置文件根节点必须是 mapping")
    return cfg


def deep_get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """安全读取嵌套字典。"""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur
