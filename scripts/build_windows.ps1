param([string]$Python = "python", [switch]$SkipTests, [string]$DjVuRuntimeDir = "", [string]$PythonAcceptance = "")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Build on native Windows x64; cross-compilation is unsupported." }
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
try {
    if (!$PythonAcceptance) { throw "First run test_windows_python.ps1 on this exact BuildKit. Supply -PythonAcceptance native-python.json." }
    $Proof = Get-Content -LiteralPath $PythonAcceptance -Raw | ConvertFrom-Json
    $ManifestPath = Join-Path $Root "BUILDKIT-MANIFEST.json"
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Proof.status -ne "PASS" -or $Proof.source_commit -ne $Manifest.source_commit -or
        $Proof.source_manifest_sha256 -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant()) {
        throw "Native Python acceptance does not match this BuildKit."
    }
    foreach ($Field in @("WINDOWS_PYTHON_GUI_START","WINDOWS_PYTHON_BKC_OPEN","WINDOWS_PYTHON_BKF_OPEN","WINDOWS_PYTHON_NEW_VARIANT_OPEN",
                        "WINDOWS_PYTHON_CONTINUOUS_SCROLL","WINDOWS_PYTHON_SINGLE_PAGE","WINDOWS_PYTHON_EXPORT","WINDOWS_PYTHON_PARTIAL_EXPORT","WINDOWS_PYTHON_PRINT_TO_PDF")) {
        if ($Proof.$Field -ne "PASS") { throw "Missing native Python acceptance: $Field" }
    }
    foreach ($File in $Manifest.files.PSObject.Properties) {
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Root $File.Name)).Hash.ToLowerInvariant() -ne $File.Value) {
            throw "Source differs from the accepted Python BuildKit: $($File.Name)"
        }
    }
    $env:PYTHONUTF8 = "1"
    $env:QT_QPA_PLATFORM = "windows"
    Invoke-Checked $Python @("-c", "import sys,struct; assert sys.platform=='win32' and struct.calcsize('P')==8; assert sys.version_info[:2] == (3,12)")
    Invoke-Checked $Python @("-m", "venv", "--clear", ".build/windows-env")
    $BuildPython = Join-Path $Root ".build/windows-env/Scripts/python.exe"
    Invoke-Checked $BuildPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked $BuildPython @("-m", "pip", "install", ".[test]", "pyinstaller>=6.16,<7", "Pillow>=11", "pefile==2024.8.26", "zstandard==0.25.0")
    if (!$DjVuRuntimeDir) {
        $DjVuRuntimeDir = Join-Path $Root ".build/djvu-runtime"
        Invoke-Checked $BuildPython @("scripts/prepare_windows_runtime.py", "--destination", $DjVuRuntimeDir)
    }
    if ($DjVuRuntimeDir) {
        $env:BOOK2PDF_DJVU_RUNTIME_DIR = (Resolve-Path $DjVuRuntimeDir).Path
        $Dll = @("libdjvulibre-21.dll", "libdjvulibre.dll", "djvulibre.dll") |
            ForEach-Object { Join-Path $env:BOOK2PDF_DJVU_RUNTIME_DIR $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (!$Dll) { throw "No DjVuLibre DLL found in supplied runtime directory" }
        $env:BOOK2PDF_DJVU_LIBRARY = $Dll
    } else {
        throw "Runtime preparation did not provide a DjVu directory."
    }
    if (!$SkipTests) { Invoke-Checked $BuildPython @("-m", "pytest", "-q") }
    Invoke-Checked $BuildPython @("-c", "from book2pdf.bkf.native import runtime_version; print(runtime_version())")
    Invoke-Checked $BuildPython @("scripts/windows_assets.py", "prepare")
    Invoke-Checked $BuildPython @("-m", "PyInstaller", "--noconfirm", "--clean", "--workpath", ".build/windows-pyinstaller", "--distpath", "dist/windows/bundle", "packaging/windows/book2pdf.spec")
    $Bundle = Join-Path $Root "dist/windows/bundle/AAG-Book2PDF"
    $Output = Join-Path $Root "dist/windows"
    # Acceptance outputs are outside dist and never included in the zip/installer.
    $Run = Join-Path $Root (".build/windows-acceptance-" + [guid]::NewGuid().ToString("N"))
    Invoke-Checked (Join-Path $Bundle "book2pdf.exe") @("--version")
    Invoke-Checked (Join-Path $Bundle "book2pdf.exe") @("--self-test", (Join-Path $Run "console"))
    $Gui = Start-Process -FilePath (Join-Path $Bundle "AAG-Book2PDF.exe") -ArgumentList @("--self-test", ('"' + (Join-Path $Run "gui") + '"')) -PassThru
    if (!$Gui.WaitForExit(300000)) { $Gui.Kill(); throw "Frozen GUI acceptance timed out" }
    if ($Gui.ExitCode -ne 0) { throw "Frozen GUI acceptance failed" }
    $Evidence = Get-Content (Join-Path $Run "gui/evidence.json") -Raw | ConvertFrom-Json
    if ($Evidence.status -ne "PASS" -or !$Evidence.frozen -or $Evidence.os -ne "win32" -or $Evidence.qt_platform -ne "windows") { throw "Not a successful native frozen GUI test" }
    # Exercise the bundled runtime, never the builder's configured external DLL.
    Remove-Item Env:BOOK2PDF_DJVU_LIBRARY -ErrorAction SilentlyContinue
    $Bkf = Join-Path $Run "native BKF fixture.book"
    Invoke-Checked $BuildPython @("scripts/windows_assets.py", "bkf-fixture", "--destination", $Bkf)
    $BkfHash = (Get-FileHash -Algorithm SHA256 $Bkf).Hash.ToLowerInvariant()
    Invoke-Checked (Join-Path $Bundle "book2pdf.exe") @("--self-test", (Join-Path $Run "bkf-console"), "--sample", $Bkf, "--expected-sha256", $BkfHash, "--expected-pages", "2")
    $BkfGui = Start-Process -FilePath (Join-Path $Bundle "AAG-Book2PDF.exe") -ArgumentList @("--self-test", ('"' + (Join-Path $Run "bkf-gui") + '"'), "--sample", ('"' + $Bkf + '"'), "--expected-sha256", $BkfHash, "--expected-pages", "2", "--expected-status", "PASS_DECODED") -PassThru
    if (!$BkfGui.WaitForExit(300000)) { $BkfGui.Kill(); throw "Frozen BKF GUI acceptance timed out" }
    if ($BkfGui.ExitCode -ne 0) { throw "Frozen BKF GUI acceptance failed" }
    $BkfEvidence = Get-Content (Join-Path $Run "bkf-gui/evidence.json") -Raw | ConvertFrom-Json
    if ($BkfEvidence.status -ne "PASS" -or $BkfEvidence.recovery_status -ne "PASS_DECODED" -or !$BkfEvidence.frozen -or $BkfEvidence.os -ne "win32" -or $BkfEvidence.qt_platform -ne "windows") { throw "Missing native frozen BKF evidence" }
    $IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $Iscc = if ($IsccCommand) { $IsccCommand.Source } else { $null }
    if (!$Iscc) { $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (!(Test-Path $Iscc)) { throw "Install Inno Setup 6 on the build machine (not the user's PC)." }
    $Version = & $BuildPython -c "from book2pdf import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) { throw "Could not determine application version" }
    Invoke-Checked $Iscc @("/DAppVersion=$Version", "/DBundleDir=$Bundle", "/DOutputPath=$Output", "packaging/windows/installer.iss")
    Invoke-Checked $BuildPython @("scripts/windows_assets.py", "release", "--bundle", $Bundle, "--destination", $Output)
    Write-Host "Built and tested native Windows runtime. Artifacts: $Output"
    Write-Host "Installer install/uninstall and physical/Microsoft printing need their separate acceptance checks."
} finally { Pop-Location }
