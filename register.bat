@echo off
rem grok-register launcher (Windows). Usage:
rem   register.bat                main TUI menu
rem   register.bat run [count]    start CLI registration directly
rem   register.bat show           show full config (masked)
rem   register.bat base|mail|proxy|local|remote|cpa    jump into one module
setlocal
cd /d "%~dp0"
set "PYCMD="
python --version >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py --version >nul 2>nul && set "PYCMD=py"
if not defined PYCMD (
    echo [ERROR] Python not found in PATH. Install Python 3 first.
    pause
    exit /b 1
)
"%PYCMD%" config_tui.py %*
pause
