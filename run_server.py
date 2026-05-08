import os
import sys
import subprocess
from pathlib import Path

# 从 .env 文件加载环境变量（如果存在）
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value

# 启动 uvicorn
if __name__ == "__main__":
    host = os.environ.get("ALPHAFLOW_HOST", "127.0.0.1")
    port = os.environ.get("ALPHAFLOW_PORT", "8710")
    print(f"Starting AlphaFlow server on {host}:{port}...")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", host,
        "--port", port,
        "--reload"
    ])
