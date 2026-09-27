param(
  [Parameter(Mandatory)][string]$Version,
  [Parameter(Mandatory)][string]$PayloadDir,
  [Parameter(Mandatory)][string]$HelperDir,
  [Parameter(Mandatory)][string]$DistDir
)
$ErrorActionPreference = 'Stop'
$clientRoot = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $clientRoot -Parent
if (Test-Path (Join-Path $repoRoot '.tools/dotnet/dotnet.exe')) { $env:DOTNET_ROOT = Join-Path $repoRoot '.tools/dotnet' }
$dotnet = if (Test-Path (Join-Path $repoRoot '.tools/dotnet/dotnet.exe')) { Join-Path $repoRoot '.tools/dotnet/dotnet.exe' } else { (Get-Command dotnet).Source }
$python = if (Test-Path (Join-Path $repoRoot '.venv/Scripts/python.exe')) { Join-Path $repoRoot '.venv/Scripts/python.exe' } else { (Get-Command python).Source }
$wix = Join-Path $repoRoot '.tools/wix/wix.exe'
if (-not (Test-Path $wix)) { $wix = (Get-Command wix).Source }
$PayloadDir = (Resolve-Path $PayloadDir).Path
$HelperDir = (Resolve-Path $HelperDir).Path
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$DistDir = (Resolve-Path $DistDir).Path
$runtime = Get-Content (Join-Path $PayloadDir 'SNUETL.runtimeconfig.json') -Raw | ConvertFrom-Json
if (-not $runtime.runtimeOptions.includedFrameworks -or $runtime.runtimeOptions.frameworks) {
  throw 'The release must be published with --self-contained true; users must not need a separate .NET installation'
}
$versionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $PayloadDir 'SNUETL.exe'))
if (-not $versionInfo.ProductVersion.StartsWith($Version + '.') -and $versionInfo.ProductVersion -ne $Version -and -not $versionInfo.ProductVersion.StartsWith($Version + '+')) {
  throw 'Payload version does not match the requested release'
}

# MSI must remove each nested runtime/browser directory after removing its files.
# These entries remove empty folders only; they never recursively delete user files.
$includeFile = Join-Path (Split-Path $PayloadDir -Parent) 'RemoveFolders.wxi'
$xml = [System.Text.StringBuilder]::new()
[void]$xml.AppendLine('<Include xmlns="http://wixtoolset.org/schemas/v4/wxs">')
$index = 0
Get-ChildItem -LiteralPath $PayloadDir -Directory -Recurse | Sort-Object FullName -Descending | ForEach-Object {
  $relative = [IO.Path]::GetRelativePath($PayloadDir, $_.FullName)
  $escaped = [Security.SecurityElement]::Escape($relative)
  [void]$xml.AppendLine("  <RemoveFolder Id='PayloadFolder$index' Directory='BINDIR' Subdirectory='$escaped' On='uninstall' />")
  $index++
}
[void]$xml.AppendLine('</Include>')
[IO.File]::WriteAllText($includeFile, $xml.ToString(), [Text.UTF8Encoding]::new($false))
$msi = Join-Path $DistDir 'SNUETL.msi'
$setup = Join-Path $DistDir 'SNUETLSetup.exe'
& $wix build -arch x64 -d "ProductVersion=$Version" -d "PayloadDir=$PayloadDir" `
  -d "ShutdownHelper=$(Join-Path $HelperDir 'Snuetl.Windows.Setup.exe')" -d "RemoveFoldersFile=$includeFile" `
  (Join-Path $PSScriptRoot 'Product.wxs') -out $msi
if ($LASTEXITCODE -ne 0) { throw 'Could not build the MSI' }
& (Join-Path $clientRoot 'tests/verify_installer.ps1') -Msi $msi -Version $Version -MinimumFolderCount ($index + 3)
if ($env:SNUETL_SIGN_COMMAND) {
  & $env:SNUETL_SIGN_COMMAND $msi
  if ($LASTEXITCODE -ne 0) { throw 'MSI signing failed' }
}
$component = Get-Content (Join-Path $PayloadDir "signin-component.json") -Raw | ConvertFrom-Json
$sizes = "{0:N1} MB download / {1:N1} MB installed" -f ($component.DownloadBytes / 1MB), ($component.InstalledBytes / 1MB)
& $wix build -arch x64 -ext WixToolset.BootstrapperApplications.wixext `
  -d "ThemeFile=$(Join-Path $PSScriptRoot 'Theme.xml')" -d "SignInSizes=$sizes" -d "ProductVersion=$Version" -d "MsiPath=$msi" (Join-Path $PSScriptRoot 'Bundle.wxs') -out $setup
if ($LASTEXITCODE -ne 0) { throw 'Could not build the setup EXE' }
if ($env:SNUETL_SIGN_COMMAND) {
  & $env:SNUETL_SIGN_COMMAND $setup
  if ($LASTEXITCODE -ne 0) { throw 'Setup signing failed' }
}
$release = [ordered]@{
  version = $Version
  builtAtUtc = [DateTime]::UtcNow.ToString('o')
  architecture = 'x64'
  coreDownloadBytes = (Get-Item $setup).Length
  coreInstalledBytes = (Get-ChildItem $PayloadDir -Recurse -File | Measure-Object Length -Sum).Sum
  signInComponent = $component
  setupSha256 = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLowerInvariant()
  msiSha256 = (Get-FileHash -Algorithm SHA256 $msi).Hash.ToLowerInvariant()
  backendVersion = (& $python -c 'import snuetl; print(snuetl.__version__)')
  python = (& $python --version)
  dotnet = (& $dotnet --version)
}
$release | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $DistDir 'release.json')
Copy-Item $setup (Join-Path $DistDir "SNUETLSetup-$Version.exe") -Force
$checksumFiles = @($setup, $msi, (Join-Path $DistDir "SNUETLSetup-$Version.exe"))
$addonPath = Join-Path $DistDir "SNUETL-SignIn-$($component.Version)-win-x64.zip"
if (Test-Path -LiteralPath $addonPath) { $checksumFiles += $addonPath }
$checksumFiles | ForEach-Object { $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_; "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($_))" } | Set-Content -Encoding ascii (Join-Path $DistDir 'SHA256SUMS.txt')
Get-FileHash -Algorithm SHA256 $setup | Format-List
