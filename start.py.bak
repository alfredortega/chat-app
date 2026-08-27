"""
start.py — Ensure the venv exists, install dependencies if needed,
           start the Flask server, and open the app URL in the default browser.

Usage:
    python3 start.py
"""

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

APP_URL    = "http://127.0.0.1:5000/"
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
APP_SCRIPT = os.path.join(BASE_DIR, "app.py")
VENV_DIR   = os.path.join(BASE_DIR, "venv")

POLL_INTERVAL = 0.25   # seconds between readiness checks
POLL_TIMEOUT  = 30     # seconds before giving up


def venv_python() -> str:
    """Return the path to the venv Python executable."""
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def venv_pip() -> str:
    """Return the path to the venv pip executable."""
    if os.name == "nt":
        return os.path.join(VENV_DIR, "Scripts", "pip.exe")
    return os.path.join(VENV_DIR, "bin", "pip")


def ensure_venv():
    """Create the venv if it doesn't exist yet, and ensure pip is available."""
    if not os.path.isfile(venv_python()):
        if os.path.isdir(VENV_DIR):
            print("Virtual environment is incomplete; recreating it...")
        print("Creating virtual environment...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
        print("Virtual environment created.")
    else:
        print("Virtual environment already exists.")

    pip_check = subprocess.run(
        [venv_python(), "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if pip_check.returncode != 0:
        print("pip is missing; bootstrapping it in the virtual environment...")
        ensurepip = subprocess.run(
            [venv_python(), "-m", "ensurepip", "--upgrade"],
        )
        if ensurepip.returncode != 0:
            subprocess.check_call([
                sys.executable, "-m", "pip", "--python", venv_python(),
                "install", "pip", "--upgrade",
            ])


def ensure_dependencies():
    """Install / upgrade packages from requirements.txt into the venv."""
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.isfile(req_file):
        print("WARNING: requirements.txt not found, skipping install.")
        return
    print("Installing dependencies...")
    subprocess.check_call([
        venv_python(), "-m", "pip", "install", "-r", req_file, "--quiet", "--upgrade"
    ])
    print("Dependencies up to date.")


def wait_for_server(url: str, timeout: float) -> bool:
    """Poll the URL until it responds or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(POLL_INTERVAL)
    return False


def main():
    ensure_venv()
    ensure_dependencies()

    print(f"Starting Flask server -> {APP_SCRIPT}")
    server = subprocess.Popen(
        [venv_python(), APP_SCRIPT],
        cwd=BASE_DIR,
    )

    print("Waiting for server to be ready...", end="", flush=True)
    ready = wait_for_server(APP_URL, POLL_TIMEOUT)
    print(" ready." if ready else " timed out.")

    if not ready:
        print("ERROR: Server did not start in time. Check for errors above.")
        server.terminate()
        sys.exit(1)

    print(f"Opening browser -> {APP_URL}")
    webbrowser.open(APP_URL)

    print("Press Ctrl+C to stop the server.\n")
    try:
        server.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.terminate()
        server.wait()
        print("Server stopped.")


if __name__ == "__main__":
    main()
