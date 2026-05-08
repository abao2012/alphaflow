# Hermes Consumer Rollout Plan for AlphaFlow Early-Signal Integration

Goal: Switch Hermes monitoring from legacy top-mainline-only change detection to unified `/api/v1/diagnose/full` consumption, so immediate push, summary push, dedupe, and early-radar wording all follow AlphaFlow's structured early-signal output.

Architecture:
- AlphaFlow remains the producer of structured market state and early-signal hints.
- Hermes cron remains the consumer, state tracker, deduper, and delivery engine.
- Hermes should treat `/api/v1/mainlines/emerging` as a manual debug endpoint only; routine polling should read only `/api/v1/diagnose/full`.

Primary consumer files to touch:
- `~/.hermes/skills/mlops/alphaflow-monitor/scripts/poll_alphaflow.py`
- optional future helper: `~/.hermes/skills/mlops/alphaflow-monitor/scripts/format_push_message.py` (only if script becomes too large)
- optional future state file under `~/.hermes/alphaflow_snapshots/` for summary queue / dedupe bookkeeping

---

## Current gaps found

1. `poll_alphaflow.py` only digests:
- market phase
- top mainline
- top score
- alert count
- buy signal count
- sell signal count

It does not yet digest:
- `emerging_mainlines`
- `polling_policy_hint`
- `market_story`
- `dedupe_keys`

2. Existing `compare_snapshots()` only supports old push rules.
It does not support:
- `probe` immediate push
- `watch` summary-only behavior
- `avoid_chase` once-per-day dedupe

3. Existing script has a comparison bug risk:
- it writes `latest.json`
- then immediately reads `latest.json` as `previous`
- this can collapse previous/current comparison into the same snapshot

This should be fixed before relying on Hermes-side change detection.

---

## Rollout objectives

Phase 1: Consumer correctness
- fix snapshot comparison ordering
- ingest new full-diagnose fields
- compute immediate/summary push from `polling_policy_hint`

Phase 2: Message quality
- use `market_story.headline` as push first sentence
- use `emerging_mainlines[*]` fields to generate structured early-radar messages

Phase 3: Dedupe and cadence
- dedupe by `polling_policy_hint.dedupe_keys`
- suppress repeated `avoid_chase` notices within same trading day
- keep `watch` items in summary channel only

Phase 4: Cron policy alignment
- update cron cadence to 15-minute default + discovery windows if desired
- keep routine polling on `/diagnose/full` only

---

## Desired Hermes behavior

### Immediate push
Trigger when either is true:
- `data.polling_policy_hint.immediate_push_recommended == true`
- or legacy market-critical changes occur:
  - market phase changed
  - top mainline changed
  - score delta threshold exceeded
  - new critical alert
  - new buy/sell signal surge

Message priority order:
1. `probe` early signal
2. market phase / mainline switch
3. critical risk alert
4. new high-quality buy/sell signals

### Summary push
Trigger when:
- `data.polling_policy_hint.summary_push_recommended == true`
- no immediate push triggered

Summary contents should include:
- `watch` items
- repeated but non-urgent drift
- avoid-chase notes that were not sent immediately

### Dedupe
Use:
- `data.polling_policy_hint.dedupe_keys`
- fallback to `item.dedupe_key` on `emerging_mainlines`

Rules:
- same dedupe key in same trading day -> suppress duplicate immediate push
- `avoid_chase` same mainline same day -> at most one push
- repeated `watch` -> prefer summary queue, not immediate push

---

## Data contract Hermes should consume

From `/api/v1/diagnose/full`, Hermes should read at least:

```json
{
  "timestamp": "...",
  "market_phase": "主升",
  "top_mainline": {"name": "...", "score": 29.0, "group": "AI算力"},
  "alerts": [...],
  "buy_signals": [...],
  "sell_signals": [...],
  "emerging_mainlines": [
    {
      "name": "AI算力/CPO共封装",
      "stage": "early_watch",
      "suggestion": "probe",
      "action_plan": "probe_small",
      "confidence": "high",
      "position_budget_pct": 0.05,
      "catalyst_tags": ["资金先行", "扩散增强"],
      "dedupe_key": "2026-04-20:AI算力/CPO共封装:probe"
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
  }
}
```

---

## Implementation tasks

### Task 1: Fix snapshot read/write ordering in `poll_alphaflow.py`

Objective: Ensure `previous` really means previous snapshot, not the just-written current one.

Required change:
- read previous snapshot before writing new `latest.json`
- or list the latest two timestamped snapshots and compare them

Recommended implementation:
1. Load `latest.json` into `previous` first
2. Poll live data
3. Save current snapshot
4. Load current snapshot from saved file
5. Compare current vs previous

