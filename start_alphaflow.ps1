param(
    [switch]$DevReload
)

Write-Host "Starting AlphaFlow service..." -ForegroundColor Green

# 定位项目根目录（脚本所在目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# 加载 .env 文件（如果存在）
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

# 使用项目 .venv 的 Python（如果存在）
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Warning: .venv not found, falling back to system uvicorn" -ForegroundColor Yellow
    $uvicorn = "uvicorn"
} else {
    $uvicorn = "$venvPython -m uvicorn"
}

$host = if ($env:ALPHAFLOW_HOST) { $env:ALPHAFLOW_HOST } else { "0.0.0.0" }
$port = if ($env:ALPHAFLOW_PORT) { $env:ALPHAFLOW_PORT } else { "8710" }

if ($DevReload) {
    Write-Host "Starting in DEV reload mode..." -ForegroundColor Yellow
    Invoke-Expression "$uvicorn app.main:app --host $host --port $port --reload"
}
else {
    Write-Host "Starting in STABLE mode (no reload)..." -ForegroundColor Green
    Invoke-Expression "$uvicorn app.main:app --host $host --port $port"
}
