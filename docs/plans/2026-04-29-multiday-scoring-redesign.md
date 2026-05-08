# AlphaFlow 多日趋势评分重构

> **问题**：当前评分7个子维度全部基于日内数据（今天的涨跌、成交额），导致"今天涨=过热"、"今天没动=早期"的虚假信号，对日更趋势交易毫无意义。

> **目标**：将评分改为以多日K线为核心（5日/10日/20日），日内数据降级为辅助确认。

## 新评分架构（已完成 ✅）

### 7个核心子维度 + 2个新增维度（各5.0，总分35.0，新增维度独立存储）

| # | 新维度 | 替换旧维度 | 数据来源 | 含义 |
|---|--------|-----------|----------|------|
| 1 | `trend_persistence` | `persistence` | 5d/10d日K | 板块多日累计涨幅 + MA对齐度 |
| 2 | `capital_flow` | `capital_strength` | 5d/10d成交额 | 资金持续流入 vs 一日游 |
| 3 | `breadth_expansion` | `diffusion` | 5d日K | 多少个股在5日内走强（MA5/MA10上方） |
| 4 | `leader_quality` | `leader_strength` | 5d/10d日K | 龙头持续性 + 龙头-跟风差距 |
| 5 | `trend_maturity` | `market_status` | 10d/20d日K | 趋势阶段：早期(甜区)/中期/晚期 |
| 6 | `industry_logic` | 不变 | 配置文件 | 板块成分股数量 |
| 7 | `intraday_momentum` | `core_strength` | 今日tick | 日内动量（仅作确认信号） |

### 各维度计算逻辑

**1. trend_persistence（趋势持续性）**
```
5d_return = mean(所有成分股5日累计涨幅)
10d_return = mean(所有成分股10日累计涨幅)
ma_alignment = %成分股 close > MA5 且 MA5 > MA10（趋势健康度）
score = normalize(5d_return, -5, 20) * 0.4 
      + normalize(10d_return, -8, 30) * 0.3
      + normalize(ma_alignment * 100, 20, 80) * 0.3
```

**2. capital_flow（资金流向）**
```
vol_5d_avg = 5日平均成交额
vol_10d_avg = 10日平均成交额
vol_trend = vol_5d_avg / vol_10d_avg（>1 = 资金加码）
today_vol_rank = 今日成交额在5日中的排名百分位
score = normalize(vol_trend, 0.5, 2.0) * 0.5
      + normalize(today_vol_rank * 100, 20, 90) * 0.3
      + normalize(vol_5d_avg, 5e7, 1e9) * 0.2
```

**3. breadth_expansion（扩散广度）**
```
ma5_above_ratio_5d = 过去5天中，平均%成分股在MA5上方
ma10_above_ratio_5d = 过去5天中，平均%成分股在MA10上方
expansion_velocity = (最近2天ma5_above - 前3天ma5_above) / 3
score = normalize(ma5_above_ratio_5d * 100, 20, 80) * 0.4
      + normalize(ma10_above_ratio_5d * 100, 15, 70) * 0.3
      + normalize(expansion_velocity * 100, -10, 20) * 0.3
```

**4. leader_quality（龙头质量）**
```
leader_5d_return = 前3名个股5日平均涨幅
follower_5d_return = 第4-8名个股5日平均涨幅
leader_gap = leader_5d_return - follower_5d_return（健康gap: 2-8%）
consistency = %成分股5日涨幅 > 0
score = normalize(leader_5d_return, 0, 25) * 0.35
      + normalize(consistency * 100, 30, 80) * 0.35
      + normalize(leader_gap, 0, 15) * 0.3  # gap太大=独涨，太小=无龙头
```

**5. trend_maturity（趋势阶段）**
```
days_above_ma20 = 平均成分股站上MA20的天数
return_accel = 5d_return - (10d_return - 5d_return)  # 加速/减速
ma20_slope = MA20的5日斜率均值
# 甜区：刚站上MA20不久（3-10天），加速而非减速
score = normalize(days_above_ma20, 0, 15) * 0.35  # 过早过晚都扣分
      + normalize(return_accel, -5, 10) * 0.35     # 加速加分
      + normalize(ma20_slope, -1, 3) * 0.3         # MA20向上
# 惩罚：>15天在MA20上方 → 可能过热
if days_above_ma20 > 15:
    score *= 0.7  # 过热惩罚
```

**6. industry_logic（行业逻辑）** — 不变
```
score = normalize(len(rows), 6, 45)
```

**7. intraday_momentum（日内动量）** — 仅确认信号
```
positive_ratio = 今日收涨占比
strong_ratio = 今日涨幅≥3%占比
score = normalize(positive_ratio * 100, 30, 75) * 0.5
      + normalize(strong_ratio * 100, 5, 30) * 0.5
```

### 拥挤度惩罚（保留但调整）
```
breadth_health = breadth_expansion + trend_persistence
# 龙头独涨 + 跟风不足 = 假高潮
if leader_quality >= 3.5 and breadth_health < 2.0:
    crowding_penalty = min(leader_quality * 0.4, 2.5)
    total -= crowding_penalty
```

### Tier判定（调整阈值）
```
breadth_health = breadth_expansion + trend_persistence
if breadth_health >= 3.0:
    tier = "core" if total >= 22 else "secondary" if total >= 16 else "rotation"
elif breadth_health >= 1.5:
    tier = "secondary" if total >= 16 else "rotation"
else:
    tier = "rotation"
```

### 健康标签（调整）
```
if breadth_health >= 3.0 and leader_quality >= 2.5:
    health_label = "healthy_rally"
elif trend_maturity <= 2.0 and breadth_health >= 1.5:
    health_label = "early_stage"     # 早期升温（甜区）
elif trend_maturity >= 4.0 and leader_quality >= 4.0:
    health_label = "late_stage"      # 晚期（警惕）
elif breadth_health < 1.0 and leader_quality < 2.0:
    health_label = "dormant"
else:
    health_label = "mixed"
```

## 实施文件清单

| 文件 | 改动 |
|------|------|
| `app/services/market_data_service.py` | 重写 `get_mainline_scores()`，新增多日数据采集 |
| `app/models/domain.py` | MainlineScore 增加多日字段（可选） |
| `app/services/emerging_detector.py` | 适配新维度（threshold调整） |
| `app/services/trend_feature_extractor.py` | 可能需要板块级聚合方法 |

## 关键约束

1. **QMT每日K线API已有**：`get_daily_bars(codes, count=20)` + PG缓存
2. **避免QMT批量崩溃**：逐个代码拉取（已有实现）
3. **性能**：34个板块 × 平均15个成分股 = ~500次日K查询，但有PG缓存应可接受
4. **向后兼容**：API响应格式不变，子维度名可平滑迁移
