# AlphaFlow Hermes 集成指南

本目录包含将 AlphaFlow 与 [Hermes Agent](https://hermes-agent.nousresearch.com) 集成所需的所有文件。

集成后，Hermes 可以：
- 自动轮询 AlphaFlow 获取市场主线状态
- 检测主线变化并推送微信/QQ 告警
- 获取催化信息解释"为什么是这条主线"
- 管理去重、汇总队列，避免推送轰炸

## 快速安装

```bash
# 1. 确保 AlphaFlow 服务已启动
curl http://127.0.0.1:8710/api/v1/system/health

# 2. 运行安装脚本
cd AlphaFlow
bash hermes/setup.sh
```

安装脚本会：
- 将技能文件复制到 `~/.hermes/skills/mlops/alphaflow-monitor/`
- 创建快照目录 `~/.hermes/alphaflow_snapshots/`
- 复制配置模板到 `~/.hermes/alphaflow_config.json`

## 创建定时轮询

安装完成后，在 Hermes CLI 中创建 cron job：

```bash
# 方式 1：使用 Hermes CLI
hermes cronjob create \
  --schedule '*/5 9-15 * * 1-5' \
  --prompt '使用 alphaflow-monitor 技能轮询 AlphaFlow /api/v1/mainlines/scores 检查市场主线变化。如检测到 probe 立即推送，watch 进汇总。' \
  --skills alphaflow-monitor

# 方式 2：在 Hermes 对话中告诉 Agent
# "帮我创建一个每 5 分钟轮询 AlphaFlow 的定时任务"
```

## 手动测试

```bash
# 测试轮询脚本
python3 ~/.hermes/skills/mlops/alphaflow-monitor/scripts/poll_alphaflow.py

# 测试催化信息
python3 ~/.hermes/skills/mlops/alphaflow-monitor/scripts/fetch_catalyst.py "AI算力/CPO共封装" "AI算力"

# 检查快照
ls ~/.hermes/alphaflow_snapshots/
cat ~/.hermes/alphaflow_snapshots/latest.json | python3 -m json.tool
```

## 目录结构

```
hermes/
├── README.md                           # 本文件
├── setup.sh                            # 一键安装脚本
├── config/
│   └── alphaflow_config.json           # 配置模板
└── skills/
    └── alphaflow-monitor/
        ├── SKILL.md                    # Hermes 技能定义
        ├── scripts/
        │   ├── poll_alphaflow.py       # 轮询/对比/推送脚本
        │   └── fetch_catalyst.py       # 催化信息搜索
        └── templates/
            └── push_message.md         # 推送消息模板
```

## 推送逻辑

### 立即推送

以下情况会触发即时微信/QQ 推送：
- 出现 `probe` 信号（早期升温机会）
- 市场阶段变化（如轮动 → 主升）
- 主线切换（Top1 主线更换）
- 评分变化 ≥ 3 分
- 新增风控告警

### 汇总推送

以下情况进入 30 分钟汇总队列：
- `watch` 信号（观察中）
- `avoid_chase` 信号（已过热，提醒别追）
- 小幅评分波动

### 去重机制

- 同一交易日同一 `dedupe_key` 只推送一次
- `avoid_chase` 同一主线同一天最多一次
- 去重状态存储在 `~/.hermes/alphaflow_snapshots/dedupe_state.json`

## 在 Hermes 中直接对话

集成后，你可以在微信/QQ 中直接问 Hermes：

- "当前市场主线是什么？"
- "AI 算力有什么催化？"
- "检查我当前持仓"
- "主线评分有变化吗？"
- "哪些方向刚开始升温？"

Hermes 会调用 AlphaFlow API 获取数据，然后用中文回答。

## 故障排查

详见 `hermes/skills/alphaflow-monitor/SKILL.md` 的"常见问题"章节。

## 自定义

### 修改轮询频率

```bash
# 每 3 分钟
hermes cronjob update <job_id> --schedule '*/3 9-15 * * 1-5'

# 盘前盘后也轮询
hermes cronjob update <job_id> --schedule '*/5 9-16 * * 1-5'
```

### 修改推送阈值

编辑 `~/.hermes/alphaflow_config.json`：

```json
{
  "push_on_score_delta": 2.0
}
```

### 添加更多主线

编辑 AlphaFlow 的 `runtime/config/mainlines.json`，添加新的主线配置。
