$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location $repoRoot

python -m pip install -e '.[codex-build]'
if ($LASTEXITCODE -ne 0) { throw 'Could not install build requirements' }
$env:PLAYWRIGHT_BROWSERS_PATH = '0'
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Could not install the bundled browser' }
pyinstaller --clean --noconfirm --onedir --name snuetl-codex --paths src `
  --distpath windows-client/codex-helper/dist --workpath windows-client/codex-helper/build --specpath windows-client/codex-helper/build `
  --hidden-import keyring.backends.Windows --collect-data tzdata `
  --exclude-module snuetl.lms_session `
  src/snuetl/codex_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Could not build the helper' }

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
  dotnet tool install --global wix --version 5.0.2
  if ($LASTEXITCODE -ne 0) { throw 'Could not install WiX' }
  $env:PATH += ";$env:USERPROFILE\.dotnet\tools"
}
$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
$payload = (Resolve-Path 'windows-client/codex-helper/dist/snuetl-codex').Path
$skill = (Resolve-Path '.agents/skills/snuetl/SKILL.md').Path
wix build -arch x64 -d "ProductVersion=$version" -d "PayloadDir=$payload" `
  -d "SkillFile=$skill" windows-client/codex-helper/SNUETL-Codex.wxs `
  -out "windows-client/codex-helper/dist/snuetl-codex-windows-x64.msi"
if ($LASTEXITCODE -ne 0) { throw 'Could not build the MSI' }
