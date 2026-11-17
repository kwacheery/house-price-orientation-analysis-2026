"""W4/W5 建模与溢价分析单元测试（不依赖完整训练）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.common import (
    compute_metrics,
    cross_features,
    feature_columns,
    orientation_features,
    premium_from_coef,
)
from models.evaluate import grouped_error_analysis, load_model_metrics, select_best_model
from models.premium import (
    build_design_matrix,
    premium_table,
)
from models.full_sample import run_full_sample
from models.train import (
    baseline_area_only,
    baseline_district_mean,
    baseline_global_mean,
    train_all,
)
from pipeline.features import build_features


def test_compute_metrics_both_scales():
    true_log = np.array([np.log(100.0), np.log(200.0)])
    pred_log = np.array([np.log(110.0), np.log(180.0)])
    metrics = compute_metrics(true_log, pred_log)
    assert metrics["log_rmse"] == pytest.approx(np.sqrt(np.mean((np.log(np.array([100.0, 200.0])) - np.log(np.array([110.0, 180.0]))) ** 2)))
    assert metrics["rmse_yuan"] == pytest.approx(np.sqrt(np.mean((np.array([100.0, 200.0]) - np.array([110.0, 180.0])) ** 2)))
    assert metrics["r2_yuan"] <= 1.0
    assert metrics["mse_log"] == pytest.approx(np.mean((true_log - pred_log) ** 2))
    assert metrics["mse_yuan"] == pytest.approx(np.mean((np.array([100.0, 200.0]) - np.array([110.0, 180.0])) ** 2))


def test_baselines():
    y = np.array([1.0, 2.0, 3.0])
    assert baseline_global_mean(y, np.zeros(2)).tolist() == [2.0, 2.0]

    districts = np.array(["A", "A", "B"])
    result = baseline_district_mean(districts, y, np.array(["A", "C"]))
    assert result.tolist() == [1.5, 2.0]  # 未见区县回退全局均值

    x = pd.DataFrame({"log_area": [1.0, 2.0, 3.0]})
    area_pred = baseline_area_only(x, y, pd.DataFrame({"log_area": [4.0]}))
    assert area_pred[0] == pytest.approx(4.0)


def test_feature_set_helpers():
    columns = [
        "area",
        "floor_ratio",
        "is_south",
        "is_south_north",
        "orientation_main=南",
        "orientation_main=北",
    ]
    expected = {"orientation_main=南", "is_south", "is_south_north"}
    assert expected.issubset(set(orientation_features(columns)))
    assert "area" not in orientation_features(columns)
    assert cross_features(columns) == ["south_x_floor_ratio", "south_north_x_floor_ratio"]


def test_premium_from_coef():
    assert premium_from_coef(0.0) == pytest.approx(0.0)
    assert premium_from_coef(np.log(1.1)) == pytest.approx(0.1)


def test_design_matrix_reference_and_rare_categories():
    frame = pd.DataFrame(
        {
            "orientation_main": ["北", "南", "南", "东南", "未知"],
            "area": [80.0, 90.0, 100.0, 110.0, 120.0],
            "floor_ratio": [0.5, 0.5, 0.85, 0.5, 0.5],
            "rooms": [2, 3, 3, 2, 3],
            "district": ["A区", "A区", "B区", "B区", "B区"],
            "unit_price": [40000.0, 45000.0, 50000.0, 55000.0, 30000.0],
        }
    )
    matrix, orientation_columns = build_design_matrix(frame)
    assert "orientation=北" not in orientation_columns  # 参照组不设 dummy
    assert "orientation=未知" not in orientation_columns  # 样本仅 1 条，被过滤
    assert "const" in matrix.columns
    assert any(column.startswith("district=") for column in matrix.columns)
    assert matrix.shape[0] == len(frame)


def test_premium_table_converts_coefficients():
    class FakeModel:
        params = {"orientation=南": np.log(1.2)}
        bse = {"orientation=南": 0.01}
        pvalues = {"orientation=南": 0.01}
        conf_int = lambda self: pd.DataFrame([[np.log(1.1), np.log(1.3)]], index=["orientation=南"])

    table = premium_table(FakeModel(), ["orientation=南"])
    south = table.loc[table["orientation"] == "南"].iloc[0]
    assert south["premium"] == pytest.approx(0.2)
    assert south["premium_ci_low"] == pytest.approx(0.1)
    assert south["premium_ci_high"] == pytest.approx(0.3)
    assert table.loc[table["orientation"] == "北", "premium"].iloc[0] == 0.0


def test_grouped_error_and_best_model_selection():
    predictions = pd.DataFrame(
        {
            "model": ["ridge", "ridge", "rf", "rf"],
            "split": ["test"] * 4,
            "row_id": [0, 1, 0, 1],
            "y_true_log": [10.0, 11.0, 10.0, 11.0],
            "y_pred_log": [10.1, 10.9, 10.5, 11.5],
        }
    )
    features = pd.DataFrame(
        {
            "row_id": [0, 1],
            "district": ["A区", "B区"],
            "orientation_main": ["南", "北"],
            "unit_price": [np.exp(10.0), np.exp(11.0)],
        }
    )
    grouped = grouped_error_analysis(predictions, features, model_name="ridge")
    assert set(grouped["group_type"]) == {"区县", "价格段", "朝向"}
    assert len(grouped) >= 4

    metrics = pd.DataFrame(
        {
            "model": ["baseline_global_mean", "ridge", "rf"],
            "rmse_yuan": [100.0, 50.0, 40.0],
        }
    )
    assert select_best_model(metrics) == "rf"


def _tiny_clean_frame(n: int = 60) -> pd.DataFrame:
    rows = []
    for i in range(n):
        south = i % 2 == 0
        rows.append(
            {
                "source": "fang",
                "city_code": "gz",
                "city": "广州",
                "district": ["天河区", "海珠区", "越秀区"][i % 3],
                "plate": f"板块{i % 4}",
                "community": f"小区{i % 5}",
                "elevator": "未知",
                "is_villa": 0,
                "is_outlier": 0,
                "unit_price_suspect": False,
                "model_keep": 1,
                "unit_price": 30000.0 + i * 50,
                "area": 60.0 + i,
                "rooms": 2 if south else 3,
                "halls": 1,
                "bathrooms": None,
                "total_floors": 18,
                "floor_ratio": 0.5,
                "age": None,
                "age_missing": 1,
                "is_south": int(south),
                "is_south_north": 0,
                "orientation_count": 1,
                "orientation_missing": 0,
                "floor_level": "中",
                "decoration": "未知",
                "building_type": "未知",
                "orientation": "南" if south else "北",
                "orientation_main": "南" if south else "北",
            }
        )
    return pd.DataFrame(rows)


def test_train_all_smoke(tmp_path: Path):
    """端到端冒烟测试：clean -> features -> train（小网格）。"""
    clean_path = tmp_path / "clean.csv"
    _tiny_clean_frame().to_csv(clean_path, encoding="utf-8-sig", index=False)
    data_dir = tmp_path / "data"
    build_features(clean_path=clean_path, out_dir=data_dir)

    report = train_all(data_dir=data_dir, artifact_dir=tmp_path / "artifacts", quick=True)
    assert report["train_rows"] + report["test_rows"] == 60
    assert len(report["model_metrics"]) == 6  # 3 基线 + 3 主模型
    assert set(report["ablation"][0]["experiment"].split("_")[0]) <= set("ABC")

    metrics = load_model_metrics(tmp_path / "artifacts")
    assert select_best_model(metrics) in {"ridge", "random_forest", "hist_gb"}
    config = json.loads((tmp_path / "artifacts" / "run_config.json").read_text(encoding="utf-8"))
    assert config["feature_version"] == "w3-v1"
    assert config["seed"] == 42


def test_full_sample_training_and_2000_flag(tmp_path: Path):
    """全样本模型：小样本上跑通流程，并如实返回是否达到 2000 条。"""
    clean_path = tmp_path / "clean.csv"
    _tiny_clean_frame().to_csv(clean_path, encoding="utf-8-sig", index=False)

    report = run_full_sample(
        clean_path=clean_path,
        artifact_dir=tmp_path / "artifacts",
        report_dir=tmp_path / "reports",
    )
    assert report["rows_total"] == 60
    assert report["meets_2000_target"] is False
    assert len(report["metrics"]) == 2
    assert (tmp_path / "reports" / "full_sample_metrics.json").exists()
    assert (tmp_path / "artifacts" / "full_sample_predictions.csv").exists()
