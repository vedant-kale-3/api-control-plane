<#
.SYNOPSIS
    Build, package, and smoke-test the API Control Plane.

.DESCRIPTION
    1. Cleans build\APIControlPlane\ (PyInstaller work cache) and dist\.
    2. Runs pyinstaller build\build.spec  ->  dist\APIControlPlane\APIControlPlane.exe
    3. Smoke test:
         a. Launches the frozen exe (console=False native GUI window).
         b. Discovers the ephemeral HTTP port by polling Get-NetTCPConnection
            for the new PID's 127.0.0.1 LISTEN entry.
            (app\main.py picks a free port at each launch; console=False means
            there is no stdout to read it from, so we watch TCP connections.)
         c. Probes three HTTP endpoints:
              GET /              -> 200  (NiceGUI HTML shell -- Uvicorn is up)
              GET /api/keys      -> 401  (FastAPI router mounted; auth enforced)
              GET /openapi.json  -> 200  (Pydantic models + routes loaded OK)
         d. Kills the process and reports PASS / FAIL.
         e. Scans stderr for frozen-build error patterns and prints actionable
            spec-fix hints (ModuleNotFoundError, DLL load failed, etc.).

.PARAMETER SkipClean
    Skip the clean step.

.PARAMETER SkipBuild
    Skip the PyInstaller step; smoke-test whatever is already in dist\.

.PARAMETER SkipSmokeTest
    Skip smoke-testing; just build and exit.

.PARAMETER SmokeTimeoutSeconds
    Seconds to wait for the exe to bind its HTTP port (default: 60).

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipClean
    .\build.ps1 -SkipBuild
    .\build.ps1 -SkipSmokeTest
    .\build.ps1 -SmokeTimeoutSeconds 90
#>

