import os
import json, urllib.request, urllib.parse, sys
sys.stdout.reconfigure(encoding='utf-8')

def fetch_core(mainline):
    encoded = urllib.parse.quote(mainline)
    url = f"http://127.0.0.1:8710/api/v1/candidates/core?mainline={encoded}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]

results = {}
for ml in ["半导体/第三代半导体", "新能源/锂电池", "新能源/固态钠电", "新能源/汽车零部件充电桩"]:
    results[ml] = fetch_core(ml)

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime", "data", "stock_pools.txt"), "w", encoding="utf-8") as f:
    for ml, d in results.items():
        f.write(f"\n{'='*50}\n{ml}\n{'='*50}\n")
        leaders = d.get("leaders", [])
        cores = d.get("core_middles", [])
        follows = d.get("followups", [])
        if leaders:
            f.write(f"  龙头: {', '.join(leaders)}\n")
        if cores:
            f.write(f"  中军: {', '.join(cores)}\n")
        if follows:
            f.write(f"  跟随: {', '.join(follows)}\n")
        f.write(f"  合计: {len(leaders)+len(cores)+len(follows)} 只\n")

print("OK")
