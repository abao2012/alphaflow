---
name: alphaflow-monitor
description: Monitor AlphaFlow market mainline status, detect changes, fetch catalyst information, and push alerts to WeChat
version: 1.0.0
author: AlphaFlow
---

# AlphaFlow 市场主线监控技能

## 概述

此技能让 Hermes Agent 能够：
1. 轮询 AlphaFlow API 获取市场主线状态
2. 对比历史快照，检测重大变化
3. 获取催化信息，解释"为什么是这条主线"
4. 生成推送消息，发送到微信/QQ

## 触发条件

- 用户通过微信/QQ 询问市场主线状态
- Cronjob 定时轮询（推荐每 5-15 分钟）
- 用户主动查询某条主线的催化剂/成因

## 前置条件

### 1. AlphaFlow 服务已启动

```bash
# 确认服务运行中
curl http://127.0.0.1:8710/api/v1/system/health

# 如果未启动，在 Windows 侧运行
# .\start_alphaflow.ps1
# 或
# python run_server.py
```

### 2. 运行安装脚本

```bash
cd AlphaFlow
bash hermes/setup.sh
```

这会将技能文件安装到 `~/.hermes/skills/mlops/alphaflow-monitor/`。

## 核心接口

### 首选：`/api/v1/mainlines/scores`

轻量级，始终有缓存，亚秒级返回。

```bash
curl -4 --noproxy '*' -s -m 30 'http://127.0.0.1:8710/api/v1/mainlines/scores?limit=5'
```

返回 9 维评分、tier、health_label 等。

### 备选：`/api/v1/diagnose/full`

重型聚合接口，含候选股诊断、新兴主线、推送提示。有 30 秒缓存。

```bash
curl -4 --noproxy '*' -s -m 30 'http://127.0.0.1:8710/api/v1/diagnose/full'
```

**重要**：仅在需要候选级诊断时使用，不要高频轮询。

## 数据结构

### /diagnose/full 关键字段

```json
{
  "timestamp": "2026-04-20T10:30:00",
  "market_phase": "主升",
  "top_mainline": {"name": "AI算力/CPO共封装", "score": 29.5, "group": "AI算力"},
  "rankings": [{"name": "...", "group": "...", "total_score": 26.5, "tier": "core", "health_label": "early_stage"}],
  "emerging_mainlines": [
    {
      "name": "AI算力/CPO共封装",
      "stage": "early_watch",
      "suggestion": "probe",
      "action_plan": "probe_small",
      "confidence": "high",
      "position_budget_pct": 0.05,
      "catalyst_tags": ["资金先行", "扩散增强"],
      "dedupe_key": "2026-04-20:AI算力/CPO共封装:probe",
      "reasons": ["短周期评分提升 2.50 分。"]
    }
  ],
  "polling_policy_hint": {
    "immediate_push_recommended": true,
    "summary_push_recommended": false,
    "trigger_reasons": ["emerging suggestion is probe"],
    "dedupe_keys": ["2026-04-20:AI算力/CPO共封装:probe"]
  },
  "market_story": {
    "headline": "AI算力分支出现升温信号，可小仓试探，但不宜把确认信号当成早期潜伏信号追高。"
  },
  "candidate_diagnostics": [
    {
      "name": "中际旭创",
      "stage": "startup",
      "system_attitude": "potential_first",
      "quality_scores": {"startup_quality": 86, "trend_integrity": 79, "capital_quality": 74, "crowding_risk": 22}
    }
  ],
  "diagnostics_error": null
}
```

## 推送决策规则

### 立即推送（immediate push）

满足任一条件：
- `polling_policy_hint.immediate_push_recommended == true`
- 市场阶段变化（轮动 → 主升 / 退潮）
- 主线切换
- 评分变化 ≥ 3.0
- 新增风控告警
- 新增买卖信号

### 汇总推送（summary push）

- `polling_policy_hint.summary_push_recommended == true`
- 且未触发立即推送
- watch/avoid_chase 类信号进入汇总队列

### 去重规则

- 使用 `polling_policy_hint.dedupe_keys` 或 `emerging_mainlines[*].dedupe_key`
- 同一交易日同一 dedupe_key 只推送一次
- avoid_chase 同一主线同一天最多一次
- 去重状态存储在 `~/.hermes/alphaflow_snapshots/dedupe_state.json`

### 消息生成

