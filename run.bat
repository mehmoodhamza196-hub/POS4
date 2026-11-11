@echo off
cd /d %~dp0
if not exist venv (python -m venv venv)
call venv\Scripts\activate
python -c "import flask" 2>nul
if errorlevel 1 (
  echo Installing Flask from the offline "wheels" folder...
  pip install -q --no-index --find-links=wheels flask
)
python -c "import flask" 2>nul
if errorlevel 1 (
  echo Offline install did not work, trying the internet...
  pip install -q -r requirements.txt
)
python -c "import flask" 2>nul
if errorlevel 1 (
  echo.
  echo Flask could not be installed. Check that Python is 3.10 or newer ^(python --version^) and read README.md.
  pause
  exit /b 1
)
python app.py
pause
