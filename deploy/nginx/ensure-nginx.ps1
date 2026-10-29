$ErrorActionPreference = 'Stop'
$prefix = 'C:\nginx-1.28.0'
$exe = Join-Path $prefix 'nginx.exe'
$running = Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" | Where-Object { $_.ExecutablePath -ieq $exe }
if ($running) { exit 0 }
Set-Location $prefix
& $exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -t
if ($LASTEXITCODE -ne 0) { throw 'Nginx configuration test failed; no process started.' }
& $exe -p C:/nginx-1.28.0/ -c conf/nginx.conf
if ($LASTEXITCODE -ne 0) { throw 'Nginx process exited with an error.' }
