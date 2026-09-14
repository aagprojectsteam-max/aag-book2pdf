# Run on the real Windows desktop with user-supplied, read-only originals.
param(
    [Parameter(Mandatory=$true)][string]$GuiExe,
    [Parameter(Mandatory=$true)][string]$BkcSample,
    [Parameter(Mandatory=$true)][int]$BkcPages,
    [Parameter(Mandatory=$true)][string]$BkfSample,
    [Parameter(Mandatory=$true)][int]$BkfPages,
    [Parameter(Mandatory=$true)][string]$Evidence
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Native Windows required; Wine is not acceptance." }
if (Test-Path $Evidence) { throw "Use a new evidence folder; existing output is protected." }
$Exe = (Resolve-Path $GuiExe).Path
$Records = @()
# Construct Hebrew without depending on PowerShell 5's source-file encoding.
$Hebrew = -join ([char[]]@(0x05E1,0x05E4,0x05E8,0x0020,0x05D1,0x05D3,0x05D9,0x05E7,0x05D4))
New-Item -ItemType Directory -Path $Evidence | Out-Null
$Root = (Resolve-Path $Evidence).Path
$Cases = @(
    @{Source=$BkcSample; Pages=$BkcPages; Status="PASS_REPAIRED"; Kind="BKC"},
    @{Source=$BkfSample; Pages=$BkfPages; Status="PASS_DECODED"; Kind="BKF"}
)
foreach ($Case in $Cases) {
    $Source = (Resolve-Path $Case.Source).Path
    $Before = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash.ToLowerInvariant()
    $Copy = Join-Path $Root ($Hebrew + " " + $Case.Kind + ".book")
    Copy-Item -LiteralPath $Source -Destination $Copy
    $Run = Join-Path $Root ($Case.Kind + " output " + $Hebrew)
    $Arguments = @("--self-test", ('"'+$Run+'"'), "--sample", ('"'+$Copy+'"'), "--expected-sha256", $Before,
                   "--expected-pages", [string]$Case.Pages, "--expected-status", $Case.Status)
    $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru
    if (!$Process.WaitForExit(900000)) { $Process.Kill(); throw "Native real-book acceptance timed out" }
    if ($Process.ExitCode -ne 0) { throw "Native real-book acceptance failed; inspect $Run" }
    $Result = Get-Content -LiteralPath (Join-Path $Run "evidence.json") -Raw | ConvertFrom-Json
    if ($Result.status -ne "PASS" -or $Result.os -ne "win32" -or !$Result.frozen -or $Result.qt_platform -ne "windows") {
        throw "Evidence is not a successful native frozen Windows run"
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash.ToLowerInvariant() -ne $Before) { throw "Original source changed" }
    $Records += @{family=$Case.Kind; source_sha256=$Before; source_unchanged=$true; evidence=$Result}
}
$Records | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Root "native-real-books.json") -Encoding UTF8
Write-Host "NATIVE_REAL_BKC_AND_BKF=PASS; DEFAULT_ASSOCIATION_AND_MICROSOFT_DRIVER=NOT_PROVEN"
