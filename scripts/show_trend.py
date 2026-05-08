import os
import json, urllib.request, sys
sys.stdout.reconfigure(encoding='utf-8')

url = "http://127.0.0.1:8710/api/v1/mainlines/snapshot"
with urllib.request.urlopen(url, timeout=60) as resp:
    data = json.loads(resp.read().decode("utf-8"))

mainlines = data["data"]["current_mainlines"]

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime", "data", "trend_report_v2.txt"), "w", encoding="utf-8") as f:
    f.write(f"市场阶段: {data['data']['market_phase']}\n")
    f.write(f"主线数量: {len(mainlines)}\n\n")

    # Group by health_label
    by_health = {}
    for m in mainlines:
        hl = m.get("health_label", "unknown")
        by_health.setdefault(hl, []).append(m)

    f.write("=== 按健康状态分组 ===\n\n")
    for hl in ["healthy_rally", "early_stage", "mixed", "concentration_peak", "dormant"]:
        items = by_health.get(hl, [])
        if not items:
            continue
        label_cn = {
            "healthy_rally": "🟢 真启动",
            "early_stage": "🟡 早期升温",
            "mixed": "⚪ 混合信号",
            "concentration_peak": "🔴 拥挤见顶",
            "dormant": "⚫ 沉寂",
        }.get(hl, hl)
        f.write(f"【{label_cn}】({len(items)} 条)\n")
        for m in sorted(items, key=lambda x: x["total_score"], reverse=True)[:8]:
            penalty = m.get("crowding_penalty", 0)
            penalty_str = f" 惩罚:-{penalty:.1f}" if penalty > 0 else ""
            f.write(f"  {m['name']:<35s} 分:{m['total_score']:>5.1f} "
                    f"扩散:{m['diffusion_score']:.1f} 持续:{m['persistence_score']:.1f} "
                    f"龙头:{m['leader_score']:.1f}{penalty_str}\n")
        f.write("\n")

    f.write("=== 完整排名（前20）===\n\n")
    for i, m in enumerate(mainlines[:20], 1):
        hl = m.get("health_label", "?")
        penalty = m.get("crowding_penalty", 0)
        penalty_str = f" 惩罚:-{penalty:.1f}" if penalty > 0 else ""
        f.write(f"{i:>2}. [{m['tier']:<10s}] {m['name']:<35s} "
                f"分:{m['total_score']:>5.1f} 扩散:{m['diffusion_score']:.1f} "
                f"持续:{m['persistence_score']:.1f} 龙头:{m['leader_score']:.1f} "
                f"({hl}){penalty_str}\n")

print("OK")
