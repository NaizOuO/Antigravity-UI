@echo off
rem Zip the app for sharing. The data folder (history, projects, uploads, settings) is NOT included.
rem Tip: friends can also download the latest version from https://github.com/NaizOuO/Antigravity-UI/releases/latest
cd /d "%~dp0"
powershell -NoProfile -Command "Compress-Archive -Path 'server.py','version.py','updater.py','manifest.json','static','*.vbs','*.bat','README.md' -DestinationPath 'share.zip' -Force"
echo Created share.zip
pause
