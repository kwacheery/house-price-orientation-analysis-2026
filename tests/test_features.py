"""W3 特征工程单元测试（不依赖真实数据，全部使用内存/临时数据）。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from pipeline.features import (
    DECORATION_CODE,
    FLOOR_LEVEL_CODE,
    build_feature_frame,
    build_features,
    derive_features,
    drop_constant_columns,
    fit_encoders,
    load_clean,
    select_main_sample,
    split_train_test,
    transform,
)


def make_clean_frame(n: int = 60) -> pd.DataFrame:
    """构造满足特征表字段要求的小型 clean 表。"""
    districts = ["天河区", "海珠区", "越秀区"]
    rows = []
    for i in range(n):
        is_south = i % 2 == 0
        rows.append(
            {
                "source": "fang",
                "city_code": "gz",
                "city": "广州",
                "district": districts[i % 3],
                "plate": f"板块{i % 5}",
                "community": f"小区{i % 7}",
                "elevator": "未知",
                "is_villa": 0,
                "is_outlier": 1 if i % 10 == 0 else 0,
                "unit_price_suspect": False,
                "model_keep": 1,
                "unit_price": 30000.0 + i * 100,
                "area": 50.0 + i,
                "rooms": 2 if is_south else None,
                "halls": 1,
                "bathrooms": None,
                "total_floors": 18,
                "floor_ratio": 0.5,
                "age": None,
                "age_missing": 1,
                "is_south": int(is_south),
                "is_south_north": 0,
                "orientation_count": 1,
                "orientation_missing": 0,
                "floor_level": ["低", "中", "高"][i % 3],
                "decoration": "未知",
                "building_type": "未知",
                "orientation": "南" if is_south else "北",
                "orientation_main": "南" if is_south else "北",
            }
        )
    return pd.DataFrame(rows)


def test_select_main_sample_filters_source_city_and_model_keep():
    frame = make_clean_frame(6)
    extra = frame.head(3).copy()
    extra.loc[extra.index, "source"] = "lianjia"
    other_city = frame.head(3).copy()
    other_city.loc[other_city.index, "city_code"] = "sz"
    dropped = frame.head(3).copy()
    dropped.loc[dropped.index, "model_keep"] = 0
    combined = pd.concat([frame, extra, other_city, dropped], ignore_index=True)

    result = select_main_sample(combined)
    assert len(result) == 6
    assert set(result["source"]) == {"fang"}
    assert set(result["city_code"]) == {"gz"}
    assert set(result["model_keep"]) == {1}


def test_derive_features_adds_expected_columns():
    frame = make_clean_frame(4)
    frame.loc[0, "orientation"] = "南北"
    frame.loc[0, "orientation_count"] = 2
    result = derive_features(frame)

    assert "log_unit_price" in result.columns
    assert result.loc[0, "log_unit_price"] == pytest.approx(math.log(30000.0))
    assert result.loc[0, "log_area"] == pytest.approx(math.log(50.0))
    assert result.loc[0, "area_per_room"] == pytest.approx(25.0)
    assert result.loc[0, "south_ratio"] == pytest.approx(0.5)
    assert result.loc[1, "orientation_score"] == 0  # 北
    assert result.loc[0, "rooms_missing"] == 0
    assert result.loc[1, "rooms_missing"] == 1


def test_build_feature_frame_keeps_flags_and_ids():
    frame = build_feature_frame(make_clean_frame())
    assert len(frame) == 60
    assert frame["row_id"].tolist() == list(range(60))
    assert "is_outlier" in frame.columns
    assert "is_villa" in frame.columns
    assert "elevator" in frame.columns
    assert set(frame["split"]) == {""}


def test_split_is_reproducible_and_stratified():
    frame = build_feature_frame(make_clean_frame())
    train_a, test_a, meta_a = split_train_test(frame, seed=42)
    train_b, test_b, meta_b = split_train_test(frame, seed=42)

    assert meta_a["train_index"] == meta_b["train_index"]
    assert meta_a["test_index"] == meta_b["test_index"]
    assert len(train_a) + len(test_a) == len(frame)
    # 各区县在训练/测试中的占比接近，说明分层生效。
    for district, share in meta_a["test_districts"].items():
        total = meta_a["train_districts"][district] + share
        assert abs(share / total - 0.2) < 0.06


def test_encoders_fit_on_train_only_no_leakage():
    frame = build_feature_frame(make_clean_frame())
    train, test, _ = split_train_test(frame, seed=42)

    unseen = test.head(1).copy()
    unseen["plate"] = "测试集独有板块"
    unseen["district"] = "测试集独有区"
    unseen["orientation_main"] = "未知方位"

    encoders = fit_encoders(train)
    train_features = transform(train, encoders)
    test_features = transform(unseen, encoders)

    assert "plate=板块0" not in train_features.columns  # 频次编码不产生 One-Hot 列
    assert test_features.loc[unseen.index[0], "plate_freq"] == 0.0
    assert train_features["area"].iloc[0] == pytest.approx(train["area"].iloc[0])
    # 训练集没有的 One-Hot 类别不会新增列，也不会产生额外正值。
    assert not any(column.startswith("district=测试集独有区") for column in test_features.columns)
    assert test_features.loc[unseen.index[0], "orientation_main=北"] in (0, 1)


def test_ordinal_mapping_and_frequency_values():
    frame = build_feature_frame(make_clean_frame())
    encoders = fit_encoders(frame)
    features = transform(frame, encoders)

    assert features["floor_level_code"].iloc[0] == FLOOR_LEVEL_CODE["低"]
    assert features["decoration_code"].iloc[0] == DECORATION_CODE["未知"]
    assert sum(encoders["frequency"]["plate"].values()) == pytest.approx(1.0)
    assert sum(encoders["frequency"]["community"].values()) == pytest.approx(1.0)
    for index in frame.index:
        assert features.loc[index, "plate_freq"] == pytest.approx(
            encoders["frequency"]["plate"][frame.loc[index, "plate"]]
        )


def test_drop_constant_columns_applies_to_both_splits():
    frame = build_feature_frame(make_clean_frame())
    train, test, _ = split_train_test(frame, seed=42)
    encoders = fit_encoders(train)
    train_features = transform(train, encoders)
    test_features = transform(test, encoders)

    train_features["always_zero"] = 0
    test_features["always_zero"] = 0
    train_kept, test_kept, dropped = drop_constant_columns(train_features, test_features)

    assert "always_zero" in dropped
    assert "always_zero" not in train_kept.columns
    assert "always_zero" not in test_kept.columns
    assert list(train_kept.columns) == list(test_kept.columns)


def test_build_features_writes_outputs_and_excludes_elevator(tmp_path: Path):
    clean_path = tmp_path / "clean.csv"
    make_clean_frame().to_csv(clean_path, encoding="utf-8-sig", index=False)
    out_dir = tmp_path / "processed"

    report = build_features(clean_path=clean_path, out_dir=out_dir, seed=42)

    assert report["rows"] == 60
    assert report["train_rows"] + report["test_rows"] == 60
    for name in ("features.csv", "train.csv", "test.csv", "feature_meta.json"):
        assert (out_dir / name).exists()

    features = load_clean(out_dir / "features.csv")
    train = pd.read_csv(out_dir / "train.csv", encoding="utf-8-sig")
    test = pd.read_csv(out_dir / "test.csv", encoding="utf-8-sig")
    meta = json.loads((out_dir / "feature_meta.json").read_text(encoding="utf-8"))

    assert set(features["split"]) == {"train", "test"}
    assert "elevator" not in train.columns and "elevator" not in test.columns
    assert list(train.drop(columns=["row_id", "log_unit_price", "unit_price"]).columns) == list(
        test.drop(columns=["row_id", "log_unit_price", "unit_price"]).columns
    )
    assert "log_unit_price" in meta["non_feature_columns"]
    assert "unit_price" not in meta["feature_columns"]
    assert "log_unit_price" not in meta["feature_columns"]
