# 项目结构说明

> 适用版本：W7 完成（2026-11-17）。  
> 维护规则：目录、模块职责、数据流或入口命令发生变化时，同步更新本文件与根目录 [`AGENTS.md`](../AGENTS.md)。

## 1. 项目一句话

用广州多平台二手房数据预测房源单价，并在控制面积、区县、楼层、房龄等因素后，量化“朝向”对房价的溢价。

- 主分析城市：广州（11 个行政区）。
- 主分析站点：房天下广州（`fang`）。
- 稳健性检验：链家、贝壳多城市首页数据，仅使用 `city` 字段，不做事后区县映射。
- 当前阶段：W0–W7 全部完成；剩余工作为报告整合与答辩材料。

## 2. 目录树（当前实际状态）

```text
.
├── AGENTS.md                     # AI/协作者工作约定（根目录）
├── README.md                     # 项目入口、结果速览、复现命令
├── requirements.txt              # 固定版本依赖（Python 3.14.5 验证）
├── .gitignore                    # raw/processed/logs/密钥/课程材料忽略规则
├── .github/
│   └── workflows/pages.yml       # GitHub Pages 静态站部署
├── docs/
│   ├── PLAN.md                   # 项目计划书（v0.6，含 W1/W2/W3 结果）
│   ├── STRUCTURE.md              # 本文件
│   └── feature_dictionary.md     # W3 特征字典
├── config/                       # 配置目录（当前仅 .gitkeep）
├── crawler/                      # 采集层
│   ├── __init__.py
│   ├── base.py                   # 限速、重试、阻断识别、JSONL 落盘、统计
│   ├── parse.py                  # 三站列表页解析与字段标准化
│   ├── cities.py                 # 链家/贝壳城市代码表
│   ├── fang.py                   # 房天下广州 11 区翻页采集
│   ├── ershoufang.py             # 链家/贝壳城市首页共用采集器
│   ├── lianjia.py                # 链家 CLI 入口
│   └── beike.py                  # 贝壳 CLI 入口
├── pipeline/                     # 清洗与特征层
│   ├── __init__.py
│   ├── clean.py                  # W2 清洗流水线（w2-v1）
│   └── features.py               # W3 特征工程（w3-v1）
├── data/
│   ├── raw/                      # 原始 JSONL（不入库）
│   │   └── {source}/{city_code}/{date}.jsonl
│   ├── processed/                # 清洗/特征数据（不入库）
│   │   ├── clean.csv
│   │   ├── features.csv / train.csv / test.csv
│   │   └── feature_meta.json     # 版本、划分索引、编码器、入模列
│   ├── samples/                  # 脱敏小样本（入库）
│   │   ├── fang_sample.csv
│   │   ├── lianjia_sample.csv
│   │   ├── beike_sample.csv
│   │   └── README.md
│   └── db/                       # SQLite 预留目录（当前仅 .gitkeep）
├── models/
│   ├── __init__.py
│   ├── common.py                 # 共享工具：读取、特征列、指标、溢价换算
│   ├── train.py                  # W4 基线 + 主模型 + 消融 + 敏感性
│   ├── evaluate.py               # W5 指标汇总、分组误差、特征重要性、图表
│   ├── premium.py                # W5 朝向溢价、bootstrap、异质性、跨城市
│   ├── predict.py                # W6 表单输入 → 40 维特征映射（双形态共用）
│   └── artifacts/                # 模型产物（不入库）
├── web/
│   ├── __init__.py
│   ├── backend/
│   │   ├── __init__.py
│   │   └── app.py                # W6 FastAPI 接口 + 静态托管
│   └── frontend/
│       ├── index.html            # 零构建单页：五个区块
│       ├── assets/
│       │   ├── style.css         # 浅色拟物（neumorphism）样式
│       │   └── echarts.min.js    # 本地 ECharts（离线可用）
│       ├── js/
│       │   ├── config.js         # 本地/Pages 模式判断与统一加载
│       │   └── app.js            # 图表、磁吸交互、双形态预测
│       └── data/                 # W7 静态导出 JSON（6 个，入库）
├── scripts/                      # 可执行脚本
│   ├── check_env.py              # W0 环境自检
│   ├── pilot_w1.py               # W1 采集与字段完整率报告
│   ├── make_samples.py           # 从 raw 生成脱敏样本
│   ├── plot_preprocessing.py     # W2 缺失值/异常值图表
│   ├── export_static.py          # W7 导出 Pages JSON
│   ├── run_web.py                # W6 启动 FastAPI
│   └── run_pipeline.py           # 清洗→特征→训练→评估→导出一键串联
├── tests/
│   ├── test_parse.py             # 解析、数字提取、阻断识别
│   ├── test_clean.py             # 清洗、区县补全、去重、特征标记
│   ├── test_features.py          # W3 编码、分层划分、无泄漏检查
│   ├── test_models.py            # W4/W5 基线、指标、设计矩阵、训练冒烟测试
│   └── test_web.py               # W6/W7 接口、静态导出、预测映射与 NaN 回归
├── notebooks/                    # 探索性分析预留
├── logs/                         # 采集日志（不入库）
└── reports/                      # 可读报告、JSON/CSV 汇总、图表
    ├── pilot_w1_summary.json
    ├── pilot_w1_fields.csv
    ├── pilot_w1_notes.md
    ├── preprocessing_report.md
    ├── preprocessing_summary.csv
    ├── preprocessing_summary.json
    ├── metrics.json / premium.json
    ├── modeling_report.md
    ├── orientation_premium.csv / district_premium.csv / cross_city_premium.csv
    ├── crawl_fang / lianjia / beike 统计与补充记录
    ├── env_check.json
    └── figures/
        ├── missing_before_after.png / outlier_boxplot.png
        ├── true_vs_predicted.png / feature_importance.png / ablation.png
        └── orientation_premium.png
```

