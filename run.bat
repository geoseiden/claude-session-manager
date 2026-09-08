@echo off
REM Windows launcher - double-click to open the session manager
python "%~dp0app.py" %*
if errorlevel 1 pause
