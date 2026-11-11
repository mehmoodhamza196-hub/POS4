"""Desktop launcher: starts the server quietly and opens Pizza POS in its own window (no browser tabs, no console)."""
import os, sys, socket, shutil, subprocess, threading, webbrowser

import db as D

if sys.stdout is None or sys.stderr is None:           # windowed .exe / pythonw have no console
    sys.stdout = sys.stderr = open(os.path.join(D.DATA, "app.log"), "a", buffering=1)

import app as A
from werkzeug.serving import make_server

PORT = int(os.environ.get("PORT", 5000))


def find_browser():
    """Edge ships with Windows 10/11; Chrome is the fallback. Both can open a site as a plain app window."""
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
              r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")


def open_window(url):
    exe = find_browser()
    if not exe:
        return None
    profile = os.path.join(D.DATA, "window-profile")      # own profile = own window process we can wait on
    return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1280,800",
                             "--no-first-run", "--no-default-browser-check"])


def fallback_window(url):
    """No Edge or Chrome found: open the default browser and show a small window that keeps the app running."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        w = tk.Tk(); w.title("Pizza POS"); w.geometry("340x130")
        tk.Label(w, text="Pizza POS is running.\nClose this window to stop it.", font=("Segoe UI", 11)).pack(pady=18)
        tk.Button(w, text="Open again", command=lambda: webbrowser.open(url)).pack()
        w.mainloop()
    except Exception:
        threading.Event().wait()


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = make_server("127.0.0.1", PORT, A.app, threaded=True)
    except OSError:                                          # already running: just open another window to it
        open_window(url) or webbrowser.open(url)
        return
    A.auto_backup()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = open_window(url)
    if proc:
        proc.wait()                                          # closing the window quits the program
    else:
        fallback_window(url)
    server.shutdown()


if __name__ == "__main__":
    main()