## 3. 模块职责

### 3.1 `crawler/` 采集层

| 文件 | 职责 | 关键约定 |
|---|---|---|
| `base.py` | HTTP/浏览器抓取、随机限速、重试、登录/验证码/403 识别、JSONL 追加落盘、按 URL 去重、统计 | 单站点串行；阻断不绕过；原始数据只追加 |
| `parse.py` | 房天下、链家、贝壳列表卡片解析；价格/面积/楼层/朝向/户型标准化；`FIELD_ORDER` 统一字段顺序 | 缺失字段返回 `None`，交给清洗层标“未知” |
| `cities.py` | 链家/贝壳城市代码、名称、拼音 | 城市代码即站点子域名前缀 |
| `fang.py` | 房天下广州 11 区县翻页采集 | 主分析数据源；每页约 60 条 |
| `ershoufang.py` | 链家/贝壳城市首页共用采集器 | 每城只取首页约 31 条；不翻页、不绕验证 |
| `lianjia.py` / `beike.py` | 站点 CLI 封装 | 支持 `--cities`、`--target-cities`、`--max-attempts` |

### 3.2 `pipeline/` 清洗与特征层

| 文件 | 职责 | 状态 |
|---|---|---|
| `clean.py` | raw JSONL → 类型转换 → 区县补全 → 朝向标准化 → 单价重算/校验 → 去重 → 缺失值处理 → 异常值/别墅标记 → `clean.csv` + W2 报告 | 已完成（`w2-v1`） |
| `features.py` | 广州房天下主样本筛选、朝向/面积衍生、One-Hot/序号/频次编码、按区县分层划分、防泄漏 | 已完成（`w3-v1`） |
| `build_dataset.py` | raw → clean → features 一键构建 | 待实现 |

### 3.3 `models/` 建模层

| 文件 | 职责 | 状态 |
|---|---|---|
| `common.py` | 数据读取、特征列识别、log/元/㎡双尺度指标、溢价换算 | 已完成 |
| `train.py` | 三个基线 + Ridge/RF/HistGB（5 折 CV 调参）+ 朝向消融 + 别墅/异常值敏感性 | 已完成 |
| `evaluate.py` | 指标汇总、分区县/价格段/朝向分组误差、permutation importance、图表 | 已完成 |
| `premium.py` | 朝向溢价（参照组北、HC3、bootstrap）+ 分区县异质性 + 跨城市稳健性 | 已完成 |
| `predict.py` | 表单输入映射为 40 维特征、Ridge 系数折算，供本地与静态版共用 | 已完成 |

