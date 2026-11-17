# AGENTS.md — 项目工作约定

本文件面向在本仓库工作的 AI Agent 与协作者。开始修改前先读本文件，再按需阅读：

- 项目计划书：[`docs/PLAN.md`](docs/PLAN.md)
- 目录、模块与数据流：[`docs/STRUCTURE.md`](docs/STRUCTURE.md)
- 项目入口与结果速览：[`README.md`](README.md)

## 1. 项目目标

《数据预处理》课程项目：**城市房价预测与朝向溢价分析**。

- 用广州多平台二手房数据预测房源单价。
- 在控制面积、区县、楼层、房龄、装修等因素后，量化“朝向”的房价溢价。
- 主分析城市：广州 11 区；主分析站点：房天下广州（`fang`）。
- 链家、贝壳多城市首页数据只用于**跨城市稳健性检验**，不用于广州区县结论。

## 2. 当前状态（2026-11-10）

| 阶段 | 状态 | 关键结果 |
|---|---|---|
| W0 环境 | 完成 | Python 3.14.5 + `.venv`；依赖见 `requirements.txt` |
| W1 采集 | 完成 | 房天下 1274、链家 1032、贝壳 1020；去重后 3326 条 |
| W2 清洗 | 完成 | 3326 → 3302 条；`model_keep=1` 3297 条；`clean_version=w2-v1` |
| W3 特征工程 | 完成 | `w3-v1`；广州房天下主样本 1251 条，训练 1000 / 测试 251，入模特征 40 个；33 项测试全通过 |
| W4/W5 建模与溢价 | 完成 | 最优 HistGB 测试 R²=0.710、RMSE 14106 元/㎡，较区县均值基线降 25.8%；朝南溢价 +4.1% 但不显著 |
| W6/W7 Web 可视化 | 完成 | 本地 FastAPI + Pages 静态版双形态，12 张真实图表、浅色拟物与磁吸交互；静态 JSON 与 API 数字一致；50 项测试全通过 |

W2 已确认的口径：

- 业务重复删除 24 条；跨平台重复标记 5 条（`cross_source_dup=1, model_keep=0`）。
- 链家广州区县补全 30/30（`community_map` 6 + `plate_map` 24）；广州 `district` 缺失为 0。
- 朝向缺失 13 条设为“未知”；建成年份缺 2636 条、装修缺 2513 条、楼层缺 930 条。
- 电梯 3302 条全部为“未知”，**W3 不将电梯作为主特征**。
- 单价偏差 >5% 的 43 条已标记 `unit_price_suspect=1`，保留原始值。
- 异常值标记 152 条、别墅/独栋标记 143 条；二者都**只标记、不删除**。

## 3. 环境与命令

### 3.1 环境

- 操作系统：Windows 10/11，PowerShell。
- Python：3.14.5，虚拟环境 `.venv/`。
- 依赖固定：[`requirements.txt`](requirements.txt)。
- 控制台代码页通常为 936（GBK）；运行 Python 前设置 UTF-8：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
```

- 若 `python` 指向 Windows 应用商店别名，直接使用：

```powershell
.\.venv\Scripts\python.exe
```

- Git 不在 PATH，使用绝对路径：

```powershell
& "C:\Program Files\Git\cmd\git.exe" status
```

### 3.2 常用命令

```powershell
# 环境自检
.\.venv\Scripts\python.exe scripts\check_env.py

# W2 清洗（从 raw 重建 clean.csv 与报告）
.\.venv\Scripts\python.exe -m pipeline.clean --day 2026-11-10

# W2 图表
.\.venv\Scripts\python.exe scripts\plot_preprocessing.py --day 2026-11-10

# 测试与语法检查
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall crawler pipeline scripts tests

# 采集（仅在确有必要时；默认低频）
.\.venv\Scripts\python.exe -m crawler.fang --max-pages 5
.\.venv\Scripts\python.exe -m crawler.lianjia --target-cities 33 --max-attempts 45
.\.venv\Scripts\python.exe -m crawler.beike --target-cities 33 --max-attempts 45

# W1 只统计已有数据
.\.venv\Scripts\python.exe scripts\pilot_w1.py --skip-crawl

