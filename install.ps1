#Requires -Version 5.1
<#
.SYNOPSIS
  k3s-join for Windows — runs a Linux worker for the Kituwa k3s cluster in WSL2.

.DESCRIPTION
  k3s is Linux-only. This script prepares WSL2 with systemd, then runs the same
  installer Linux uses inside that small Linux environment. Your Windows files
  are not shared into the cluster; the node only runs cluster workloads.

.EXAMPLE
  irm https://k3s.kituwa.com/install.ps1 | iex

.EXAMPLE
  .\install.ps1 -Code 'k3s1....'

.EXAMPLE
  .\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
  [string]$Code = "",
  [string]$CodeFile = "",
  [string]$Distro = "Ubuntu",
  [string]$NodeName = $env:COMPUTERNAME,
  [switch]$Uninstall,
  [switch]$RemoveDistro,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$InstallerUrl = "https://k3s.kituwa.com/install.sh"

function Say($m)  { Write-Host $m }
function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ok   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  warn $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host ""; Write-Host "error $m" -ForegroundColor Red; exit 1 }

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Usage {
  @"
k3s-join for Windows

  -Code 'k3s1...'   join code from the cluster owner (prompted if omitted)
  -CodeFile PATH    read the code from a file
  -Distro NAME      WSL distribution to use (default Ubuntu)
  -NodeName NAME    node name in the cluster (default this PC's name)
  -Uninstall        remove the agent from the WSL distribution
  -RemoveDistro     with -Uninstall, also delete the WSL distribution
  -DryRun           show every action, change nothing
"@
}

# ---------------------------------------------------------------- helpers --
function Wsl-Exists {
  param([string]$Name)
  $out = (& wsl.exe -l -q) 2>$null
  if (-not $out) { return $false }
  $list = ($out -join "`n") -replace "`0", "" -split "`r?`n" | ForEach-Object { $_.Trim() }
  return ($list -contains $Name)
}

function Invoke-Bash {
  <# Runs a bash script inside the distribution and returns its output.
     The script goes in over stdin, so no PowerShell quoting can break it. #>
  param([string]$Script, [switch]$AsRoot)
  if ($AsRoot) {
    return (($Script | & wsl.exe -d $Distro -- sudo -n bash -s) 2>$null | Out-String)
  }
  return (($Script | & wsl.exe -d $Distro -- bash -s) 2>$null | Out-String)
}

function Get-BashExit {
  <# Same, but returns only the exit status. #>
  param([string]$Script, [switch]$AsRoot)
  $out = ""
  if ($AsRoot) {
    $out = ($Script | & wsl.exe -d $Distro -- sudo -n bash -s) 2>$null
  } else {
    $out = ($Script | & wsl.exe -d $Distro -- bash -s) 2>$null
  }
  return $LASTEXITCODE
}

function Write-CodeToWsl {
  param([string]$Value)
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
  $script = "umask 077; mkdir -p /root/.kituwa; echo '$b64' | base64 -d > /root/.kituwa/code; chmod 600 /root/.kituwa/code"
  return (Get-BashExit -Script $script -AsRoot)
}

# ------------------------------------------------------------------ start --
Say ""
Say "k3s-join for Windows — WSL2 worker for the Kituwa k3s cluster"
Say "source: https://github.com/CrystalRockMarket/k3s-join (MIT)"

if (-not (Test-Admin)) {
  Die @"
this needs an elevated PowerShell. Open Start, type PowerShell, right-click
'Windows PowerShell' and choose 'Run as administrator', then run:

  irm https://k3s.kituwa.com/install.ps1 | iex
"@
}

Step "checking Windows features"
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
  Say "  WSL is not installed. Installing WSL2 with $Distro (this can take a while,"
  Say "  and Windows may ask you to reboot)."
  if (-not $DryRun) {
    & wsl.exe --install -d $Distro --no-launch
    if ($LASTEXITCODE -ne 0) { Die "wsl --install failed. Enable the 'Virtual Machine Platform' and 'Windows Subsystem for Linux' features, reboot, and run this again." }
    Warn "reboot if Windows asks, then run this installer again."
  }
} else {
  Ok "wsl.exe present"
  if (-not $DryRun) {
    & wsl.exe --set-default-version 2 *> $null
    $v = (& wsl.exe --status) 2>$null | Out-String
    if ($v -match "WSL version:\s*1" -or $v -match "Default Version:\s*1") {
      Warn "default WSL version is 1; switching to 2"
      & wsl.exe --set-default-version 2 *> $null
    }
  }
}

Step "checking the $Distro distribution"
if (-not (Wsl-Exists $Distro)) {
  Say "  $Distro is not installed yet; installing it now (the Microsoft Store may open)."
  if (-not $DryRun) {
    & wsl.exe --install -d $Distro --no-launch
    if ($LASTEXITCODE -ne 0) { Die "could not install $Distro. Install it from the Microsoft Store and run this again." }
    Say "  starting $Distro once to finish its first-run setup"
    & wsl.exe -d $Distro -- bash -lc "true" 2>$null
  }
} else { Ok "$Distro present" }

if (-not $DryRun) {
  $probe = (Invoke-Bash -Script "echo ready").Trim()
  if ($probe -ne "ready") {
    Die ("could not run bash in " + $Distro + ". Open '" + $Distro + "' from the Start menu once, finish its first-run user setup, then run this installer again.")
  }
}
Ok "bash works in $Distro"

