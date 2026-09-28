#!/usr/bin/env python3
"""
SmartVerify - Unified Single-Project Runner
Runs both FastAPI backend and React frontend as a unified single project.

Usage:
    python run.py              # Run unified server (FastAPI serves React SPA on http://localhost:8000)
    python run.py --dev        # Run in development mode (concurrent live backend + React dev server)
    python run.py --build      # Force rebuild of frontend SPA bundle before starting
    python run.py --check      # Run health checks and diagnostics
"""

import os
import sys
import time
import signal
import shutil
import argparse
import subprocess
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
BUILD_DIR = FRONTEND_DIR / "build"

def get_venv_python():
    """Find virtualenv python executable if available."""
    if os.name == "nt":
        candidates = [
            BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
            BACKEND_DIR / "venv" / "Scripts" / "python.exe",
            ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            BACKEND_DIR / ".venv" / "bin" / "python",
            BACKEND_DIR / "venv" / "bin" / "python",
            ROOT_DIR / ".venv" / "bin" / "python",
        ]

    for p in candidates:
        if p.is_file():
            return str(p)
    return sys.executable


def ensure_virtualenv():
    """If not running in the backend venv and it exists, re-exec with venv python."""
    venv_python = get_venv_python()
    current_python = str(Path(sys.executable).resolve())
    target_python = str(Path(venv_python).resolve())

    if os.getenv("SMARTVERIFY_VENV_ACTIVATED") != "1" and current_python != target_python:
        env = os.environ.copy()
        env["SMARTVERIFY_VENV_ACTIVATED"] = "1"
        if os.name == "nt":
            res = subprocess.run([venv_python] + sys.argv, env=env)
            sys.exit(res.returncode)
        else:
            os.execv(venv_python, [venv_python] + sys.argv)


def ensure_frontend_build(force_rebuild=False):
    """Ensure that the React frontend is built and up to date."""
    index_html = BUILD_DIR / "index.html"
    needs_rebuild = force_rebuild or not index_html.is_file()

    if not needs_rebuild and index_html.is_file():
        build_mtime = index_html.stat().st_mtime
        src_dir = FRONTEND_DIR / "src"
        public_dir = FRONTEND_DIR / "public"
        for check_dir in (src_dir, public_dir):
            if check_dir.is_dir():
                for f in check_dir.rglob("*"):
                    if f.is_file() and f.stat().st_mtime > build_mtime:
                        needs_rebuild = True
                        break
            if needs_rebuild:
                break

    if needs_rebuild:
        print("\n[*] Frontend production build missing or source files updated.")
        print("[*] Building React frontend (npm run build)...")

        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        if not shutil.which(npm_cmd) and not shutil.which("npm"):
            print("[!] ERROR: 'npm' not found in PATH. Please install Node.js.")
            sys.exit(1)

        result = subprocess.run([npm_cmd, "run", "build"], cwd=str(FRONTEND_DIR), shell=(os.name == "nt"))
        if result.returncode != 0 or not index_html.is_file():
            print("[!] ERROR: Failed to build frontend.")
            sys.exit(1)
        print("[+] Frontend build completed successfully.\n")
    else:
        print("[+] Frontend build is up to date at:", BUILD_DIR)


def print_banner(host, port, mode="Production (Single Port)"):
    display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    url = f"http://{display_host}:{port}"

    print("=" * 68)
    print(f"  SmartVerify - Autonomous AI Loan Verification System")
    print(f"  Mode: {mode}")
    print("=" * 68)
    print(f"  -> Web Application:   {url}")
    print(f"  -> API Documentation: {url}/api/docs")
    print(f"  -> Health Check:      {url}/health")
    print("-" * 68)
    print("  Default Login Credentials:")
    print("    * Admin:         admin@smartverify.com  /  admin123")
    print("    * Loan Officer:  officer@smartverify.com  /  officer123")
    print("=" * 68)
    print("  Press Ctrl+C to stop the server.\n")