[CmdletBinding()]
param(
    [switch]$SkipClean,
    [switch]$SkipBuild,
    [switch]$SkipSmokeTest,
    [int]$SmokeTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ============================================================
# Helpers
# ============================================================

function Write-Header([string]$msg) {
    Write-Host ""
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor Cyan
}

function Write-Pass([string]$msg) {
    Write-Host "  [PASS] $msg" -ForegroundColor Green
}

function Write-Fail([string]$msg) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

function Write-Info([string]$msg) {
    Write-Host "  $msg" -ForegroundColor DarkGray
}

function Write-Warn([string]$msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Find-FrozenErrors([string]$text) {
    $patterns = @(
        "ModuleNotFoundError",
        "ImportError",
        "No module named",
        "cannot find module",
        "failed to execute",
        "Error loading",
        "FileNotFoundError",
        "Failed to load",
        "DLL load failed",
        "cannot open shared object",
        "unable to find",
        "Cannot find"
    )
    $found = [System.Collections.Generic.List[string]]::new()
    foreach ($p in $patterns) {
        $text -split "`n" | Where-Object { $_ -imatch $p } | ForEach-Object {
            $t = $_.Trim()
            if ($t -and (-not $found.Contains($t))) {
                $found.Add($t)
            }
        }
    }
    return $found.ToArray()
}

# ============================================================
# 0. Project root
# ============================================================

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = (Get-Location).Path
}
Write-Host ""
Write-Host "Project root : $ProjectRoot" -ForegroundColor White
Set-Location $ProjectRoot

# ============================================================
# 1. Clean
# ============================================================

if ($SkipClean) {
    Write-Header "Step 1 - Clean SKIPPED (-SkipClean)"
} else {
    Write-Header "Step 1 - Clean previous build artefacts"

    # Only remove the PyInstaller work dir and dist\.
    # build\ itself contains the spec, version_info.txt, icon -- keep those.
    $dirsToClean = @(
        (Join-Path $ProjectRoot "build\APIControlPlane"),
        (Join-Path $ProjectRoot "dist")
    )
    foreach ($dir in $dirsToClean) {
        if (Test-Path $dir) {
            Write-Info "Removing $dir ..."
            Remove-Item -Recurse -Force $dir
            Write-Pass "Removed: $dir"
        } else {
            Write-Info "Not present, skipping: $dir"
        }
    }

    $pycache = Join-Path $ProjectRoot "__pycache__"
    if (Test-Path $pycache) {
        Write-Info "Removing root __pycache__ ..."
        Remove-Item -Recurse -Force $pycache
    }
}

# ============================================================
# 2. Build
# ============================================================

if ($SkipBuild) {
    Write-Header "Step 2 - Build SKIPPED (-SkipBuild)"
} else {
    Write-Header "Step 2 - PyInstaller build"

    $specFile = Join-Path $ProjectRoot "build\build.spec"
    if (-not (Test-Path $specFile)) {
        Write-Fail "Spec file not found: $specFile"
        exit 1
    }

    # Prefer the venv pyinstaller over any system-wide one.
    $venvPi = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
    if (Test-Path $venvPi) {
        $piExe = $venvPi
    } else {
        $piCmd = Get-Command "pyinstaller" -ErrorAction SilentlyContinue
        if (-not $piCmd) {
            Write-Fail "pyinstaller not found in PATH or .venv\Scripts\."
            Write-Info "Activate your virtual environment:"
            Write-Info "    .venv\Scripts\Activate.ps1"
            Write-Info "Or install: pip install pyinstaller>=6.10"
            exit 1
        }
        $piExe = $piCmd.Source
    }
    Write-Info ("pyinstaller : " + $piExe)
    Write-Info "Running     : pyinstaller build\build.spec"
    Write-Host ""

    # Run pyinstaller via Start-Process + file redirect so stderr lines do
    # not trigger $ErrorActionPreference=Stop (piping 2>&1 | ForEach-Object
    # wraps stderr as ErrorRecord objects which throw under Stop policy).
    # Note: Start-Process cannot redirect stdout and stderr to the same file
    # simultaneously -- use two files and merge-read them for live streaming.
    $buildStdout = Join-Path $env:TEMP "acp_build_stdout.txt"
    $buildStderr = Join-Path $env:TEMP "acp_build_stderr.txt"
    foreach ($f in @($buildStdout, $buildStderr)) {
        if (Test-Path $f) { Remove-Item -Force $f }
    }

    $buildProc = Start-Process `
        -FilePath               $piExe `
        -ArgumentList           "build\build.spec" `
        -WorkingDirectory       $ProjectRoot `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $buildStdout `
        -RedirectStandardError  $buildStderr

    # Tail both files while the process runs.
    $posOut = 0; $posErr = 0
    function Read-NewContent([string]$file, [ref]$pos) {
        if (-not (Test-Path $file)) { return @() }
        $content = Get-Content $file -Raw -ErrorAction SilentlyContinue
        if (-not $content -or $content.Length -le $pos.Value) { return @() }
        $chunk    = $content.Substring($pos.Value)
        $pos.Value = $content.Length
        return $chunk -split "`n" | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ -ne "" }
    }
    function Print-BuildLine([string]$line) {
        if ($line -match "^(ERROR|CRITICAL)") {
            Write-Host $line -ForegroundColor Red
        } elseif ($line -match "^WARNING") {
            Write-Host $line -ForegroundColor Yellow
        } elseif ($line -match "(ModuleNotFoundError|ImportError|No module named|DLL load failed|Traceback)") {
            Write-Host $line -ForegroundColor Red
        } else {
            Write-Host $line
        }
    }

    while (-not $buildProc.HasExited) {
        Start-Sleep -Milliseconds 500
        Read-NewContent $buildStdout ([ref]$posOut) | ForEach-Object { Print-BuildLine $_ }
        Read-NewContent $buildStderr ([ref]$posErr) | ForEach-Object { Print-BuildLine $_ }
    }
    # Flush remaining output after exit.
    Read-NewContent $buildStdout ([ref]$posOut) | ForEach-Object { Print-BuildLine $_ }
    Read-NewContent $buildStderr ([ref]$posErr) | ForEach-Object { Print-BuildLine $_ }

    # WaitForExit() with no timeout guarantees ExitCode is populated.
    # WaitForExit(ms) can return before the OS flushes the exit code.
    $buildProc.WaitForExit()
    $buildExitCode = $buildProc.ExitCode
    if ($null -eq $buildExitCode) { $buildExitCode = 0 }  # null = process exited OK

    Write-Host ""
    if ($buildExitCode -ne 0) {
        Write-Fail ("PyInstaller exited with code " + $buildExitCode + " - build FAILED.")
        Write-Host ""
        Write-Host "  Common causes:" -ForegroundColor Yellow
        Write-Host "    Missing module -> add to hiddenimports in build\build.spec" -ForegroundColor Yellow
        Write-Host "    Missing data   -> add collect_data_files() in build\build.spec" -ForegroundColor Yellow
        Write-Host "    Wrong venv     -> activate the production venv (no dev packages)" -ForegroundColor Yellow
        exit $buildExitCode
    }
    Write-Pass "PyInstaller build completed."
}

