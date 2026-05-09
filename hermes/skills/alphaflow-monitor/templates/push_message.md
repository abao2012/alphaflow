# 推送消息模板

## 高优先级推送（立即发送）

### 市场阶段变化
```
📊 市场阶段变化提醒

{prev_phase} → {new_phase}

最强主线：{top_mainline} ({top_score}分)
建议仓位：{suggested_exposure}%

市场解读：{phase_interpretation}

操作建议：
{action_advice}

生成时间：{timestamp}
```

### 主线评分大幅波动
```
📈 主线评分大幅波动

{mainline_name}: {prev_score} → {curr_score} ({delta}分)

可能原因:
{catalyst_summary}

操作建议：{action_advice}
```

### 新增风控告警
```
⚠️ 新增风控告警

级别：{alert_level}
标题：{alert_title}
详情：{alert_detail}

当前持仓健康度：{health_score}
```

### 新增买卖信号
```
💡 新增买卖信号

买入信号：
{buy_signals}

卖出信号：
{sell_signals}
```

---

## 低优先级推送（汇总发送）

### 30 分钟汇总
```
📋 市场主线监控汇总（{time_range}）

市场阶段：{market_phase}
最强主线：{top_mainline} ({top_score}分)
建议仓位：{suggested_exposure}%

主线评分 Top5:
{rankings}

持仓建议：{position_advice}
```

### 盘后复盘报告
```
📊 盘后复盘报告 ({date})

【市场阶段】{market_phase}

【主线表现】
{mainline_performance}

【持仓诊断】
{portfolio_diagnosis}

【明日关注】
{tomorrow_focus}

【操作建议】
{action_advice}
```

---

## 用户查询响应

### 查询当前主线
```
当前市场主线是 {top_mainline}，评分为 {score}分。

市场处于{market_phase}阶段，建议仓位{exposure}%。

主线 Top3:
1. {rank1} - {score1}分
2. {rank2} - {score2}分
3. {rank3} - {score3}分

需要我详细分析某条主线的催化因素吗？
```

### 查询主线成因
```
{mainline_name} 成为主线的原因：

【政策面】
{policy_points}

【事件面】
{event_points}

【资金面】
{capital_points}

【情绪面】
{sentiment_points}

【产业链】
{chain_points}

结论：{conclusion}
```

### 查询持仓诊断
```
当前持仓健康度：{health_score}/100

【主线内持仓】
{core_positions}

【非主线持仓】
{non_core_positions}

【调仓建议】
{rebalance_advice}
```
