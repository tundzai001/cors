$ErrorActionPreference = 'Stop'

$buildScript = Join-Path $PSScriptRoot 'build_frontend_assets.mjs'
& node $buildScript
if ($LASTEXITCODE -ne 0) {
    throw "Frontend build failed with exit code $LASTEXITCODE"
}