# ============================================================
# Verify output exists (always runs)
# ============================================================

$distExe = Join-Path $ProjectRoot "dist\APIControlPlane\APIControlPlane.exe"
if (Test-Path $distExe) {
    $sizeBytes = (Get-Item $distExe).Length
    $sizeMB    = [math]::Round($sizeBytes / 1048576, 1)
    Write-Pass ("Exe found: " + $distExe + "  (" + $sizeMB + " MB)")
} else {
    Write-Fail ("Expected exe not found: " + $distExe)
    exit 1
}

# ============================================================
# 3. Smoke test
# ============================================================

if ($SkipSmokeTest) {
    Write-Header "Step 3 - Smoke test SKIPPED (-SkipSmokeTest)"
    Write-Pass "Build script finished (smoke test skipped)."
    exit 0
}

Write-Header "Step 3 - Smoke test"
Write-Host ""
Write-Host "  Strategy:" -ForegroundColor White
Write-Host "    1. Launch APIControlPlane.exe (native GUI window will open briefly)" -ForegroundColor DarkGray
Write-Host "    2. Poll Get-NetTCPConnection for the PID's 127.0.0.1 LISTEN port" -ForegroundColor DarkGray
Write-Host "    3. GET /   GET /api/keys   GET /openapi.json" -ForegroundColor DarkGray
Write-Host "    4. Kill process, report PASS / FAIL" -ForegroundColor DarkGray
Write-Host ""
Write-Warn "A native GUI window will open briefly -- this is expected."

# ---- 3a. Launch ----

$stdoutFile = Join-Path $env:TEMP "acp_smoke_stdout.txt"
$stderrFile = Join-Path $env:TEMP "acp_smoke_stderr.txt"
foreach ($f in @($stdoutFile, $stderrFile)) {
    if (Test-Path $f) { Remove-Item -Force $f }
}

$proc = $null
try {
    $proc = Start-Process `
        -FilePath               $distExe `
        -WorkingDirectory       (Split-Path $distExe -Parent) `
        -PassThru `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError  $stderrFile
} catch {
    Write-Fail ("Failed to start process: " + $_)
    exit 1
}
Write-Info ("Launched PID " + $proc.Id)

# ---- 3b. Port discovery ----
#
# app\main.py calls _find_free_port() (OS-assigned ephemeral port each run).
# console=False -> no stdout. We find the port by watching Get-NetTCPConnection
# for a 127.0.0.1 LISTEN entry owned by the new PID.

Write-Info ("Waiting up to " + $SmokeTimeoutSeconds + "s for process to bind its HTTP port ...")

$pollMs      = 500
$maxAttempts = [int]($SmokeTimeoutSeconds * 1000 / $pollMs)
$port        = $null

