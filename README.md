# AlphaFlow

> A-share 主线趋势识别引擎 — 9 维评分体系自动发现市场主线，辅助日更级趋势交易。

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 什么是主线？

A 股市场中，资金总会在某些板块反复聚焦、形成趋势。这些被资金选中的方向就是**主线**。主线的形成、加速、扩散和衰退，构成了市场的核心节奏。

AlphaFlow 的目标：**在主线刚启动时发现它，在主线过热时提醒你别追。**

## 核心理念

```
拒绝追高 → 偏好潜伏 → 重视趋势定性
```

AlphaFlow 不是选股器，不是量化回测框架。它是一个**主线定性工具**，帮你回答三个问题：

1. **现在谁是主线？** — 9 维评分排名
2. **主线处于什么阶段？** — startup → markup → pullback → tail_rebound
3. **哪些方向刚开始升温？** — early_stage 潜伏机会筛选

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                       用户交互入口                          │
│      微信 / QQ / WebUI / CLI / Hermes Chat / 定时推送        │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                         Hermes 编排层                       │
│  自然语言理解 / 工具调用 / 定时任务 / 消息推送 / 人工确认     │
│  示例：今天主线是什么？AI 算力有什么催化？检查我当前持仓。   │
└──────────────────────────────┬──────────────────────────────┘
                               │ 本地 HTTP 接口调用
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                      AlphaFlow 策略服务                      │
│  主线评分 / 新兴主线检测 / 候选股诊断 / 买卖观察 / 风控复盘   │
│  输出结构化结果，供 Hermes 组织成可读的中文结论与提醒。      │
└──────────────────────────────┬──────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
┌─────────────────────────────┐   ┌───────────────────────────┐
│       QMT / xtdata 数据层    │   │  PostgreSQL 缓存（可选）   │
│  板块、个股、日线、分钟线等   │   │  减少重复拉取、提升响应速度 │
└─────────────────────────────┘   └───────────────────────────┘
```

AlphaFlow 起初就是作为 Hermes 可调用的本地策略服务来设计的：Hermes 负责接收微信、QQ、WebUI 或 CLI 里的自然语言问题，再调用 AlphaFlow 的 API 获取主线排名、板块热度、候选股、持仓和风险信息。

微信、QQ 等即时通讯工具可以作为 Hermes 的机器人入口，用户不用直接打开接口文档，也可以用自然语言查询主线。

在这个分工里，AlphaFlow 更像“结构化行情与策略判断引擎”，Hermes 更像“对话入口和解释层”。例如用户可以在 QQ 或微信里问 Hermes：

- 今天最强主线是什么？
- AI 算力、机器人、半导体这些方向分别有什么催化？
- 某条主线是不是已经过热？
- 我当前持仓是否还贴合主线？
- 盘前/盘中/盘后有哪些需要提醒的变化？

板块催化因素可以由 Hermes 在 AlphaFlow 输出的主线、板块、候选股和风险数据基础上，结合新闻、公告、研报摘要或用户自己的事件库进行归因分析；AlphaFlow 负责提供稳定、可追踪的市场结构数据。

### 项目结构

```
AlphaFlow/
├── app/
│   ├── api/routes.py              # API 路由定义
│   ├── core/
│   │   ├── config.py              # 配置管理（pydantic-settings）
│   │   └── logging.py             # 日志配置
│   ├── models/
│   │   ├── api.py                 # API 响应模型
│   │   ├── common.py              # 通用模型
│   │   └── domain.py              # 领域模型（MainlineScore 等）
│   ├── repositories/
│   │   ├── cache_repository.py    # PostgreSQL 缓存层
│   │   ├── config_repository.py   # 主线配置加载
│   │   ├── log_repository.py      # JSONL 日志
│   │   └── polling_policy_repository.py
│   └── services/
│       ├── market_data_service.py       # 核心数据编排
│       ├── qmt_connector.py             # QMT xtdata 桥接
│       ├── mainline_scoring_service.py  # 9 维评分计算
│       ├── trend_feature_extractor.py   # 趋势特征提取
│       ├── breadth_feature_extractor.py # 广度特征提取
│       ├── quality_score_engine.py      # 质量评分
│       ├── trend_stage_engine.py        # 趋势阶段分类
│       ├── candidate_diagnostics_service.py
│       ├── emerging_detector.py         # 新兴主线检测
│       ├── stock_pool_builder.py        # 股票池构建
│       ├── signal_engine.py             # 买卖信号
│       ├── risk_engine.py               # 风险评估
│       ├── review_engine.py             # 每日复盘
│       └── ...
├── runtime/
│   └── config/mainlines.json     # 主线配置（可自定义）
├── scripts/                      # 辅助脚本
├── tests/                        # 单元测试
├── pyproject.toml                # 项目元数据
├── .env.example                  # 环境变量模板
└── README.md
```

## 9 维评分体系

AlphaFlow 对每条主线计算 9 个维度的分数，每个维度 0-5 分（满分 45）：

| 维度 | 说明 | 数据源 |
|------|------|--------|
| **行业逻辑** | 成分股数量 & 板块覆盖度 | 板块配置 |
| **资金强度** | 5d/10d 成交额趋势 | 日线成交额 |
| **龙头质量** | 多日持续涨幅 + 跟风差距 | 日线涨跌幅 |
| **趋势加速度** | 今日加速 vs 减速 | 日线动量 |
| **扩散广度** | 站上 MA5/MA10 的个股占比 | 日线均线 |
| **持续性** | 5d/10d 累计涨幅 | 日线累计收益 |
| **市场状态** | 日内动量确认 | 分钟线 |
| **资本属性** | 机构抱团 vs 游资炒作 | 成交结构 |
| **趋势成熟度** | 站上 MA20 天数 & 斜率 | 日线均线 |

### Tier 划分

- **core** (≥22 分) — 核心主线，趋势明确
- **secondary** (≥16 分) — 次级主线，值得跟踪
- **rotation** — 轮动观察
- **noise** — 噪音，忽略

### 健康标签

根据各维度得分组合，AlphaFlow 给每条主线标注健康状态：

- **early_stage** — 刚开始升温，位置安全，适合潜伏
- **healthy_rally** — 趋势健康，可持仓观察
- **markup** — 加速上涨，注意追高风险
- **tail_rebound** — 鱼尾反弹，不建议参与

### 拥挤惩罚

当扩散度高但资金跟不上时，触发拥挤惩罚（crowding_penalty），防止"假高潮"被误标为核心。

## 快速开始

### 环境要求

- **操作系统**: Windows 10/11
- **QMT 客户端**: 需要安装 QMT 交易终端（提供 xtdata 行情源）
- **Python**: 3.11+
- **PostgreSQL**: 可选（用于缓存持久化，不装也能跑）

### 安装

```bash
git clone https://github.com/yourname/AlphaFlow.git
cd AlphaFlow

