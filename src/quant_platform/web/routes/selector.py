"""选股路由。"""
from flask import Blueprint, current_app, render_template, request, jsonify

from ...selector.schema import SelectorSpec
from ...selector.templates import list_templates

bp = Blueprint("selector", __name__, url_prefix="/selector")


@bp.route("/")
def index():
    return render_template(
        "selector/index.html",
        templates=list_templates(),
    )


@bp.route("/api/run", methods=["POST"])
def api_run():
    """调用选股服务（不持久化）。"""
    data = request.get_json(force=True, silent=True) or {}
    svc: SelectorService = current_app.config["selector_service"]
    template = data.get("template")
    json_spec = data.get("json")
    if template:
        from ...selector.templates import get_template
        spec = get_template(template)
    elif json_spec:
        spec = SelectorSpec.from_dict(json_spec)
    else:
        return jsonify({"error": "missing template or json"}), 400
    out = svc.run(spec, save=False)
    result = out["result"]
    cols = [c for c in ("code", "name", "close", "pe_ttm", "pb", "change_pct", "turnover_rate", "market_cap") if c in result.columns]
    return jsonify({
        "count": len(result),
        "rows": result[cols].to_dict(orient="records") if not result.empty else [],
        "spec": spec.to_dict(),
    })