Step "making sure systemd is enabled inside WSL"
# k3s is a systemd service; WSL only runs systemd when /etc/wsl.conf says so.
$conf = Invoke-Bash -Script "cat /etc/wsl.conf 2>/dev/null || true"
if ($conf -notmatch "systemd\s*=\s*true") {
  if ($DryRun) {
    Say "  (dry run) would set [boot] systemd=true in /etc/wsl.conf and restart WSL"
  } else {
    Say "  enabling systemd and restarting the Linux environment"
    $null = Invoke-Bash -Script "printf '[boot]\nsystemd=true\n' > /etc/wsl.conf" -AsRoot
    & wsl.exe --shutdown
    Start-Sleep -Seconds 3
    $okSys = (Invoke-Bash -Script "systemctl is-system-running >/dev/null 2>&1; echo `$?").Trim()
    if ($okSys -ne "0") { Warn "systemd did not come up; reboot Windows and run this again." }
  }
} else { Ok "systemd already enabled" }

# --------------------------------------------------------------- uninstall --
if ($Uninstall) {
  Step "removing the cluster agent from $Distro"
  if (-not $DryRun) {
    $null = Invoke-Bash -Script "if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then sudo -n /usr/local/bin/k3s-agent-uninstall.sh; fi; sudo -n rm -rf /etc/rancher/k3s /root/.kituwa" -AsRoot
    $null = Invoke-Bash -Script "if command -v tailscale >/dev/null 2>&1; then sudo -n tailscale logout || true; fi" -AsRoot
  }
  if ($RemoveDistro -and -not $DryRun) {
    Say "  deleting the $Distro distribution"
    & wsl.exe --unregister $Distro
    Warn "the distribution and everything in it are gone"
  }
  Say ""
  Say "This PC is no longer part of the cluster."
  exit 0
}

# --------------------------------------------------------------- join code --
if (-not $Code -and $env:KITUWA_JOIN_CODE) { $Code = $env:KITUWA_JOIN_CODE.Trim() }
if (-not $Code -and $CodeFile) {
  if (-not (Test-Path $CodeFile)) { Die "cannot read $CodeFile" }
  $Code = (Get-Content -Raw $CodeFile).Trim()
}
if (-not $Code) {
  if ($DryRun) { $Code = "k3s1.dryrun" }
  else {
    Say ""
    Write-Host "Paste your join code (it starts with k3s1.) and press Enter: " -NoNewline
    $Code = Read-Host
  }
}
if ($Code -notlike "k3s1.*") { Die "that does not look like a join code (expected it to start with k3s1.)" }

$safeName = ($NodeName.ToLower() -replace "[^a-z0-9-]", "-") -replace "-+", "-"
$safeName = $safeName.Trim("-")
if (-not $safeName) { $safeName = "windows-node" }

Say ""
Say "  This will join this PC to the Kituwa cluster as node '$safeName'."
Say "  It runs a Linux worker inside WSL2; cluster workloads get no access to your Windows files."
Say "  It is reversible at any time with: .\install.ps1 -Uninstall"

# --------------------------------------------------------------------- run --
Step "installing the agent inside $Distro"
if ($DryRun) {
  Say "  (dry run) would write the join code to /root/.kituwa/code (0600)"
  Say "  (dry run) would run: curl -fsSL $InstallerUrl | sudo sh -s -- --code-file /root/.kituwa/code --yes --node-name $safeName"
} else {
  $rc = Write-CodeToWsl -Value $Code
  if ($rc -ne 0) { Die "could not write the join code inside $Distro" }
  $run = "set -e; curl -fsSL $InstallerUrl -o /root/.kituwa/install.sh; chmod 600 /root/.kituwa/install.sh; sudo -n sh /root/.kituwa/install.sh --code-file /root/.kituwa/code --yes --node-name '$safeName'; rm -f /root/.kituwa/code /root/.kituwa/install.sh"
  $rc = Get-BashExit -Script $run -AsRoot
  if ($rc -ne 0) {
    Die "the installer failed inside $Distro. Read the messages above; nothing on Windows was changed."
  }
  Ok "agent installed"
}

Step "keeping the Linux environment running after sign-in"
if ($DryRun) {
  Say "  (dry run) would register a logon task: wsl.exe -d $Distro --exec /bin/sh -c 'sleep infinity'"
} else {
  $taskName = "Kituwa k3s worker"
  $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if (-not $existing) {
    $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --exec /bin/sh -c `"sleep infinity`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Description "Keeps the Kituwa k3s worker VM up" | Out-Null
    Ok "logon task '$taskName' registered"
  } else { Ok "logon task already registered" }
}

Say ""
Say "────────────────────────────────────────────────────────"
Say " This PC now runs a Linux worker for the Kituwa cluster."
Say ""
Say "   node      $safeName"
Say "   distro    $Distro"
Say "   check     wsl -d $Distro -- sudo systemctl status k3s-agent"
Say "   leave     irm https://k3s.kituwa.com/install.ps1 | iex   # then choose -Uninstall"
Say "────────────────────────────────────────────────────────"
Say "Thank you for donating compute. https://k3s.kituwa.com"
