"""Debug startup with stdout/stderr redirected to log file."""
import os
import subprocess
import sys
from pathlib import Path

# 加载 .env 文件
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value

project_dir = str(Path(__file__).parent)
log_file = os.path.join(project_dir, "server_debug.log")
host = os.environ.get("ALPHAFLOW_HOST", "0.0.0.0")
port = os.environ.get("ALPHAFLOW_PORT", "8710")

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", port],
    cwd=project_dir,
    stdout=open(log_file, "w", buffering=1),
    stderr=subprocess.STDOUT,
    env={**os.environ}
)
print(f"Server PID: {proc.pid}, log: {log_file}")
proc.wait()