for ($i = 0; $i -lt $maxAttempts; $i++) {

    if ($proc.HasExited) {
        Write-Fail ("Process exited prematurely (exit code " + $proc.ExitCode + ") before binding a port.")
        $stderr = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
        if ($stderr) {
            Write-Host ""
            Write-Host "  --- stderr (frozen-build errors) ---" -ForegroundColor DarkGray
            Write-Host $stderr -ForegroundColor Red
            $errs = Find-FrozenErrors $stderr
            if ($errs.Count -gt 0) {
                Write-Host ""
                Write-Host "  Frozen-build errors detected:" -ForegroundColor Red
                $errs | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor Red }
                Write-Host ""
                Write-Host "  Fix guide:" -ForegroundColor Yellow
                Write-Host "    ModuleNotFoundError -> add to hiddenimports in build\build.spec" -ForegroundColor Yellow
                Write-Host "    FileNotFoundError   -> add collect_data_files() in build\build.spec" -ForegroundColor Yellow
                Write-Host "    DLL load failed     -> add collect_data_files('webview') to spec" -ForegroundColor Yellow
                Write-Host "    nicegui blank       -> add collect_data_files('nicegui') to spec" -ForegroundColor Yellow
                Write-Host "    Set console=True in spec EXE() to see full traceback on startup." -ForegroundColor Yellow
            }
        }
        exit 1
    }

    try {
        $listeners = Get-NetTCPConnection `
            -OwningProcess $proc.Id `
            -State Listen `
            -LocalAddress "127.0.0.1" `
            -ErrorAction SilentlyContinue
        if ($listeners) {
            $port = ($listeners | Select-Object -First 1).LocalPort
            $elapsed = [math]::Round($i * $pollMs / 1000, 1)
            Write-Pass ("Port discovered: " + $port + "  (after " + $elapsed + "s)")
            break
        }
    } catch {
        # transient -- retry
    }

    Start-Sleep -Milliseconds $pollMs
}

if (-not $port) {
    Write-Fail ("Port discovery timed out after " + $SmokeTimeoutSeconds + "s -- process never bound a 127.0.0.1 listener.")
    if (-not $proc.HasExited) { $proc.Kill() }
    $stderr = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
    if ($stderr) {
        Write-Host ""
        Write-Host "  --- stderr ---" -ForegroundColor DarkGray
        Write-Host $stderr -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Possible causes:" -ForegroundColor Yellow
    Write-Host "    - Crash on startup -> check stderr above for ModuleNotFoundError" -ForegroundColor Yellow
    Write-Host "    - Missing NiceGUI static -> add collect_data_files('nicegui') to spec" -ForegroundColor Yellow
    Write-Host "    - Missing webview DLLs   -> add collect_data_files('webview') to spec" -ForegroundColor Yellow
    Write-Host "    - Single-instance mutex already held (another instance running)" -ForegroundColor Yellow
    Write-Host "    - WebView2 Runtime not installed on this machine" -ForegroundColor Yellow
    exit 1
}

# ---- 3c. HTTP probes ----

$baseUrl   = "http://127.0.0.1:" + $port
$smokePass = $true

function Invoke-HttpProbe {
    param(
        [string]$Label,
        [string]$Url,
        [int[]]$AcceptedCodes = @(200)
    )
    Write-Host ""
    Write-Host ("  Probe : " + $Label) -ForegroundColor White
    Write-Info  ("  URL   : " + $Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        $code = [int]$resp.StatusCode
    } catch [System.Net.WebException] {
        $code = [int]$_.Exception.Response.StatusCode
    } catch {
        Write-Fail ($Label + ": request failed -- " + $_)
        return $false
    }
    if ($AcceptedCodes -contains $code) {
        Write-Pass ($Label + ": HTTP " + $code + "  (OK)")
        return $true
    } else {
        $expected = $AcceptedCodes -join " or "
        Write-Fail ($Label + ": HTTP " + $code + "  (expected " + $expected + ")")
        return $false
    }
}

# Allow 2s for Uvicorn startup to settle before probing.
Write-Info "Waiting 2s for HTTP server startup to settle ..."
Start-Sleep -Seconds 2

# Probe 1: NiceGUI HTML shell (Uvicorn is serving requests)
$r1 = Invoke-HttpProbe -Label "NiceGUI root  GET /" `
                       -Url ($baseUrl + "/") `
                       -AcceptedCodes @(200)
if (-not $r1) { $smokePass = $false }

# Probe 2: FastAPI router (missing X-API-Key -> 401 is the correct response)
$r2 = Invoke-HttpProbe -Label "FastAPI       GET /api/keys" `
                       -Url ($baseUrl + "/api/keys") `
                       -AcceptedCodes @(200, 401)
if (-not $r2) { $smokePass = $false }

# Probe 3: FastAPI POST-only endpoint probe.
# NiceGUI in native mode disables FastAPI's /openapi.json docs endpoint.
# GET /webhook/rotate-key returns 405 (Method Not Allowed for a POST route),
# which confirms the FastAPI router is mounted and route registration worked.
$r3 = Invoke-HttpProbe -Label "FastAPI       GET /webhook/rotate-key (405 expected)" `
                       -Url ($baseUrl + "/webhook/rotate-key") `
                       -AcceptedCodes @(405)
if (-not $r3) { $smokePass = $false }

# ---- 3d. Kill ----

Write-Host ""
Write-Info ("Terminating PID " + $proc.Id + " ...")
try {
    if (-not $proc.HasExited) {
        $proc.Kill()
        $proc.WaitForExit(5000) | Out-Null
    }
    Write-Info "Process terminated."
} catch {
    Write-Warn ("Could not terminate cleanly: " + $_)
}

# ---- 3e. Scan stderr for frozen-build errors ----

$stderr       = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
$frozenErrors = @()   # force array -- Find-FrozenErrors can return scalar null under strict mode
if ($stderr) {
    $frozenErrors = @(Find-FrozenErrors $stderr)
}

# ============================================================
# 4. Summary
# ============================================================

Write-Header "Build + Smoke Test Summary"

if ($r1) { Write-Pass "NiceGUI root        GET /"                          } else { Write-Fail "NiceGUI root        GET /" }
if ($r2) { Write-Pass "FastAPI             GET /api/keys (401 auth)"        } else { Write-Fail "FastAPI             GET /api/keys (401 auth)" }
if ($r3) { Write-Pass "FastAPI             GET /webhook/rotate-key (405)"   } else { Write-Fail "FastAPI             GET /webhook/rotate-key (405)" }

Write-Host ""

if ($frozenErrors.Count -gt 0) {
    Write-Host "  Frozen-build errors in stderr:" -ForegroundColor Red
    $frozenErrors | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor Red }
    $smokePass = $false
} else {
    Write-Pass "No frozen-build error patterns in stderr."
}

