@echo off
title Controller Bridge (auto)

REM Optional firewall open (skip if not admin)
net session >nul 2>&1
if %errorlevel%==0 (
  netsh advfirewall firewall add rule name="CB Host" dir=in action=allow protocol=UDP localport=49001 >nul 2>&1
  netsh advfirewall firewall add rule name="CB Client" dir=in action=allow protocol=UDP localport=49002 >nul 2>&1
  netsh advfirewall firewall add rule name="CB Discovery" dir=in action=allow protocol=UDP localport=49010 >nul 2>&1
  netsh advfirewall firewall add rule name="CB ScreenCast" dir=in action=allow protocol=UDP localport=49011 >nul 2>&1
) else (
  echo (Not admin: firewall rules may need manual allow if prompted.)
)

python -V >nul 2>&1
if errorlevel 1 (
  echo Python not found. Please install Python 3.11+ and re-run.
  pause
  exit /b 1
)

python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt

echo.
echo Launching Controller Bridge...
python bridge.py --auto
pause
