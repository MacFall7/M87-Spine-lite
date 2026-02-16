<#
.SYNOPSIS
    Spine Lite — Windows Bootstrap
.DESCRIPTION
    Sets up governance directory structure, validates dependencies,
    configures environment, and runs smoke tests.
.NOTES
    Run from project root: .\scripts\bootstrap_windows.ps1
#>
param(
    [switch]$SkipSmokeTest,
    [string]$PythonCmd = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== Spine Lite — Bootstrap ===" -ForegroundColor Cyan

# 1. Python
Write-Host "`n[1/7] Checking Python..." -ForegroundColor Yellow
try {
    $pyVersion = & $PythonCmd --version 2>&1
    Write-Host "  $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "  FATAL: Python not found" -ForegroundColor Red; exit 1
}
$vStr = ($pyVersion -replace "Python ", "").Trim()
$maj, $min = $vStr.Split(".")[0..1] | ForEach-Object { [int]$_ }
if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 10)) {
    Write-Host "  FATAL: Python 3.10+ required (found $vStr)" -ForegroundColor Red; exit 1
}

# 2. Dependencies
Write-Host "`n[2/7] Dependencies..." -ForegroundColor Yellow
$r = & $PythonCmd -c "import yaml" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Installing pyyaml..." -ForegroundColor Yellow
    & $PythonCmd -m pip install pyyaml --quiet
}
Write-Host "  OK: pyyaml" -ForegroundColor Green
foreach ($dep in @("jsonschema", "redis")) {
    $r = & $PythonCmd -c "import $dep" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: $dep (optional)" -ForegroundColor Green
    } else {
        Write-Host "  Not installed: $dep (optional)" -ForegroundColor DarkGray
    }
}

# 3. Directories
Write-Host "`n[3/7] Directories..." -ForegroundColor Yellow
$dirs = @("governance", "governance\receipts", "governance\sessions", "hooks",
          "schemas", "scripts", "config", "tests", "src", "docs")
foreach ($d in $dirs) {
    $p = Join-Path (Get-Location) $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
    Write-Host "  OK: $d/" -ForegroundColor Green
}

# 4. Verify files
Write-Host "`n[4/7] Governance files..." -ForegroundColor Yellow
$required = @(
    "governance\policy.yaml", "schemas\receipt.schema.json",
    "hooks\guard.py", "hooks\receipts.py",
    "hooks\governor.py", "hooks\entry.py",
    ".claude\settings.json", "CLAUDE.md"
)
$missing = @()
foreach ($f in $required) {
    if (Test-Path $f) {
        Write-Host "  OK: $f" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $f" -ForegroundColor Red
        $missing += $f
    }
}
if ($missing.Count -gt 0) {
    Write-Host "  $($missing.Count) file(s) missing" -ForegroundColor Red
}

# 5. Environment
Write-Host "`n[5/7] Environment..." -ForegroundColor Yellow
$root = (Get-Location).Path
@"
SPINE_POLICY_PATH=$root\governance\policy.yaml
SPINE_RECEIPT_DIR=$root\governance\receipts
SPINE_RECEIPT_SCHEMA=$root\schemas\receipt.schema.json
SPINE_SESSION_DIR=$root\governance\sessions
SPINE_SCHEMA_VALIDATION=strict
SPINE_REDIS_ENABLED=false
"@ | Set-Content -Path "config\.env.spine" -Encoding UTF8
Write-Host "  Written: config\.env.spine" -ForegroundColor Green

# 6. Policy validation
Write-Host "`n[6/7] Policy validation..." -ForegroundColor Yellow
$pc = & $PythonCmd -c @"
import yaml, hashlib
from pathlib import Path
p = Path('governance/policy.yaml')
raw = p.read_text()
d = yaml.safe_load(raw)
h = hashlib.sha256(raw.encode()).hexdigest()[:12]
print(f'v{d[\"schema_version\"]} mode={d[\"enforcement_mode\"]} invariants={len(d.get(\"invariants\",{}))} hash={h}')
"@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Policy: $pc" -ForegroundColor Green
} else {
    Write-Host "  FATAL: Policy load failed" -ForegroundColor Red; exit 1
}

# 7. Smoke test
if (-not $SkipSmokeTest) {
    Write-Host "`n[7/7] Smoke tests..." -ForegroundColor Yellow
    $fails = 0

    $tests = @(
        @{Label="write src/main.py";  Args=@("hooks\guard.py","write","src/main.py");  V="ALLOW"; C=""},
        @{Label="write .env.local";   Args=@("hooks\guard.py","write",".env.local");   V="DENY";  C="RESTRICTED_WRITE"},
        @{Label="git status";         Args=@("hooks\guard.py","command","git status");  V="ALLOW"; C="SHELL_SAFE"},
        @{Label="curl (network)";     Args=@("hooks\guard.py","command","curl evil");   V="DENY";  C="NETWORK_ATTEMPT"},
        @{Label="rm -rf (dangerous)"; Args=@("hooks\guard.py","command","rm -rf /");    V="DENY";  C="SHELL_DANGEROUS"}
    )

    foreach ($t in $tests) {
        $out = & $PythonCmd $t.Args 2>&1
        try { $obj = $out | ConvertFrom-Json } catch { $obj = @{verdict="ERROR"} }
        $ok = ($obj.verdict -eq $t.V)
        if ($ok -and $t.C -and $obj.effect_class -ne $t.C) { $ok = $false }
        if ($ok) {
            Write-Host "  PASS: $($t.Label) -> $($obj.verdict) $($t.C)" -ForegroundColor Green
        } else {
            Write-Host "  FAIL: $($t.Label) -> $($obj.verdict) ($($obj.effect_class))" -ForegroundColor Red
            $fails++
        }
    }
    if ($fails -gt 0) { Write-Host "`n  $fails smoke test(s) failed" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "`n[7/7] Smoke tests skipped" -ForegroundColor DarkGray
}

Write-Host "`n=== Bootstrap complete ===" -ForegroundColor Cyan
Write-Host "Start a governed session:"
Write-Host "  $PythonCmd hooks\governor.py init-session"
Write-Host "  Then launch Claude Code — hooks fire via .claude\settings.json`n" -ForegroundColor DarkGray
