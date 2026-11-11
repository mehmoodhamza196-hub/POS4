@echo off
cd /d %~dp0
if not exist venv\Scripts\pythonw.exe (
  echo Run run.bat once first, so the venv folder is created.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Pizza POS.lnk'); $s.TargetPath='%~dp0venv\Scripts\pythonw.exe'; $s.Arguments='desktop.py'; $s.WorkingDirectory='%~dp0'; $s.Save()"
echo.
echo Done. A "Pizza POS" icon is now on your Desktop. Double-click it to open the app in its own window.
pause
