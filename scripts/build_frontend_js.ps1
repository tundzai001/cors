$ErrorActionPreference = 'Stop'

$frontendRoot = Join-Path $PSScriptRoot '..\cors_dashboard\frontend'
$sourceRoot = Join-Path $frontendRoot 'js'
$outputRoot = Join-Path $sourceRoot 'dist'
$entries = @(
    'react-components.js',
    'dashboard-forms.js',
    'dashboard-list.js',
    'dashboard-live.js',
    'dashboard-detail.js',
    'stations-map.js',
    'homepage-enterprise.v2.js',
    'enterprise-station-detail.js',
    'blog-system.js',
    'api-docs.js',
    'advanced-features.js'
) | ForEach-Object { Join-Path $sourceRoot $_ }

& npx --yes esbuild@0.25.8 @entries `
    --outdir=$outputRoot `
    --loader:.js=jsx `
    --target=es2018 `
    --format=iife `
    --jsx-factory=React.createElement `
    --jsx-fragment=React.Fragment `
    --minify-syntax `
    --minify-whitespace

if ($LASTEXITCODE -ne 0) {
    throw "Frontend JSX build failed with exit code $LASTEXITCODE"
}
