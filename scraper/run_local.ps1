<#
.SYNOPSIS
    İndirim Radarı — Yerel (WAF Korumalı) Marka Taraması (adidasoutlet & mangooutlet)
.DESCRIPTION
    Windows Görev Zamanlayıcı (Task Scheduler) tarafından düzenli olarak
    çalıştırılmak üzere hazırlanmıştır. Unicode/ASCII dışı klasör yollarına
    (ör. Masaüstü) tam uyumludur.
#>
[CmdletBinding()]
param(
    [int]$MaxCategories = 12,
    [switch]$DryRun,
    [switch]$SkipAI,
    [switch]$NoNotify
)

$ErrorActionPreference = "Stop"

# UTF-8 konsol ve Python I/O ayarı (Masaüstü gibi Türkçe yol karakterleri için)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScraperDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScraperDir
$LogDir = Join-Path $ScraperDir "logs"

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "local_scrape_$Timestamp.log"

# Son 30 günden eski logları temizle
Get-ChildItem -LiteralPath $LogDir -Filter "local_scrape_*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Sanal ortam Python'unu bul (scraper/.venv veya kök .venv veya sistem python)
$CandidatePythons = @(
    (Join-Path $ScraperDir ".venv\Scripts\python.exe"),
    (Join-Path $RepoRoot ".venv\Scripts\python.exe")
)
$PythonExe = $null
foreach ($candidate in $CandidatePythons) {
    if (Test-Path -LiteralPath $candidate) {
        $PythonExe = $candidate
        break
    }
}
if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonExe = $cmd.Source
    } else {
        throw "Python yürütülebilir dosyası bulunamadı. Lütfen scraper/.venv kurulu olduğundan emin olun."
    }
}

$Arguments = @(
    "-m", "scraper.main",
    "--brand", "all",
    "--only-local",
    "--run-mode", "local",
    "--max-categories", "$MaxCategories"
)
if ($DryRun) { $Arguments += "--dry-run" }
if ($SkipAI) { $Arguments += "--skip-ai" }
if ($NoNotify) { $Arguments += "--no-notify" }

Push-Location -LiteralPath $RepoRoot
try {
    $Header = "=== İndirim Radarı Local Tarama Başladı: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
    $Header | Tee-Object -FilePath $LogFile
    "Python: $PythonExe" | Tee-Object -FilePath $LogFile -Append
    "Komut: $PythonExe $($Arguments -join ' ')" | Tee-Object -FilePath $LogFile -Append
    "" | Tee-Object -FilePath $LogFile -Append

    & $PythonExe @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
    $ExitCode = $LASTEXITCODE

    "" | Tee-Object -FilePath $LogFile -Append
    "=== Tamamlandı (ExitCode=$ExitCode): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Tee-Object -FilePath $LogFile -Append
    exit $ExitCode
}
finally {
    Pop-Location
}