# 创建虚拟环境（推荐使用项目内 .venv，避免全局 Python 与 QMT numpy 冲突）
python -m venv .venv
.venv\Scripts\activate    # Windows PowerShell

# 安装依赖
pip install -e .

# 安装 PostgreSQL 缓存层（可选）
pip install psycopg2-binary
```

### 配置（交互式向导）

```bash
# 首次运行配置向导（交互式引导填写所有配置项）
python setup.py

# 快速配置（只填必要项）
python setup.py --quick
```

向导会自动：
1. 扫描本机 QMT 安装路径
2. 引导填写资金账号、服务端口等
3. 配置数据库（可选）
4. 生成 `.env` 配置文件

也可以手动配置：

```bash
cp .env.example .env
# 编辑 .env，填写你的 QMT 路径和账号
notepad .env
```

`.env` 关键配置项：

```ini
# QMT 连接（必填）
ALPHAFLOW_QMT_SITE_PACKAGES=D:/你的QMT路径/bin.x64/Lib/site-packages
ALPHAFLOW_QMT_USERDATA_PATH=D:/你的QMT路径/userdata_mini
ALPHAFLOW_QMT_ACCOUNT_ID=你的资金账号

# 数据库（可选，不填则禁用 DB 缓存）
ALPHAFLOW_DB_HOST=localhost
ALPHAFLOW_DB_PORT=5432
ALPHAFLOW_DB_USER=postgres
ALPHAFLOW_DB_PASSWORD=你的密码
ALPHAFLOW_DB_NAME=quant
```

### 启动

```bash
# 方式 1: 使用启动脚本（推荐）
.\start_alphaflow.ps1

# 方式 2: 使用 Python 脑本
python run_server.py

# 方式 3: 直接 uvicorn
uvicorn app.main:app --host 127.0.0.1 --port 8710
```

服务启动后访问 `http://127.0.0.1:8710/docs` 查看交互式 API 文档。

### 运行测试

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## API 接口

### 主线评分（最常用）

```
GET /api/v1/mainlines/scores?limit=5
```

返回按总分排序的主线列表，包含 9 维评分、tier、health_label。

