"""导出 GitHub Pages 静态版所需的 JSON 数据。

直接调用 ``web.backend.app`` 的接口函数，保证静态版与本地动态版使用同一份
计算逻辑和数字，不做二次加工。

用法：

    python scripts/export_static.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.predict import (  # noqa: E402
    DEFAULT_INPUTS,
    FLOOR_CODE,
    FLOOR_RATIO,
    ORIENTATION_INPUTS,
    ORIENTATION_SCORE,
    effective_ridge_coefficients,
    load_context,
)
from web.backend.app import (  # noqa: E402
    model_importance,
    model_metrics,
    orientation,
    overview,
    preprocessing,
)

OUTPUT_DIR = PROJECT_ROOT / "web" / "frontend" / "data"


def _sanitize(value):
    """把 NaN/Inf 转为 None，保证浏览器 JSON.parse 能正常解析。"""
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _dump(name: str, payload: dict) -> Path:
    path = OUTPUT_DIR / f"{name}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean = _sanitize(payload)
    path.write_text(
        json.dumps(clean, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def export_all() -> dict[str, str]:
    outputs: dict[str, str] = {}
    for name, function in (
        ("overview", overview),
        ("preprocessing", preprocessing),
        ("orientation", orientation),
        ("metrics", model_metrics),
        ("importance", model_importance),
    ):
        outputs[name] = str(_dump(name, function()))

    # 静态预测模型：把 StandardScaler 吸收进 Ridge 系数，浏览器端可直接点乘。
    try:
        context = load_context()
        coefficients, intercept = effective_ridge_coefficients(columns=context["columns"])
        static_model = {
            "features": context["columns"],
            "coefficients": coefficients,
            "intercept": intercept,
            "defaults": context["defaults"],
            "districts": context["districts"],
            "orientations": context["orientations"],
            "building_types": context["building_types"],
            "orientation_options": list(ORIENTATION_INPUTS),
            "floor_options": list(FLOOR_RATIO),
            "floor_ratio": FLOOR_RATIO,
            "floor_code": FLOOR_CODE,
            "orientation_score": ORIENTATION_SCORE,
            "orientation_inputs": ORIENTATION_INPUTS,
            "default_inputs": DEFAULT_INPUTS,
            "target": "log_unit_price",
        }
        outputs["static_model"] = str(_dump("static_model", static_model))
    except (FileNotFoundError, KeyError, ValueError) as exc:
        outputs["static_model"] = str(_dump("static_model", {"available": False, "reason": str(exc)}))

    return outputs


def main() -> int:
    outputs = export_all()
    print(f"静态数据已导出 {len(outputs)} 个文件到 {OUTPUT_DIR}")
    for name, path in outputs.items():
        size = Path(path).stat().st_size
        print(f"  {name}.json: {size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
