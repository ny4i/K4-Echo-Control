<#
.SYNOPSIS
    Install the K4 Echo Control bridge on Windows as an auto-starting task.

.DESCRIPTION
    Creates a virtualenv under C:\Program Files\k4echo, copies the bridge code,
    seeds a config file in C:\ProgramData\k4echo, and registers a scheduled task
    that starts the bridge at boot and restarts it if it stops.

    Run from an elevated PowerShell prompt, from the root of the repository:

        .\bridge\windows\install-windows.ps1

.PARAMETER InstallDir
    Where the code and virtualenv go.

.PARAMETER RunAsUser
    Account the bridge runs as. Defaults to the machine's LocalService-style
    built-in NETWORK SERVICE, which can open outbound sockets but little else.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\k4echo",
    [string]$ConfigDir  = "$env:ProgramData\k4echo",
    [string]$RunAsUser  = "NT AUTHORITY\NETWORK SERVICE",
    [string]$TaskName   = "K4EchoBridge"
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated (Administrator) PowerShell prompt."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Write-Host "==> installing from $repoRoot"

# --- code -------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$target = Join-Path $InstallDir "k4echo"
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
Copy-Item -Recurse (Join-Path $repoRoot "k4echo") $target
Get-ChildItem -Path $target -Recurse -Filter "__pycache__" -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# --- virtualenv -------------------------------------------------------------
$python = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "==> creating virtualenv"
    & python -m venv (Join-Path $InstallDir "venv")
}
Write-Host "==> installing dependencies"
& $python -m pip install --quiet --upgrade pip
& $python -m pip install --quiet -r (Join-Path $repoRoot "bridge\requirements.txt")

# --- configuration ----------------------------------------------------------
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$configFile = Join-Path $ConfigDir "bridge.ini"
if (-not (Test-Path $configFile)) {
    Copy-Item (Join-Path $repoRoot "bridge\bridge.ini.example") $configFile
    Write-Host "    wrote $configFile -- EDIT IT before starting the bridge"
} else {
    Write-Host "    $configFile already exists, left untouched"
}

# The config and any IoT certificates hold the bridge's credentials: readable
# by the service account and administrators, nobody else.
icacls $ConfigDir /inheritance:r /grant:r "$($RunAsUser):(OI)(CI)(RX)" `
    "BUILTIN\Administrators:(OI)(CI)F" "NT AUTHORITY\SYSTEM:(OI)(CI)F" | Out-Null

# --- scheduled task ---------------------------------------------------------
Write-Host "==> registering scheduled task '$TaskName'"
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m k4echo.bridge --config `"$configFile`"" `
    -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
# Built-in accounts such as NETWORK SERVICE need the ServiceAccount logon
# type; without it Register-ScheduledTask asks for a password.
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser `
    -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host @"

Installed.

  1. Edit $configFile  (at minimum: [radio] host)
  2. Check the radio is reachable:
       & "$python" -m k4echo.bridge --config "$configFile" --selftest
  3. Start it now (it will also start at every boot):
       Start-ScheduledTask -TaskName $TaskName
  4. Watch it:
       Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo

  To remove:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false
"@
