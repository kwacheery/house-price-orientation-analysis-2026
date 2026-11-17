"""交互式价格预测：把表单输入映射为 W3 训练时的 40 维特征向量。

后端 API 加载 ``model_ridge.joblib`` 直接预测；GitHub Pages 静态版使用
``export_static.py`` 导出的等效 Ridge 系数（已吸收 StandardScaler），
两者共用本模块生成特征向量，保证数字一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from models.common import ARTIFACT_DIR, DATA_DIR

FLOOR_RATIO = {"低": 0.15, "中": 0.5, "高": 0.85}
FLOOR_CODE = {"低": 0, "中": 1, "高": 2, "未知": -1}
ORIENTATION_SCORE = {"南": 4, "东南": 3, "西南": 3, "东": 2, "西": 2, "东北": 1, "西北": 1, "北": 0, "未知": -1}

# 用户可选朝向 → (主朝向, is_south, is_south_north, 方向数, 朝南比例)
ORIENTATION_INPUTS: dict[str, tuple[str, int, int, int, float]] = {
    "南": ("南", 1, 0, 1, 1.0),
    "南北": ("南", 1, 1, 2, 0.5),
    "东南": ("东南", 0, 0, 1, 1.0),
    "西南": ("西南", 0, 0, 1, 1.0),
    "东": ("东", 0, 0, 1, 0.0),
    "西": ("西", 0, 0, 1, 0.0),
    "北": ("北", 0, 0, 1, 0.0),
    "东北": ("东北", 0, 0, 1, 0.0),
    "西北": ("西北", 0, 0, 1, 0.0),
    "未知": ("未知", 0, 0, 0, 0.0),
}

DEFAULT_INPUTS: dict[str, Any] = {
    "district": "天河区",
    "area": 90.0,
    "orientation": "南",
    "floor_level": "中",
    "rooms": 3,
    "halls": 2,
    "total_floors": 18,
    "building_type": "未知",
}


def load_context(
    data_dir: str | Path = DATA_DIR,
    artifact_dir: str | Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    """读取训练时的编码器与训练集分位数，作为预测默认值。"""
    data_root = Path(data_dir)
    meta = json.loads((data_root / "feature_meta.json").read_text(encoding="utf-8"))
    train = pd.read_csv(data_root / "train.csv", encoding="utf-8-sig")
    columns = list(meta["feature_columns"])

    defaults: dict[str, float] = {}
    for column in columns:
        if "=" in column:
            defaults[column] = 0.0
            continue
        median = pd.to_numeric(train[column], errors="coerce").median()
        defaults[column] = float(median) if pd.notna(median) else 0.0

    encoders = meta["encoders"]
    return {
        "columns": columns,
        "defaults": defaults,
        "encoders": encoders,
        "districts": encoders["one_hot"].get("district", []),
        "orientations": encoders["one_hot"].get("orientation_main", []),
        "building_types": encoders["one_hot"].get("building_type", []),
        "orientation_options": list(ORIENTATION_INPUTS),
        "floor_options": list(FLOOR_RATIO),
        "default_inputs": DEFAULT_INPUTS,
    }


def build_feature_vector(inputs: dict[str, Any], context: dict[str, Any]) -> np.ndarray:
    """按训练时的特征顺序构造单条样本向量；输入非法时抛出 ValueError。"""
    values = dict(context["defaults"])

    try:
        area = float(inputs.get("area", DEFAULT_INPUTS["area"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("面积必须是数字") from exc
    if not np.isfinite(area) or area <= 0:
        raise ValueError("面积必须为正数")
    if area > 1000:
        raise ValueError("面积应在 10–1000 ㎡ 之间")
    values["area"] = area
    values["log_area"] = float(np.log(area))

    for column, key in (("rooms", "rooms"), ("halls", "halls"), ("total_floors", "total_floors")):
        raw = inputs.get(key)
        if raw not in (None, ""):
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not np.isfinite(number) or number < 0:
                raise ValueError(f"{key} 不能为负数")
            if column in values:
                values[column] = number

    if "rooms_missing" in values:
        values["rooms_missing"] = 0.0 if inputs.get("rooms") not in (None, "") else 1.0

    floor_level = str(inputs.get("floor_level", "中"))
    if floor_level not in FLOOR_RATIO:
        raise ValueError("楼层只能是低/中/高")
    if "floor_ratio" in values:
        values["floor_ratio"] = FLOOR_RATIO[floor_level]
    if "floor_level_code" in values:
        values["floor_level_code"] = FLOOR_CODE[floor_level]
    if "floor_missing" in values:
        values["floor_missing"] = 0.0

    orientation = str(inputs.get("orientation", "南"))
    if orientation not in ORIENTATION_INPUTS:
        raise ValueError("不支持的朝向")
    main, is_south, is_south_north, count, south_ratio = ORIENTATION_INPUTS[orientation]
    if "is_south" in values:
        values["is_south"] = float(is_south)
    if "is_south_north" in values:
        values["is_south_north"] = float(is_south_north)
    if "orientation_count" in values:
        values["orientation_count"] = float(count)
    if "orientation_missing" in values:
        values["orientation_missing"] = 1.0 if main == "未知" else 0.0
    if "orientation_score" in values:
        values["orientation_score"] = float(ORIENTATION_SCORE[main])
    if "south_ratio" in values:
        values["south_ratio"] = south_ratio
    orientation_column = f"orientation_main={main}"
    if orientation_column in values:
        for column in context["orientations"]:
            values[f"orientation_main={column}"] = 0.0
        values[orientation_column] = 1.0

    district = str(inputs.get("district", DEFAULT_INPUTS["district"]))
    district_column = f"district={district}"
    if district_column in values:
        for column in context["districts"]:
            values[f"district={column}"] = 0.0
        values[district_column] = 1.0
    else:
        raise ValueError("不支持的区县")

    building_type = str(inputs.get("building_type", "未知"))
    building_column = f"building_type={building_type}"
    if building_column in values:
        for column in context["building_types"]:
            values[f"building_type={column}"] = 0.0
        values[building_column] = 1.0

    return np.array([float(values[column]) for column in context["columns"]], dtype=float)


def effective_ridge_coefficients(
    *,
    artifact_dir: str | Path = ARTIFACT_DIR,
    columns: list[str],
) -> tuple[list[float], float]:
    """把 Pipeline(StandardScaler, Ridge) 折算为原始特征空间的线性系数。"""
    import joblib

    pipeline = joblib.load(Path(artifact_dir) / "model_ridge.joblib")
    scaler = pipeline.named_steps["scale"]
    ridge = pipeline.named_steps["model"]
    coefficients = np.asarray(ridge.coef_, dtype=float) / np.asarray(scaler.scale_, dtype=float)
    intercept = float(ridge.intercept_) - float(
        np.sum(np.asarray(ridge.coef_, dtype=float) * np.asarray(scaler.mean_, dtype=float) / np.asarray(scaler.scale_, dtype=float))
    )
    return coefficients.tolist(), intercept
