"""系统设置路由：在线查看/修改配置（LLM / 邮件 / 数据源）。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

import yaml
from flask import Blueprint, current_app, render_template, request, jsonify

from ...notify.email import SmtpClient, SmtpConfig
from ...utils.config import deep_get

bp = Blueprint("settings", __name__, url_prefix="/settings")

# 配置文件路径（相对于项目根）
_CONFIG_REL = "config/settings.yaml"


def _config_path() -> Path:
    return Path(__file__).resolve().parents[4] / _CONFIG_REL


def _load_yaml() -> Dict[str, Any]:
    p = _config_path()
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(data: Dict[str, Any]) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再替换，防止写到一半崩溃
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    shutil.move(str(tmp), str(p))


@bp.route("/")
def index():
    """设置总览页。"""
    cfg = _load_yaml()
    return render_template("settings/index.html", cfg=cfg)


@bp.route("/api/get", methods=["GET"])
def api_get():
    """返回完整配置（敏感字段脱敏）。"""
    cfg = _load_yaml()
    out = _sanitize(cfg)
    return jsonify(out)


@bp.route("/api/save", methods=["POST"])
def api_save():
    """保存配置（接受 partial dict，只更新传入的 section）。"""
    data = request.get_json(force=True, silent=True) or {}
    cfg = _load_yaml()
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        if section not in cfg:
            cfg[section] = {}
        cfg[section].update(values)
    _save_yaml(cfg)
    # 同步更新 Flask app 内缓存
    current_app.config["QUANT_CONFIG"] = cfg
    return jsonify({"ok": True, "message": "配置已保存"})


@bp.route("/api/test-smtp", methods=["POST"])
def api_test_smtp():
    """发送测试邮件。"""
    payload = request.get_json(force=True, silent=True) or {}
    host = payload.get("smtp_host", "")
    port = int(payload.get("smtp_port", 465))
    user = payload.get("smtp_user", "")
    password = payload.get("smtp_password", "")
    use_ssl = payload.get("smtp_ssl", True)
    from_addr = payload.get("from_addr", "") or user
    to_str = payload.get("to_addrs", "")

    if not host or not user or not password or not to_str:
        return jsonify({"ok": False, "message": "SMTP 配置不完整"}), 400

    to_addrs = [a.strip() for a in to_str.split(",") if a.strip()]
    try:
        smtp_cfg = SmtpConfig(
            host=host, port=port, username=user, password=password,
            use_ssl=use_ssl, from_addr=from_addr,
        )
        client = SmtpClient(smtp_cfg)
        client.send(
            subject="量化平台 - 邮件测试",
            body_text="这是一封来自量化平台的测试邮件。如果你看到了，说明 SMTP 配置正确。",
            to_addrs=to_addrs,
        )
        return jsonify({"ok": True, "message": f"测试邮件已发送至 {', '.join(to_addrs)}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"发送失败: {e}"}), 500


@bp.route("/api/test-llm", methods=["POST"])
def api_test_llm():
    """测试 LLM 连通性（发一条简单 prompt 验证 API Key）。"""
    payload = request.get_json(force=True, silent=True) or {}
    api_key = payload.get("api_key", "")
    base_url = payload.get("base_url", "")
    model = payload.get("model", "")
    timeout = int(payload.get("timeout", 30))

    if not api_key:
        return jsonify({"ok": False, "message": "API Key 不能为空"}), 400

    try:
        from ...llm.openai_compatible import OpenAICompatibleClient
        from ...llm.base import LLMMessage
        client = OpenAICompatibleClient(
            api_key=api_key, base_url=base_url or None,
            model=model or "gpt-4o-mini", timeout=timeout,
        )
        resp = client.chat([
            LLMMessage(role="user", content="回复 OK 两个字母即可"),
        ], max_tokens=10)
        return jsonify({
            "ok": True,
            "message": f"LLM 响应正常: {resp.content.strip()[:100]}",
            "model": resp.model,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": f"连接失败: {e}"}), 500


def _sanitize(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏：api_key / smtp_password / token 用 *** 掩盖。"""
    import copy
    out = copy.deepcopy(cfg)
    # LLM api_key
    key = deep_get(out, "llm", "api_key", default="")
    if key:
        out["llm"]["api_key"] = _mask(key)
    # notify smtp_password
    pwd = deep_get(out, "notify", "smtp_password", default="")
    if pwd:
        out["notify"]["smtp_password"] = _mask(pwd)
    # data_sources tokens
    for src in ("tushare",):
        tok = deep_get(out, "data_sources", src, "token", default="")
        if tok:
            out["data_sources"][src]["token"] = _mask(tok)
    return out


def _mask(s: str, show: int = 4) -> str:
    if len(s) <= show:
        return "****"
    return s[:show] + "****"
