# Certbot deploy hook: public certificate files must remain readable by Nginx.
param(
    [string]$NginxPrefix = 'C:\nginx-1.28.0',
    [string]$Lineage = $env:RENEWED_LINEAGE
)
$ErrorActionPreference = 'Stop'

if ($Lineage) {
    foreach ($name in @('cert.pem', 'chain.pem', 'fullchain.pem')) {
        $file = Join-Path $Lineage $name
        # Work on the archive file; Windows can deny ACL writes through a symlink.
        # Private-key ownership and permissions are never changed.
        $entry = Get-Item -LiteralPath $file -Force
        $target = $file
        if ($entry.LinkType -eq 'SymbolicLink') {
            $target = $entry.Target[0]
            if (-not [IO.Path]::IsPathRooted($target)) {
                $target = Join-Path (Split-Path -Parent $file) $target
            }
            $target = [IO.Path]::GetFullPath($target)
        }
        & takeown.exe /f $target /a
        if ($LASTEXITCODE -ne 0) { throw "Cannot recover certificate ownership: $target" }
        & icacls.exe $target /grant '*S-1-5-32-544:(R)' '*S-1-5-18:(F)'
        if ($LASTEXITCODE -ne 0) { throw "Cannot grant certificate read access: $file" }
        $stream = [IO.File]::OpenRead($file)
        $stream.Dispose()
    }
}

$executable = Join-Path $NginxPrefix 'nginx.exe'
& $executable -p ($NginxPrefix.Replace('\', '/') + '/') -c conf/nginx.conf -t
if ($LASTEXITCODE -ne 0) { throw 'Nginx test failed; existing workers remain running.' }
& $executable -p ($NginxPrefix.Replace('\', '/') + '/') -c conf/nginx.conf -s reload
if ($LASTEXITCODE -ne 0) { throw 'Nginx reload command failed.' }
