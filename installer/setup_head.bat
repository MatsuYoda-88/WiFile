@echo off
setlocal EnableExtensions
title WiFile Setup - same files on all your PCs
REM ===================================================================
REM  WiFile - one click. Double-click me on EACH of your PCs.
REM  Keeps your folders the same on every PC, privately, over Tailscale.
REM  Starts with one folder: Documents\Shared. Drag any folder onto the
REM  WiFile window later to share that too.
REM  Deleted on one PC = moved to a todelete folder on the others. Never wiped.
REM ===================================================================
set "APP=%LOCALAPPDATA%\Programs\WiFile\"
set "PAY=%TEMP%\wifile_payload.zip"
echo.
echo   WiFile setup. This takes a few minutes the first time.
echo.

REM --- 1. unpack the app ---
powershell -NoProfile -Command "$t=[IO.File]::ReadAllText('%~f0'); $m='::PAY'+'LOAD::'; $b=$t.Substring($t.LastIndexOf($m)+$m.Length).Trim(); [IO.File]::WriteAllBytes('%PAY%',[Convert]::FromBase64String($b))"
if not exist "%PAY%" (
  echo Couldn't unpack WiFile. Make sure you ran the whole file, not a preview.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*WiFile*sync_app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
if not exist "%APP%" mkdir "%APP%"
powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath '%PAY%' -DestinationPath '%APP%'"
del "%PAY%" >nul 2>nul
echo [1/5] App unpacked to %APP%

REM --- 2. Python (one time) ---
set "VPY=%APP%venv\Scripts\python.exe"
set "VPYW=%APP%venv\Scripts\pythonw.exe"
if exist "%VPY%" goto have_venv
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  echo [2/5] Installing Python 3.12 - one time only...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
)
if not exist "%PY%" (
  echo Python didn't install. Get it from python.org ^(3.12^), then run this again.
  pause
  exit /b 1
)
"%PY%" -m venv "%APP%venv"
"%VPY%" -m pip install --upgrade pip -q
:have_venv
echo [2/5] Getting WiFile's parts...
"%VPY%" -m pip install -q -r "%APP%requirements.txt"
if errorlevel 1 (
  echo Couldn't download WiFile's parts. Check the internet and run this again.
  pause
  exit /b 1
)

REM --- 3. Tailscale = the private tunnel between YOUR PCs ---
set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
if not exist "%TS%" (
  echo [3/5] Installing Tailscale - the private tunnel between your PCs...
  winget install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
)
if not exist "%TS%" (
  echo Tailscale didn't install. Get it from tailscale.com, then run this again.
  pause
  exit /b 1
)
"%TS%" ip -4 >nul 2>nul
if errorlevel 1 (
  echo.
  echo   ** Sign in to Tailscale in the window that pops up.            **
  echo   ** Use the SAME account on every PC you want WiFile on.        **
  start "" "%TS%" up
  echo   Press any key once you're signed in...
  pause >nul
)
echo [3/5] Tailscale on.

REM --- 4. let your own PCs in (Tailscale addresses only - asks for admin once) ---
netsh advfirewall firewall show rule name=WiFile-Tailscale-7790 >nul 2>nul || powershell -NoProfile -Command "Start-Process netsh -Verb RunAs -Wait -ArgumentList 'advfirewall firewall add rule name=WiFile-Tailscale-7790 dir=in action=allow protocol=TCP localport=7790 remoteip=100.64.0.0/10'"
echo [4/5] Door open for your own PCs only.

REM --- 5. shortcuts (desktop + start menu) and go ---
powershell -NoProfile -Command "$s=New-Object -ComObject WScript.Shell; foreach($p in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))){ $l=$s.CreateShortcut((Join-Path $p 'WiFile.lnk')); $l.TargetPath='%VPYW%'; $l.Arguments='\"%APP%sync_app.py\"'; $l.WorkingDirectory='%APP%'; $l.IconLocation='%APP%wifile.ico'; $l.Description='WiFile - same files on all your PCs'; $l.Save() }"
start "" "%VPYW%" "%APP%sync_app.py"
echo [5/5] WiFile is running - look for the green icon by the clock.
echo.
echo   NEXT: do this same setup on your other PC, then in WiFile on one PC
echo   open "Paired PCs", copy its code, and paste it into the other one.
echo   ^(Pair both ways: paste each PC's code into the other.^)
echo.
pause
exit /b 0

