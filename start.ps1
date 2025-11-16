param(
  [string]$EnvFile = ".env",
  [string]$BindAddr = $env:HOST,
  [int]$Port = [int]($env:PORT)
)
$ErrorActionPreference = "Stop"
if (-not $BindAddr) { $BindAddr = "127.0.0.1" }
if (-not $Port)     { $Port = 8000 }

$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$EnvPath = Join-Path $RepoRoot $EnvFile
if (-not (Test-Path $EnvPath)) {
  $AltEnv = Join-Path $PSScriptRoot ".env"
  if (Test-Path $AltEnv) { $EnvPath = $AltEnv }
}

python -m uvicorn paper_survey.main:app `
  --host $BindAddr `
  --port $Port `
  --reload `
  --env-file $EnvPath
.\