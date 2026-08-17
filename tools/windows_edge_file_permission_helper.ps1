param(
  [Parameter(Mandatory = $true)]
  [string]$FolderPath,

  [int]$TimeoutSeconds = 120,

  [int]$PollIntervalMilliseconds = 500,

  [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

function Write-HelperLog {
  param([string]$Message)
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message
  Write-Host $line
  if ($LogPath) {
    Add-Content -LiteralPath $LogPath -Value $line
  }
}

function Get-Pattern {
  param($Element, $PatternId)
  try {
    return $Element.GetCurrentPattern($PatternId)
  } catch {
    return $null
  }
}

function Invoke-Element {
  param($Element)
  if ($null -eq $Element) {
    return $false
  }

  $invokePattern = Get-Pattern $Element ([System.Windows.Automation.InvokePattern]::Pattern)
  if ($null -ne $invokePattern) {
    $invokePattern.Invoke()
    return $true
  }

  try {
    $Element.SetFocus()
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    return $true
  } catch {
    return $false
  }
}

function Set-ElementValue {
  param($Element, [string]$Value)
  if ($null -eq $Element) {
    return $false
  }

  $valuePattern = Get-Pattern $Element ([System.Windows.Automation.ValuePattern]::Pattern)
  if ($null -ne $valuePattern) {
    $valuePattern.SetValue($Value)
    return $true
  }

  try {
    Add-Type -AssemblyName System.Windows.Forms
    $Element.SetFocus()
    [System.Windows.Forms.Clipboard]::SetText($Value)
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    return $true
  } catch {
    return $false
  }
}

function Find-DescendantByNameRegex {
  param(
    $Root,
    [string]$Regex,
    $ControlType = $null
  )

  $condition = [System.Windows.Automation.Condition]::TrueCondition
  if ($null -ne $ControlType) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      $ControlType
    )
  }

  $items = $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
  foreach ($item in $items) {
    $name = $item.Current.Name
    if ($name -match $Regex) {
      return $item
    }
  }
  return $null
}

function Find-ElementByNameRegex {
  param(
    $Root,
    [string]$Regex,
    $ControlType = $null,
    $Scope = [System.Windows.Automation.TreeScope]::Descendants
  )

  $condition = [System.Windows.Automation.Condition]::TrueCondition
  if ($null -ne $ControlType) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      $ControlType
    )
  }

  $items = $Root.FindAll($Scope, $condition)
  foreach ($item in $items) {
    $name = $item.Current.Name
    if ($name -match $Regex) {
      return $item
    }
  }
  return $null
}

function Get-ParentWindowOrSelf {
  param($Element)
  if ($null -eq $Element) {
    return $null
  }

  $current = $Element
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  while ($null -ne $current) {
    if ($current.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window) {
      return $current
    }
    $current = $walker.GetParent($current)
  }
  return $Element
}

function Find-DialogByNameRegex {
  param([string]$Regex)
  $root = [System.Windows.Automation.AutomationElement]::RootElement
  $condition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Window
  )
  $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
  foreach ($window in $windows) {
    if ($window.Current.Name -match $Regex) {
      return $window
    }
  }

  $allWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
  foreach ($window in $allWindows) {
    if ($window.Current.Name -match $Regex) {
      return $window
    }
  }

  return $null
}

function Write-VisibleUiDiagnostics {
  param([string]$Reason)

  Write-HelperLog "diagnostics: $Reason"
  $root = [System.Windows.Automation.AutomationElement]::RootElement
  $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $diagnosticItems = @()
  foreach ($item in $items) {
    $name = $item.Current.Name
    if ([string]::IsNullOrWhiteSpace($name)) {
      continue
    }
    if ($name -match "Select|Folder|Allow|edit files|xiaohongshu|xhs-files|文件夹|允许|选择") {
      $diagnosticItems += ("{0} | {1}" -f $item.Current.ControlType.ProgrammaticName, $name)
      if ($diagnosticItems.Count -ge 40) {
        break
      }
    }
  }
  if ($diagnosticItems.Count -eq 0) {
    Write-HelperLog "diagnostics: no matching UIA elements found"
  } else {
    foreach ($diagnosticItem in $diagnosticItems) {
      Write-HelperLog "diagnostics match: $diagnosticItem"
    }
  }
}

