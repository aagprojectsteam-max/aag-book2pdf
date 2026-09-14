# Runs ONLY on an ephemeral CI account; never uninstalls a user's existing app.
param([Parameter(Mandatory=$true)][string]$Setup)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT" -or $env:CI -ne "true") { throw "Requires an ephemeral native Windows CI runner." }
$Classes = "HKCU:\Software\Classes"
$ProgId = "$Classes\AAG.Book2PDF.Book"
if (Test-Path $ProgId) { throw "Existing installation detected; refusing to replace it." }
$Base = Join-Path $env:TEMP ("AAG install test " + [guid]::NewGuid().ToString("N"))
$Install = Join-Path $Base "app"
$Settings = "HKCU:\Software\AAG\AAG Book2PDF"
if (Test-Path $Settings) { throw "Existing user settings detected; refusing to change them." }
New-Item $Settings -Force | Out-Null
New-ItemProperty $Settings -Name "acceptance-sentinel" -Value "preserve" | Out-Null
$DefaultBefore = Get-ItemProperty "$Classes\.book" -ErrorAction SilentlyContinue
$BookDefault = if ($DefaultBefore) { $DefaultBefore.'(default)' } else { $null }
function Run-Setup([string]$Exe, [string[]]$Parameters) {
    $Process = Start-Process -FilePath $Exe -ArgumentList $Parameters -PassThru
    if (!$Process.WaitForExit(300000)) { $Process.Kill(); throw "Installer test timed out" }
    if ($Process.ExitCode -ne 0) { throw "Installer/uninstaller failed: $($Process.ExitCode)" }
}
Run-Setup (Resolve-Path $Setup).Path @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", ('/DIR="' + $Install + '"'), "/TASKS=bookassociation")
$Command = (Get-Item "$ProgId\shell\open\command").GetValue("")
if ($Command -ne ('"' + $Install + '\AAG-Book2PDF.exe" "%1"')) { throw "Unquoted or incorrect file association" }
$OpenWith = Get-Item "$Classes\.book\OpenWithProgids"
if ($OpenWith.GetValueNames() -notcontains "AAG.Book2PDF.Book") { throw "Open with registration missing" }
if (!(Test-Path (Join-Path $Install "AAG-Book2PDF.exe"))) { throw "Installed GUI is missing" }
Run-Setup (Join-Path $Install "AAG-Book2PDF.exe") @("--self-test", ('"' + (Join-Path $Base "installed-evidence") + '"'))
$Evidence = Get-Content (Join-Path $Base "installed-evidence/evidence.json") -Raw | ConvertFrom-Json
if ($Evidence.status -ne "PASS" -or !$Evidence.frozen -or $Evidence.os -ne "win32") { throw "Installed application acceptance failed" }
# An ordinary PDF placed in the application folder must survive uninstall.
$Sentinel = Join-Path $Install "user-document.pdf"
Set-Content $Sentinel "user-data-sentinel"
Run-Setup (Join-Path $Install "unins000.exe") @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
if (Test-Path $ProgId) { throw "ProgID left behind" }
if (Test-Path (Join-Path $Install "AAG-Book2PDF.exe")) { throw "Application not removed" }
if (!(Test-Path $Sentinel)) { throw "Uninstaller deleted user data" }
if ((Get-ItemPropertyValue $Settings "acceptance-sentinel") -ne "preserve") { throw "User settings removed" }
$DefaultAfter = Get-ItemProperty "$Classes\.book" -ErrorAction SilentlyContinue
$After = if ($DefaultAfter) { $DefaultAfter.'(default)' } else { $null }
if ($After -ne $BookDefault) { throw "Default application was changed" }
Remove-Item $Settings -Recurse
Write-Host "INSTALL=PASS UNINSTALL=PASS OPEN_WITH_REGISTRATION=PASS USER_DATA_PRESERVED=PASS DOUBLE_CLICK=NOT_PROVEN"
