param([string]$Configuration = 'Release')
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

Set-Location $repoRoot
$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if (Test-Path $buildRoot) {
  Remove-Item -Recurse -Force $buildRoot
}
python -m pip install -e '.[test,codex-build]'
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python build requirements' }
python -m pytest -q windows-client/tests/backend
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
$env:PLAYWRIGHT_BROWSERS_PATH = '0'
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Could not install the bundled setup browser' }

pyinstaller --clean --noconfirm (Join-Path $clientRoot 'backend/backend.spec') `
  --distpath $backendDist --workpath (Join-Path $buildRoot 'pyinstaller')
if ($LASTEXITCODE -ne 0) { throw 'Could not build the backend' }

dotnet test (Join-Path $clientRoot 'tests/Snuetl.Windows.Tests/Snuetl.Windows.Tests.csproj') `
  --configuration $Configuration
if ($LASTEXITCODE -ne 0) { throw 'Windows client tests failed' }
dotnet publish (Join-Path $clientRoot 'src/Snuetl.Windows.App/Snuetl.Windows.App.csproj') `
  --configuration $Configuration --runtime win-x64 --self-contained true `
  -p:PublishSingleFile=false -p:Version=$version -o $publishRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not publish the Windows application' }

Copy-Item (Join-Path $backendDist 'snuetl-windows-backend') `
  (Join-Path $publishRoot 'backend') -Recurse -Force

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
  dotnet tool install --global wix --version 5.0.2
  if ($LASTEXITCODE -ne 0) { throw 'Could not install WiX' }
  $env:PATH += ";$env:USERPROFILE\.dotnet\tools"
}
wix extension add --global WixToolset.Bal.wixext/5.0.2
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
$msi = Join-Path $distRoot 'SNUETL.msi'
$setup = Join-Path $distRoot 'SNUETLSetup.exe'
Remove-Item $msi, $setup -Force -ErrorAction SilentlyContinue
wix build -arch x64 -d "ProductVersion=$version" -d "PayloadDir=$publishRoot" `
  (Join-Path $clientRoot 'installer/Product.wxs') -out $msi
if ($LASTEXITCODE -ne 0) { throw 'Could not build the MSI' }

wix build -arch x64 -ext WixToolset.Bal.wixext `
  -d "ProductVersion=$version" -d "MsiPath=$msi" `
  (Join-Path $clientRoot 'installer/Bundle.wxs') -out $setup
if ($LASTEXITCODE -ne 0) { throw 'Could not build the setup EXE' }

if ($env:SNUETL_SIGN_COMMAND) {
  & $env:SNUETL_SIGN_COMMAND $msi
  if ($LASTEXITCODE -ne 0) { throw 'MSI signing failed' }
  & $env:SNUETL_SIGN_COMMAND $setup
  if ($LASTEXITCODE -ne 0) { throw 'Setup signing failed' }
}

Get-FileHash -Algorithm SHA256 $setup | Format-List
