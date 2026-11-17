"""建模层共享工具：数据读取、特征列识别、指标计算。

所有指标同时在 log 尺度与元/㎡尺度报告。元/㎡尺度由 ``exp`` 反变换得到，
与目标变量 ``log_unit_price`` 的定义一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "processed"
ARTIFACT_DIR = PROJECT_ROOT / "models" / "artifacts"
REPORT_DIR = PROJECT_ROOT / "reports"

TARGET_COLUMN = "unit_price"
LOG_TARGET_COLUMN = "log_unit_price"
ID_COLUMN = "row_id"
NON_FEATURE_COLUMNS = {ID_COLUMN, TARGET_COLUMN, LOG_TARGET_COLUMN}

# 朝向相关特征（用于消融实验 A/B/C）。
ORIENTATION_COLUMNS = {
    "is_south",
    "is_south_north",
    "orientation_count",
    "orientation_missing",
    "orientation_score",
    "south_ratio",
}


def load_split(data_dir: str | Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """读取 W3 产物：训练集、测试集与带分组列的完整特征表。"""
    root = Path(data_dir)
    train = pd.read_csv(root / "train.csv", encoding="utf-8-sig")
    test = pd.read_csv(root / "test.csv", encoding="utf-8-sig")
    features = pd.read_csv(root / "features.csv", encoding="utf-8-sig")
    return train, test, features


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """返回可用于建模的列（排除 id 与目标列）。"""
    return [column for column in frame.columns if column not in NON_FEATURE_COLUMNS]


def orientation_features(columns: Iterable[str]) -> list[str]:
    """识别朝向相关列：One-Hot + 二值 + 辅助衍生。"""
    return [
        column
        for column in columns
        if column.startswith("orientation_main=") or column in ORIENTATION_COLUMNS
    ]


def cross_features(columns: Iterable[str]) -> list[str]:
    """基于主样本可构造的交叉特征：朝向 × 楼层。

    房龄在主样本中 100% 缺失，因此“朝向 × 房龄”“房龄 × 区域”无法构造，
    在报告中作为数据源局限性说明。
    """
    available = set(columns)
    result = []
    if "is_south" in available and "floor_ratio" in available:
        result.append("south_x_floor_ratio")
    if "is_south_north" in available and "floor_ratio" in available:
        result.append("south_north_x_floor_ratio")
    return result


def add_cross_features(frame: pd.DataFrame) -> pd.DataFrame:
    """在特征表副本上追加交叉特征列。"""
    out = frame.copy()
    if "is_south" in out.columns and "floor_ratio" in out.columns:
        out["south_x_floor_ratio"] = out["is_south"] * out["floor_ratio"]
    if "is_south_north" in out.columns and "floor_ratio" in out.columns:
        out["south_north_x_floor_ratio"] = out["is_south_north"] * out["floor_ratio"]
    return out


def compute_metrics(y_true_log: np.ndarray, y_pred_log: np.ndarray) -> dict[str, float]:
    """计算 log 尺度与元/㎡尺度的 RMSE / MAE / R²。"""
    true_log = np.asarray(y_true_log, dtype=float)
    pred_log = np.asarray(y_pred_log, dtype=float)
    true_yuan = np.exp(true_log)
    pred_yuan = np.exp(pred_log)

    def r2(true: np.ndarray, pred: np.ndarray) -> float:
        ss_res = float(np.sum((true - pred) ** 2))
        ss_tot = float(np.sum((true - np.mean(true)) ** 2))
        return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "mse_log": float(np.mean((true_log - pred_log) ** 2)),
        "log_rmse": float(np.sqrt(np.mean((true_log - pred_log) ** 2))),
        "log_mae": float(np.mean(np.abs(true_log - pred_log))),
        "r2_log": r2(true_log, pred_log),
        "mse_yuan": float(np.mean((true_yuan - pred_yuan) ** 2)),
        "rmse_yuan": float(np.sqrt(np.mean((true_yuan - pred_yuan) ** 2))),
        "mae_yuan": float(np.mean(np.abs(true_yuan - pred_yuan))),
        "r2_yuan": r2(true_yuan, pred_yuan),
    }


def premium_from_coef(coefficient: float) -> float:
    """把 log 模型朝向系数换算为溢价比例：exp(β) − 1。"""
    return float(np.exp(coefficient) - 1)
