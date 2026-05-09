#!/usr/bin/env python3
"""
轮询 AlphaFlow API 并保存快照。
用法：python poll_alphaflow.py [api_base] [snapshot_dir]
"""

import importlib.util
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


def poll_alphaflow(api_base: str = "http://127.0.0.1:8710", max_retries: int = 3, retry_delay: int = 2, request_timeout: int = 30) -> dict:
    """轮询 AlphaFlow 主线评分接口（支持重试机制）。

    首选 /mainlines/scores（始终有缓存，亚秒级返回），
    仅在 scores 不可用时 fallback 到 /diagnose/full。

    WSL 环境下，AlphaFlow 服务运行在 Windows 侧，初次连接可能失败。
    使用重试机制提高成功率，避免 transient network errors。
    对本地地址显式禁用代理，避免 NO_PROXY 配置异常导致请求被转发到本地代理。
    """
    import subprocess
    import time

    # 优先用轻量 scores 接口
    endpoints = [
        f"{api_base}/api/v1/mainlines/scores?limit=5",
        f"{api_base}/api/v1/diagnose/full",
    ]

    last_error = None

    for attempt in range(max_retries):
        for url in endpoints:
            try:
                result = subprocess.run(
                    ["curl", "-4", "--noproxy", "*", "-s", "-m", str(request_timeout), url],
                    capture_output=True, text=True, timeout=request_timeout + 10
                )
                if result.returncode != 0:
                    raise RuntimeError(f"curl exit {result.returncode}: {result.stderr[:200]}")
                data = json.loads(result.stdout)
                return data.get("data", {})
            except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
                last_error = str(e)[:200]
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    print(f"Retry {attempt + 1}/{max_retries} for {url} after error: {e}")
                continue
        # 如果所有 endpoint 都失败了，重试一轮
        break

    print(f"Error polling AlphaFlow after {max_retries} retries: {last_error}")
    return {}


