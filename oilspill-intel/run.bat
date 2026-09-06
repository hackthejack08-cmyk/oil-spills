@echo off
REM One-command launcher for Windows (needs Python 3.10–3.13 from python.org, "Add to PATH" ticked).
cd /d %~dp0
if not exist .venv (
  echo Creating virtualenv and installing dependencies (2-4 min, ~1 GB incl. CPU torch)...
  python -m venv .venv || goto :err
  call .venv\Scripts\activate.bat
  python -m pip install -q --upgrade pip
  pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :err
  pip install -q -r backend\requirements.txt || goto :err
) else (
  call .venv\Scripts\activate.bat
)
if not exist demo\data\synthetic_s1_scene.tif python demo\make_demo_data.py
echo Oil Spill Intelligence -^> http://localhost:8000   (Ctrl-C to stop)
start "" http://localhost:8000
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof
:err
echo Installation failed. See docs\INSTALL.md (troubleshooting).
pause
