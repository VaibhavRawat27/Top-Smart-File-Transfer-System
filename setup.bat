@echo off
echo 🚀 Smart File Transfer System - Setup Script
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

echo ✅ Python found
echo.

REM Create virtual environment
echo 📦 Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ❌ Failed to create virtual environment
    pause
    exit /b 1
)

echo ✅ Virtual environment created
echo.

REM Activate virtual environment and install dependencies
echo 📥 Installing dependencies...
call venv\Scripts\activate.bat

echo Installing coordinator dependencies...
pip install -r coordinator\requirements.txt
if errorlevel 1 (
    echo ❌ Failed to install coordinator dependencies
    pause
    exit /b 1
)

echo Installing sender dependencies...
pip install -r sender\requirements.txt
if errorlevel 1 (
    echo ❌ Failed to install sender dependencies
    pause
    exit /b 1
)

echo.
echo ✅ Setup completed successfully!
echo.
echo 🎯 Next steps:
echo 1. Start the coordinator: venv\Scripts\activate.bat ^&^& python coordinator\app.py
echo 2. Open dashboard: http://localhost:5000
echo 3. Send a file: venv\Scripts\activate.bat ^&^& python sender\send_file.py demo_files\sample.bin
echo 4. List files: venv\Scripts\activate.bat ^&^& python sender\receive_file.py --list
echo.
pause