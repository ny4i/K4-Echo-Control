# Running the bridge on Windows

The bridge is plain Python and has no Linux-specific code, so a Windows machine
that stays on works just as well as a Raspberry Pi.

## Install

From an **elevated** PowerShell prompt, at the root of the repository:

```powershell
.\bridge\windows\install-windows.ps1
```

That creates a virtualenv in `C:\Program Files\k4echo`, seeds a config file at
`C:\ProgramData\k4echo\bridge.ini`, and registers a scheduled task that starts
the bridge at boot and restarts it if it exits.

Edit `C:\ProgramData\k4echo\bridge.ini`, then:

```powershell
& "C:\Program Files\k4echo\venv\Scripts\python.exe" -m k4echo.bridge `
    --config "C:\ProgramData\k4echo\bridge.ini" --selftest
Start-ScheduledTask -TaskName K4EchoBridge
```

## Running it by hand instead

For a first test, skip the service entirely:

```powershell
python -m k4echo.bridge --config .\bridge.ini
```

## If you prefer a real Windows service

A scheduled task is used here because it needs nothing beyond what ships with
Windows. If you would rather have a proper service entry, [NSSM](https://nssm.cc)
wraps the same command:

```powershell
nssm install K4EchoBridge "C:\Program Files\k4echo\venv\Scripts\python.exe" `
    "-m k4echo.bridge --config C:\ProgramData\k4echo\bridge.ini"
nssm set K4EchoBridge AppDirectory "C:\Program Files\k4echo"
nssm start K4EchoBridge
```

## Windows Firewall

Only needed for the `webhook` transport — the `iot` transport makes an outbound
connection and needs no inbound rule at all.

```powershell
New-NetFirewallRule -DisplayName "K4 Echo bridge" -Direction Inbound `
    -Protocol TCP -LocalPort 8443 -Action Allow -Profile Private
```

Note `-Profile Private`: this should never be opened on a public network
profile.