### 3.4 `web/` 展示层

| 文件 | 职责 | 状态 |
|---|---|---|
| `backend/app.py` | FastAPI 六个接口 + 静态文件托管 + 空数据兜底 | 已完成 |
| `frontend/index.html` | 五个区块单页（概况/预处理/朝向/模型/预测） | 已完成 |
| `frontend/assets/style.css` | 浅色拟物风格、磁吸与区块吸附动画样式 | 已完成 |
| `frontend/assets/echarts.min.js` | 本地 ECharts 5.6，离线可用 | 已完成 |
| `frontend/js/config.js` | 本地/Pages 模式判断与统一数据加载 | 已完成 |
| `frontend/js/app.js` | 12 张图表、磁吸交互、双形态预测 | 已完成 |
| `frontend/data/*.json` | 六个静态数据文件（约 25 KB） | 已生成 |

### 3.5 `scripts/` 入口脚本

| 脚本 | 作用 | 状态 |
|---|---|---|
| `check_env.py` | Python/依赖/SQLite/Playwright 自检 | 已完成 |
| `pilot_w1.py` | W1 采集、字段完整率统计与报告 | 已完成 |
| `make_samples.py` | 从 raw 抽样生成脱敏 CSV | 已完成 |
| `plot_preprocessing.py` | 生成缺失值对比图、异常值箱线图 | 已完成 |
| `run_pipeline.py` | 清洗 → 特征 → 训练 → 评估 | 待实现 |
| `export_static.py` | 导出 Pages 静态 JSON | 待实现 |
| `run_web.py` | 启动本地 FastAPI | 待实现 |

### 3.6 其他目录

| 目录 | 用途 | 提交策略 |
|---|---|---|
| `docs/` | 计划书、结构说明、W3 特征字典；后续补 `data_dictionary.md` | 提交 |
| `tests/` | 解析、清洗、特征、建模与 Web 单测（50 项通过） | 提交 |
| `reports/` | 报告、汇总 JSON/CSV、图表 | 提交最新版本 |
| `data/samples/` | 每站 60 条脱敏样本 | 提交 |
| `web/` | 本地动态版 + Pages 静态版 | 提交源码与静态数据 |
| `models/artifacts/` | 训练产物（`.joblib` 等） | 忽略 |
| `notebooks/` | 探索性分析，最终逻辑沉淀到 `pipeline/` | 提交（数据不入库） |

## 4. 数据流

```text
站点 HTML
   │
   ▼
crawler/base.py + crawler/parse.py
   │
   ▼
data/raw/{source}/{city_code}/{date}.jsonl        # 只追加、按 URL 去重、不入库
   │
   ▼
pipeline/clean.py（W2, w2-v1）
   ├── data/processed/clean.csv                   # 48 列，不入库
   └── reports/preprocessing_report.md
       reports/preprocessing_summary.csv/.json
       reports/figures/*.png
   │
   ▼
pipeline/features.py（W3, w3-v1）
   ├── data/processed/features.csv
   ├── data/processed/train.csv / test.csv
   ├── data/processed/feature_meta.json
   └── docs/feature_dictionary.md
   │
   ▼
models/train.py → evaluate.py → premium.py（W4/W5，已完成）
   ├── models/artifacts/*.joblib + *.csv          # 不入库
   ├── reports/metrics.json / premium.json / modeling_report.md
   └── reports/figures/*                          # 真实 vs 预测、重要性、消融、溢价
    │
    ▼
models/predict.py + web/backend/app.py（W6/W7，已完成）
   ├── /api/*                → 本地 FastAPI 版（答辩主用，POST 预测）
   └── scripts/export_static.py → web/frontend/data/*.json（Pages 静态版，Ridge JS 预测）
```

## 5. 数据分层与命名

### 5.1 原始层 `data/raw/`

```text
data/raw/{source}/{city_code}/{date}.jsonl
```

