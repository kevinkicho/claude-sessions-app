@echo off
rem Launcher for rotate-ssh.ps1 - sidesteps the .ps1 Notepad file association.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rotate-ssh.ps1" %*
pause