def run_single_server(host="0.0.0.0", port=8000, reload=False, open_browser=False):
    """Run unified server where FastAPI serves both REST API and React SPA."""
    ensure_frontend_build()
    print_banner(host, port, "Unified Single-Server")

    if open_browser:
        def _open():
            time.sleep(1.5)
            webbrowser.open(f"http://localhost:{port}")
        import threading
        threading.Thread(target=_open, daemon=True).start()

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(str(BACKEND_DIR))
    import uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


def run_dev_mode(host="127.0.0.1", port=8000):
    """Run backend with auto-reload and frontend webpack dev server concurrently."""
    print("\nStarting SmartVerify in Dual Development Mode...")
    print(f"  - Backend API:     http://localhost:{port}")
    print(f"  - Frontend Dev:    http://localhost:3000 (proxies to :{port})")
    print("  Press Ctrl+C to terminate both.\n")

    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    python_cmd = sys.executable

    backend_proc = subprocess.Popen(
        [python_cmd, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port), "--reload"],
        cwd=str(BACKEND_DIR),
    )

    frontend_proc = subprocess.Popen(
        [npm_cmd, "start"],
        cwd=str(FRONTEND_DIR),
        shell=(os.name == "nt"),
    )

    def shutdown(signum=None, frame=None):
        print("\nStopping SmartVerify services...")
        try:
            backend_proc.terminate()
            frontend_proc.terminate()
            backend_proc.wait(timeout=5)
            frontend_proc.wait(timeout=5)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
            if backend_proc.poll() is not None:
                print("[!] Backend exited unexpectedly.")
                shutdown()
            if frontend_proc.poll() is not None:
                print("[!] Frontend exited unexpectedly.")
                shutdown()
    except KeyboardInterrupt:
        shutdown()


def run_check():
    """Run diagnostics to verify backend, database, and frontend build."""
    print("=" * 60)
    print("  SmartVerify Self-Check & Diagnostics")
    print("=" * 60)

    # 1. Python venv
    py_ver = sys.version.split()[0]
    print(f"[*] Python: {py_ver} ({sys.executable})")

    # 2. Database
    db_file = BACKEND_DIR / "smartverify.db"
    if db_file.is_file():
        print(f"[+] SQLite Database: Found ({db_file.stat().st_size:,} bytes)")
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_file))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM users")
            user_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM applications")
            app_count = c.fetchone()[0]
            print(f"[+] Database Records: {user_count} users, {app_count} applications")
            conn.close()
        except Exception as e:
            print(f"[!] Database query warning: {e}")
    else:
        print("[!] Database file not found yet. It will be created on first start.")

    # 3. Frontend Build
    index_html = BUILD_DIR / "index.html"
    if index_html.is_file():
        print(f"[+] Frontend Build: Present at {BUILD_DIR}")
    else:
        print(f"[-] Frontend Build: Not built yet (will build automatically on run)")

    # 4. Import app
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        from app.main import app
        print(f"[+] FastAPI Application: Successfully loaded ({len(app.routes)} routes)")
    except Exception as e:
        print(f"[!] Error loading FastAPI app: {e}")
        return False

    print("\n[+] All diagnostics passed! Ready to run.")
    return True


def main():
    ensure_virtualenv()

    parser = argparse.ArgumentParser(
        description="SmartVerify - Unified Single-Project Runner"
    )
    parser.add_argument(
        "--dev", action="store_true",
        help="Run in development mode (live reload backend + React dev server)"
    )
    parser.add_argument(
        "--build", action="store_true",
        help="Force rebuild frontend bundle before starting"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Run diagnostics and test database/app configuration"
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="Host address to bind (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Port to listen on (default: 8000)"
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Automatically open web browser"
    )

    args = parser.parse_args()

    if args.check:
        success = run_check()
        sys.exit(0 if success else 1)

    if args.build:
        ensure_frontend_build(force_rebuild=True)

    if args.dev:
        run_dev_mode(host=args.host, port=args.port)
    else:
        run_single_server(host=args.host, port=args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