Verification:
- compare two synthetic snapshots with different top mainlines
- confirm `mainline_changed == true`

### Task 2: Expand digest to include early-signal fields

Objective: Let Hermes compare and log emerging-signal state efficiently.

Add to snapshot digest:
- `immediate_push_recommended`
- `summary_push_recommended`
- `trigger_reasons`
- `dedupe_keys`
- `emerging_count`
- `probe_count`
- `watch_count`
- `avoid_chase_count`
- `top_emerging_name`
- `top_emerging_suggestion`

Verification:
- saved snapshot JSON should include these keys under `digest`

### Task 3: Add emerging-aware comparison logic

Objective: Detect early-signal events, not just top-mainline shifts.

Extend `compare_snapshots()` with:
- `immediate_push_changed`
- `summary_push_changed`
- `new_dedupe_keys`
- `top_emerging_changed`
- `probe_added`
- `watch_added`
- `avoid_chase_added`

Push decision logic should become:
- immediate push if:
  - AlphaFlow says immediate push
  - and dedupe keys are new
- summary push if:
  - AlphaFlow says summary push
  - and item is not already deduped for current summary window
- fallback to legacy rules if emerging block missing

### Task 4: Add dedupe state persistence

Objective: Stop repeated spam across cron runs.

Recommended file:
- `~/.hermes/alphaflow_snapshots/dedupe_state.json`

Structure:
```json
{
  "2026-04-20": {
    "sent_keys": ["2026-04-20:AI算力/CPO共封装:probe"],
    "summary_keys": []
  }
}
```

Rules:
- rotate by trading day
- prune old days automatically
- immediate push only if key not in `sent_keys`

### Task 5: Build message formatter for emerging signals

Objective: Make pushes readable and trading-oriented.

Recommended message order:
1. `market_story.headline`
2. strongest emerging item summary
3. catalyst tags
4. action/position note
5. trigger reasons

Example immediate `probe` push:
```text
AI算力分支出现升温信号，可小仓试探，但不宜把确认信号当成早期潜伏信号追高。
- 主线：AI算力/CPO共封装
- 阶段：early_watch | 建议：probe_small | 置信度：high
- 试错仓位：5%
- 结构标签：资金先行、扩散增强、评分抬升
- 触发原因：emerging suggestion is probe
```

Example `avoid_chase` push:
```text
AI算力/CPO共封装已进入高分确认区，更适合作为强势确认观察，不宜再按早期启动思路追高。
- 建议：avoid_chase
- 原因：确认区过热
```

### Task 6: Add summary queue behavior

Objective: Stop `watch` items from causing noisy immediate pushes.

Recommended implementation:
- if `summary_push_recommended == true`:
  - append summary item to `summary_queue.json`
- flush summary queue:
  - every 30 minutes
  - or at end of trading session

Minimal V1 alternative:
- no persistent queue yet
- just mark summary-only and print message without sending

### Task 7: Update cron prompt / execution contract

Objective: Align the cron job with the new single-entry payload.

Cron instructions should explicitly say:
- poll `/api/v1/diagnose/full`
- honor `polling_policy_hint`
- dedupe by `dedupe_keys`
- use `market_story.headline` in push drafts
- treat `/mainlines/emerging` as debug only

### Task 8: Validation checklist

Manual validation scenarios:
1. one `probe` item
- expected: immediate push
2. one `watch` item only
- expected: no immediate push, summary path only
3. repeated same `probe` key same day
- expected: suppressed
4. repeated same `avoid_chase` key same day
- expected: suppressed
5. no emerging items but market phase changes
- expected: legacy immediate push still works
6. AlphaFlow unavailable
- expected: script falls back gracefully, no crash

---

## Minimal V1 implementation order

Recommended order:
1. Fix previous/current snapshot bug
2. Expand digest with emerging fields
3. Extend compare logic with dedupe keys
4. Add immediate-push formatting with `market_story.headline`
5. Add dedupe state persistence
6. Add summary queue later if still needed

---

## Suggested acceptance criteria

Hermes rollout can be considered complete when:
- routine cron polling uses only `/api/v1/diagnose/full`
- `probe` messages push immediately once
- repeated same `probe` on same day is suppressed
- `watch` does not spam
- `avoid_chase` does not repeat on every run
- legacy market-phase-change push still works
- script no longer compares current snapshot to itself

---

## Notes for future optimization

Backlog after rollout:
- add trading-session-aware time windows to dedupe reset
- classify push priority as `high/medium/low`
- merge AlphaFlow catalyst tags with `fetch_catalyst.py` narrative output
- add daily digest / intraday digest modes
- add explicit night-mode suppression in Hermes
