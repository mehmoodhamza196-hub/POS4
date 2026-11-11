@echo off
cd /d %~dp0
if not exist venv (python -m venv venv)
call venv\Scripts\activate
pip install -q --no-index --find-links=wheels flask pyinstaller
if errorlevel 1 pip install -q flask pyinstaller
pyinstaller --noconfirm --clean --onedir --windowed --name PizzaPOS --add-data "templates;templates" --add-data "static;static" desktop.py
if errorlevel 1 (
  echo.
  echo Build failed. See the messages above.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Pizza POS.lnk'); $s.TargetPath='%~dp0dist\PizzaPOS\PizzaPOS.exe'; $s.WorkingDirectory='%~dp0dist\PizzaPOS'; $s.Save()"
echo.
echo Done. The program is in dist\PizzaPOS\PizzaPOS.exe and a Desktop icon was created.
echo To use it on another PC, copy the whole dist\PizzaPOS folder (no Python needed there).
pause
