@echo off
rem Build Elforan2CDF.exe (window) and elforan2cdf.exe (console).
rem Requires Python 3.8+ for Windows with tcl/tk. Full log: build.log
setlocal
cd /d "%~dp0"
echo Build log > build.log

echo [1/4] Checking Python and tkinter...
python --version
if errorlevel 1 goto :nopython
python -c "import tkinter; print('tkinter OK, Tk', tkinter.TkVersion)"
if errorlevel 1 goto :notk

echo [2/4] Installing PyInstaller...
python -m pip install --upgrade pyinstaller >> build.log 2>&1
if errorlevel 1 goto :faillog

echo [3/4] Building Elforan2CDF.exe (window)...
if exist dist\Elforan2CDF.exe del /f /q dist\Elforan2CDF.exe
python -m PyInstaller --onefile --windowed --noconfirm --clean --name Elforan2CDF elforan2cdf_gui.py >> build.log 2>&1
if errorlevel 1 goto :faillog
rem give antivirus a moment, then check that the file survived
timeout /t 3 /nobreak > nul
if not exist dist\Elforan2CDF.exe goto :antivirus

echo [4/4] Building elforan2cdf.exe (console)...
python -m PyInstaller --onefile --console --noconfirm --name elforan2cdf elforan2cdf.py >> build.log 2>&1
if errorlevel 1 goto :faillog

echo.
echo Done:
dir /b dist\*.exe
goto :end

:nopython
echo.
echo ERROR: python not found. Install Python from https://www.python.org/downloads/windows/
echo and tick "Add Python to PATH" during installation.
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
powershell -NoProfile -Command "Get-Content build.log -Tail 25"
echo Full log: %~dp0build.log

:end
echo.
pause
