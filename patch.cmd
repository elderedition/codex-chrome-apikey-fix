@echo off
powershell.exe -NoLogo -NoProfile -File "%~dp0launch.ps1" %*
exit /b %errorlevel%
