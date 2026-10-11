$taskConfigPath = Join-Path $PSScriptRoot '..\references\local_config.json'
$taskConfig = @{ local_url = 'http://127.0.0.1:8000/' }
if (Test-Path -LiteralPath $taskConfigPath) { $taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw | ConvertFrom-Json }
$taskRoot = $env:SJTU_CANVAS2NOTE_ROOT
if (-not $taskRoot) { $taskRoot = $taskConfig.tool_root }
if (-not $taskRoot) { throw 'Set SJTU_CANVAS2NOTE_ROOT or create local_config.json from the example.' }
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '工具环境不存在。' }
$taskListener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($taskListener) {
    $taskProc = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListener[0].OwningProcess)"
    if ($taskProc.CommandLine -notlike '*uvicorn server:app*') { throw '8000端口被其他程序占用。' }
} else {
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList '-m uvicorn server:app --host 127.0.0.1 --port 8000 --no-access-log' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot 'server.stdout.log') -RedirectStandardError (Join-Path $taskRoot 'server.stderr.log')
    $taskProcess.Id | Set-Content -LiteralPath (Join-Path $taskRoot 'server.pid')
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
        try { $null = Invoke-WebRequest $taskConfig.local_url -TimeoutSec 2; $taskReady = $true; break } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $taskReady) { throw '服务尚未就绪，请查看工具 server.stderr.log。' }
}
Start-Process 'msedge.exe' -ArgumentList $taskConfig.local_url