function Handle-FolderPicker {
  param([string]$TargetFolder)

  $dialog = Find-DialogByNameRegex "Select where this site can save changes|Select Folder|Browse For Folder|选择文件夹"
  if ($null -eq $dialog) {
    return $false
  }

  Write-HelperLog "folder picker detected: $($dialog.Current.Name)"
  try {
    $dialog.SetFocus()
  } catch {}

  $edit = $null
  try {
    $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
    if ($null -ne $focused -and $focused.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit) {
      $edit = $focused
      Write-HelperLog "using focused edit control"
    }
  } catch {}

  if ($null -eq $edit) {
    $edit = Find-DescendantByNameRegex $dialog "Folder|File name|文件夹|文件名|.*" ([System.Windows.Automation.ControlType]::Edit)
  }

  if ($null -ne $edit) {
    Write-HelperLog "setting folder picker edit value"
    [void](Set-ElementValue $edit $TargetFolder)
  } else {
    Write-HelperLog "folder picker edit control not found; using keyboard fallback"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Clipboard]::SetText($TargetFolder)
    [System.Windows.Forms.SendKeys]::SendWait("^v")
  }

  Start-Sleep -Milliseconds 250

  $selectButton = Find-DescendantByNameRegex $dialog "^(Select Folder|Open|OK|Choose|选择文件夹|确定)$" ([System.Windows.Automation.ControlType]::Button)
  if ($null -ne $selectButton) {
    Write-HelperLog "clicking folder picker button: $($selectButton.Current.Name)"
    return Invoke-Element $selectButton
  }

  Write-HelperLog "folder picker button not found; pressing Enter"
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
  Start-Sleep -Milliseconds 500
  $remainingDialog = Find-DialogByNameRegex "Select where this site can save changes|Select Folder|Browse For Folder"
  if ($null -eq $remainingDialog) {
    Write-HelperLog "folder picker closed after pressing Enter"
    return $true
  }
  Write-HelperLog "folder picker still open after pressing Enter; will retry Select Folder"
  return $false
}

function Handle-AllowEditFilesPrompt {
  $root = [System.Windows.Automation.AutomationElement]::RootElement
  $promptText = Find-ElementByNameRegex $root "Allow this site to edit files\?|will be able to edit files|edit files in xhs-files-downloads"
  if ($null -eq $promptText) {
    return $false
  }

  Write-HelperLog "Edge file edit permission prompt detected"
  $allowButton = Find-DescendantByNameRegex $root "^Allow$" ([System.Windows.Automation.ControlType]::Button)
  if ($null -ne $allowButton) {
    Write-HelperLog "clicking Allow"
    return Invoke-Element $allowButton
  }

  Write-HelperLog "Allow button not found; using keyboard fallback"
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.SendKeys]::SendWait("%a")
  return $true
}

if (-not (Test-Path -LiteralPath $FolderPath)) {
  New-Item -ItemType Directory -Force -Path $FolderPath | Out-Null
}

if ($LogPath) {
  $logDir = Split-Path -Parent $LogPath
  if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  }
  Set-Content -LiteralPath $LogPath -Value ""
}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Write-HelperLog "watching Edge file permission prompts; folder=$FolderPath; timeoutSeconds=$TimeoutSeconds"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$handledFolder = $false
$handledAllow = $false
$diagnosticsWritten = $false
$startedAt = Get-Date

while ((Get-Date) -lt $deadline) {
  if (-not $handledFolder) {
    $handledFolder = Handle-FolderPicker $FolderPath
  }

  if (-not $handledAllow) {
    $handledAllow = Handle-AllowEditFilesPrompt
  }

  if ($handledFolder -and $handledAllow) {
    Write-HelperLog "handled folder picker and allow prompt"
    exit 0
  }

  if (-not $diagnosticsWritten -and ((Get-Date) - $startedAt).TotalSeconds -ge 3) {
    Write-VisibleUiDiagnostics "no permission prompt detected after 3 seconds"
    $diagnosticsWritten = $true
  }

  Start-Sleep -Milliseconds $PollIntervalMilliseconds
}

Write-HelperLog "timeout; handledFolder=$handledFolder; handledAllow=$handledAllow"
exit 0
