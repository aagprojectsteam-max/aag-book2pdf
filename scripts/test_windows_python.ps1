# Python-first acceptance of the shared application. No EXE or installer is used.
param(
    [string]$Python = ".venv-win/Scripts/python.exe",
    [Parameter(Mandatory=$true)][string]$BkcSample,
    [Parameter(Mandatory=$true)][int]$BkcPages,
    [Parameter(Mandatory=$true)][string]$BkfSample,
    [Parameter(Mandatory=$true)][int]$BkfPages,
    [Parameter(Mandatory=$true)][string]$NewVariantSample,
    [Parameter(Mandatory=$true)][int]$NewVariantPages,
    [Parameter(Mandatory=$true)][string]$Evidence
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Native Windows is required; Linux/Wine is not native acceptance." }
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}
try {
    $env:PYTHONUTF8 = "1"
    $env:QT_QPA_PLATFORM = "windows"
    Invoke-Checked $Python @("-c", "import sys,struct; assert sys.platform=='win32' and sys.version_info[:2]==(3,12) and struct.calcsize('P')==8")
    $ManifestPath = Join-Path $Root "BUILDKIT-MANIFEST.json"
    if (!(Test-Path $ManifestPath)) { throw "Use the fresh committed BuildKit, including BUILDKIT-MANIFEST.json." }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if (!$Manifest.tracked_worktree_clean) { throw "BuildKit was not produced from a clean committed source tree." }
    foreach ($File in $Manifest.files.PSObject.Properties) {
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Root $File.Name)).Hash.ToLowerInvariant() -ne $File.Value) {
            throw "BuildKit source changed: $($File.Name)"
        }
    }
    if (Test-Path $Evidence) { throw "Use a new evidence directory; existing outputs are protected." }
    New-Item -ItemType Directory -Path $Evidence | Out-Null
    $Output = (Resolve-Path $Evidence).Path
    $Cases = @(
        @{Source=$BkcSample; Pages=$BkcPages; Status="PASS_DECODED"; Kind="BKC"},
        @{Source=$BkfSample; Pages=$BkfPages; Status="PASS_DECODED"; Kind="BKF"},
        @{Source=$NewVariantSample; Pages=$NewVariantPages; Status="PASS_DECODED"; Kind="NEW_VARIANT"}
    )
    $Records = @()
    foreach ($Case in $Cases) {
        $Source = (Resolve-Path $Case.Source).Path
        $Before = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash.ToLowerInvariant()
        $Run = Join-Path $Output ($Case.Kind + " Python output")
        Invoke-Checked $Python @("-m", "book2pdf.selftest", $Run, "--gui", "--sample", $Source,
            "--expected-sha256", $Before, "--expected-pages", [string]$Case.Pages, "--expected-status", $Case.Status)
        $Result = Get-Content -LiteralPath (Join-Path $Run "evidence.json") -Raw | ConvertFrom-Json
        if ($Result.status -ne "PASS" -or $Result.os -ne "win32" -or $Result.frozen -or $Result.qt_platform -ne "windows" -or
            $Result.python -notlike "3.12.*" -or $Result.python_bits -ne 64 -or $Result.continuous_scroll -ne "PASS") {
            throw "Not native Python 3.12 x64 GUI evidence for $($Case.Kind)"
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash.ToLowerInvariant() -ne $Before) { throw "Source changed" }
        $Records += @{family=$Case.Kind; source_sha256=$Before; evidence=$Result}
    }
    $Report = @{status="PASS"; source_commit=$Manifest.source_commit;
        source_manifest_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant();
        WINDOWS_PYTHON_GUI_START="PASS"; WINDOWS_PYTHON_BKC_OPEN="PASS"; WINDOWS_PYTHON_BKF_OPEN="PASS";
        WINDOWS_PYTHON_NEW_VARIANT_OPEN="PASS"; WINDOWS_PYTHON_CONTINUOUS_SCROLL="PASS"; WINDOWS_PYTHON_SINGLE_PAGE="PASS";
        WINDOWS_PYTHON_EXPORT="PASS"; WINDOWS_PYTHON_PARTIAL_EXPORT="PASS"; WINDOWS_PYTHON_PRINT_TO_PDF="PASS";
        MICROSOFT_PRINT_TO_PDF="NOT_PROVEN"; records=$Records}
    $Report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Output "native-python.json") -Encoding UTF8
    Write-Host "Native Python acceptance passed. Evidence: $Output/native-python.json"
} finally { Pop-Location }
