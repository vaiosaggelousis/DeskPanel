@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install required Python packages.
    pause
    exit /b 1
)
echo Installing optional Windows audio and media integrations...
%PY% -m pip install -q pycaw >nul 2>nul
%PY% -m pip install -q winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Storage.Streams >nul 2>nul
echo.
echo Optional CPU temperature sensors: install LibreHardwareMonitor and run
 echo   %PY% -m pip install wmi
 echo only if you need Windows CPU temperature readings.
echo.
%PY% server.py
pause
