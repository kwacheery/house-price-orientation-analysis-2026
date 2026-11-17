"""Web 层测试：接口函数、静态导出与预测特征映射（不依赖 HTTP 客户端）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from models.predict import build_feature_vector, effective_ridge_coefficients, load_context
from scripts import export_static
from web.backend.app import (
    PredictRequest,
    model_importance,
    model_metrics,
    model_predictions,
    orientation,
    overview,
    predict,
    preprocessing,
)


def test_overview_reads_real_pipeline_numbers():
    data = overview()
    assert data["available"] is True
    assert data["totals"]["raw"] == 3326
    assert data["totals"]["clean"] == 3302
    assert data["totals"]["main_sample"] == 1251
    assert data["totals"]["train"] + data["totals"]["test"] == 1251
    assert len(data["sources"]) == 3


def test_preprocessing_endpoint():
    data = preprocessing()
    assert data["available"] is True
    assert data["clean_version"] == "w2-v1"
    assert len(data["summary"]) >= 10
    assert "orientation" in data["missing"]
    assert data["outliers"]["rows"] == 152


def test_orientation_endpoint():
    data = orientation()
    assert data["available"] is True
    assert len(data["distribution"]) >= 8
    assert len(data["premium"]) >= 8
    assert "南" in data["boxplot"]
    assert data["reference"] == "北"
    assert data["kruskal_wallis"]["p_value"] < 0.01


def test_model_endpoints():
    metrics = model_metrics()
    assert metrics["available"] is True
    assert metrics["best_model"] == "hist_gb"
    assert len(metrics["models"]) == 6
    assert len(metrics["ablation"]) == 3

    importance = model_importance()
    assert importance["available"] is True
    assert len(importance["features"]) <= 15
    assert importance["features"][0]["importance_mean"] >= 0


def test_model_predictions_endpoint_returns_scatter_points():
    data = model_predictions()
    assert data["available"] is True
    assert data["model"] == "hist_gb"
    assert len(data["points"]) > 0
    assert set(data["points"][0]) == {"actual", "predicted"}


def test_predict_vector_matches_training_columns():
    context = load_context()
    assert len(context["columns"]) == 40
    vector = build_feature_vector(
        {"district": "天河区", "area": 90.0, "orientation": "南", "floor_level": "中", "rooms": 3},
        context,
    )
    assert isinstance(vector, np.ndarray)
    assert len(vector) == 40
    columns = context["columns"]
    assert vector[columns.index("area")] == pytest.approx(90.0)
    assert vector[columns.index("log_area")] == pytest.approx(np.log(90.0))
    assert vector[columns.index("district=天河区")] == 1.0
    assert vector[columns.index("is_south")] == 1.0
    assert vector[columns.index("floor_ratio")] == pytest.approx(0.5)


def test_predict_rejects_invalid_area():
    context = load_context()
    with pytest.raises(ValueError, match="面积必须为正数"):
        build_feature_vector({"area": -5}, context)


def test_predict_endpoint_returns_positive_price():
    result = predict(
        PredictRequest(
            district="天河区",
            area=90.0,
            orientation="南",
            floor_level="中",
            rooms=3,
            halls=2,
            total_floors=18,
        )
    )
    assert result["available"] is True
    assert result["model"] == "ridge"
    assert result["predicted_unit_price"] > 0


def test_effective_ridge_coefficients_are_aligned():
    context = load_context()
    coefficients, intercept = effective_ridge_coefficients(columns=context["columns"])
    assert len(coefficients) == len(context["columns"])
    assert np.isfinite(intercept)


def test_static_export_writes_all_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(export_static, "OUTPUT_DIR", tmp_path)
    outputs = export_static.export_all()
    assert set(outputs) == {
        "overview", "preprocessing", "orientation", "metrics", "importance",
        "predictions", "static_model",
    }
    for name, path in outputs.items():
        assert Path(path).exists()
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(payload, dict)

    model = json.loads((tmp_path / "static_model.json").read_text(encoding="utf-8"))
    assert len(model["features"]) == len(model["coefficients"])
    assert len(model["features"]) == 40
    assert model["intercept"] is not None

    # 回归：参考组的 p_value 为 NaN，必须以 null 写出，否则浏览器 JSON.parse 失败。
    raw_text = (tmp_path / "orientation.json").read_text(encoding="utf-8")
    assert "NaN" not in raw_text and "Infinity" not in raw_text
    payload = json.loads(raw_text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    assert any(item.get("p_value") is None for item in payload["premium"])
