"""W4 模型训练：基线 + 主模型 + 消融 + 敏感性对比。

目标变量为 ``log_unit_price``，所有模型只在训练集拟合 / 调参，测试集仅用于评估。

产物（写入 ``models/artifacts/``，不入库）：

- ``model_<name>.joblib``：训练好的模型 Pipeline
- ``predictions.csv``：各模型在训练/测试集上的预测（log 与元/㎡两种尺度）
- ``cv_results.csv``：网格/随机搜索的交叉验证结果
- ``ablation.csv``：朝向特征消融实验（A 无朝向 / B 有朝向 / C 加交叉）
- ``sensitivity.csv``：剔除别墅/异常值的敏感性对比
- ``run_config.json``：数据版本、随机种子、超参数等复现信息

用法：

    python -m models.train
    python -m models.train --quick        # 测试用小网格
    python -m models.train --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.model_selection import KFold, RandomizedSearchCV, GridSearchCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.common import (  # noqa: E402
    ARTIFACT_DIR,
    DATA_DIR,
    ID_COLUMN,
    LOG_TARGET_COLUMN,
    TARGET_COLUMN,
    add_cross_features,
    compute_metrics,
    cross_features,
    feature_columns,
    load_split,
    orientation_features,
)

FEATURE_VERSION = "w3-v1"
CLEAN_VERSION = "w2-v1"
SEED = 42
CV_FOLDS = 5


# ---------------------------------------------------------------------------
# 基线
# ---------------------------------------------------------------------------


def baseline_global_mean(y_train: np.ndarray, y_eval: np.ndarray) -> np.ndarray:
    """全局均值：所有房源预测同一个 log 单价。"""
    return np.full(len(y_eval), float(np.mean(y_train)))


def baseline_district_mean(
    district_train: np.ndarray,
    y_train: np.ndarray,
    district_eval: np.ndarray,
) -> np.ndarray:
    """区县均值：用所在区县训练集 log 均价预测，未见区县回退全局均值。"""
    global_mean = float(np.mean(y_train))
    group_sums: dict[str, list[float]] = {}
    for district, value in zip(district_train, y_train):
        group_sums.setdefault(str(district), []).append(float(value))
    group_means = {key: float(np.mean(values)) for key, values in group_sums.items()}
    return np.array([group_means.get(str(key), global_mean) for key in district_eval])


def baseline_area_only(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_eval: pd.DataFrame,
) -> np.ndarray:
    """面积单变量线性回归：仅用 log_area 作为特征。"""
    model = LinearRegression()
    model.fit(x_train[["log_area"]], y_train)
    return model.predict(x_eval[["log_area"]])


# ---------------------------------------------------------------------------
# 主模型
# ---------------------------------------------------------------------------


def make_pipeline(name: str, seed: int = SEED) -> Pipeline:
    """构造模型 Pipeline；线性模型做标准化，树模型不做。"""
    if name == "ridge":
        return Pipeline([("scale", StandardScaler()), ("model", Ridge())])
    if name == "random_forest":
        return Pipeline(
            [
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=150,
                        random_state=seed,
                        n_jobs=1,
                    ),
                )
            ]
        )
    if name == "hist_gb":
        return Pipeline([("model", HistGradientBoostingRegressor(random_state=seed))])
    raise ValueError(f"未知模型：{name}")


def model_search(name: str, seed: int = SEED, quick: bool = False) -> tuple[Any, dict[str, Any]]:
    """返回只在训练集做 CV 的超参搜索器及其配置。"""
    cv = KFold(n_splits=CV_FOLDS, shuffle=True, random_state=seed)
    common = {
        "cv": cv,
        "scoring": "neg_mean_squared_error",
        "refit": True,
        "n_jobs": 1,
        "return_train_score": False,
    }

    if name == "ridge":
        alphas = [10.0] if quick else [0.1, 1.0, 10.0, 100.0]
        search = GridSearchCV(
            make_pipeline(name, seed),
            {"model__alpha": alphas},
            **common,
        )
        return search, {"alpha": alphas}

    if name == "random_forest":
        param_dist = (
            {"model__n_estimators": [150], "model__min_samples_leaf": [1]}
            if quick
            else {
                "model__n_estimators": [100, 150, 200],
                "model__max_depth": [None, 12, 20],
                "model__min_samples_leaf": [1, 2, 5],
            }
        )
        search = RandomizedSearchCV(
            make_pipeline(name, seed),
            param_dist,
            n_iter=1 if quick else 4,
            random_state=seed,
            **common,
        )
        return search, param_dist

    if name == "hist_gb":
        param_dist = (
            {"model__learning_rate": [0.1], "model__max_iter": [100]}
            if quick
            else {
                "model__learning_rate": [0.05, 0.1],
                "model__max_iter": [100, 200],
                "model__max_leaf_nodes": [15, 31],
                "model__l2_regularization": [0.0, 1.0],
            }
        )
        search = RandomizedSearchCV(
            make_pipeline(name, seed),
            param_dist,
            n_iter=1 if quick else 4,
            random_state=seed,
            **common,
        )
        return search, param_dist

    raise ValueError(f"未知模型：{name}")


# ---------------------------------------------------------------------------
# 消融与敏感性
# ---------------------------------------------------------------------------


def ablation_experiment(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_test: pd.DataFrame,
    y_test: np.ndarray,
    *,
    seed: int = SEED,
) -> pd.DataFrame:
    """朝向特征消融：A 无朝向 / B 有朝向 / C 加朝向×楼层交叉。"""
    columns = list(x_train.columns)
    orientation_cols = orientation_features(columns)
    cross_cols = cross_features(columns)
    extended_columns = columns + cross_cols
    x_train_extended = add_cross_features(x_train)
    x_test_extended = add_cross_features(x_test)

    experiments = {
        "A_无朝向": [c for c in columns if c not in orientation_cols],
        "B_含朝向": columns,
        "C_朝向加交叉": extended_columns,
    }

    rows = []
    for name, selected in experiments.items():
        source_train = x_train_extended if name == "C_朝向加交叉" else x_train
        source_test = x_test_extended if name == "C_朝向加交叉" else x_test
        pipeline = Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=10.0))])
        pipeline.fit(source_train[selected], y_train)
        predictions = pipeline.predict(source_test[selected])
        metrics = compute_metrics(y_test, predictions)
        rows.append({"experiment": name, "feature_count": len(selected), **metrics})

    result = pd.DataFrame(rows)
    base_b = result.loc[result["experiment"] == "B_含朝向", ["r2_yuan", "rmse_yuan"]].iloc[0]
    result["delta_r2_vs_A"] = result["r2_yuan"] - float(
        result.loc[result["experiment"] == "A_无朝向", "r2_yuan"].iloc[0]
    )
    result["delta_rmse_vs_A"] = float(
        result.loc[result["experiment"] == "A_无朝向", "rmse_yuan"].iloc[0]
    ) - result["rmse_yuan"]
    result["delta_r2_vs_B"] = result["r2_yuan"] - float(base_b["r2_yuan"])
    result["delta_rmse_vs_B"] = float(base_b["rmse_yuan"]) - result["rmse_yuan"]
    return result


def sensitivity_experiment(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: pd.DataFrame,
    *,
    seed: int = SEED,
) -> pd.DataFrame:
    """剔除别墅/异常值后的 Ridge 敏感性对比（保留与剔除两种口径）。"""
    columns = feature_columns(train)
    lookup = features.set_index(ID_COLUMN)
    train_flags = lookup.loc[train[ID_COLUMN], ["is_villa", "is_outlier"]].reset_index(drop=True)
    test_flags = lookup.loc[test[ID_COLUMN], ["is_villa", "is_outlier"]].reset_index(drop=True)

    masks = {
        "主样本（全保留）": (np.ones(len(train), dtype=bool), np.ones(len(test), dtype=bool)),
        "剔除别墅": (train_flags["is_villa"] != 1, test_flags["is_villa"] != 1),
        "剔除异常值": (train_flags["is_outlier"] != 1, test_flags["is_outlier"] != 1),
        "两者都剔除": (
            (train_flags["is_villa"] != 1) & (train_flags["is_outlier"] != 1),
            (test_flags["is_villa"] != 1) & (test_flags["is_outlier"] != 1),
        ),
    }

    rows = []
    for name, (train_mask, test_mask) in masks.items():
        x_tr = train.loc[train_mask, columns]
        x_te = test.loc[test_mask, columns]
        y_tr = train.loc[train_mask, LOG_TARGET_COLUMN].to_numpy()
        y_te = test.loc[test_mask, LOG_TARGET_COLUMN].to_numpy()
        pipeline = Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=10.0))])
        pipeline.fit(x_tr, y_tr)
        metrics = compute_metrics(y_te, pipeline.predict(x_te))
        rows.append(
            {
                "scenario": name,
                "train_rows": int(train_mask.sum()),
                "test_rows": int(test_mask.sum()),
                **metrics,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def train_all(
    *,
    data_dir: str | Path = DATA_DIR,
    artifact_dir: str | Path = ARTIFACT_DIR,
    seed: int = SEED,
    quick: bool = False,
) -> dict[str, Any]:
    """训练全部模型并落盘产物，返回汇总报告。"""
    output = Path(artifact_dir)
    output.mkdir(parents=True, exist_ok=True)
    train, test, features = load_split(data_dir)

    columns = feature_columns(train)
    x_train = train[columns]
    x_test = test[columns]
    y_train = train[LOG_TARGET_COLUMN].to_numpy()
    y_test = test[LOG_TARGET_COLUMN].to_numpy()

    lookup = features.set_index(ID_COLUMN)
    district_train = lookup.loc[train[ID_COLUMN], "district"].to_numpy()
    district_test = lookup.loc[test[ID_COLUMN], "district"].to_numpy()

    prediction_rows: list[dict[str, Any]] = []
    cv_frames: list[pd.DataFrame] = []

    def record(model_name: str, split: str, y_true: np.ndarray, y_pred: np.ndarray, row_ids: np.ndarray) -> None:
        for row_id, actual, predicted in zip(row_ids, y_true, y_pred):
            prediction_rows.append(
                {
                    "model": model_name,
                    "split": split,
                    "row_id": int(row_id),
                    "y_true_log": float(actual),
                    "y_pred_log": float(predicted),
                    "y_true_yuan": float(np.exp(actual)),
                    "y_pred_yuan": float(np.exp(predicted)),
                }
            )

    # 三个基线。
    baselines = {
        "baseline_global_mean": (
            baseline_global_mean(y_train, y_train),
            baseline_global_mean(y_train, y_test),
        ),
        "baseline_district_mean": (
            baseline_district_mean(district_train, y_train, district_train),
            baseline_district_mean(district_train, y_train, district_test),
        ),
        "baseline_area_only": (
            baseline_area_only(x_train, y_train, x_train),
            baseline_area_only(x_train, y_train, x_test),
        ),
    }
    for name, (pred_train, pred_test) in baselines.items():
        record(name, "train", y_train, pred_train, train[ID_COLUMN].to_numpy())
        record(name, "test", y_test, pred_test, test[ID_COLUMN].to_numpy())

    # 主模型：只在训练集做超参搜索。
    best_params: dict[str, Any] = {}
    for name in ("ridge", "random_forest", "hist_gb"):
        search, grid = model_search(name, seed=seed, quick=quick)
        search.fit(x_train, y_train)
        best = search.best_estimator_
        best_params[name] = {key: (None if isinstance(value, float) and np.isnan(value) else value)
                             for key, value in search.best_params_.items()}

        record(name, "train", y_train, best.predict(x_train), train[ID_COLUMN].to_numpy())
        record(name, "test", y_test, best.predict(x_test), test[ID_COLUMN].to_numpy())

        frame = pd.DataFrame(search.cv_results_)
        frame.insert(0, "model", name)
        frame["cv_rmse"] = np.sqrt(-frame["mean_test_score"])
        keep = ["model", "params", "cv_rmse", "mean_test_score", "std_test_score", "rank_test_score"]
        cv_frames.append(frame[[column for column in keep if column in frame.columns]])

        import joblib

        joblib.dump(best, output / f"model_{name}.joblib")

    pd.DataFrame(prediction_rows).to_csv(output / "predictions.csv", encoding="utf-8-sig", index=False)
    pd.concat(cv_frames, ignore_index=True).to_csv(output / "cv_results.csv", encoding="utf-8-sig", index=False)

    # 消融与敏感性（Ridge 固定超参，便于对比）。
    ablation = ablation_experiment(x_train, y_train, x_test, y_test, seed=seed)
    ablation.to_csv(output / "ablation.csv", encoding="utf-8-sig", index=False)
    sensitivity = sensitivity_experiment(train, test, features, seed=seed)
    sensitivity.to_csv(output / "sensitivity.csv", encoding="utf-8-sig", index=False)

    predictions = pd.DataFrame(prediction_rows)
    test_metrics = (
        predictions[predictions["split"] == "test"]
        .groupby("model")
        .apply(
            lambda group: pd.Series(
                compute_metrics(group["y_true_log"].to_numpy(), group["y_pred_log"].to_numpy())
            ),
            include_groups=False,
        )
        .reset_index()
    )
    test_metrics.to_csv(output / "model_metrics.csv", encoding="utf-8-sig", index=False)

    config = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "clean_version": CLEAN_VERSION,
        "feature_version": FEATURE_VERSION,
        "seed": seed,
        "quick": quick,
        "cv_folds": CV_FOLDS,
        "target": LOG_TARGET_COLUMN,
        "sample": {
            "source": "fang",
            "city_code": "gz",
            "train_rows": len(train),
            "test_rows": len(test),
            "feature_count": len(columns),
            "villas_kept": True,
            "outliers_kept": True,
        },
        "best_params": best_params,
        "search_grids": grid if quick else {},
    }
    (output / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "train_rows": len(train),
        "test_rows": len(test),
        "feature_count": len(columns),
        "model_metrics": test_metrics.to_dict("records"),
        "ablation": ablation.to_dict("records"),
        "outputs": [str(output / name) for name in (
            "predictions.csv", "cv_results.csv", "ablation.csv", "sensitivity.csv",
            "model_metrics.csv", "run_config.json",
        )],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W4 模型训练")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="W3 特征层目录")
    parser.add_argument("--artifact-dir", default=str(ARTIFACT_DIR), help="模型产物目录")
    parser.add_argument("--seed", type=int, default=SEED, help="随机种子")
    parser.add_argument("--quick", action="store_true", help="缩小超参网格，用于快速验证")
    parser.add_argument("--json", action="store_true", help="打印汇总 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = train_all(
        data_dir=args.data_dir,
        artifact_dir=args.artifact_dir,
        seed=args.seed,
        quick=args.quick,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"训练完成：{report['train_rows']} 训练 / {report['test_rows']} 测试，{report['feature_count']} 特征")
        for item in report["model_metrics"]:
            print(
                f"  {item['model']}: 测试 RMSE={item['rmse_yuan']:.0f} 元/㎡, "
                f"R²={item['r2_yuan']:.4f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
