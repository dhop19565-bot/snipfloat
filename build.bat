@echo off
title DarcySnipTool Builder
echo.
echo  ==========================================
echo   DarcySnipTool - Building EXE, please wait...
echo  ==========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Please install Python from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  [1/3] Installing required packages...
pip install pillow pystray pywin32 keyboard pyinstaller --quiet
if errorlevel 1 (
    echo  [ERROR] Failed to install packages. Check your internet connection.
    pause
    exit /b 1
)

echo  [2/3] Building DarcySnipTool.exe...
python -c "from snip_tool import make_ico_file; make_ico_file()"
pyinstaller --onefile --windowed --name DarcySnipTool --icon=darcysniptool.ico --exclude-module numpy --exclude-module matplotlib --exclude-module scipy --exclude-module pandas snip_tool.py --clean --noconfirm
if errorlevel 1 (
    echo  [ERROR] Build failed. See output above for details.
    pause
    exit /b 1
)

echo  [3/3] Done!
echo.
echo  ==========================================
echo   Your EXE is ready:  dist\DarcySnipTool.exe
echo  ==========================================
echo.

explorer dist
pause
