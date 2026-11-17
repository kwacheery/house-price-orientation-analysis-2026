# 城市房价预测与朝向溢价分析

广州二手房 · 3 人组 · 《数据预处理》课程项目

**当前状态：W0 环境自检完成（Python 3.14.5 + venv）；W1 采集完成（3326 条）；W2 清洗完成——输出 3302 条、`model_keep=1` 共 3297 条；W3 特征工程完成——广州房天下主样本 1251 条，训练 1000 / 测试 251，入模特征 40 个。下一步进入 W4 建模与朝向溢价分析。**

## 先看这里

- 项目计划书：[docs/PLAN.md](docs/PLAN.md)
- 项目结构与数据流：[docs/STRUCTURE.md](docs/STRUCTURE.md)
- W3 特征字典：[docs/feature_dictionary.md](docs/feature_dictionary.md)
- 协作与 Agent 约定：[AGENTS.md](AGENTS.md)
- 课程原始材料（PDF / DOCX）已通过 `.gitignore` 排除，不进入版本管理。
- Web 双形态：**本地 FastAPI 动态版**（答辩主用）+ **GitHub Pages 静态版**（在线预览/备份）。

## 一句话目标

用广州多平台二手房数据预测房源单价，并在控制面积、区县、楼层、房龄等因素后，量化“朝向”对房价的溢价。

## W1 结果速览

| 站点 | 采集范围 | 去重后条数 | 核心字段完整率 |
|---|---|---:|---|
| 房天下 | 广州 11 区 × 2 页 | 1274 | 区县/面积/朝向/总价/单价 100%，楼层 85.2% |
| 链家 | 34 城首页（每城 1 页） | 1032 | 面积/朝向/总价/单价 99%+，区县 12.9%* |
| 贝壳 | 34 城首页（每城 1 页） | 1020 | 区县 87.5%，面积/朝向/总价/单价 99%+ |
| **合计** | 3 站 | **3326** | 详见报告 |

\* 链家城市首页卡片不展示区县，`district` 原值为空；W2 已用“小区/板块 → 区县”映射补全广州链家样本 30/30，并标记 `district_source`。广州主分析使用房天下数据，跨城市稳健性检验使用 `city`。

- 详细报告：[`reports/pilot_w1_summary.json`](reports/pilot_w1_summary.json)、[`reports/pilot_w1_fields.csv`](reports/pilot_w1_fields.csv)、[`reports/pilot_w1_notes.md`](reports/pilot_w1_notes.md)。
- 脱敏样本：[`data/samples/`](data/samples/)，每站 60 条，由 `python scripts/make_samples.py` 生成。
- 链家/贝壳每城只取首页约 31 条，不翻页、不破解验证码；建成年份、装修、电梯等列表页缺失字段按“未知”处理。

## W2 清洗结果速览

| 指标 | 结果 |
|---|---|
| 清洗脚本 | `pipeline/clean.py`（`w2-v1`） |
| 输入 / 输出 | 3326 条 → 3302 条 |
| 建模可用 | 3297 条（5 条跨平台重复标记 `model_keep=0`） |
| 异常值标记 | 152 条（4.60%，`model_keep` 仍为 1，建模阶段做剔除敏感性对比） |
| 去重 | 业务重复 24 条、跨平台重复 5 条 |
| 链家广州区县补全 | 30/30（小区映射 6 + 板块映射 24） |
| 朝向缺失 | 13 条（设“未知”，不删除） |
| 别墅/独栋标记 | 143 条（W3 主模型剔除或做敏感性对比） |
| 单价偏差 >5% | 43 条（房天下别墅/大平层为主，保留原始值并标记） |

- 预处理报告：[`reports/preprocessing_report.md`](reports/preprocessing_report.md)
- 每步汇总：[`reports/preprocessing_summary.csv`](reports/preprocessing_summary.csv)、[`reports/preprocessing_summary.json`](reports/preprocessing_summary.json)
- 图表：[`reports/figures/missing_before_after.png`](reports/figures/missing_before_after.png)、[`reports/figures/outlier_boxplot.png`](reports/figures/outlier_boxplot.png)
- 清洗后数据：`data/processed/clean.csv`（由 raw 重新生成，不入库）。

## W3 特征工程结果速览

