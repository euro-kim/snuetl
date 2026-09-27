param([string]$Msi, [string]$Version, [int]$MinimumFolderCount)
$ErrorActionPreference = 'Stop'
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $installer.OpenDatabase((Resolve-Path $Msi).Path, 0)
function Read-Table([string]$query, [string[]]$columns) {
  $view = $db.OpenView($query)
  $view.Execute()
  while ($record = $view.Fetch()) {
    $row = [ordered]@{}
    for ($i = 0; $i -lt $columns.Count; $i++) { $row[$columns[$i]] = $record.StringData($i + 1) }
    [pscustomobject]$row
  }
  $view.Close()
}
try {
  $properties = @(Read-Table 'SELECT `Property`,`Value` FROM `Property`' @('Name','Value'))
  if (($properties | Where-Object Name -EQ 'ProductVersion').Value -ne $Version) { throw 'MSI version mismatch' }
  $actions = @(Read-Table 'SELECT `Action`,`Condition`,`Sequence` FROM `InstallExecuteSequence`' @('Action','Condition','Sequence'))
  $cleanup = $actions | Where-Object Action -EQ 'UnregisterSyncRoot'
  if ($cleanup.Condition -ne 'REMOVE~="ALL" AND NOT UPGRADINGPRODUCTCODE') { throw 'Uninstall cleanup must not run during upgrades' }
  $stop = $actions | Where-Object Action -EQ 'StopSNUETL'
  $validate = $actions | Where-Object Action -EQ 'InstallValidate'
  if ([int]$stop.Sequence -ge [int]$validate.Sequence) { throw 'Client must stop before files are validated/replaced' }
  $upgrade = $actions | Where-Object Action -EQ 'RemoveExistingProducts'
  $execute = $actions | Where-Object Action -EQ 'InstallExecute'
  if ([int]$upgrade.Sequence -le [int]$execute.Sequence) { throw 'Legacy upgrade compatibility requires late removal' }
  $folders = @(Read-Table 'SELECT `FileKey`,`FileName` FROM `RemoveFile`' @('Key','FileName'))
  if (@($folders | Where-Object FileName -EQ '').Count -lt $MinimumFolderCount) { throw 'MSI leaves runtime directories behind' }
  $shortcutProperties = @(Read-Table 'SELECT `PropertyKey`,`PropVariantValue` FROM `MsiShortcutProperty`' @('Key','Value'))
  if (-not ($shortcutProperties | Where-Object { $_.Key -eq 'System.AppUserModel.ID' -and $_.Value -eq 'SNUETL.Windows' })) { throw 'Notification identity is missing from the Start Menu shortcut' }
  if (-not ($shortcutProperties | Where-Object Key -EQ 'System.AppUserModel.ToastActivatorCLSID')) { throw 'Notification activation identity is missing' }
  if (($properties | Where-Object Name -EQ 'INSTALLSIGNIN').Value -ne '1') { throw 'Automatic API setup must be checked by default' }
  Write-Host 'Installer checks passed: version, upgrade isolation, shutdown sequence and runtime-folder cleanup.'
} finally {
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
}
