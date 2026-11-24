@echo off
rem Build Elforan2CDF.exe (window) and elforan2cdf.exe (console). Requires Python 3.8+ for Windows.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --windowed --noconfirm --name Elforan2CDF elforan2cdf_gui.py
python -m PyInstaller --onefile --console --noconfirm --name elforan2cdf elforan2cdf.py
echo.
echo Done: dist\Elforan2CDF.exe, dist\elforan2cdf.exe
pause
