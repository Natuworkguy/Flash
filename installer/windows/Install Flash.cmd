@echo off
rem Double-click to install Flash: its installer opens in a window of its own.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0InstallFlash.ps1" %*