### 全量诊断

```
GET /api/v1/diagnose/full
```

返回主线排名 + 候选股诊断 + 新兴主线检测，适合深度分析。

### 候选股

```
GET /api/v1/candidates/core?mainline=AI算力
```

返回指定主线的核心候选股列表。

### 买卖观察

```
GET /api/v1/signals/buy-watchlist
GET /api/v1/signals/sell-watchlist
```

### 组合诊断

```
GET /api/v1/portfolio/inspect
```

### 每日简报

```
GET /api/v1/mainlines/daily-brief
```

### 风险告警

```
GET /api/v1/risk/alerts
```

### 新兴主线检测

```
GET /api/v1/mainlines/emerging?lookback_minutes=30
```

检测近期排名快速上升的主线，标注 early_watch / warming / confirmed_hot 阶段。

### 轮询策略建议

```
GET /api/v1/polling-policy
```

根据市场状态返回推荐的轮询间隔和推送策略。

## 主线配置

AlphaFlow 支持配置驱动的主线映射，编辑 `runtime/config/mainlines.json`：

```json
{
  "filters": {
    "allowed_markets": ["SH", "SZ"],
    "exclude_name_prefixes": ["ST", "*ST", "N", "C"],
    "min_snapshot_amount": 30000000,
    "min_price": 2.5
  },
  "mainlines": [
    {
      "name": "AI算力/服务器液冷",
      "group": "AI算力",
      "exact_sectors": ["GN服务器", "GN液冷服务器"],
      "keyword_sectors": [],
      "min_sector_hits": 2
    },
    {
      "name": "机器人/人形机器人",
      "group": "机器人",
      "exact_sectors": ["GN人形机器人"],
      "keyword_sectors": ["人形机器人", "具身智能"],
      "min_sector_hits": 2
    }
  ]
}
```

### 映射方式

- `exact_sectors` — 精确匹配板块名称
- `keyword_sectors` — 关键词模糊匹配
- `min_sector_hits` — 至少命中几个板块才纳入

### 主线组/二级分支

AlphaFlow 支持层级结构：

```
消费电子
├── 消费电子/苹果链        ← 核心主线
├── 消费电子/PCB           ← 核心主线
└── 消费电子/AI眼镜折叠屏  ← 新兴方向
```

分组让扩散度计算更精确，也方便 Fallback 跳转：当主线过热时，优先看同组的冷分支。

### Fallback 机制

当主线过热时，系统按以下优先级跳转：

1. **同组分支** — 先看同一 group 下的冷门分支
2. **相邻链条** — 再看关联行业的分支
3. **全市场** — 最后才扫描全市场

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ALPHAFLOW_QMT_SITE_PACKAGES` | — | QMT site-packages 路径 |
| `ALPHAFLOW_QMT_USERDATA_PATH` | — | QMT userdata 路径 |
| `ALPHAFLOW_QMT_ACCOUNT_ID` | — | QMT 资金账号 |
| `ALPHAFLOW_QMT_SESSION_ID` | `101` | QMT session ID |
| `ALPHAFLOW_HOST` | `127.0.0.1` | 服务监听地址 |
| `ALPHAFLOW_PORT` | `8710` | 服务端口 |
| `ALPHAFLOW_ADVISORY_ONLY_MODE` | `true` | 仅建议模式 |
| `ALPHAFLOW_ENABLE_ORDER_SUBMISSION` | `false` | 允许下单 |
| `ALPHAFLOW_MAX_TOTAL_EXPOSURE` | `0.8` | 最大总仓位 |
| `ALPHAFLOW_MAX_SINGLE_POSITION` | `0.3` | 单股最大仓位 |
| `ALPHAFLOW_DB_HOST` | `localhost` | PostgreSQL 主机 |
| `ALPHAFLOW_DB_PORT` | `5432` | PostgreSQL 端口 |
| `ALPHAFLOW_DB_USER` | `postgres` | 数据库用户 |
| `ALPHAFLOW_DB_PASSWORD` | — | 数据库密码 |
| `ALPHAFLOW_DB_NAME` | `quant` | 数据库名 |
| `ALPHAFLOW_QMT_CALL_TIMEOUT` | `15` | QMT 调用超时(秒) |
| `ALPHAFLOW_QMT_MAX_BATCH` | `20` | 批量查询上限 |
| `ALPHAFLOW_MAINLINE_SCORE_CACHE_TTL` | `30` | 评分缓存 TTL(秒) |

## License

MIT License - 详见 [LICENSE](LICENSE)