def load_latest_snapshot(snapshot_dir: str = "~/.hermes/alphaflow_snapshots") -> dict:
    """读取上一次快照。必须在保存当前快照前调用。"""
    latest_path = Path(snapshot_dir).expanduser() / "latest.json"
    if not latest_path.exists():
        return {}
    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_fetch_catalyst_module():
    """按需加载 fetch_catalyst.py，避免在无网络场景下主逻辑直接失败。"""
    script_path = Path(__file__).with_name("fetch_catalyst.py")
    if not script_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("fetch_catalyst", script_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _build_catalyst_excerpt(catalyst_info: dict[str, Any] | None) -> dict[str, Any]:
    if not catalyst_info:
        return {"conclusion": "", "highlights": []}
    catalyst = catalyst_info.get("catalyst", {})
    highlights: list[str] = []
    for section in ["policy", "events", "capital", "sentiment", "chain"]:
        for item in catalyst.get(section, [])[:1]:
            if item and item not in highlights:
                highlights.append(item)
    return {
        "conclusion": catalyst_info.get("conclusion", ""),
        "highlights": highlights[:4],
    }


def enrich_emerging_items_with_catalyst(current: dict) -> dict[str, dict[str, Any]]:
    """为当前 emerging items 补充催化摘要，失败时静默回退。"""
    data = current.get("data", {})
    emerging_items = data.get("emerging_mainlines") or []
    if not emerging_items:
        return {}

    module = _load_fetch_catalyst_module()
    if module is None or not hasattr(module, "fetch_catalyst_info"):
        return {}

    catalyst_map: dict[str, dict[str, Any]] = {}
    for item in emerging_items[:5]:
        name = item.get("name", "")
        group = item.get("group", "") or name.split("/")[0] if name else ""
        if not name:
            continue
        try:
            info = module.fetch_catalyst_info(name, group)
            catalyst_map[name] = _build_catalyst_excerpt(info)
        except Exception:
            catalyst_map[name] = {"conclusion": "", "highlights": []}
    return catalyst_map


def _count_emerging_by_suggestion(emerging_items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"probe": 0, "watch": 0, "avoid_chase": 0}
    for item in emerging_items:
        suggestion = item.get("suggestion")
        if suggestion in counts:
            counts[suggestion] += 1
    return counts


def build_digest(data: dict) -> dict:
    """生成兼容 early-signal 的摘要。"""
    top_mainline = data.get("top_mainline") or {}
    emerging_items = data.get("emerging_mainlines") or []
    polling_hint = data.get("polling_policy_hint") or {}
    market_story = data.get("market_story") or {}
    emerging_counts = _count_emerging_by_suggestion(emerging_items)
    top_emerging = emerging_items[0] if emerging_items else {}
    dedupe_keys = polling_hint.get("dedupe_keys") or [
        item.get("dedupe_key") for item in emerging_items if item.get("dedupe_key")
    ]

    return {
        "market_phase": data.get("market_phase", ""),
        "top_mainline": top_mainline.get("name", ""),
        "top_score": top_mainline.get("score", 0),
        "alert_count": len(data.get("alerts", [])),
        "buy_signal_count": len(data.get("buy_signals", [])),
        "sell_signal_count": len(data.get("sell_signals", [])),
        "immediate_push_recommended": bool(polling_hint.get("immediate_push_recommended", False)),
        "summary_push_recommended": bool(polling_hint.get("summary_push_recommended", False)),
        "trigger_reasons": polling_hint.get("trigger_reasons", []),
        "dedupe_keys": dedupe_keys,
        "emerging_count": len(emerging_items),
        "probe_count": emerging_counts["probe"],
        "watch_count": emerging_counts["watch"],
        "avoid_chase_count": emerging_counts["avoid_chase"],
        "top_emerging_name": top_emerging.get("name", ""),
        "top_emerging_suggestion": top_emerging.get("suggestion", ""),
        "market_story_headline": market_story.get("headline", ""),
    }


def save_snapshot(data: dict, snapshot_dir: str = "~/.hermes/alphaflow_snapshots") -> str:
    """保存快照到文件。"""
    if not data:
               return ""

    snapshot_dir_path = Path(snapshot_dir).expanduser()
    snapshot_dir_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat()))
    filename = timestamp.strftime("%Y-%m-%d_%H%M") + ".json"
    filepath = snapshot_dir_path / filename

    snapshot = {
        "timestamp": data.get("timestamp", datetime.now().isoformat()),
        "data": data,
        "digest": build_digest(data),
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    latest_path = snapshot_dir_path / "latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    return str(filepath)


def _load_dedupe_state(snapshot_dir: str) -> dict[str, Any]:
    state_path = Path(snapshot_dir).expanduser() / "dedupe_state.json"
    if not state_path.exists():
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_dedupe_state(snapshot_dir: str, state: dict[str, Any]) -> None:
    state_path = Path(snapshot_dir).expanduser() / "dedupe_state.json"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _prune_dedupe_state(state: dict[str, Any], keep_days: int = 5) -> dict[str, Any]:
    if not state:
        return {}
    ordered_days = sorted(state.keys())
    if len(ordered_days) <= keep_days:
        return state
    return {day: state[day] for day in ordered_days[-keep_days:]}


def _load_summary_queue(snapshot_dir: str) -> dict[str, Any]:
    queue_path = Path(snapshot_dir).expanduser() / "summary_queue.json"
    if not queue_path.exists():
        return {}
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_summary_queue(snapshot_dir: str, queue: dict[str, Any]) -> None:
    queue_path = Path(snapshot_dir).expanduser() / "summary_queue.json"
    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def _prune_summary_queue(queue: dict[str, Any], keep_days: int = 5) -> dict[str, Any]:
    if not queue:
        return {}
    ordered_days = sorted(queue.keys())
    if len(ordered_days) <= keep_days:
        return queue
    return {day: queue[day] for day in ordered_days[-keep_days:]}


def evaluate_dedupe(current: dict, snapshot_dir: str = "~/.hermes/alphaflow_snapshots") -> dict[str, Any]:
    """根据 dedupe_keys 评估是否允许立即推送。"""
    digest = current.get("digest", {})
    trade_date = str(current.get("timestamp", ""))[:10] or datetime.now().strftime("%Y-%m-%d")
    state = _prune_dedupe_state(_load_dedupe_state(snapshot_dir))
    day_state = state.setdefault(trade_date, {"sent_keys": [], "summary_keys": []})

    dedupe_keys = digest.get("dedupe_keys", [])
    new_keys = [key for key in dedupe_keys if key not in day_state["sent_keys"]]
    duplicate_keys = [key for key in dedupe_keys if key in day_state["sent_keys"]]
    # 【修复 1】强制去重：只要 dedupe_key 存在，一律不允许推送，不再检查其他例外
    allow_immediate_push = bool(new_keys) and not duplicate_keys

    return {
        "trade_date": trade_date,
        "state": state,
        "new_keys": new_keys,
        "duplicate_keys": duplicate_keys,
        "allow_immediate_push": allow_immediate_push,
    }


def mark_keys_sent(snapshot_dir: str, trade_date: str, state: dict[str, Any], keys: list[str]) -> None:
    """记录已发送 dedupe keys。"""
    if not keys:
        return
    day_state = state.setdefault(trade_date, {"sent_keys": [], "summary_keys": []})
    for key in keys:
        if key not in day_state["sent_keys"]:
            day_state["sent_keys"].append(key)
    _save_dedupe_state(snapshot_dir, _prune_dedupe_state(state))


def enqueue_summary(snapshot_dir: str, current: dict, changes: dict) -> dict[str, Any]:
    """将 summary-only 变化落盘到 summary_queue.json。"""
    data = current.get("data", {})
    digest = current.get("digest", {})
    trade_date = str(current.get("timestamp", ""))[:10] or datetime.now().strftime("%Y-%m-%d")
    queue = _prune_summary_queue(_load_summary_queue(snapshot_dir))
    day_queue = queue.setdefault(trade_date, {"items": [], "last_flushed_at": ""})

    dedupe_state = _prune_dedupe_state(_load_dedupe_state(snapshot_dir))
    day_state = dedupe_state.setdefault(trade_date, {"sent_keys": [], "summary_keys": []})
    summary_keys = digest.get("dedupe_keys", []) or [
        item.get("dedupe_key")
        for item in (data.get("emerging_mainlines") or [])
        if item.get("dedupe_key")
    ]
    new_summary_keys = [key for key in summary_keys if key not in day_state["summary_keys"]]

    message = format_push_message(current, changes)
    primary_item = _pick_primary_emerging_item(data.get("emerging_mainlines") or [])
    catalyst_map = current.get("_catalyst_map", {})
    entry = {
        "timestamp": current.get("timestamp", datetime.now().isoformat()),
        "headline": digest.get("market_story_headline", ""),
        "message": message,
        "trigger_reasons": changes.get("trigger_reasons", []),
        "summary_keys": summary_keys,
        "new_summary_keys": new_summary_keys,
        "top_emerging_name": digest.get("top_emerging_name", ""),
        "top_emerging_suggestion": digest.get("top_emerging_suggestion", ""),
        "primary_item": primary_item,
        "catalyst_excerpt": catalyst_map.get((primary_item or {}).get("name", ""), {}),
    }

    existing_keys = {
        tuple(item.get("summary_keys", []))
        for item in day_queue["items"]
    }
    if new_summary_keys or tuple(summary_keys) not in existing_keys:
        day_queue["items"].append(entry)

    for key in new_summary_keys:
        day_state["summary_keys"].append(key)

    _save_summary_queue(snapshot_dir, queue)
    _save_dedupe_state(snapshot_dir, dedupe_state)
    return entry


def should_flush_summary_queue(current: dict, snapshot_dir: str = "~/.hermes/alphaflow_snapshots", interval_minutes: int = 30) -> bool:
    """判断是否到达汇总发送窗口。"""
    trade_date = str(current.get("timestamp", ""))[:10] or datetime.now().strftime("%Y-%m-%d")
    queue = _prune_summary_queue(_load_summary_queue(snapshot_dir))
    day_queue = queue.get(trade_date, {})
    items = day_queue.get("items", [])
    if not items:
        return False

    timestamp_str = current.get("timestamp") or datetime.now().isoformat()
    current_dt = datetime.fromisoformat(timestamp_str)
    if current_dt.minute % interval_minutes != 0:
        return False

    last_flushed_at = day_queue.get("last_flushed_at", "")
    if last_flushed_at:
        try:
            last_flushed_dt = datetime.fromisoformat(last_flushed_at)
            if last_flushed_dt.hour == current_dt.hour and last_flushed_dt.minute == current_dt.minute:
                return False
        except ValueError:
            pass
    return True


def flush_summary_queue(snapshot_dir: str, current: dict) -> str:
    """生成更适合微信阅读的汇总消息并清空当日 summary queue。"""
    trade_date = str(current.get("timestamp", ""))[:10] or datetime.now().strftime("%Y-%m-%d")
    queue = _prune_summary_queue(_load_summary_queue(snapshot_dir))
    day_queue = queue.get(trade_date, {})
    items = day_queue.get("items", [])
    if not items:
               return ""

    unique_items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    for item in items:
        key_tuple = tuple(item.get("summary_keys", []))
        if key_tuple in seen_keys:
            continue
        seen_keys.add(key_tuple)
        unique_items.append(item)

    suggestion_counts = {"probe": 0, "watch": 0, "avoid_chase": 0}
    for item in unique_items:
        suggestion = item.get("top_emerging_suggestion")
        if suggestion in suggestion_counts:
            suggestion_counts[suggestion] += 1

    catalyst_map = current.get("_catalyst_map", {})
    timestamp_str = current.get("timestamp") or datetime.now().isoformat()
    current_dt = datetime.fromisoformat(timestamp_str)
    lines = [
        f"📦 盘中汇总 {current_dt.strftime('%H:%M')}",
        f"共 {len(unique_items)} 条 summary 级变化，其中 watch {suggestion_counts['watch']} 条，avoid_chase {suggestion_counts['avoid_chase']} 条。",
    ]

    for index, item in enumerate(unique_items[:5], start=1):
        name = item.get("top_emerging_name") or "未识别"
        suggestion = item.get("top_emerging_suggestion") or "summary"
        headline = item.get("headline") or ""
        trigger_reasons = item.get("trigger_reasons") or []
        primary_item = item.get("primary_item") or {}
        confidence = primary_item.get("confidence", "")
        position_budget_pct = int(round(float(primary_item.get("position_budget_pct", 0)) * 100))
        catalyst_tags = primary_item.get("catalyst_tags") or []
        catalyst_excerpt = catalyst_map.get(name, item.get("catalyst_excerpt") or {})

        lines.append(f"{index}. {name}")
        lines.append(f"   - 类型：{suggestion} | 置信度：{confidence or 'unknown'} | 参考仓位：{position_budget_pct}%")
        if headline:
            lines.append(f"   - 摘要：{headline}")
        if catalyst_tags:
            lines.append(f"   - 标签：{'、'.join(catalyst_tags[:4])}")
        if catalyst_excerpt.get("conclusion"):
            lines.append(f"   - 催化结论：{catalyst_excerpt['conclusion']}")
        if catalyst_excerpt.get("highlights"):
            lines.append(f"   - 催化亮点：{'；'.join(catalyst_excerpt['highlights'][:2])}")
        if trigger_reasons:
            lines.append(f"   - 触发：{'；'.join(trigger_reasons[:3])}")

    if len(unique_items) > 5:
        lines.append(f"其余 {len(unique_items) - 5} 条变化已入队，可在下一次汇总中继续查看。")

    day_queue["items"] = []
    day_queue["last_flushed_at"] = timestamp_str
    queue[trade_date] = day_queue
    _save_summary_queue(snapshot_dir, queue)
    return "\n".join(lines)

def compare_snapshots(current: dict, previous: dict, snapshot_dir: str = "~/.hermes/alphaflow_snapshots") -> dict:
    """对比两个快照，返回兼容 early-signal 的差异。"""
    if not current:
        return {}

    curr_digest = current.get("digest", {})
    prev_digest = previous.get("digest", {}) if previous else {}

    dedupe_eval = evaluate_dedupe(current, snapshot_dir)
    immediate_push_recommended = bool(curr_digest.get("immediate_push_recommended", False))
    summary_push_recommended = bool(curr_digest.get("summary_push_recommended", False))

    changes = {
        "phase_changed": curr_digest.get("market_phase") != prev_digest.get("market_phase"),
        "mainline_changed": curr_digest.get("top_mainline") != prev_digest.get("top_mainline"),
        "score_delta": round(float(curr_digest.get("top_score", 0) - prev_digest.get("top_score", 0)), 2),
        "alert_delta": curr_digest.get("alert_count", 0) - prev_digest.get("alert_count", 0),
        "buy_signal_delta": curr_digest.get("buy_signal_count", 0) - prev_digest.get("buy_signal_count", 0),
        "sell_signal_delta": curr_digest.get("sell_signal_count", 0) - prev_digest.get("sell_signal_count", 0),
        "immediate_push_recommended": immediate_push_recommended,
        "summary_push_recommended": summary_push_recommended,
        "immediate_push_changed": immediate_push_recommended != bool(prev_digest.get("immediate_push_recommended", False)),
        "summary_push_changed": summary_push_recommended != bool(prev_digest.get("summary_push_recommended", False)),
        "top_emerging_changed": curr_digest.get("top_emerging_name") != prev_digest.get("top_emerging_name"),
        "probe_added": curr_digest.get("probe_count", 0) > prev_digest.get("probe_count", 0),
        "watch_added": curr_digest.get("watch_count", 0) > prev_digest.get("watch_count", 0),
        "avoid_chase_added": curr_digest.get("avoid_chase_count", 0) > prev_digest.get("avoid_chase_count", 0),
        "new_dedupe_keys": dedupe_eval["new_keys"],
        "duplicate_dedupe_keys": dedupe_eval["duplicate_keys"],
        "trigger_reasons": curr_digest.get("trigger_reasons", []),
        "market_story_headline": curr_digest.get("market_story_headline", ""),
    }

    legacy_should_push = (
        changes["phase_changed"]
        or changes["mainline_changed"]
        or abs(changes["score_delta"]) >= 3.0
        or changes["alert_delta"] > 0
        or changes["buy_signal_delta"] > 0
        or changes["sell_signal_delta"] > 0
    )

    changes["should_push_immediately"] = (
        (immediate_push_recommended and dedupe_eval["allow_immediate_push"]) or legacy_should_push
    )
    changes["should_queue_summary"] = summary_push_recommended and not changes["should_push_immediately"]
    changes["should_push"] = changes["should_push_immediately"] or changes["should_queue_summary"]
    changes["dedupe_status"] = {
        "trade_date": dedupe_eval["trade_date"],
        "new_keys": dedupe_eval["new_keys"],
        "duplicate_keys": dedupe_eval["duplicate_keys"],
        "allow_immediate_push": dedupe_eval["allow_immediate_push"],
    }
    return changes


def _candidate_sort_key(item: dict[str, Any]) -> tuple[int, float, float]:
    attitude = item.get("system_attitude", "")
    stage = item.get("stage", "")
    quality_scores = item.get("quality_scores", {}) or {}
    crowding_risk = float(quality_scores.get("crowding_risk", 100) or 100)
    startup_quality = float(quality_scores.get("startup_quality", 0) or 0)
    attitude_priority = {
        "potential_first": 0,
        "monitor_closely": 1,
        "trend_follow_only": 2,
        "high_risk_tail": 3,
    }.get(attitude, 4)
    stage_priority = {
        "startup": 0,
        "pullback": 1,
        "markup": 2,
        "tail_rebound": 3,
    }.get(stage, 4)
    return (attitude_priority, stage_priority, crowding_risk, -startup_quality)



def _emerging_sort_key(item: dict[str, Any]) -> tuple[int, float, float]:
    suggestion = item.get("suggestion", "")
    priority = {"probe": 0, "watch": 1, "avoid_chase": 2}.get(suggestion, 3)
    return (
        priority,
        -float(item.get("position_budget_pct", 0.0) or 0.0),
        -float(item.get("current_score", item.get("early_score", 0.0)) or 0.0),
    )



def _pick_primary_emerging_item(
    emerging_items: list[dict[str, Any]],
    candidate_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """优先选择 candidate_diagnostics 中的潜力型启动样本，否则回退到 early-signal。"""
    candidate_diagnostics = candidate_diagnostics or []
    viable_candidates = [
        item for item in candidate_diagnostics if item.get("system_attitude") != "high_risk_tail"
    ]
    if viable_candidates:
        selected = sorted(viable_candidates, key=_candidate_sort_key)[0].copy()
        selected["source"] = "candidate_diagnostics"
        return selected
    if not emerging_items:
        return None
    selected = sorted(emerging_items, key=_emerging_sort_key)[0].copy()
    selected["source"] = "emerging_mainlines"
    return selected



def _format_primary_candidate_section(primary_item: dict[str, Any]) -> list[str]:
    quality_scores = primary_item.get("quality_scores", {}) or {}
    lines = [
        f"- 标的：{primary_item.get('name', '')}",
        f"- 阶段：{primary_item.get('stage', '')} | 系统态度：{primary_item.get('system_attitude', '')}",
        (
            f"- 质量：启动质量 {quality_scores.get('startup_quality', 0)} | 趋势完整度 {quality_scores.get('trend_integrity', 0)} | "
            f"资金质量 {quality_scores.get('capital_quality', 0)} | 拥挤风险 {quality_scores.get('crowding_risk', 0)}"
        ),
    ]
    stage_reasons = primary_item.get("stage_reasons") or []
    if stage_reasons:
        lines.append(f"- 阶段依据：{'；'.join(stage_reasons[:3])}")
    attribution = primary_item.get("attribution") or []
    if attribution:
        lines.append(
            "- 数据归因：" + "；".join(
                f"{item.get('metric')}={item.get('value')}({item.get('conclusion')})" for item in attribution[:3]
            )
        )
    if primary_item.get("system_attitude") == "potential_first":
        lines.append("- 系统判断：潜力优先，可列入主监控池。")
    elif primary_item.get("system_attitude") == "trend_follow_only":
        lines.append("- 系统判断：更适合趋势跟踪，不宜当作早期潜伏。")
    return lines



def format_push_message(current: dict, changes: dict) -> str:
    """生成结构化推送草稿，优先使用 candidate_diagnostics。"""
    if not current:
        return ""

    data = current.get("data", {})
    digest = current.get("digest", {})
    catalyst_map = current.get("_catalyst_map", {})
    lines: list[str] = []

    emerging_items = data.get("emerging_mainlines") or []
    candidate_diagnostics = data.get("candidate_diagnostics") or []
    primary_item = _pick_primary_emerging_item(emerging_items, candidate_diagnostics)
    avoid_items = [
        item for item in candidate_diagnostics if item.get("system_attitude") == "high_risk_tail"
    ]
    if not avoid_items:
        avoid_items = [item for item in emerging_items if item.get("suggestion") == "avoid_chase"]

    headline = digest.get("market_story_headline") or f"当前主线：{data.get('top_mainline', {}).get('name', '未识别')}"
    lines.append(headline)

    if primary_item:
        if primary_item.get("source") == "candidate_diagnostics":
            lines.extend(_format_primary_candidate_section(primary_item))
            catalyst_excerpt = catalyst_map.get(primary_item.get("name", ""), {})
            if catalyst_excerpt.get("conclusion"):
                lines.append(f"- 催化结论：{catalyst_excerpt['conclusion']}")
            if catalyst_excerpt.get("highlights"):
                lines.append(f"- 催化亮点：{'；'.join(catalyst_excerpt['highlights'][:3])}")
        else:
            primary_name = primary_item.get("name", "")
            lines.extend([
                f"- 主线：{primary_name}",
                f"- 阶段：{primary_item.get('stage', '')} | 建议：{primary_item.get('action_plan', primary_item.get('suggestion', ''))} | 置信度：{primary_item.get('confidence', '')}",
                f"- 试错仓位：{int(round(float(primary_item.get('position_budget_pct', 0)) * 100))}%",
            ])
            catalyst_tags = primary_item.get("catalyst_tags") or []
            if catalyst_tags:
                lines.append(f"- 结构标签：{','.join(catalyst_tags)}")
            catalyst_excerpt = catalyst_map.get(primary_name, {})
            if catalyst_excerpt.get("conclusion"):
                lines.append(f"- 催化结论：{catalyst_excerpt['conclusion']}")
            if catalyst_excerpt.get("highlights"):
                lines.append(f"- 催化亮点：{';'.join(catalyst_excerpt['highlights'][:3])}")

    if avoid_items:
        risk_names = "、".join(item.get("name", "") for item in avoid_items[:3] if item.get("name"))
        if risk_names:
            lines.append(f"- 风险提示：{risk_names} 属于高风险尾端，不建议追涨。")

    trigger_reasons = changes.get("trigger_reasons") or []
    if trigger_reasons:
        lines.append(f"- 触发原因：{';'.join(trigger_reasons[:3])}")

    if changes.get("duplicate_dedupe_keys"):
        lines.append(f"- 去重命中：{', '.join(changes['duplicate_dedupe_keys'])}")

    return "\n".join(lines)


def main():
    api_base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8710"
    snapshot_dir = sys.argv[2] if len(sys.argv) > 2 else "~/.hermes/alphaflow_snapshots"

    previous = load_latest_snapshot(snapshot_dir)
    data = poll_alphaflow(api_base)
    if not data:
        print("Failed to poll AlphaFlow")
        return

    filepath = save_snapshot(data, snapshot_dir)
    print(f"Snapshot saved to: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        current = json.load(f)

    current["_catalyst_map"] = enrich_emerging_items_with_catalyst(current)
    changes = compare_snapshots(current, previous, snapshot_dir)
    print(f"Changes: {json.dumps(changes, indent=2, ensure_ascii=False)}")

    if changes.get("should_push_immediately"):
        message = format_push_message(current, changes)
        print("⚠️  检测到需要立即推送的变化")
        if message:
            print("Push draft:")
            print(message)
        dedupe_status = changes.get("dedupe_status", {})
        mark_keys_sent(snapshot_dir, dedupe_status.get("trade_date", ""), _load_dedupe_state(snapshot_dir), dedupe_status.get("new_keys", []))
    elif changes.get("should_queue_summary"):
        print("📝 检测到汇总级变化，建议进入 summary queue")
        entry = enqueue_summary(snapshot_dir, current, changes)
        if entry.get("message"):
            print("Summary draft:")
            print(entry["message"])
        print(f"Summary queued with keys: {json.dumps(entry.get('new_summary_keys', []), ensure_ascii=False)}")
    else:
        print("✅ 无重大变化")

    if should_flush_summary_queue(current, snapshot_dir):
        summary_message = flush_summary_queue(snapshot_dir, current)
        if summary_message:
            print("📦 Summary flush draft:")
            print(summary_message)


if __name__ == "__main__":
    main()
