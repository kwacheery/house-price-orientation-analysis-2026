"""W5 朝向溢价分析（项目核心）。

模型：

    log(unit_price) = β0 + Σ βk·orientation_k + β1·log(area) + β2·floor_ratio
                    + β3·rooms + 区县固定效应 + ε

- 参照组为“北”；溢价 = exp(β) − 1，给出 95% 置信区间。
- 使用 HC3 异方差稳健标准误；Kruskal-Wallis 检验各朝向单价分布差异。
- Bootstrap（1000 次重采样）给出溢价的稳健置信区间。
- 分区县异质性与跨城市稳健性检验（链家/贝壳多城市首页，城市固定效应）。
- 结论纪律：CI 跨 0 时写“未发现显著差异”，不做因果断言、不 p-hacking。

产物：``reports/orientation_premium.csv``、``district_premium.csv``、
``cross_city_premium.csv``、``premium.json``、``figures/orientation_premium.png``。

用法：

    python -m models.premium
    python -m models.premium --bootstrap 1000
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
import statsmodels.api as sm  # noqa: E402
from scipy import stats  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.common import DATA_DIR, REPORT_DIR, premium_from_coef  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REFERENCE_ORIENTATION = "北"
ORIENTATION_ORDER = ["北", "南", "东南", "西南", "东", "西", "东北", "西北", "未知"]
FLOOR_LEVEL_CODE = {"低": 0, "中": 1, "高": 2, "未知": -1}


# ---------------------------------------------------------------------------
# 设计矩阵
# ---------------------------------------------------------------------------


def build_design_matrix(
    frame: pd.DataFrame,
    *,
    reference: str = REFERENCE_ORIENTATION,
    with_district: bool = True,
    with_city: bool = False,
    min_category_count: int = 10,
) -> tuple[pd.DataFrame, list[str]]:
    """构造回归设计矩阵：朝向 dummy（指定参照）+ 控制变量 + 固定效应。

    样本数低于 ``min_category_count`` 的朝向类别不单独设虚拟变量，避免
    极小类别产生无意义的标准误与置信区间（如主样本中“未知”仅 1 条）。
    """
    parts: list[pd.Series] = []
    orientation_columns: list[str] = []
    counts = frame["orientation_main"].astype(str).value_counts()
    orientations = [
        value
        for value in ORIENTATION_ORDER
        if value != reference and counts.get(value, 0) >= min_category_count
    ]
    for category in orientations:
        name = f"orientation={category}"
        parts.append((frame["orientation_main"].astype(str) == category).astype(float).rename(name))
        orientation_columns.append(name)

    controls = pd.DataFrame(index=frame.index)
    controls["log_area"] = np.log(pd.to_numeric(frame["area"], errors="coerce"))
    if "floor_ratio" in frame.columns:
        controls["floor_ratio"] = pd.to_numeric(frame["floor_ratio"], errors="coerce")
    else:
        controls["floor_ratio"] = frame["floor_level"].astype(str).map(FLOOR_LEVEL_CODE)
    if "rooms" in frame.columns:
        controls["rooms"] = pd.to_numeric(frame["rooms"], errors="coerce")
    controls = controls.fillna(controls.median(numeric_only=True)).fillna(0)

    fixed_effects = pd.DataFrame(index=frame.index)
    if with_district and "district" in frame.columns:
        for district in sorted(frame["district"].astype(str).unique())[1:]:
            fixed_effects[f"district={district}"] = (frame["district"].astype(str) == district).astype(float)
    if with_city and "city" in frame.columns:
        for city in sorted(frame["city"].astype(str).unique())[1:]:
            fixed_effects[f"city={city}"] = (frame["city"].astype(str) == city).astype(float)

    matrix = pd.concat([*parts, controls, fixed_effects], axis=1)
    matrix = sm.add_constant(matrix, has_constant="add")
    matrix = matrix.replace([np.inf, -np.inf], np.nan).fillna(0)
    return matrix, orientation_columns


def premium_table(
    model: Any,
    orientation_columns: list[str],
    *,
    reference: str = REFERENCE_ORIENTATION,
) -> pd.DataFrame:
    """把回归系数换算为溢价百分比与 95% CI。"""
    confidence = model.conf_int()
    rows = [
        {
            "orientation": reference,
            "coefficient": 0.0,
            "std_error": 0.0,
            "p_value": np.nan,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "premium": 0.0,
            "premium_ci_low": 0.0,
            "premium_ci_high": 0.0,
            "n": None,
        }
    ]
    for column in orientation_columns:
        category = column.split("orientation=", 1)[1]
        coefficient = float(model.params[column])
        low, high = float(confidence.loc[column, 0]), float(confidence.loc[column, 1])
        rows.append(
            {
                "orientation": category,
                "coefficient": coefficient,
                "std_error": float(model.bse[column]),
                "p_value": float(model.pvalues[column]),
                "ci_low": low,
                "ci_high": high,
                "premium": premium_from_coef(coefficient),
                "premium_ci_low": premium_from_coef(low),
                "premium_ci_high": premium_from_coef(high),
                "n": None,
            }
        )
    return pd.DataFrame(rows)


def attach_sample_sizes(table: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    counts = frame["orientation_main"].astype(str).value_counts()
    table = table.copy()
    table["n"] = table["orientation"].map(counts).fillna(0).astype(int)
    return table


# ---------------------------------------------------------------------------
# 检验与 bootstrap
# ---------------------------------------------------------------------------


def kruskal_wallis(frame: pd.DataFrame) -> dict[str, float]:
    """各朝向单价分布的 Kruskal-Wallis 检验（样本过少的类别合并为“其他”）。"""
    counts = frame["orientation_main"].astype(str).value_counts()
    keep = counts[counts >= 30].index.tolist()
    groups = [
        frame.loc[frame["orientation_main"].astype(str) == category, "unit_price"].to_numpy()
        for category in keep
    ]
    other = frame.loc[~frame["orientation_main"].astype(str).isin(keep), "unit_price"].to_numpy()
    if len(other) >= 30:
        groups.append(other)
    groups = [group for group in groups if len(group) >= 30]
    if len(groups) < 2:
        return {"statistic": 0.0, "p_value": 1.0, "groups": len(groups)}
    statistic, p_value = stats.kruskal(*groups)
    return {"statistic": float(statistic), "p_value": float(p_value), "groups": len(groups)}


def bootstrap_premium(
    frame: pd.DataFrame,
    *,
    n_boot: int = 1000,
    seed: int = 42,
    reference: str = REFERENCE_ORIENTATION,
) -> pd.DataFrame:
    """对主回归做行重采样 bootstrap，给出各朝向溢价的稳健 95% CI。"""
    matrix, orientation_columns = build_design_matrix(frame, reference=reference)
    values = matrix.to_numpy(dtype=float)
    target = np.log(frame["unit_price"].to_numpy(dtype=float))
    generator = np.random.default_rng(seed)
    samples = {column: [] for column in orientation_columns}
    n_rows = len(values)

    for _ in range(n_boot):
        indices = generator.integers(0, n_rows, size=n_rows)
        coefficients = np.linalg.lstsq(values[indices], target[indices], rcond=None)[0]
        for position, column in enumerate(orientation_columns, start=1):
            samples[column].append(coefficients[position])

    rows = []
    for column in orientation_columns:
        coefficients = np.asarray(samples[column])
        low, high = np.quantile(coefficients, [0.025, 0.975])
        rows.append(
            {
                "orientation": column.split("orientation=", 1)[1],
                "bootstrap_premium": premium_from_coef(float(np.mean(coefficients))),
                "bootstrap_ci_low": premium_from_coef(float(low)),
                "bootstrap_ci_high": premium_from_coef(float(high)),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 异质性与跨城市稳健性
# ---------------------------------------------------------------------------


def district_heterogeneity(frame: pd.DataFrame, *, min_rows: int = 100) -> pd.DataFrame:
    """分区县估计“朝南 vs 朝北”溢价（洪特样本不足则跳过）。"""
    rows = []
    for district, group in frame.groupby("district"):
        if len(group) < min_rows or group["orientation_main"].astype(str).nunique() < 2:
            continue
        matrix, columns = build_design_matrix(group, with_district=False)
        if "orientation=南" not in columns:
            continue
        # 单区县内可能出现零方差列或秩亏，剔除后仍秩亏则跳过该区县。
        matrix = matrix.loc[:, matrix.nunique(dropna=False) > 1]
        if np.linalg.matrix_rank(matrix.to_numpy(dtype=float)) < matrix.shape[1]:
            continue
        model = sm.OLS(np.log(group["unit_price"].to_numpy(dtype=float)), matrix).fit(cov_type="HC3")
        column = "orientation=南"
        low, high = float(model.conf_int().loc[column, 0]), float(model.conf_int().loc[column, 1])
        rows.append(
            {
                "district": str(district),
                "n": len(group),
                "coefficient": float(model.params[column]),
                "p_value": float(model.pvalues[column]),
                "premium": premium_from_coef(float(model.params[column])),
                "premium_ci_low": premium_from_coef(low),
                "premium_ci_high": premium_from_coef(high),
            }
        )
    return pd.DataFrame(rows).sort_values("premium", ascending=False)


def cross_city_robustness(clean_path: str | Path) -> pd.DataFrame:
    """用链家/贝壳多城市首页样本重复估计，加入城市固定效应。"""
    clean = pd.read_csv(clean_path, encoding="utf-8-sig")
    subset = clean[(clean["source"].isin(["lianjia", "beike"])) & (clean["model_keep"] == 1)].copy()
    subset = subset[subset["unit_price"].notna() & subset["area"].notna()]
    subset = subset[subset["unit_price"] > 0]
    if subset.empty:
        return pd.DataFrame()

    matrix, columns = build_design_matrix(subset, with_district=False, with_city=True)
    model = sm.OLS(np.log(subset["unit_price"].to_numpy(dtype=float)), matrix).fit(cov_type="HC3")
    confidence = model.conf_int()
    rows = []
    for column in columns:
        category = column.split("orientation=", 1)[1]
        low, high = float(confidence.loc[column, 0]), float(confidence.loc[column, 1])
        rows.append(
            {
                "orientation": category,
                "n": int((subset["orientation_main"].astype(str) == category).sum()),
                "coefficient": float(model.params[column]),
                "p_value": float(model.pvalues[column]),
                "premium": premium_from_coef(float(model.params[column])),
                "premium_ci_low": premium_from_coef(low),
                "premium_ci_high": premium_from_coef(high),
            }
        )
    result = pd.DataFrame(rows)
    result.insert(0, "scope", "lianjia+beike 多城市首页（城市固定效应）")
    return result


def make_premium_figure(table: pd.DataFrame, figure_path: Path) -> None:
    """绘制朝向溢价点估计与 95% CI。"""
    plot = table[table["orientation"] != REFERENCE_ORIENTATION].copy()
    plot = plot.sort_values("premium")
    fig, ax = plt.subplots(figsize=(7, 5))
    y = np.arange(len(plot))
    lower = plot["premium"] - plot["premium_ci_low"]
    upper = plot["premium_ci_high"] - plot["premium"]
    ax.errorbar(plot["premium"] * 100, y, xerr=[lower * 100, upper * 100], fmt="o", color="#4C78A8")
    ax.axvline(0, color="red", linestyle="--", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(plot["orientation"])
    ax.set_xlabel("相对朝北的溢价（%）")
    ax.set_title("朝向溢价估计与 95% 置信区间")
    fig.tight_layout()
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def analyze_premium(
    *,
    data_dir: str | Path = DATA_DIR,
    report_dir: str | Path = REPORT_DIR,
    bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """生成全部朝向溢价分析结果。"""
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    features = pd.read_csv(Path(data_dir) / "features.csv", encoding="utf-8-sig")

    matrix, orientation_columns = build_design_matrix(features)
    model = sm.OLS(np.log(features["unit_price"].to_numpy(dtype=float)), matrix).fit(cov_type="HC3")
    main_table = attach_sample_sizes(premium_table(model, orientation_columns), features)
    main_table.to_csv(output / "orientation_premium.csv", encoding="utf-8-sig", index=False)

    bootstrap_table = bootstrap_premium(features, n_boot=bootstrap, seed=seed)
    bootstrap_table.to_csv(output / "orientation_bootstrap.csv", encoding="utf-8-sig", index=False)

    districts = district_heterogeneity(features)
    districts.to_csv(output / "district_premium.csv", encoding="utf-8-sig", index=False)

    clean_path = Path(data_dir) / "clean.csv"
    cross = cross_city_robustness(clean_path)
    if not cross.empty:
        cross.to_csv(output / "cross_city_premium.csv", encoding="utf-8-sig", index=False)

    figure = output / "figures" / "orientation_premium.png"
    make_premium_figure(main_table, figure)

    south = main_table.loc[main_table["orientation"] == "南"].iloc[0]
    kw = kruskal_wallis(features)
    summary = {
        "reference": REFERENCE_ORIENTATION,
        "control_variables": ["log_area", "floor_ratio", "rooms", "区县固定效应"],
        "standard_errors": "HC3",
        "n": len(features),
        "r2": float(model.rsquared),
        "south_premium": float(south["premium"]),
        "south_premium_ci": [float(south["premium_ci_low"]), float(south["premium_ci_high"])],
        "south_p_value": float(south["p_value"]),
        "kruskal_wallis": kw,
        "bootstrap_draws": bootstrap,
        "districts_reported": len(districts),
        "cross_city_rows": len(cross),
        "limitation": "房龄与装修在主样本中全缺，未作为控制变量；观察性数据，不做因果断言。",
        "outputs": [
            str(output / "orientation_premium.csv"),
            str(output / "orientation_bootstrap.csv"),
            str(output / "district_premium.csv"),
            str(output / "cross_city_premium.csv"),
            str(figure),
        ],
    }
    (output / "premium.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W5 朝向溢价分析")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="W3 特征层目录")
    parser.add_argument("--report-dir", default=str(REPORT_DIR), help="报告输出目录")
    parser.add_argument("--bootstrap", type=int, default=1000, help="bootstrap 重采样次数")
    parser.add_argument("--json", action="store_true", help="打印汇总 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = analyze_premium(
        data_dir=args.data_dir,
        report_dir=args.report_dir,
        bootstrap=args.bootstrap,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"溢价分析完成：样本 {summary['n']} 条，R²={summary['r2']:.4f}；"
            f"朝南相对朝北溢价 {summary['south_premium'] * 100:.1f}% "
            f"(95% CI {summary['south_premium_ci'][0] * 100:.1f}%~{summary['south_premium_ci'][1] * 100:.1f}%)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
