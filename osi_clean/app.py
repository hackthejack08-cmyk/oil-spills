"""`python app.py` — cross-platform launcher (same as run.sh / run.bat but pure Python).
Creates .venv on first run, installs dependencies, generates demo data, starts the server on :8000."""
import os, subprocess, sys, venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sh(*cmd):
    print("▶", " ".join(map(str, cmd))); subprocess.check_call([str(c) for c in cmd])


if not PY.exists():
    print("Creating virtualenv and installing dependencies (2–4 min, ~1 GB incl. CPU torch)…")
    venv.create(VENV, with_pip=True)
    sh(PY, "-m", "pip", "install", "-q", "--upgrade", "pip")
    sh(PY, "-m", "pip", "install", "-q", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu")
    sh(PY, "-m", "pip", "install", "-q", "-r", ROOT / "backend" / "requirements.txt")
if not (ROOT / "demo/data/synthetic_s1_scene.tif").exists():
    sh(PY, ROOT / "demo/make_demo_data.py")
env = dict(os.environ)
envf = ROOT / ".env"
if envf.exists():
    for line in envf.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); env.setdefault(k.strip(), v.strip())
port = os.environ.get("PORT", "8000")
print(f"Oil Spill Intelligence → http://localhost:{port}  (Ctrl-C to stop)")
os.chdir(ROOT / "backend")
os.execve(str(PY), [str(PY), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port], env)
