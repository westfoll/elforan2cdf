@echo off
rem Build Elforan2CDF.exe (window) and elforan2cdf-cli.exe (console). Full log: build.log
rem
rem Windows 7 compatibility: the exe must be built with Python 3.8 (Python 3.9+
rem does not run on Windows 7). This script prefers Python 3.8 32-bit if installed,
rem because a 32-bit exe runs on both 32- and 64-bit Windows 7/8/10/11.
setlocal
cd /d "%~dp0"
echo Build log > build.log

echo [1/4] Looking for Python...
set "PY="
py -3.8-32 -c "import sys" >nul 2>&1 && set "PY=py -3.8-32"
if not defined PY py -3.8 -c "import sys" >nul 2>&1 && set "PY=py -3.8"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython

%PY% -c "import sys, struct; print('Using Python %%d.%%d.%%d %%d-bit' %% (sys.version_info[:3] + (struct.calcsize('P') * 8,)))"
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 8) else 1)"
if errorlevel 1 (set "WIN7=0") else (set "WIN7=1")
if "%WIN7%"=="1" (set "PYI=pyinstaller==5.13.2") else (set "PYI=pyinstaller")
if "%WIN7%"=="0" call :warn7

%PY% -c "import tkinter; print('tkinter OK, Tk', tkinter.TkVersion)"
if errorlevel 1 goto :notk

echo [2/4] Installing %PYI%...
%PY% -m pip install --upgrade "%PYI%" >> build.log 2>&1
if errorlevel 1 goto :faillog

echo [3/4] Building Elforan2CDF.exe (window)...
if exist dist\Elforan2CDF.exe del /f /q dist\Elforan2CDF.exe
%PY% -m PyInstaller --onefile --windowed --noconfirm --clean --name Elforan2CDF elforan2cdf_gui.py >> build.log 2>&1
if errorlevel 1 goto :faillog
rem give antivirus a moment, then check that the file survived
timeout /t 3 /nobreak > nul
if not exist dist\Elforan2CDF.exe goto :antivirus

echo [4/4] Building elforan2cdf-cli.exe (console)...
%PY% -m PyInstaller --onefile --console --noconfirm --name elforan2cdf-cli elforan2cdf.py >> build.log 2>&1
if errorlevel 1 goto :faillog
if not exist dist\Elforan2CDF.exe goto :antivirus

echo.
echo Done:
dir /b dist\*.exe
if "%WIN7%"=="1" echo These exe files are compatible with Windows 7 SP1.
if "%WIN7%"=="0" echo WARNING: built with Python newer than 3.8 - will NOT run on Windows 7.
goto :end

:warn7
echo.
echo WARNING: Python 3.8 not found. The exe will work on Windows 8/10/11 only, NOT on Windows 7.
echo For Windows 7 install Python 3.8.10 "Windows installer (32-bit)" from
echo https://www.python.org/downloads/release/python-3810/  and run this file again.
echo It can be installed next to a newer Python; this script will pick 3.8 automatically.
echo.
exit /b 0

:nopython
echo.
echo ERROR: Python not found. For Windows 7 compatibility install Python 3.8.10 32-bit:
echo https://www.python.org/downloads/release/python-3810/  - "Windows installer (32-bit)",
echo tick "Add Python to PATH" during installation.
goto :end

:notk
echo.
echo ERROR: tkinter is missing in this Python, the window version cannot be built.
echo Fix: Settings - Apps - Python - Modify, tick "tcl/tk and IDLE", finish, run this file again.
echo Or reinstall Python from python.org with default options.
goto :end

:antivirus
echo.
echo ERROR: PyInstaller finished, but dist\Elforan2CDF.exe is missing.
echo Most likely the antivirus deleted it - Windows Defender often flags new PyInstaller exe files.
echo Fix: Windows Security - Virus and threat protection - Protection history - restore the file,
echo or add this folder to exclusions, then run this file again.
goto :end

:faillog
echo.
echo ERROR during build. Last lines of build.log:
powershell -NoProfile -Command "Get-Content build.log | Select-Object -Last 25"
echo Full log: %~dp0build.log

:end
echo.
pause