| 指标 | 结果 |
|---|---|
| 特征脚本 | `pipeline/features.py`（`w3-v1`） |
| 主样本 | 广州 + 房天下 + `model_keep=1`：1251 条（别墅 128、异常值 70 全部保留，只做标记） |
| 训练 / 测试 | 1000 / 251，按 `district` 分层，随机种子 42，索引落盘 |
| 入模特征 | 40 个（数值/有序 16 + 区县 One-Hot 11 + 朝向 One-Hot 9 + 建筑类型 One-Hot 2 + 频次 2） |
| 目标变量 | `log(unit_price)`，保留 `unit_price` 供评估还原 |
| 防泄漏 | 中位数 / One-Hot 类别 / 频次表只在训练集 fit；测试集仅 transform |
| 剔除的常量列 | `bathrooms`、`age`、`age_missing`、`decoration_code`（房天下样本中无变化） |
| 明确不入模 | `elevator`（3302 条全为“未知”）；小区不做 Target Encoding，改用频次编码 |

- 特征层数据：`data/processed/features.csv`、`train.csv`、`test.csv`、`feature_meta.json`（不入库）。
- 字段说明与口径：[docs/feature_dictionary.md](docs/feature_dictionary.md)
- 数据局限：房天下列表页不展示建成年份与装修，主模型无法控制房龄/装修，报告中作为局限性说明。

## 数据与分工

- **城市**：主分析广州，按天河、越秀、海珠、荔湾、白云、黄埔、番禺、花都、南沙、从化、增城 11 区拆分采集；链家/贝壳另取 34 个城市首页，用于跨城市稳健性检验。
- **站点**：A 房天下广州（区县翻页，1274 条）；B 链家多城市首页（1032 条）；C 贝壳多城市首页（1020 条）。链家/贝壳翻页会触发登录或人机验证，因此只取城市首页。
- **数据量**：已采集去重后 3326 条，三站均 ≥1000 条；全组目标 ≥3000 条已达成。
- **分工**：每人一个不同站点，同时交叉负责预处理、建模、Web 中的一个模块。

## 目录结构

```
.
├── .github/workflows/   # GitHub Pages 自动部署
├── crawler/             # 数据采集（链家/房天下/贝壳/安居客）
├── pipeline/            # 清洗、特征工程、数据集构建
├── models/              # 模型训练、评估、朝向溢价分析
├── web/                 # 本地 FastAPI + 静态前端（双形态）
├── data/                # raw / processed / samples / db
├── docs/                # 计划书与项目文档
├── config/              # 配置模板
├── scripts/             # 环境自检、一键流水线、静态导出
├── tests/               # 单元测试
├── notebooks/           # 探索性分析
└── reports/             # 图表与报告草稿
```

## 运行方式

环境部分已在 W0 验证通过（Python 3.14.5）；采集与后续步骤从 W1 起逐步验证：

```bash
# 1. 创建环境并安装依赖
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. 环境自检
python scripts/check_env.py

# 3. 采集（默认低频、断点续爬、按 URL 去重）
python -m crawler.fang --max-pages 5
python -m crawler.lianjia --target-cities 33 --max-attempts 45
python -m crawler.beike --target-cities 33 --max-attempts 45

# 3.1 只统计已有数据，生成 W1 字段完整率报告
python scripts/pilot_w1.py --skip-crawl

# 4. 清洗与预处理（W2）
python -m pipeline.clean --day 2026-11-10

# 4.1 预处理图表
python scripts/plot_preprocessing.py --day 2026-11-10

# 5. W3 特征工程（已实现）
python -m pipeline.features

# 6. 训练 + 评估 + 朝向溢价（W4/W5，待实现）
# python scripts/run_pipeline.py

# 7. 导出 GitHub Pages 静态数据
python scripts/export_static.py

# 8. 启动本地 Web
python scripts/run_web.py
```

> 采集依赖网络与站点状态，不放进“一键全链路”默认流程；W1/W2/W3 均可基于 `data/raw/` 现有快照复现。

## 开发提示

- 依赖版本固定在 `requirements.txt`，已在 Python 3.14.5 下验证；环境自检结果见 `reports/env_check.json`。
- 本机 Git 未加入 PATH，可用绝对路径：`"C:\Program Files\Git\cmd\git.exe"`。
- 已验证 Python 3.14.5；若其他机器安装二进制依赖失败，可回退到 3.11/3.12 或 conda。
- 前端零构建，不需要 Node.js；GitHub Pages 由 Actions 发布 `web/frontend/`。
