"""
AlphaFlow K 线回填守护脚本
=========================
每次调 backfill_db.py --bars，如果进程 crash 了自动重启继续。
直到所有数据回填完毕或用户手动停止。

在 Windows PowerShell 中运行:
    cd <your-project-dir>
    . .\.venv\bin\activate.ps1
    python scripts\backfill_guard.py
"""
import os
import subprocess
import sys
import time

PYTHON = sys.executable
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill_db.py")
MAX_ROUNDS = 50  # 最多跑 50 轮，防止无限循环

for round_num in range(1, MAX_ROUNDS + 1):
    print(f"\n{'='*50}")
    print(f"  第 {round_num} 轮回填")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(
            [PYTHON, SCRIPT, "--bars", "--limit", "15"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            timeout=300,  # 5 分钟超时
        )
        if result.returncode == 0:
            print(f"\n✅ 回填完成！")
            break
        else:
            print(f"⚠️ 进程退出码: {result.returncode}，等待 3 秒后重试...")
    except subprocess.TimeoutExpired:
        print(f"⏰ 超时，等待 3 秒后重试...")
    except KeyboardInterrupt:
        print(f"\n用户中断，退出")
        break
    except Exception as e:
        print(f"❌ 异常: {e}")
    
    time.sleep(3)

print("\n回填守护脚本结束")
