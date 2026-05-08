# AlphaFlow 数据库缓存层一键安装脚本
# 在 Windows PowerShell 中运行此脚本即可完成所有配置

Write-Host "=== AlphaFlow 数据库缓存层安装 ===" -ForegroundColor Cyan

# 1. 进入项目目录（脚本所在目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# 2. 加载 .env 文件
$envFile = Join-Path $scriptDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^(.+?)=(.*)$") {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            if ($key -and $value -and -not [Environment]::GetEnvironmentVariable($key)) {
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }
    Write-Host "Loaded .env file" -ForegroundColor DarkGray
}

# 3. 检查虚拟环境
if (Test-Path ".venv\Scripts\activate.ps1") {
    Write-Host "[1/3] 激活虚拟环境..." -ForegroundColor Yellow
    & .\.venv\Scripts\activate.ps1
} else {
    Write-Host "未找到 .env，使用全局 Python" -ForegroundColor Yellow
}

# 4. 安装 psycopg2-binary
Write-Host "[2/3] 安装 psycopg2-binary..." -ForegroundColor Yellow
& python -m pip install psycopg2-binary --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "  psycopg2-binary 安装成功" -ForegroundColor Green
} else {
    Write-Host "  安装失败，请检查 Python 环境" -ForegroundColor Red
    exit 1
}

# 5. 停止旧服务
Write-Host "[3/3] 重启 AlphaFlow 服务..." -ForegroundColor Yellow
$procs = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*uvicorn*8710*" -or $_.CommandLine -like "*alphaflow*"
}
foreach ($proc in $procs) {
    Write-Host "  停止进程 PID: $($proc.Id)" -ForegroundColor DarkYellow
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 6. 启动新服务（后台）
Write-Host "  启动 AlphaFlow..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8710" -WorkingDirectory $scriptDir -WindowStyle Minimized

Start-Sleep -Seconds 5

# 7. 验证
Write-Host "`n=== 验证 ===" -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8710/api/v1/system/health" -TimeoutSec 10
    Write-Host "服务已启动: $($health.data.market_connected)" -ForegroundColor Green
} catch {
    Write-Host "服务未响应，请检查日志" -ForegroundColor Red
}

Write-Host "`n完成！数据库缓存层已启用。" -ForegroundColor Green
