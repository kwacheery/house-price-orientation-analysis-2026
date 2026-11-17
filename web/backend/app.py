"""本地 FastAPI 后端：为前端提供真实数据接口并托管静态页面。

所有接口读取 ``reports/``、``models/artifacts/`` 与 ``data/processed/`` 的真实产物，
不硬编码任何数字；数据缺失时返回 ``available=false``，前端显示空状态而非白屏。

启动：

    uvicorn web.backend.app:app --reload
    python scripts/run_web.py
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "processed"
REPORT_DIR = PROJECT_ROOT / "reports"
ARTIFACT_DIR = PROJECT_ROOT / "models" / "artifacts"
FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend"

app = FastAPI(title="城市房价预测与朝向溢价分析", version="w5")


# ---------------------------------------------------------------------------
# 数据加载（带缓存）
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


@lru_cache(maxsize=1)
def _context() -> dict[str, Any]:
    from models.predict import load_context

    try:
        return load_context(DATA_DIR, ARTIFACT_DIR)
    except (FileNotFoundError, KeyError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _models() -> dict[str, Any]:
    import joblib

    path = ARTIFACT_DIR / "model_ridge.joblib"
    if not path.exists():
        return {}
    return {"ridge": joblib.load(path)}


@lru_cache(maxsize=1)
def _features() -> pd.DataFrame:
    return _read_csv(DATA_DIR / "features.csv")


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("available", True)
    return payload


def _empty(reason: str = "暂无数据") -> dict[str, Any]:
    return {"available": False, "reason": reason}


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    """数据概况：采集/清洗/建模各阶段样本量与来源分布。"""
    w1 = _read_json(REPORT_DIR / "pilot_w1_summary.json")
    prep = _read_json(REPORT_DIR / "preprocessing_summary.json")
    if not w1 and not prep:
        return _empty()

    features = _features()
    district_counts = (
        features["district"].value_counts().to_dict() if "district" in features.columns else {}
    )
    sources = []
    for item in w1.get("sources", {}).values():
        if isinstance(item, dict):
            sources.append({"source": item.get("source"), "records": item.get("records", 0)})
    if not sources and prep.get("sources"):
        sources = [{"source": key, "records": value} for key, value in prep["sources"].items()]

    totals = w1.get("totals", {})
    return _ok(
        {
            "totals": {
                "raw": totals.get("records", prep.get("raw", {}).get("lines", 0)),
                "clean": prep.get("rows", 0),
                "model_keep": prep.get("model_keep", 0),
                "main_sample": len(features),
                "train": int((features["split"] == "train").sum()) if "split" in features.columns else 0,
                "test": int((features["split"] == "test").sum()) if "split" in features.columns else 0,
            },
            "sources": sources,
            "district_counts": {str(k): int(v) for k, v in district_counts.items()},
            "city_count": prep.get("cities", 0),
            "generated_at": prep.get("generated_at", w1.get("generated_at", "")),
        }
    )


@app.get("/api/preprocessing")
def preprocessing() -> dict[str, Any]:
    """预处理前后对比：每步行数、缺失统计、异常值与区县来源。"""
    prep = _read_json(REPORT_DIR / "preprocessing_summary.json")
    if not prep:
        return _empty()
    w1 = _read_json(REPORT_DIR / "pilot_w1_summary.json")

    before: dict[str, float] = {}
    for item in w1.get("sources", {}).values():
        if not isinstance(item, dict):
            continue
        for field, detail in item.get("core_completeness", {}).items():
            if isinstance(detail, dict) and "rate" in detail:
                before[field] = before.get(field, 0.0) + float(detail["rate"]) / max(len(w1.get("sources", {})), 1)

    after = {
        key: float(detail.get("rate", 0.0))
        for key, detail in prep.get("completeness", {}).items()
        if isinstance(detail, dict)
    }
    return _ok(
        {
            "clean_version": prep.get("clean_version", ""),
            "summary": prep.get("summary", []),
            "missing": prep.get("missing", {}),
            "before": before,
            "after": after,
            "outliers": prep.get("outliers", {}),
            "unit_price_suspect": prep.get("unit_price_suspect", 0),
            "cross_source_dup": prep.get("cross_source_dup", 0),
            "villas": prep.get("villas", 0),
            "district_sources": prep.get("district_sources", {}),
            "generated_at": prep.get("generated_at", ""),
        }
    )


@app.get("/api/orientation")
def orientation() -> dict[str, Any]:
    """朝向分布、单价分位数、溢价估计、分区县与跨城市结果。"""
    features = _features()
    premium = _read_csv(REPORT_DIR / "orientation_premium.csv")
    district = _read_csv(REPORT_DIR / "district_premium.csv")
    cross = _read_csv(REPORT_DIR / "cross_city_premium.csv")
    summary = _read_json(REPORT_DIR / "premium.json")
    if features.empty or premium.empty:
        return _empty()

    distribution = []
    quantiles: dict[str, list[float]] = {}
    if "orientation_main" in features.columns:
        for name, group in features.groupby("orientation_main"):
            prices = pd.to_numeric(group["unit_price"], errors="coerce").dropna()
            if prices.empty:
                continue
            q = np.quantile(prices.to_numpy(), [0.0, 0.25, 0.5, 0.75, 1.0])
            quantiles[str(name)] = [round(float(value), 1) for value in q]
            distribution.append({"orientation": str(name), "n": len(group), "avg_price": round(float(prices.mean()), 1)})

    return _ok(
        {
            "distribution": distribution,
            "boxplot": quantiles,
            "premium": premium.where(pd.notna(premium), None).to_dict("records"),
            "district_premium": district.where(pd.notna(district), None).to_dict("records") if not district.empty else [],
            "cross_city": cross.where(pd.notna(cross), None).to_dict("records") if not cross.empty else [],
            "kruskal_wallis": summary.get("kruskal_wallis", {}),
            "reference": summary.get("reference", "北"),
            "n": summary.get("n", 0),
            "limitation": summary.get("limitation", ""),
        }
    )


@app.get("/api/model/metrics")
def model_metrics() -> dict[str, Any]:
    """模型指标、基线对比、消融与敏感性结果。"""
    metrics = _read_json(REPORT_DIR / "metrics.json")
    if not metrics:
        return _empty()
    cv = _read_csv(ARTIFACT_DIR / "cv_results.csv")
    return _ok(
        {
            "best_model": metrics.get("best_model", ""),
            "best_test_rmse_yuan": metrics.get("best_test_rmse_yuan", 0),
            "best_test_r2_yuan": metrics.get("best_test_r2_yuan", 0),
            "rmse_reduction_vs_district": metrics.get("rmse_reduction_vs_district", 0),
            "models": metrics.get("model_metrics", []),
            "ablation": metrics.get("ablation", []),
            "sensitivity": metrics.get("sensitivity", []),
            "cv": cv.where(pd.notna(cv), None).to_dict("records") if not cv.empty else [],
        }
    )


@app.get("/api/model/importance")
def model_importance() -> dict[str, Any]:
    """最优模型的 permutation importance（Top 15）。"""
    frame = _read_csv(ARTIFACT_DIR / "feature_importance.csv")
    if frame.empty:
        return _empty()
    return _ok(
        {
            "features": frame.head(15).to_dict("records"),
        }
    )


class PredictRequest(BaseModel):
    district: str = Field(default="天河区", description="区县")
    area: float = Field(default=90.0, gt=0, le=1000, description="建筑面积（㎡）")
    orientation: str = Field(default="南", description="朝向")
    floor_level: str = Field(default="中", description="楼层：低/中/高")
    rooms: float | None = Field(default=3, ge=0, description="室数")
    halls: float | None = Field(default=2, ge=0, description="厅数")
    total_floors: float | None = Field(default=18, ge=0, description="总层数")
    building_type: str = Field(default="未知", description="建筑类型")


@app.post("/api/predict")
def predict(request: PredictRequest) -> dict[str, Any]:
    """用训练好的 Ridge 模型预测单价（元/㎡）。"""
    context = _context()
    models = _models()
    if not context or not models:
        return _empty("模型或特征编码器缺失")

    import pandas as pd

    from models.predict import build_feature_vector

    try:
        vector = build_feature_vector(request.model_dump(), context)
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    # 用 DataFrame 并带上训练时的列名，避免 sklearn 的 feature-name 警告。
    frame = pd.DataFrame([vector], columns=context["columns"])

    log_prediction = float(models["ridge"].predict(frame)[0])
    return _ok(
        {
            "predicted_unit_price": round(float(np.exp(log_prediction)), 2),
            "log_prediction": round(log_prediction, 6),
            "model": "ridge",
            "note": "线性模型，用于可解释预测；完整 GBDT 模型见本地评估产物",
        }
    )


# ---------------------------------------------------------------------------
# 静态文件托管（放在 API 路由之后）
# ---------------------------------------------------------------------------

for folder in ("assets", "js", "data"):
    path = FRONTEND_DIR / folder
    if path.is_dir():
        app.mount(f"/{folder}", StaticFiles(directory=str(path)), name=folder)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{full_path:path}")
def fallback(full_path: str) -> FileResponse:
    """未知路径回退到首页，避免前端资源 404 白屏。"""
    return FileResponse(FRONTEND_DIR / "index.html")
