# 城市房价预测与朝向溢价分析

广州二手房 · 3 人组 · 《数据预处理》课程项目

**当前状态：项目骨架与计划 v0.2 已确认，下一步进入 W0 环境自检与 W1 三站试点；业务代码尚未开始。**

## 先看这里

- 项目计划书：[docs/PLAN.md](docs/PLAN.md)
- 课程原始材料（PDF / DOCX）已通过 `.gitignore` 排除，不进入版本管理。
- Web 双形态：**本地 FastAPI 动态版**（答辩主用）+ **GitHub Pages 静态版**（在线预览/备份）。

## 一句话目标

用广州多平台二手房数据预测房源单价，并在控制面积、区县、楼层、房龄等因素后，量化“朝向”对房价的溢价。

## 数据与分工

- **城市**：广州，按天河、越秀、海珠、荔湾、白云、黄埔、番禺、花都、南沙、从化、增城 11 区拆分采集。
- **站点**：链家、房天下、贝壳（安居客备选）；无账号，按“好爬优先”在 W1 试爬后确定 3 个主站点。
- **数据量**：每人 ≥1000 条，全组 ≥3000 条。
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

计划进入 W0 后补充并验证。目标流程：

```bash
# 1. 创建环境并安装依赖
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. 环境自检
python scripts/check_env.py

# 3. 采集（广州，按站点）
python -m crawler.lianjia --city guangzhou --max-pages 50
python -m crawler.fang --city guangzhou --max-pages 50
python -m crawler.beike --city guangzhou --max-pages 50

# 4. 清洗 + 特征 + 训练 + 评估
python scripts/run_pipeline.py

# 5. 导出 GitHub Pages 静态数据
python scripts/export_static.py

# 6. 启动本地 Web
python scripts/run_web.py
```

> 采集依赖网络与站点状态，不放进“一键全链路”默认流程；`run_pipeline.py` 支持 `--skip-crawl` 使用现有数据复现。

## 开发提示

- 本机 Git 未加入 PATH，可用绝对路径：`"C:\Program Files\Git\cmd\git.exe"`。
- 推荐 Python 3.11 / 3.12；若 3.14 安装二进制依赖失败，W0 切换到 3.12 或 conda。
- 前端零构建，不需要 Node.js；GitHub Pages 由 Actions 发布 `web/frontend/`。
