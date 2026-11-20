@echo off
rem XR Analyze launcher. Double-click to start.
rem First run downloads Python and dependencies (about 3GB with GPU), later runs start at once.
rem Details: docs/windows.md (Japanese). Keep this file ASCII-only: cmd.exe reads it in the OEM code page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\bootstrap.ps1" %*
