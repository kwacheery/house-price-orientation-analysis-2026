"""一键流水线：清洗 → 特征 → 训练 → 评估 → 溢价 → 静态导出。

不执行采集；采集依赖网络与站点状态，按项目约定单独低频运行。

用法：

    python scripts/run_pipeline.py --day 2026-11-10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="一键流水线（不含采集）")
    parser.add_argument("--day", default="2026-11-10", help="raw 数据日期")
    parser.add_argument("--skip-clean", action="store_true", help="复用已有 clean.csv")
    parser.add_argument("--skip-train", action="store_true", help="复用已有模型产物")
    parser.add_argument("--json", action="store_true", help="打印汇总 JSON")
    args = parser.parse_args()

    report: dict[str, object] = {}

    if not args.skip_clean:
        from pipeline.clean import clean

        clean_result = clean(day=args.day)
        report["clean"] = {"rows": clean_result["rows"], "model_keep": clean_result["model_keep"]}

    from pipeline.features import build_features

    feature_report = build_features()
    report["features"] = {
        "rows": feature_report["rows"],
        "train_rows": feature_report["train_rows"],
        "test_rows": feature_report["test_rows"],
    }

    if not args.skip_train:
        from models.evaluate import evaluate_all
        from models.premium import analyze_premium
        from models.train import train_all

        train_report = train_all()
        report["train"] = {
            "best_model": min(train_report["model_metrics"], key=lambda item: item["rmse_yuan"])["model"]
        }
        metrics = evaluate_all()
        report["evaluate"] = {
            "best_model": metrics["best_model"],
            "best_rmse": metrics["best_test_rmse_yuan"],
        }
        premium = analyze_premium()
        report["premium"] = {
            "south_premium": premium["south_premium"],
            "south_ci": premium["south_premium_ci"],
        }
        from models.full_sample import run_full_sample

        full = run_full_sample()
        report["full_sample"] = {
            "rows": full["rows_total"],
            "meets_2000": full["meets_2000_target"],
        }

    from scripts.export_static import export_all

    outputs = export_all()
    report["static"] = {"files": len(outputs)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print("流水线完成：", json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
