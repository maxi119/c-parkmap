# 註冊 Windows 排程：每週一 09:00 檢查更新（關機錯過時，開機後補跑）
# 移除排程：Unregister-ScheduledTask -TaskName 'TaipeiMotoParkingUpdate' -Confirm:$false
$python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$script = Join-Path $PSScriptRoot 'update_moto_parking.py'
if (-not (Test-Path $python)) { throw "找不到 $python，請先依 README 建立 .venv" }

$action   = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --notify" -WorkingDirectory $PSScriptRoot
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'TaipeiMotoParkingUpdate' -Action $action -Trigger $trigger -Settings $settings `
    -Description '檢查臺北市路邊機車收費路段 PDF，有更新時重建 KML 並通知' -Force | Out-Null
Write-Output '已註冊排程 TaipeiMotoParkingUpdate'