# 生成脱敏样本
.\.venv\Scripts\python.exe scripts\make_samples.py
```

> 采集依赖网络与站点状态，不放入默认一键流程；W1/W2 均可基于 `data/raw/` 现有快照复现。

## 4. 代码结构约定

| 需求 | 放置位置 |
|---|---|
| 新站点采集器 | `crawler/<site>.py`，复用 `crawler/base.py`、`crawler/parse.py` |
| 解析/字段标准化 | `crawler/parse.py`；同步补 `tests/test_parse.py` |
| 清洗步骤 | `pipeline/clean.py`；同步更新 W2 报告与测试 |
| 特征工程 | `pipeline/features.py`（W3 新建） |
| 数据集构建 | `pipeline/build_dataset.py`（待建） |
| 训练/评估/溢价 | `models/train.py`、`models/evaluate.py`、`models/premium.py` |
| 一键脚本 | `scripts/`，业务逻辑仍放在 `pipeline/` 或 `models/` |
| Web 后端 | `web/backend/app.py`（待建，FastAPI） |
| 前端 | `web/frontend/`，零构建，不引入 Node.js |

原则：

- 函数职责单一；变量命名语义化；关键函数写 docstring。
- 不把最终逻辑堆在 Notebook；Notebook 只做探索。
- 能用标准库/现有依赖解决，不新增重量级依赖。
- 新增依赖必须固定版本并写入 `requirements.txt`。

## 5. 数据规则（重要）

### 5.1 分层

```text
data/raw/{source}/{city_code}/{date}.jsonl   # 原始层，只追加，不入库
data/processed/clean.csv                     # 清洗层，可重建，不入库
data/processed/features.csv                  # 特征层，W3 生成，不入库
data/samples/{source}_sample.csv             # 脱敏样本，入库
```

### 5.2 禁止事项

- **禁止手工修改、覆盖或删除 `data/raw/` 下的原始 JSONL。**
- **禁止手工编辑 `data/processed/clean.csv`**；必须改 `pipeline/clean.py` 后重跑。
- 禁止把 `data/raw/`、`data/processed/`、`logs/`、`models/artifacts/` 提交到 Git。
- 禁止在代码或仓库中写入 Cookie、账号、手机号、微信、门牌号、密钥。
- 禁止为采集而破解验证码、登录、403/429 限制或绕过 robots 合规限制。

### 5.3 采集合规

- 链家、贝壳翻页/筛选/区县页会触发登录或人机验证，**只取城市首页**，不重试绕过。
- 已披露：链家/贝壳 robots 对列表页有限制；报告与 README 中保留该限制说明。
- 单站点串行、随机间隔 1–3 秒、失败最多重试 3 次；遇阻断立即停止该站点。
- 只采集公开房源字段，不采集个人身份信息。

### 5.4 字段与建模口径

- 单价统一：`unit_price = total_price × 10000 / area`；原始展示值保留 `unit_price_raw`。
- 原始单价与重算单价偏差 >5%：`unit_price_suspect=1`，保留样本并说明面积口径。
- `is_outlier=1` **只标记不删除**；W3/W4 通过 `model_keep` 或敏感性对比决定是否剔除。
- `model_keep=0` 当前只用于跨平台重复，不能理解为“异常值已删除”。
- `district_source`：`site` / `community_map` / `plate_map` / `missing`；映射只用于广州链家样本。
- 非广州样本不补区县，跨城市分析使用 `city`。
- `is_villa=1` 为别墅/独栋/大平层类，W3 主模型剔除或做敏感性分析。
- 缺失值优先保留样本 + “未知”类别，禁止无依据 `fillna(0)`。
- 目标变量优先使用对数单价 `log(unit_price)`；评估时还原到元/㎡ 解释。

## 6. 测试与质量门槛

提交前至少执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall crawler pipeline scripts tests
```

要求：

- 当前基线：`pytest` 50 项全部通过（解析 11 + 清洗 14 + 特征 8 + 建模 8 + Web 9）。
- 新增解析规则 → 补 `tests/test_parse.py`。
- 新增清洗/特征逻辑 → 补 `tests/test_clean.py` 或 `tests/test_features.py`。
- 特征工程必须包含防泄漏检查：目标编码/统计量只能从训练集拟合。
- 修改清洗逻辑后，重跑 `pipeline.clean`，确认输出行数与报告同步更新。

## 7. Git 与提交约定

- 当前仓库所有 W1/W2 改动可能尚未提交；提交前先看 `git status`，不要覆盖他人未提交工作。
- 使用绝对路径 Git：`& "C:\Program Files\Git\cmd\git.exe"`。
- 建议按里程碑提交，例如：`feat: W2 清洗流水线`、`docs: 补充项目结构`、`feat: W3 特征工程`。
- 提交前确认没有 `.venv/`、`data/raw/`、`data/processed/`、`logs/`、密钥文件。
- 课程 PDF/DOCX 已在 `.gitignore` 中排除，不要强制加入。
- 不执行 `git reset --hard`、`git clean -fd` 等可能丢失用户数据的命令。

## 8. 文档同步要求

以下变更必须同步文档：

| 变更 | 需要更新 |
|---|---|
| 目录/模块职责变化 | `docs/STRUCTURE.md`、本文件 |
| 阶段状态/数据量变化 | `README.md`、`docs/PLAN.md` |
| 清洗口径变化 | `reports/preprocessing_report.md`、`docs/PLAN.md` 6.x |
| 新增特征 | `docs/feature_dictionary.md`（W3 已建，新特征需同步补充） |
| 建模/溢价结果变化 | `reports/modeling_report.md`、`reports/metrics.json`、`docs/PLAN.md` 8.x |
| 新增原始/清洗字段 | `docs/data_dictionary.md`（待建）、`docs/STRUCTURE.md` |
| 新增依赖 | `requirements.txt`、`README.md` |

## 9. Windows 注意事项

- 控制台 GBK 容易让中文输出乱码；Python 命令前设 `$env:PYTHONIOENCODING='utf-8'`。
- CSV 读写使用 `utf-8-sig`，避免 Excel 打开中文乱码。
- 路径含空格时加引号；Git 路径为 `C:\Program Files\Git\cmd\git.exe`。
- 文件编码统一 UTF-8；不要用系统默认编码写中文文件。
- 长路径/权限问题优先在项目根目录内操作，不使用系统临时目录存放项目数据。

## 10. 下一步（最终交付）

W0–W7 全部完成。Web 代码见 `web/`、`models/predict.py`、`scripts/{export_static,run_web,run_pipeline}.py`；双形态数字一致（实测同一房源 44474 元/㎡）。

1. 新建 `docs/data_dictionary.md`：原始字段与清洗字段对照。
2. 整合 10–15 页课程报告：摘要、引言、采集与合规、预处理与特征工程、模型与评估、可视化、总结与展望、参考文献、附录；图表统一编号。
3. 补 AI 使用声明、三人贡献说明（人—站点—区县—数据量—模块—代码路径）、仓库与 Pages 链接。
4. 截图与现场系统一致（本地版 + Pages 版），答辩前完整彩排一键流水线：`python scripts/run_pipeline.py --day 2026-11-10`。
