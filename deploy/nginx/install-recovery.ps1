$ErrorActionPreference = 'Stop'
$scriptPath = 'C:\ProgramData\Aitogy\Nginx\ensure-nginx.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$scriptPath+'"') -WorkingDirectory 'C:\nginx-1.28.0'
$boot = New-ScheduledTaskTrigger -AtStartup
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'AitogyNginxRecovery' -Action $action -Trigger @($boot,$retry) -Settings $settings -User 'SYSTEM' -RunLevel Highest -Description 'Validate and start Nginx after reboot or process exit; preserve IIS on 8081.'
Start-ScheduledTask -TaskName 'AitogyNginxRecovery'
