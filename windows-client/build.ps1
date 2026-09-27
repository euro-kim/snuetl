param([string]$Configuration = 'Release', [switch]$BuildSignInAddon)
$ErrorActionPreference = 'Stop'

$clientRoot = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $clientRoot '..')).Path
$buildRoot = Join-Path $clientRoot 'build'
$publishRoot = Join-Path $buildRoot 'publish'
$distRoot = Join-Path $clientRoot 'dist'
$backendDist = Join-Path $buildRoot 'backend-dist'

if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
  throw 'The pilot build supports Windows x64 only'
}

# Prefer isolated tools when present; CI can continue to supply them through PATH.
foreach ($toolDirectory in @('.venv\Scripts', '.tools\dotnet', '.tools\wix')) {
  $toolPath = Join-Path $repoRoot $toolDirectory
  if (Test-Path $toolPath) { $env:PATH = "$toolPath;$env:PATH" }
}
if (Test-Path (Join-Path $repoRoot '.tools\dotnet\dotnet.exe')) {
  $env:DOTNET_ROOT = Join-Path $repoRoot '.tools\dotnet'
}
Set-Location $repoRoot
$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Could not read the project version' }
$backendVersion = python -c 'import snuetl; print(snuetl.__version__)'
if ($backendVersion -ne $version) { throw 'pyproject.toml and snuetl.__version__ must match before building a release' }
if (([IO.Path]::GetFullPath($buildRoot)) -ne (Join-Path $clientRoot 'build')) {
  throw 'Build output must stay inside windows-client/build'
}
if (Test-Path $buildRoot) {
  Remove-Item -Recurse -Force $buildRoot
}
python -m pip install -e '.[test,codex-build]'
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python build requirements' }
python -m pytest -q windows-client/tests/backend tests/test_codex_desktop.py tests/test_canvas_api.py
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
if ($BuildSignInAddon) {
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $buildRoot 'signin-browser'
python -m playwright install --no-shell chromium
if ($LASTEXITCODE -ne 0) { throw 'Could not install the bundled setup browser' }

python -m PyInstaller --clean --noconfirm (Join-Path $clientRoot 'backend/signin.spec') `
  --distpath (Join-Path $buildRoot 'signin-dist') --workpath (Join-Path $buildRoot 'signin-pyinstaller')
if ($LASTEXITCODE -ne 0) { throw 'Could not build optional sign-in component' }
$signInRoot = Join-Path $buildRoot 'signin-dist/snuetl-signin'
$browserTarget = Join-Path $signInRoot '_internal/playwright/driver/package/.local-browsers'
New-Item -ItemType Directory -Force $browserTarget | Out-Null
Get-ChildItem $env:PLAYWRIGHT_BROWSERS_PATH -Directory | Where-Object { $_.Name -like 'chromium-*' -or $_.Name -like 'winldd-*' } | Copy-Item -Destination $browserTarget -Recurse
& (Join-Path $signInRoot "snuetl-signin.exe") --self-test
if ($LASTEXITCODE -ne 0) { throw "Optional browser self-test failed" }
New-Item -ItemType Directory -Force $distRoot | Out-Null
$addonZip = Join-Path $distRoot "SNUETL-SignIn-$version-win-x64.zip"
python (Join-Path $clientRoot 'installer/package_addon.py') $signInRoot $addonZip $version (Join-Path $buildRoot 'signin-component.json')
if ($LASTEXITCODE -ne 0) { throw 'Could not package optional sign-in' }
} else {
  New-Item -ItemType Directory -Force $buildRoot | Out-Null
  Copy-Item (Join-Path $clientRoot 'installer/signin-component.json') (Join-Path $buildRoot 'signin-component.json')
}
$env:PLAYWRIGHT_BROWSERS_PATH = '0'

python -m PyInstaller --clean --noconfirm (Join-Path $clientRoot 'backend/backend.spec') `
  --distpath $backendDist --workpath (Join-Path $buildRoot 'pyinstaller')
if ($LASTEXITCODE -ne 0) { throw 'Could not build the backend' }
python (Join-Path $clientRoot 'tests/smoke_backend.py') `
  (Join-Path $backendDist 'snuetl-windows-backend/snuetl-windows-backend.exe')
if ($LASTEXITCODE -ne 0) { throw 'Packaged backend smoke test failed' }

dotnet test (Join-Path $clientRoot 'tests/Snuetl.Windows.Tests/Snuetl.Windows.Tests.csproj') `
  --configuration $Configuration
if ($LASTEXITCODE -ne 0) { throw 'Windows client tests failed' }
dotnet publish (Join-Path $clientRoot 'src/Snuetl.Windows.App/Snuetl.Windows.App.csproj') `
  --configuration $Configuration --runtime win-x64 --self-contained true `
  -p:PublishSingleFile=false -p:Version=$version -o $publishRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not publish the Windows application' }

$helperRoot = Join-Path $buildRoot 'setup-helper'
dotnet publish (Join-Path $clientRoot 'src/Snuetl.Windows.Setup/Snuetl.Windows.Setup.csproj') `
  --configuration $Configuration -p:Version=$version -o $helperRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not build the setup shutdown helper' }

$previousBackendCommand = $env:SNUETL_BACKEND_COMMAND
try {
  $pythonExecutable = (Get-Command python).Source
  $fixture = Join-Path $clientRoot 'tests/Snuetl.Windows.Integration/test_backend.py'
  $env:SNUETL_BACKEND_COMMAND = '"' + $pythonExecutable + '" -X utf8 "' + $fixture + '"'
  dotnet run --project (Join-Path $clientRoot 'tests/Snuetl.Windows.Integration') `
    --configuration $Configuration -- (Join-Path $buildRoot 'ui-preview')
  if ($LASTEXITCODE -ne 0) { throw 'Cloud Files lifecycle integration tests failed' }
} finally {
  $env:SNUETL_BACKEND_COMMAND = $previousBackendCommand
}


Copy-Item (Join-Path $buildRoot 'signin-component.json') (Join-Path $publishRoot 'signin-component.json') -Force
Copy-Item (Join-Path $backendDist 'snuetl-windows-backend') `
  (Join-Path $publishRoot 'backend') -Recurse -Force

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
  dotnet tool install --tool-path (Join-Path $repoRoot '.tools/wix') wix --version 5.0.2
  if ($LASTEXITCODE -ne 0) { throw 'Could not install WiX' }
  $env:PATH += ";$(Join-Path $repoRoot '.tools/wix')"
}
wix extension add --global WixToolset.BootstrapperApplications.wixext/5.0.2
if ($LASTEXITCODE -ne 0) { throw 'Could not install the WiX bundle extension' }
& (Join-Path $clientRoot 'installer/package.ps1') -Version $version -PayloadDir $publishRoot -HelperDir $helperRoot -DistDir $distRoot
