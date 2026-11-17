# W3 特征字典

> 版本：`w3-v1`（2026-11-17）
> 生成脚本：`pipeline/features.py`
> 主样本：广州（`city_code=gz`）+ 房天下（`source=fang`）+ `model_keep=1`，共 **1251** 条。
> 别墅 `is_villa` 与异常值 `is_outlier` **全部保留、只做标记**，供敏感性对比。

## 1. 产物

| 文件 | 内容 |
|---|---|
| `data/processed/features.csv` | 1251 行主样本，原始清洗字段 + 派生特征 + `split` 划分标签 |
| `data/processed/train.csv` | 1000 行，仅含 `row_id` + 编码后特征 + 目标列 |
| `data/processed/test.csv` | 251 行，列结构与 `train.csv` 完全一致 |
| `data/processed/feature_meta.json` | 版本号、随机种子、划分索引、编码器（中位数/类别/频次）、入模列、剔除的常量列 |

## 2. 样本与划分口径

- 过滤条件：`source='fang' AND city_code='gz' AND model_keep=1`。链家/贝壳多城市首页样本**不进入主模型**，仅用于后续跨城市稳健性检验。
- 划分：`train_test_split`，按 `district` 分层，80/20，`random_state=42`；各区县训练/测试占比均接近 20%（见 `feature_meta.json`）。
- 划分索引落盘，可完全复现。
- **防泄漏**：中位数、One-Hot 类别集合、频次表全部只在训练集 `fit`；测试集只 `transform`，未见类别不会新建列、频次按 0 处理。
- 标签：目标为 `log(unit_price)`，`unit_price`（元/㎡）保留供评估反变换。

## 3. 目标列

| 字段 | 类型 | 定义 | 是否入模 |
|---|---|---|---|
| `unit_price` | float | `total_price × 10000 / area`，W2 重算值（元/㎡） | 否（评估用） |
| `log_unit_price` | float | `log(unit_price)` | **是（目标）** |

## 4. 数值特征

| 字段 | 类型 | 来源/定义 | 缺失处理 | 是否入模 |
|---|---|---|---|---|
| `area` | float | W2 建筑面积（㎡） | 训练集中位数 | 是 |
| `log_area` | float | `log(area)` | 训练集中位数 | 是 |
| `rooms` | int | W2 户型解析 | 训练集中位数 | 是 |
| `halls` | int | W2 户型解析 | 训练集中位数 | 是 |
| `bathrooms` | int | W2 户型解析 | 训练集中位数 | **否（主样本全缺，零方差剔除）** |
| `total_floors` | int | W2 楼层解析 | 训练集中位数 | 是 |
| `floor_ratio` | float | W2 楼层比例（低 0.15 / 中 0.5 / 高 0.85） | 训练集中位数 | 是 |
| `age` | int | 采集年份 − 建成年份 | 训练集中位数 | **否（房天下全缺，零方差剔除）** |
| `age_missing` | 0/1 | 建成年份是否缺失 | — | **否（主样本恒为 1，剔除）** |
| `area_per_room` | float | `area / rooms`，rooms=0 视为缺失 | 训练集中位数 | 是 |
| `rooms_missing` | 0/1 | `rooms` 是否缺失 | — | 是 |
| `floor_missing` | 0/1 | `floor_level` 是否为“未知” | — | 是 |
| `is_south` | 0/1 | W2 主朝向为南 | — | 是 |
| `is_south_north` | 0/1 | W2 同时含南、北 | — | 是 |
| `orientation_count` | int | W2 朝向方向数 | 训练集中位数 | 是 |
| `orientation_missing` | 0/1 | W2 朝向缺失标记 | — | 是 |
| `orientation_score` | ordinal | 主观辅助打分：南 4 / 东南·西南 3 / 东·西 2 / 东北·西北 1 / 北 0 / 未知 −1 | 未知 → −1 | 是（辅助，主结论仍用 One-Hot 系数） |
| `south_ratio` | float | 是否含“南” / 方向数 | 训练集中位数 | 是（辅助） |

## 5. 类别与编码特征

| 字段 | 类型 | 来源/定义 | 缺失处理 | 是否入模 |
|---|---|---|---|---|
| `district` | 类别 | W2 区县（广州 11 区） | 缺失设为“未知”类 | 是：One-Hot `district=*` |
| `orientation_main` | 类别 | W2 主朝向（8 方位 + 未知） | 缺失设为“未知”类 | 是：One-Hot `orientation_main=*` |
| `building_type` | 类别 | W2 建筑类型 | 缺失设为“未知”类 | 是：One-Hot `building_type=*` |
| `floor_level` | 有序 | 低 / 中 / 高 / 未知 | 缺失设为“未知” | 是：`floor_level_code`（低 0 / 中 1 / 高 2 / 未知 −1） |
| `decoration` | 有序 | 毛坯 / 简装 / 精装 / 豪装 / 未知 | 缺失设为“未知” | **否（主样本全缺，零方差剔除）**；映射为 毛坯 0 / 简装 1 / 精装 2 / 豪装 3 / 未知 −1 |
| `plate` | 高基数类别 | W2 板块 | 缺失设为“未知” | 是：频次编码 `plate_freq`（训练集占比，未见值 0） |
| `community` | 高基数类别 | W2 小区 | 缺失设为“未知小区” | 是：频次编码 `community_freq`（不做 Target Encoding，避免泄漏） |
| `elevator` | 类别 | W2 电梯 | 全缺 → “未知” | **否（字段全缺，明确不入模）** |

## 6. 标记与追溯列（不入模）

| 字段 | 用途 |
|---|---|
| `row_id` | 主样本内稳定行号，用于把 train/test 关联回 features.csv 与后续分组分析 |
| `source` / `city_code` / `city` | 来源与城市追溯（主样本为常量） |
| `split` | `train` / `test` 划分标签 |
| `is_villa` | 别墅标记，主样本保留，供“含/不含别墅”敏感性对比 |
| `is_outlier` | 异常值标记，主样本保留，供“剔除/保留”敏感性对比 |
| `unit_price_suspect` | W2 单价偏差 >5% 标记（43 条，均为别墅类） |
| `model_keep` | W2 建模可用标记（主样本恒为 1） |
| `orientation` / `floor_level` / `decoration` / `building_type` / `orientation_main` | 编码前的原始类别值，供解释与分组分析 |

## 7. 主样本实际入模列

`train.csv` / `test.csv` 共 43 列：`row_id` + **40 个特征** + 2 个目标列。40 个特征包括：

- 数值/有序 16 个：area、log_area、rooms、halls、total_floors、floor_ratio、area_per_room、rooms_missing、floor_missing、is_south、is_south_north、orientation_count、orientation_missing、orientation_score、south_ratio（15 个）+ floor_level_code（1 个）
- 区县 One-Hot 11 个、朝向 One-Hot 9 个、建筑类型 One-Hot 2 个
- 频次编码 2 个：plate_freq、community_freq

> 说明：`bathrooms`、`age`、`age_missing`、`decoration_code` 在广州房天下样本中为常量，训练/测试集已自动剔除；`elevator` 全程不入模。房龄与装修缺失是数据源限制（列表页不展示），将在报告中作为局限性说明，不做无依据填充。