- 首句优先使用 `market_story.headline`
- 优先消费 `candidate_diagnostics`（潜力型 > 跟踪型 > 尾端型）
- 回退到 `emerging_mainlines`（probe > watch > avoid_chase）

## 轮询脚本

### poll_alphaflow.py

位于 `~/.hermes/skills/mlops/alphaflow-monitor/scripts/poll_alphaflow.py`

功能：
1. 轮询 AlphaFlow API（首选 scores，备选 diagnose/full）
2. 保存快照到 `~/.hermes/alphaflow_snapshots/`
3. 对比上一次快照
4. 评估去重状态
5. 生成推送消息草稿
6. 管理 summary 队列

```bash
# 手动运行测试
python3 ~/.hermes/skills/mlops/alphaflow-monitor/scripts/poll_alphaflow.py

# 指定 API 地址
python3 ~/.hermes/skills/mlops/alphaflow-monitor/scripts/poll_alphaflow.py http://127.0.0.1:8710
```

### fetch_catalyst.py

位于 `~/.hermes/skills/mlops/alphaflow-monitor/scripts/fetch_catalyst.py`

功能：为指定主线搜索五大维度催化信息
- 政策面（国务院政策文件库）
- 事件面（财联社/东方财富）
- 资金面（北向资金/主力流向）
- 情绪面（百度指数/雪球讨论）
- 产业链（上下游映射）

```bash
python3 ~/.hermes/skills/mlops/alphaflow-monitor/scripts/fetch_catalyst.py "AI算力/CPO共封装" "AI算力"
```

## Cronjob 配置

推荐配置：

```bash
# 每 5 分钟轮询（盘中活跃期）
hermes cronjob create \
  --schedule '*/5 9-15 * * 1-5' \
  --prompt '使用 alphaflow-monitor 技能轮询 AlphaFlow /api/v1/mainlines/scores 检查市场主线变化。如检测到 probe 立即推送，watch 进汇总。' \
  --skills alphaflow-monitor

# 或使用轻量 runtime 版本
hermes cronjob create \
  --schedule '*/5 9-15 * * 1-5' \
  --prompt '使用 alphaflow-monitor-runtime 技能快速检查市场状态。' \
  --skills alphaflow-monitor-runtime
```

## 运行时目录结构

```
~/.hermes/
├── alphaflow_config.json           # 配置文件
├── alphaflow_snapshots/            # 运行时数据
│   ├── latest.json                 # 最新快照
│   ├── 2026-04-20_1030.json        # 历史快照
│   ├── dedupe_state.json           # 去重状态
│   └── summary_queue.json          # 汇总队列
└── skills/mlops/alphaflow-monitor/
    ├── SKILL.md                    # 本文件
    ├── scripts/
    │   ├── poll_alphaflow.py       # 轮询脚本
    │   └── fetch_catalyst.py       # 催化信息
    └── templates/
        └── push_message.md         # 推送模板
```

## 常见问题

### Q1: AlphaFlow 服务不可达

```bash
# 检查端口
ss -tlnp | grep 8710

# WSL 下访问 Windows 侧服务，确保：
# 1. 使用 curl -4 --noproxy '*' 强制 IPv4 并绕过代理
# 2. Windows 防火墙未阻止 8710 端口
curl -4 --noproxy '*' -s http://127.0.0.1:8710/api/v1/system/health
```

### Q2: /diagnose/full 返回 500

通常是 QMT 数据层问题（numpy 版本冲突、xtdata 连接断开）。
- 检查 Windows 侧 AlphaFlow 控制台日志
- 确认 QMT 客户端在运行
- 尝试重启 AlphaFlow 服务

### Q3: 快照目录为空

确认 AlphaFlow API 正常返回数据，然后手动运行一次轮询脚本。

### Q4: 微信未收到推送

- 确认 Hermes 微信网关已配置
- 检查 cronjob 的 deliver 配置
- 查看 cronjob 执行日志

### Q5: WSL 网络问题

WSL 环境下 `NO_PROXY` 可能未正确绕过本地地址。脚本已内置 `--noproxy '*'` 处理，但如果用 Python urllib 直接请求，需显式禁用代理：

```python
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
```

## 与相关技能的分工

- `alphaflow-monitor`：日常运行。cron 轮询、实时监控、推送草稿、催化补充、dedupe
- `alphaflow-monitor-runtime`：轻量 cron 版。优先 /mainlines/scores，快速检查
- `alphaflow-early-signal-detection`：识别方法。单独分析早期启动/升温/过热阶段