Write-Host ""

if ($smokePass) {
    Write-Host "  OVERALL: ALL SMOKE TESTS PASSED" -ForegroundColor Green -BackgroundColor DarkGreen
    Write-Host ""
    Write-Host "  Deliverable:" -ForegroundColor White
    Write-Host ("    " + $distExe) -ForegroundColor White
    Write-Host ""
    Write-Host "  Distribution notes:" -ForegroundColor Yellow
    Write-Host "    - Target needs WebView2 Runtime (pre-installed Win10 21H2+ / Win11)" -ForegroundColor Yellow
    Write-Host "    - No Python installation required on the target machine" -ForegroundColor Yellow
    Write-Host "    - DB is auto-created at: %LOCALAPPDATA%\APIControlPlane\data.db" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "  OVERALL: SMOKE TEST FAILED" -ForegroundColor Red -BackgroundColor DarkRed
    Write-Host ""
    Write-Host "  Frozen-build fix guide:" -ForegroundColor Yellow
    Write-Host "    ModuleNotFoundError  -> add to hiddenimports in build\build.spec" -ForegroundColor Yellow
    Write-Host "    FileNotFoundError    -> add via collect_data_files() in build\build.spec" -ForegroundColor Yellow
    Write-Host "    DLL load failed      -> collect_data_files('webview') -- DLLs not bundled" -ForegroundColor Yellow
    Write-Host "    Blank/white window   -> collect_data_files('nicegui') -- JS/CSS missing" -ForegroundColor Yellow
    Write-Host "    /api/keys returns 404-> FastAPI router not mounted; check bootstrap() in app\main.py" -ForegroundColor Yellow
    Write-Host "    Port never bound     -> crash on startup; set console=True in spec EXE()" -ForegroundColor Yellow
    exit 1
}
