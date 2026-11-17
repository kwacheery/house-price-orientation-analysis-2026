"""W5 模型评估：指标汇总、分组误差、特征重要性与图表。

读取 ``models.train`` 的产物，不重复训练。输出：

- ``reports/metrics.json``：模型指标、基线对比、消融、敏感性、最优模型
- ``models/artifacts/grouped_errors.csv``：按区县/价格段/朝向的分组误差
- ``models/artifacts/feature_importance.csv``：最优模型的 permutation importance
- ``reports/figures/true_vs_predicted.png``：真实值 vs 预测值
- ``reports/figures/feature_importance.png``：特征重要性
- ``reports/figures/ablation.png``：消融实验对比

用法：

    python -m models.evaluate
    python -m models.evaluate --model random_forest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import joblib  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.common import (  # noqa: E402
    ARTIFACT_DIR,
    DATA_DIR,
    ID_COLUMN,
    LOG_TARGET_COLUMN,
    REPORT_DIR,
    TARGET_COLUMN,
    compute_metrics,
    feature_columns,
    load_split,
)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ---------------------------------------------------------------------------
# 指标与分组误差
# ---------------------------------------------------------------------------


def load_model_metrics(artifact_dir: str | Path = ARTIFACT_DIR) -> pd.DataFrame:
    return pd.read_csv(Path(artifact_dir) / "model_metrics.csv", encoding="utf-8-sig")


def load_predictions(artifact_dir: str | Path = ARTIFACT_DIR) -> pd.DataFrame:
    return pd.read_csv(Path(artifact_dir) / "predictions.csv", encoding="utf-8-sig")


def select_best_model(metrics: pd.DataFrame) -> str:
    """以测试集元/㎡尺度 RMSE 最小者为最优模型（不含基线）。"""
    candidates = metrics[~metrics["model"].str.startswith("baseline_")]
    if candidates.empty:
        raise ValueError("模型指标中没有主模型")
    return str(candidates.loc[candidates["rmse_yuan"].idxmin(), "model"])


def grouped_error_analysis(
    predictions: pd.DataFrame,
    features: pd.DataFrame,
    *,
    model_name: str,
) -> pd.DataFrame:
    """按区县、价格段、朝向分组计算 RMSE/MAE/偏差。"""
    subset = predictions[(predictions["model"] == model_name) & (predictions["split"] == "test")].copy()
    merged = subset.merge(
        features[[ID_COLUMN, "district", "orientation_main", TARGET_COLUMN]],
        on=ID_COLUMN,
        how="left",
    )
    merged["价格段"] = pd.qcut(
        merged[TARGET_COLUMN],
        q=3,
        labels=["低价段", "中价段", "高价段"],
        duplicates="drop",
    ).astype(str)

    rows = []
    for group_type, column in (("区县", "district"), ("价格段", "价格段"), ("朝向", "orientation_main")):
        for value, group in merged.groupby(column, dropna=False):
            actual = group["y_true_log"].to_numpy()
            predicted = group["y_pred_log"].to_numpy()
            metrics = compute_metrics(actual, predicted)
            rows.append(
                {
                    "group_type": group_type,
                    "group": str(value),
                    "n": len(group),
                    "rmse_yuan": metrics["rmse_yuan"],
                    "mae_yuan": metrics["mae_yuan"],
                    "bias_yuan": float(np.mean(np.exp(predicted) - np.exp(actual))),
                }
            )
    return pd.DataFrame(rows).sort_values(["group_type", "rmse_yuan"], ascending=[True, False])


def compute_feature_importance(
    *,
    model_name: str,
    data_dir: str | Path = DATA_DIR,
    artifact_dir: str | Path = ARTIFACT_DIR,
    n_repeats: int = 5,
    seed: int = 42,
) -> pd.DataFrame:
    """在测试集上做 permutation importance（评估用途，不重新拟合模型）。"""
    output = Path(artifact_dir)
    model = joblib.load(output / f"model_{model_name}.joblib")
    train, test, _ = load_split(data_dir)
    columns = feature_columns(train)
    x_test = test[columns]
    y_test = test[LOG_TARGET_COLUMN].to_numpy()

    result = permutation_importance(
        model,
        x_test,
        y_test,
        scoring="neg_mean_squared_error",
        n_repeats=n_repeats,
        random_state=seed,
        n_jobs=1,
    )
    # scoring=neg_mean_squared_error 时，importances_mean 已是“打乱后分数下降量”，
    # 数值越大表示特征越重要，无需再取负号。
    frame = pd.DataFrame(
        {
            "feature": columns,
            "importance_mean": result.importances_mean,
            "importance_std": result.importances_std,
        }
    )
    frame = frame.sort_values("importance_mean", ascending=False).reset_index(drop=True)
    frame.to_csv(output / "feature_importance.csv", encoding="utf-8-sig", index=False)
    return frame


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------


def feature_correlation(
    *,
    data_dir: str | Path = DATA_DIR,
    artifact_dir: str | Path = ARTIFACT_DIR,
) -> pd.DataFrame:
    """训练特征与目标的 Pearson 相关（用于初步筛选，只统计不拟合模型）。"""
    train, _, _ = load_split(data_dir)
    columns = feature_columns(train)
    correlations = []
    target = train[LOG_TARGET_COLUMN]
    for column in columns:
        values = pd.to_numeric(train[column], errors="coerce")
        if values.nunique(dropna=False) <= 1:
            continue
        correlations.append(
            {
                "feature": column,
                "corr_with_log_price": float(values.corr(target)),
            }
        )
    result = pd.DataFrame(correlations)
    result["absolute"] = result["corr_with_log_price"].abs()
    result = result.sort_values("absolute", ascending=False).drop(columns="absolute").reset_index(drop=True)
    result.to_csv(Path(artifact_dir) / "feature_correlation.csv", encoding="utf-8-sig", index=False)
    return result


def district_price_gap(
    *,
    data_dir: str | Path = DATA_DIR,
    report_dir: str | Path = REPORT_DIR,
) -> pd.DataFrame:
    """区县均价偏离度（分析特征）：区县均价相对全市均价的差异。

    说明：该指标用于描述区域价格水平，**不入模**，避免把目标变量的变换重复
    放进特征造成泄漏；模型内的区域信息由区县 One-Hot 表达。
    """
    _, _, features = load_split(data_dir)
    overall = float(features[TARGET_COLUMN].mean())
    grouped = features.groupby("district")[TARGET_COLUMN].agg(["mean", "count"]).reset_index()
    grouped["overall_mean"] = overall
    grouped["district_price_gap"] = (grouped["mean"] - overall) / overall
    grouped = grouped.rename(columns={"mean": "district_mean_price", "count": "n"})
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    grouped.to_csv(output / "district_price_gap.csv", encoding="utf-8-sig", index=False)
    return grouped


def make_figures(
    *,
    predictions: pd.DataFrame,
    importance: pd.DataFrame,
    ablation: pd.DataFrame,
    model_name: str,
    figure_dir: str | Path = REPORT_DIR / "figures",
) -> list[str]:
    """生成真实 vs 预测、特征重要性、消融对比三张图。"""
    output = Path(figure_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []

    subset = predictions[(predictions["model"] == model_name) & (predictions["split"] == "test")]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(subset["y_true_yuan"], subset["y_pred_yuan"], s=14, alpha=0.5)
    limits = [
        min(subset["y_true_yuan"].min(), subset["y_pred_yuan"].min()),
        max(subset["y_true_yuan"].max(), subset["y_pred_yuan"].max()),
    ]
    ax.plot(limits, limits, "r--", linewidth=1, label="理想预测")
    ax.set_xlabel("真实单价（元/㎡）")
    ax.set_ylabel("预测单价（元/㎡）")
    ax.set_title(f"真实值 vs 预测值（{model_name}，测试集）")
    ax.legend()
    fig.tight_layout()
    path = output / "true_vs_predicted.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(str(path))

    top = importance.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(top["feature"], top["importance_mean"], xerr=top["importance_std"], color="#4C78A8")
    ax.set_xlabel("Permutation importance（MSE 增加量，log 尺度）")
    ax.set_title("特征重要性（Top 15）")
    fig.tight_layout()
    path = output / "feature_importance.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(str(path))

    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(ablation))
    width = 0.35
    ax.bar(x - width / 2, ablation["r2_yuan"], width, label="R²（元/㎡）")
    ax.bar(x + width / 2, ablation["rmse_yuan"] / 10000, width, label="RMSE（万元/㎡）")
    ax.set_xticks(x)
    ax.set_xticklabels(ablation["experiment"])
    ax.set_title("朝向特征消融实验")
    ax.legend()
    fig.tight_layout()
    path = output / "ablation.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(str(path))

    return paths


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def evaluate_all(
    *,
    data_dir: str | Path = DATA_DIR,
    artifact_dir: str | Path = ARTIFACT_DIR,
    report_dir: str | Path = REPORT_DIR,
    model_name: str | None = None,
    n_repeats: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    """汇总全部评估结果并落盘。"""
    output = Path(artifact_dir)
    metrics = load_model_metrics(output)
    predictions = load_predictions(output)
    best = model_name or select_best_model(metrics)

    ablation = pd.read_csv(output / "ablation.csv", encoding="utf-8-sig")
    sensitivity = pd.read_csv(output / "sensitivity.csv", encoding="utf-8-sig")
    _, _, features = load_split(data_dir)

    grouped = grouped_error_analysis(predictions, features, model_name=best)
    grouped.to_csv(output / "grouped_errors.csv", encoding="utf-8-sig", index=False)

    correlation = feature_correlation(data_dir=data_dir, artifact_dir=artifact_dir)
    price_gap = district_price_gap(data_dir=data_dir, report_dir=report_dir)

    importance = compute_feature_importance(
        model_name=best,
        data_dir=data_dir,
        artifact_dir=artifact_dir,
        n_repeats=n_repeats,
        seed=seed,
    )

    figures = make_figures(
        predictions=predictions,
        importance=importance,
        ablation=ablation,
        model_name=best,
        figure_dir=Path(report_dir) / "figures",
    )

    baseline_rmse = float(metrics.loc[metrics["model"] == "baseline_district_mean", "rmse_yuan"].iloc[0])
    best_rmse = float(metrics.loc[metrics["model"] == best, "rmse_yuan"].iloc[0])
    report = {
        "best_model": best,
        "best_test_rmse_yuan": best_rmse,
        "best_test_r2_yuan": float(metrics.loc[metrics["model"] == best, "r2_yuan"].iloc[0]),
        "district_baseline_rmse_yuan": baseline_rmse,
        "rmse_reduction_vs_district": (baseline_rmse - best_rmse) / baseline_rmse,
        "model_metrics": metrics.to_dict("records"),
        "ablation": ablation.to_dict("records"),
        "sensitivity": sensitivity.to_dict("records"),
        "top_features": importance.head(10).to_dict("records"),
        "top_correlations": correlation.head(10).to_dict("records"),
        "district_price_gap": price_gap.to_dict("records"),
        "figures": figures,
    }
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    (Path(report_dir) / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W5 模型评估与可视化")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="W3 特征层目录")
    parser.add_argument("--artifact-dir", default=str(ARTIFACT_DIR), help="模型产物目录")
    parser.add_argument("--report-dir", default=str(REPORT_DIR), help="报告输出目录")
    parser.add_argument("--model", help="指定评估模型；默认按测试 RMSE 自动选最优")
    parser.add_argument("--n-repeats", type=int, default=5, help="permutation importance 重复次数")
    parser.add_argument("--json", action="store_true", help="打印汇总 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_all(
        data_dir=args.data_dir,
        artifact_dir=args.artifact_dir,
        report_dir=args.report_dir,
        model_name=args.model,
        n_repeats=args.n_repeats,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"评估完成：最优模型 {report['best_model']}，测试 RMSE={report['best_test_rmse_yuan']:.0f} 元/㎡，"
            f"R²={report['best_test_r2_yuan']:.4f}"
        )
        print(f"相对区县均值基线 RMSE 降幅：{report['rmse_reduction_vs_district'] * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