- `source`：`fang` / `lianjia` / `beike`。
- `city_code`：`gz`、`sz`、`bj` 等；广州固定为 `gz`。
- `date`：采集日期，例如 `2026-11-10`。
- 每行一条 JSON 记录，字段顺序由 `crawler.parse.FIELD_ORDER` 统一。
- 只追加、不覆盖；同一 URL 不重复写入。
- **禁止手工修改或删除 raw 文件**；清洗结果必须能从 raw 重建。

### 5.2 清洗层 `data/processed/clean.csv`

当前 48 列，按用途分组：

| 分组 | 字段 |
|---|---|
| 来源与位置 | `source`, `source_id`, `city`, `city_code`, `district`, `plate`, `community`, `district_source` |
| 房源标识 | `title`, `url`, `page`, `list_url`, `crawl_time` |
| 户型与面积 | `layout`, `rooms`, `halls`, `bathrooms`, `area` |
| 朝向 | `orientation_raw`, `orientation`, `orientation_main`, `is_south`, `is_south_north`, `orientation_count`, `orientation_missing` |
| 楼层与房龄 | `floor_raw`, `floor_level`, `total_floors`, `floor_ratio`, `build_year`, `age`, `age_missing` |
| 装修与建筑 | `decoration`, `elevator`, `building_type`, `is_villa` |
| 价格 | `total_price`, `unit_price`, `unit_price_calc`, `unit_price_raw`, `unit_price_mismatch`, `unit_price_suspect` |
| 质量与去重 | `is_outlier`, `outlier_reason`, `cross_source_dup`, `dup_of_url`, `model_keep`, `clean_version` |

关键口径：

- `unit_price = total_price × 10000 / area`，原始展示值保留在 `unit_price_raw`。
- 原始单价与重算单价偏差 >5% 时 `unit_price_suspect=1`，不直接删样本。
- `is_outlier` 只标记，不删除；`model_keep=0` 目前只用于跨平台重复。
- `district_source` 取值：`site` / `community_map` / `plate_map` / `missing`；映射只用于广州链家样本。
- `is_villa` 依据标题、小区、建筑类型关键词标记；W3 主模型剔除或做敏感性分析。
- 电梯字段当前全部为“未知”，**不作为主特征**。

### 5.3 样本层 `data/samples/`

- 每站 60 条，去掉标题中的敏感/可定位信息后提交。
- 用于报告附录、Web 演示和快速查看字段。
- 由 `python scripts/make_samples.py` 从 raw 重新生成。

### 5.4 特征层与模型层（W3+）

W3 已生成（默认不入库）：

```text
data/processed/features.csv       # 1251 行主样本 + split 标签
data/processed/train.csv          # 1000 行，编码特征 + 目标
data/processed/test.csv           # 251 行，列结构与 train 一致
data/processed/feature_meta.json  # 版本、种子、划分索引、编码器、入模列
```

主样本口径：广州 + 房天下 + `model_keep=1`；别墅与异常值保留、只做标记；`elevator` 不入模。特征口径见 `docs/feature_dictionary.md`。

W4/W5 已生成（默认不入库）：

```text
models/artifacts/model_*.joblib          # 三个主模型
models/artifacts/predictions.csv         # 各模型训练/测试预测
models/artifacts/{cv_results,model_metrics,ablation,sensitivity,feature_importance,grouped_errors}.csv
models/artifacts/run_config.json
reports/{metrics.json,premium.json,modeling_report.md,orientation_premium.csv,...}
```

## 6. 提交与忽略规则

| 路径 | 是否提交 | 说明 |
|---|---|---|
| `crawler/`、`pipeline/`、`models/`、`scripts/`、`tests/`、`web/` | 提交 | 源码与测试 |
| `docs/`、`reports/`、`README.md`、`AGENTS.md` | 提交 | 文档与最新报告 |
| `data/samples/` | 提交 | 脱敏小样本 |
| `data/raw/**` | 忽略 | 原始数据体积大、含站点信息 |
| `data/processed/**` | 忽略 | 可由 raw 重建 |
| `data/db/**`、`*.sqlite*`、`*.db` | 忽略 | 数据库产物 |
| `logs/**`、`*.log` | 忽略 | 运行日志 |
| `models/artifacts/**` | 忽略 | 模型二进制 |
| `.venv/`、`__pycache__/`、`.pytest_cache/` | 忽略 | 本地环境与缓存 |
| `.env*`、`config/settings.yaml`、`config/*.local.yaml` | 忽略 | 配置与密钥 |
| 课程 PDF/DOCX | 忽略 | 版权与体积原因 |

