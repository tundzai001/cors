param(
    [string]$ReloadAccount = "$env:COMPUTERNAME\Administrator"
)
$ErrorActionPreference = 'Stop'
$prefix = 'C:\nginx-1.28.0'
$exe = Join-Path $prefix 'nginx.exe'
$running = Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" | Where-Object { $_.ExecutablePath -ieq $exe }
if (-not $running) {
    & $exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -t
    if ($LASTEXITCODE -ne 0) { throw 'Nginx configuration test failed; no process started.' }
    # Do not keep the recovery task waiting for the lifetime of the master.
    Start-Process -FilePath $exe -ArgumentList '-p C:/nginx-1.28.0/ -c conf/nginx.conf' -WorkingDirectory $prefix | Out-Null
}

# The SYSTEM master creates a new named event on each start. Give only the
# configured backend account the right used by `nginx -s reload`, not full control.
$account = [System.Security.Principal.NTAccount]::new($ReloadAccount)
$accountSid = $account.Translate([System.Security.Principal.SecurityIdentifier])
$rights = [System.Security.AccessControl.EventWaitHandleRights]::ReadPermissions -bor
          [System.Security.AccessControl.EventWaitHandleRights]::ChangePermissions
$eventHandle = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $masterId = 0
    $pidFile = Join-Path $prefix 'logs\nginx.pid'
    if ((Test-Path $pidFile) -and [int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$masterId)) {
        $master = Get-CimInstance Win32_Process -Filter "ProcessId=$masterId"
        if ($master -and $master.ExecutablePath -ieq $exe) {
            try {
                $eventHandle = [System.Threading.EventWaitHandle]::OpenExisting("Global\ngx_reload_$masterId", $rights)
                break
            } catch [System.Threading.WaitHandleCannotBeOpenedException] {
                # The pid file can precede event creation during startup.
            }
        }
    }
    Start-Sleep -Milliseconds 500
}
if (-not $eventHandle) { throw 'Nginx reload event is unavailable; no permissions changed.' }
try {
    $security = $eventHandle.GetAccessControl()
    $backup = Join-Path $PSScriptRoot "reload-event-$masterId.original.sddl"
    if (-not (Test-Path $backup)) {
        [IO.File]::WriteAllText($backup, $security.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access))
    }
    $rule = [System.Security.AccessControl.EventWaitHandleAccessRule]::new(
        $accountSid, [System.Security.AccessControl.EventWaitHandleRights]::Modify,
        [System.Security.AccessControl.AccessControlType]::Allow)
    $security.AddAccessRule($rule)
    $eventHandle.SetAccessControl($security)
    Write-Output "Nginx master ${masterId}: reload permission granted to $ReloadAccount."
} finally {
    $eventHandle.Dispose()
}
