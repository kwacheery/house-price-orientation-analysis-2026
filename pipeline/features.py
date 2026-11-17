"""W3 特征工程：clean.csv → 特征表 + 训练/测试划分。

设计要点：

- 主样本：广州（`city_code=gz`）房天下（`source=fang`）且 `model_keep=1` 的记录。
  链家/贝壳多城市首页样本只用于后续跨城市稳健性检验，不混入主模型。
- 别墅 `is_villa` 与异常值 `is_outlier` **保留、只做标记**，供建模阶段做敏感性对比。
- 目标变量：`log_unit_price = log(unit_price)`，同时保留 `unit_price` 供评估时还原到元/㎡。
- 防泄漏：所有统计量（中位数、频次、One-Hot 类别）只在训练集 fit，测试集仅 transform；
  训练/测试索引按 `district` 分层、固定随机种子并落盘。
- 电梯字段全部为“未知”，明确不入模。

用法：

    python -m pipeline.features
    python -m pipeline.features --clean data/processed/clean.csv --out-dir data/processed
    python -m pipeline.features --seed 42 --test-size 0.2 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FEATURE_VERSION = "w3-v1"
MAIN_SOURCE = "fang"
MAIN_CITY_CODE = "gz"
RANDOM_SEED = 42
TEST_SIZE = 0.2

TARGET_COLUMN = "unit_price"
LOG_TARGET_COLUMN = "log_unit_price"
ID_COLUMN = "row_id"

# 数值特征：直接透传或简单派生，缺失用训练集中位数填充。
NUMERIC_FEATURES = [
    "area",
    "log_area",
    "rooms",
    "halls",
    "bathrooms",
    "total_floors",
    "floor_ratio",
    "age",
    "age_missing",
    "area_per_room",
    "rooms_missing",
    "floor_missing",
    "is_south",
    "is_south_north",
    "orientation_count",
    "orientation_missing",
    "orientation_score",
    "south_ratio",
]

# 有序编码：存在天然顺序，映射为整数；未知统一为 -1。
FLOOR_LEVEL_CODE = {"低": 0, "中": 1, "高": 2, "未知": -1}
DECORATION_CODE = {"毛坯": 0, "简装": 1, "精装": 2, "豪装": 3, "未知": -1}
ORDINAL_FEATURES = ("floor_level_code", "decoration_code")

# 主观辅助打分（计划书 7.1）：只作为辅助特征，主结论仍用 One-Hot + 回归系数。
ORIENTATION_SCORE = {
    "南": 4,
    "东南": 3,
    "西南": 3,
    "东": 2,
    "西": 2,
    "东北": 1,
    "西北": 1,
    "北": 0,
    "未知": -1,
}

# One-Hot 与频次编码的源列；类别集合与频次表只在训练集拟合。
ONE_HOT_COLUMNS = ("district", "orientation_main", "building_type")
FREQUENCY_COLUMNS = {"plate": "plate_freq", "community": "community_freq"}

# 特征表中保留但不进入编码特征的列（仅用于分组/追溯/敏感性分析）。
FEATURE_TABLE_EXTRAS = [
    "row_id",
    "source",
    "city_code",
    "city",
    "district",
    "plate",
    "community",
    "elevator",
    "is_villa",
    "is_outlier",
    "unit_price_suspect",
    "model_keep",
    "split",
    "unit_price",
    "log_unit_price",
]

FEATURE_TABLE_COLUMNS = (
    FEATURE_TABLE_EXTRAS
    + NUMERIC_FEATURES
    + ["floor_level", "decoration", "building_type", "orientation", "orientation_main"]
)


# ---------------------------------------------------------------------------
# 样本选择与派生
# ---------------------------------------------------------------------------


def load_clean(path: str | Path) -> pd.DataFrame:
    """读取 W2 清洗结果，保留 CSV 中的空值供后续显式处理。"""
    return pd.read_csv(path, encoding="utf-8-sig")


def select_main_sample(frame: pd.DataFrame) -> pd.DataFrame:
    """筛选主分析样本：广州 + 房天下 + model_keep=1。"""
    mask = (
        (frame["source"] == MAIN_SOURCE)
        & (frame["city_code"] == MAIN_CITY_CODE)
        & (frame["model_keep"] == 1)
    )
    return frame.loc[mask].copy().reset_index(drop=True)


def derive_features(frame: pd.DataFrame) -> pd.DataFrame:
    """在清洗字段基础上派生建模特征；纯变换，不依赖全量数据。"""
    out = frame.copy()

    out[LOG_TARGET_COLUMN] = np.log(out[TARGET_COLUMN].astype(float))
    out["log_area"] = np.log(out["area"].astype(float))

    rooms = pd.to_numeric(out["rooms"], errors="coerce")
    out["area_per_room"] = out["area"].astype(float) / rooms.replace(0, np.nan)
    out["rooms_missing"] = rooms.isna().astype(int)

    floor_level = out["floor_level"].astype(str)
    out["floor_missing"] = (floor_level == "未知").astype(int)

    orientation = out["orientation"].astype(str)
    main_orientation = out["orientation_main"].astype(str)
    out["orientation_score"] = main_orientation.map(ORIENTATION_SCORE).fillna(-1).astype(int)
    count = pd.to_numeric(out["orientation_count"], errors="coerce").fillna(0)
    out["south_ratio"] = orientation.str.contains("南", regex=False).astype(int) / count.replace(0, np.nan)

    return out


def build_feature_frame(clean_frame: pd.DataFrame) -> pd.DataFrame:
    """从 clean 表生成主样本特征表（未编码，含 row_id）。"""
    sample = select_main_sample(clean_frame)
    if sample.empty:
        raise ValueError("主样本为空：请检查 clean.csv 是否包含广州房天下且 model_keep=1 的记录")
    frame = derive_features(sample)
    frame[ID_COLUMN] = np.arange(len(frame))
    frame["split"] = ""
    missing = [column for column in FEATURE_TABLE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"特征表缺少字段：{missing}")
    return frame[FEATURE_TABLE_COLUMNS].copy()


# ---------------------------------------------------------------------------
# 分层划分与编码（只在训练集 fit）
# ---------------------------------------------------------------------------


def split_train_test(
    frame: pd.DataFrame,
    *,
    seed: int = RANDOM_SEED,
    test_size: float = TEST_SIZE,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """按 district 分层划分训练/测试集，返回划分索引以便复现。"""
    indices = np.arange(len(frame))
    train_index, test_index = train_test_split(
        indices,
        test_size=test_size,
        random_state=seed,
        stratify=frame["district"],
    )
    train = frame.iloc[train_index].copy()
    test = frame.iloc[test_index].copy()
    meta = {
        "seed": seed,
        "test_size": test_size,
        "train_index": [int(i) for i in train_index],
        "test_index": [int(i) for i in test_index],
        "train_rows": len(train),
        "test_rows": len(test),
        "train_districts": {str(k): int(v) for k, v in train["district"].value_counts().items()},
        "test_districts": {str(k): int(v) for k, v in test["district"].value_counts().items()},
    }
    return train, test, meta


def fit_encoders(train: pd.DataFrame) -> dict[str, Any]:
    """只用训练集拟合数值中位数、One-Hot 类别与频次表。"""
    medians: dict[str, float] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(train[column], errors="coerce").dropna()
        medians[column] = float(values.median()) if not values.empty else 0.0

    one_hot: dict[str, list[str]] = {}
    for column in ONE_HOT_COLUMNS:
        categories = sorted(train[column].dropna().astype(str).unique().tolist())
        one_hot[column] = categories

    frequency: dict[str, dict[str, float]] = {}
    for column in FREQUENCY_COLUMNS:
        counts = train[column].astype(str).value_counts(normalize=True)
        frequency[column] = {str(key): float(value) for key, value in counts.items()}

    return {"medians": medians, "one_hot": one_hot, "frequency": frequency}


def transform(frame: pd.DataFrame, encoders: dict[str, Any]) -> pd.DataFrame:
    """用已拟合的编码器转换数据；测试集中的未见类别不会新建列。"""
    features = pd.DataFrame(index=frame.index)

    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(frame[column], errors="coerce")
        features[column] = values.fillna(encoders["medians"][column]).astype(float)

    features["floor_level_code"] = (
        frame["floor_level"].astype(str).map(FLOOR_LEVEL_CODE).fillna(-1).astype(int)
    )
    features["decoration_code"] = (
        frame["decoration"].astype(str).map(DECORATION_CODE).fillna(-1).astype(int)
    )

    for column, categories in encoders["one_hot"].items():
        values = frame[column].astype(str)
        for category in categories:
            features[f"{column}={category}"] = (values == category).astype(int)

    for column, output_name in FREQUENCY_COLUMNS.items():
        features[output_name] = (
            frame[column].astype(str).map(encoders["frequency"][column]).fillna(0.0).astype(float)
        )

    return features


def drop_constant_columns(
    train_features: pd.DataFrame,
    test_features: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """删除训练集内零方差列（如房天下全缺的房龄、装修），测试集同步删除。"""
    constants = [column for column in train_features.columns if train_features[column].nunique(dropna=False) <= 1]
    return (
        train_features.drop(columns=constants),
        test_features.drop(columns=constants),
        constants,
    )


def _assemble_encoded(
    frame: pd.DataFrame,
    features: pd.DataFrame,
) -> pd.DataFrame:
    """拼接 row_id + 编码特征 + 目标列，作为 train.csv / test.csv 的最终形态。"""
    pieces = [
        frame[[ID_COLUMN]].reset_index(drop=True),
        features.reset_index(drop=True),
        frame[[LOG_TARGET_COLUMN, TARGET_COLUMN]].reset_index(drop=True),
    ]
    return pd.concat(pieces, axis=1)


def _to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


# ---------------------------------------------------------------------------
# 编排与 CLI
# ---------------------------------------------------------------------------


def build_features(
    *,
    clean_path: str | Path = PROJECT_ROOT / "data" / "processed" / "clean.csv",
    out_dir: str | Path = PROJECT_ROOT / "data" / "processed",
    seed: int = RANDOM_SEED,
    test_size: float = TEST_SIZE,
) -> dict[str, Any]:
    """生成 features.csv / train.csv / test.csv / feature_meta.json。"""
    clean_frame = load_clean(clean_path)
    frame = build_feature_frame(clean_frame)
    train, test, split_meta = split_train_test(frame, seed=seed, test_size=test_size)

    encoders = fit_encoders(train)
    train_features = transform(train, encoders)
    test_features = transform(test, encoders)
    train_features, test_features, dropped = drop_constant_columns(train_features, test_features)

    frame.loc[split_meta["train_index"], "split"] = "train"
    frame.loc[split_meta["test_index"], "split"] = "test"

    train_out = _assemble_encoded(train, train_features)
    test_out = _assemble_encoded(test, test_features)

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "features.csv", encoding="utf-8-sig", index=False)
    train_out.to_csv(output / "train.csv", encoding="utf-8-sig", index=False)
    test_out.to_csv(output / "test.csv", encoding="utf-8-sig", index=False)

    meta = {
        "feature_version": FEATURE_VERSION,
        "clean_path": str(clean_path),
        "main_sample": {"source": MAIN_SOURCE, "city_code": MAIN_CITY_CODE, "filter": "model_keep=1"},
        "rows": len(frame),
        "seed": seed,
        "test_size": test_size,
        "split": _to_builtin(split_meta),
        "encoders": _to_builtin(encoders),
        "dropped_constant_columns": dropped,
        "feature_columns": list(train_features.columns),
        "non_feature_columns": [ID_COLUMN, LOG_TARGET_COLUMN, TARGET_COLUMN],
        "excluded_features": ["elevator"],
        "target": {"name": LOG_TARGET_COLUMN, "raw": TARGET_COLUMN, "transform": "log"},
    }
    (output / "feature_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "feature_version": FEATURE_VERSION,
        "rows": len(frame),
        "train_rows": len(train_out),
        "test_rows": len(test_out),
        "feature_count": len(train_features.columns),
        "dropped_constant_columns": dropped,
        "outputs": [str(output / name) for name in ("features.csv", "train.csv", "test.csv", "feature_meta.json")],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W3 特征工程：clean.csv -> features/train/test")
    parser.add_argument(
        "--clean",
        default=str(PROJECT_ROOT / "data" / "processed" / "clean.csv"),
        help="W2 清洗结果路径",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "data" / "processed"),
        help="特征层输出目录",
    )
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="随机种子")
    parser.add_argument("--test-size", type=float, default=TEST_SIZE, help="测试集比例")
    parser.add_argument("--json", action="store_true", help="把汇总 JSON 打印到标准输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_features(
        clean_path=args.clean,
        out_dir=args.out_dir,
        seed=args.seed,
        test_size=args.test_size,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"特征工程完成：{report['rows']} 条（train {report['train_rows']} / "
            f"test {report['test_rows']}），入模特征 {report['feature_count']} 个"
        )
        if report["dropped_constant_columns"]:
            print(f"已剔除训练集零方差列：{report['dropped_constant_columns']}")
        print(f"输出目录：{Path(args.out_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
