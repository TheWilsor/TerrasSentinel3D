"""TerraSentinel 3D — lanzador de demo.

Levanta el servidor FastAPI y abre el visor 3D en el navegador.

Uso:  python run.py  (o doble clic en start.bat)
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _wait_for_server(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def _open_browser(delay: float = 1.0) -> None:
    time.sleep(delay)
    webbrowser.open(f"{BASE_URL}/")


def main() -> None:
    print(f"◈ TerraSentinel 3D — arrancando en {BASE_URL}")
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", HOST,
            "--port", str(PORT),
        ],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    try:
        if _wait_for_server():
            print("◈ Servidor listo — abriendo visor 3D en el navegador…")
            threading.Thread(target=_open_browser, daemon=True).start()
        else:
            print("⚠ El servidor no respondió a tiempo; abre", BASE_URL, "manualmente.")
        server.wait()
    except KeyboardInterrupt:
        print("\n◈ Apagando servidor…")
        server.terminate()
    finally:
        server.poll() or server.wait(timeout=10)


if __name__ == "__main__":
    main()