## 7. 常用入口命令

> Windows PowerShell。若 `python` 指向系统别名，优先使用 `.venv\Scripts\python.exe`。

```powershell
# 环境自检
.\.venv\Scripts\python.exe scripts\check_env.py

# W1 采集（低频、断点续爬）
.\.venv\Scripts\python.exe -m crawler.fang --max-pages 5
.\.venv\Scripts\python.exe -m crawler.lianjia --target-cities 33 --max-attempts 45
.\.venv\Scripts\python.exe -m crawler.beike --target-cities 33 --max-attempts 45

# W1 只统计已有数据
.\.venv\Scripts\python.exe scripts\pilot_w1.py --skip-crawl

# W2 清洗与图表
.\.venv\Scripts\python.exe -m pipeline.clean --day 2026-11-10
.\.venv\Scripts\python.exe scripts\plot_preprocessing.py --day 2026-11-10

# W3 特征工程（生成 features/train/test + feature_meta.json）
.\.venv\Scripts\python.exe -m pipeline.features

# W4/W5 训练、评估、朝向溢价
.\.venv\Scripts\python.exe -m models.train
.\.venv\Scripts\python.exe -m models.evaluate
.\.venv\Scripts\python.exe -m models.premium

# W6/W7 Web：导出静态数据 + 启动本地版
.\.venv\Scripts\python.exe scripts\export_static.py
.\.venv\Scripts\python.exe scripts\run_web.py

# 一键流水线（清洗→特征→训练→评估→导出，不含采集）
.\.venv\Scripts\python.exe scripts\run_pipeline.py --day 2026-11-10

# 测试与语法检查
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall crawler pipeline models scripts tests web
```

## 8. 新增代码放置约定

| 需求 | 放置位置 |
|---|---|
| 新站点采集器 | `crawler/<site>.py`，复用 `base.py` 与 `parse.py` |
| 新解析规则 | `crawler/parse.py`，并补 `tests/test_parse.py` |
| 新清洗步骤 | `pipeline/clean.py`，同步更新 `reports/preprocessing_report.md` |
| 特征工程 | `pipeline/features.py` + `tests/test_features.py` + `docs/feature_dictionary.md` |
| 模型训练/评估 | `models/train.py`、`models/evaluate.py`、`models/premium.py` |
| 一键脚本 | `scripts/run_pipeline.py` 等，不把业务逻辑写进脚本 |
| Web API | `web/backend/app.py` |
| 前端 | `web/frontend/`，零构建，不引入 Node.js |

## 9. 当前缺口与最终交付待办

已完成（W3–W7）：

- [x] `pipeline/features.py`、`tests/test_features.py`、`docs/feature_dictionary.md`。
- [x] `data/processed/features.csv`、`train.csv`、`test.csv`、`feature_meta.json`。
- [x] 主样本口径：广州房天下 1251 条；别墅与异常值保留、只做标记。
- [x] `models/{common,train,evaluate,premium,predict}.py`、`tests/test_models.py`：基线、主模型、消融、敏感性、朝向溢价与置信区间。
- [x] 最优 HistGB 测试 R²=0.710、RMSE 14106 元/㎡；朝向溢价控制混杂后不显著（如实报告）。
- [x] `web/backend/app.py`、`web/frontend/`、`scripts/{export_static,run_web,run_pipeline}.py`、`tests/test_web.py`：本地/Pages 双形态 Web，12 张真实图表、浅色拟物与磁吸交互。
- [x] 静态 JSON 无 `NaN` 非法值；两套预测同一房源输出一致（44474 元/㎡）。

待办：

- [ ] `docs/data_dictionary.md`：原始字段与清洗字段对照。
- [ ] 最终报告（10–15 页）：整合 W1/W2 报告、建模简报、Web 截图，补 AI 声明与贡献说明。
- [x] 房龄/装修在主样本中全缺，已在 `reports/modeling_report.md` 作为数据源局限性说明；多城市样本仅做稳健性检验。
