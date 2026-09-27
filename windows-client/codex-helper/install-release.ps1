param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = 'Stop'

if ($Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$') {
  throw 'Version must look like v1.2.3'
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
  throw 'This pilot installer supports native Windows x64 only'
}

$archive = 'snuetl-codex-windows-x64.msi'
$base = "https://github.com/euro-kim/snuetl/releases/download/$Version"
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("snuetl-codex-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
  $msi = Join-Path $temporary $archive
  $sums = Join-Path $temporary 'SHA256SUMS.txt'
  Invoke-WebRequest "$base/$archive" -OutFile $msi
  Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile $sums
  $line = Get-Content $sums | Where-Object { $_ -match "^[0-9a-fA-F]{64}\s+$([regex]::Escape($archive))$" } | Select-Object -First 1
  if (-not $line) { throw 'Release checksum is missing' }
  $expected = ($line -split '\s+')[0].ToLowerInvariant()
  $actual = (Get-FileHash -Algorithm SHA256 $msi).Hash.ToLowerInvariant()
  if ($expected -ne $actual) { throw 'Release checksum verification failed' }
  $process = Start-Process msiexec.exe -ArgumentList @('/i', ('"' + $msi + '"')) -Wait -PassThru
  if ($process.ExitCode -ne 0) { throw "Installer exited with code $($process.ExitCode)" }
}
finally {
  Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
