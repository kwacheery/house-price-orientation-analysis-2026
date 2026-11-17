"""全样本建模：用全部 `model_keep=1` 记录（3297 条）训练，满足大纲"建模数据 ≥2000 条"。

广州房天下 1251 条仍是**主分析样本**（主模型与朝向溢价表以其为准）；
本模块额外提供覆盖三站、34 城的全样本模型，加入 `city` / `source` 固定效应，
用于证明数据量达标并检验主结论的稳健性。所有编码只在训练集拟合。

产物：``reports/full_sample_metrics.json``、``models/artifacts/full_sample_predictions.csv``

用法：

    python -m models.full_sample
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.common import ARTIFACT_DIR, DATA_DIR, REPORT_DIR, compute_metrics  # noqa: E402

SEED = 42
NUMERIC_COLUMNS = [
    "area",
    "log_area",
    "rooms",
    "halls",
    "bathrooms",
    "total_floors",
    "floor_ratio",
    "is_south",
    "is_south_north",
    "orientation_count",
    "orientation_missing",
]
CATEGORICAL_COLUMNS = ["city", "source", "district", "orientation_main", "building_type"]
FREQUENCY_COLUMNS = ["plate", "community"]


def build_matrix(
    frame: pd.DataFrame,
    *,
    categories: dict[str, list[str]],
    medians: dict[str, float],
    frequencies: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """按训练集拟合的类别集合与中位数构造特征矩阵。"""
    columns: dict[str, pd.Series] = {}
    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce") if column in frame.columns else np.nan
        columns[column] = values.fillna(medians.get(column, 0.0)).astype(float)

    for column in CATEGORICAL_COLUMNS:
        values = frame[column].fillna("未知").map(str) if column in frame.columns else pd.Series("未知", index=frame.index)
        for category in categories.get(column, []):
            columns[f"{column}={category}"] = (values == category).astype(int)

    for column in FREQUENCY_COLUMNS:
        values = frame[column].fillna("未知").map(str) if column in frame.columns else pd.Series("未知", index=frame.index)
        columns[f"{column}_freq"] = values.map(frequencies.get(column, {})).fillna(0.0).astype(float)
    return pd.concat(columns, axis=1)


def fit_encoders(train: pd.DataFrame) -> dict[str, Any]:
    """只在训练集拟合中位数、类别集合与频次表。"""
    medians = {}
    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(train[column], errors="coerce") if column in train.columns else pd.Series(dtype=float)
        medians[column] = float(values.median()) if values.notna().any() else 0.0

    categories = {}
    for column in CATEGORICAL_COLUMNS:
        values = train[column].fillna("未知").map(str) if column in train.columns else pd.Series(dtype=str)
        categories[column] = sorted(values.unique().tolist())

    frequencies = {}
    for column in FREQUENCY_COLUMNS:
        values = train[column].fillna("未知").map(str) if column in train.columns else pd.Series(dtype=str)
        counts = values.value_counts(normalize=True)
        frequencies[column] = {str(key): float(value) for key, value in counts.items()}
    return {"medians": medians, "categories": categories, "frequencies": frequencies}


def run_full_sample(
    *,
    clean_path: str | Path = DATA_DIR / "clean.csv",
    artifact_dir: str | Path = ARTIFACT_DIR,
    report_dir: str | Path = REPORT_DIR,
    seed: int = SEED,
) -> dict[str, Any]:
    """训练并评估全样本模型，落盘指标与预测。"""
    clean = pd.read_csv(clean_path, encoding="utf-8-sig")
    frame = clean[clean["model_keep"] == 1].copy().reset_index(drop=True)
    frame["log_area"] = np.log(pd.to_numeric(frame["area"], errors="coerce"))
    frame["log_unit_price"] = np.log(pd.to_numeric(frame["unit_price"], errors="coerce"))
    frame = frame[frame["log_unit_price"].notna()].reset_index(drop=True)

    indices = np.arange(len(frame))
    train_index, test_index = train_test_split(
        indices, test_size=0.2, random_state=seed, stratify=frame["city"]
    )
    train, test = frame.iloc[train_index].copy(), frame.iloc[test_index].copy()
    encoders = fit_encoders(train)
    x_train = build_matrix(train, **encoders)
    x_test = build_matrix(test, **encoders)
    y_train = train["log_unit_price"].to_numpy()
    y_test = test["log_unit_price"].to_numpy()

    models = {
        "ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=10.0))]),
        "hist_gb": Pipeline([("model", HistGradientBoostingRegressor(random_state=seed))]),
    }
    rows = []
    predictions = []
    for name, model in models.items():
        model.fit(x_train, y_train)
        for split, data_x, data_y, data_index in (
            ("train", x_train, y_train, train_index),
            ("test", x_test, y_test, test_index),
        ):
            predicted = model.predict(data_x)
            for row_id, actual, prediction in zip(data_index, data_y, predicted):
                predictions.append(
                    {
                        "model": name,
                        "split": split,
                        "row_id": int(row_id),
                        "y_true_log": float(actual),
                        "y_pred_log": float(prediction),
                        "y_true_yuan": float(np.exp(actual)),
                        "y_pred_yuan": float(np.exp(prediction)),
                    }
                )
            if split == "test":
                rows.append({"model": name, **compute_metrics(data_y, predicted)})

    output = Path(artifact_dir)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(predictions).to_csv(output / "full_sample_predictions.csv", encoding="utf-8-sig", index=False)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "全样本建模数据量达标与稳健性检验；广州子集仍为主分析样本",
        "rows_total": len(frame),
        "train_rows": len(train),
        "test_rows": len(test),
        "feature_count": x_train.shape[1],
        "fixed_effects": ["city", "source", "district"],
        "seed": seed,
        "metrics": rows,
        "main_sample_rows": 1251,
        "meets_2000_target": len(frame) >= 2000,
    }
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    (report_path / "full_sample_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run_full_sample()
    print(
        f"全样本建模完成：{report['rows_total']} 条（训练 {report['train_rows']} / "
        f"测试 {report['test_rows']}），满足 ≥2000：{report['meets_2000_target']}"
    )
    for item in report["metrics"]:
        print(f"  {item['model']}: RMSE={item['rmse_yuan']:.0f} 元/㎡, R²={item['r2_yuan']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
