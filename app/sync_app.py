"""WiFile - keeps your shared folders the same on every PC. Starts with Documents\\Shared;
drag any folder onto the window to share it too.

Pulled out of Freedom 35 so it runs on its own. Lives in the tray by the clock:
  Open        - the files page (browse, upload from the phone, pair PCs)
  Sync now    - check the other PC right away (it also checks every 2 minutes)
  Pause/Start - stop or start syncing
  Copy pair code - paste it into the other PC's files page to hook them together
  Start with Windows - off unless you turn it on
  Quit

Talks to the other PC over Tailscale (private, your devices only) on port 7790.
Deleted on one PC = moved to todelete\\sync trash on the other. Never wiped.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

import filesync

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("F35_SYNC_PORT", "7790"))
F35_PORTS = ("7777", "7801", "7802")                   # Freedom 35's port (old pairing codes point at it)
IS_WIN = sys.platform == "win32"
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}
LOG_DIR = HERE / "logs"
MEM = HERE / "memory"
F35 = HERE.parent / "AI Crew" / "Freedom 35"           # where Freedom 35 lives (we take over its sync)


def log(msg):
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        with open(LOG_DIR / "sync.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def documents():
    """The real Documents folder (OneDrive moves it on some PCs)."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
            u = UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                out = Path(p.value)
                ctypes.windll.ole32.CoTaskMemFree(p)
                return out
        except Exception:
            pass
    return Path.home() / "Documents"


# ------------------------------------------------------------------ taking over from Freedom 35
def take_over():
    """First run: bring over Freedom 35's key, pairings and sync records, so nothing re-copies from scratch."""
    MEM.mkdir(exist_ok=True)
    key_f = HERE / "key.txt"
    if not key_f.exists():
        old = F35 / "crew_key.txt"
        key_f.write_text(old.read_text(encoding="utf-8").strip() if old.exists() else secrets.token_urlsafe(18), encoding="utf-8")
        log("key: " + ("same as Freedom 35's (old pairings keep working)" if old.exists() else "brand new"))
    if (MEM / "filesync.json").exists():
        return
    old = HERE.parent / "F35 Sync" / "memory"              # same app under its old name - newest records, take those
    if (old / "filesync.json").exists():
        for f in old.glob("*.json"):
            shutil.copy2(f, MEM / f.name)
        log("took over the old F35 Sync's pairing + records")
        return
    src = F35 / "memory"
    if not (src / "filesync.json").exists():
        log("no Freedom 35 sync to take over - starting fresh")
        return
    for f in list(src.glob("sync_state_*.json")) + [src / "sync_seen.json", src / "sync_gone.json"]:
        if f.exists():
            shutil.copy2(f, MEM / f.name)
    cfg = json.loads((src / "filesync.json").read_text(encoding="utf-8"))
    for p in cfg.get("peers", []):                       # same PC address, the sync app's door instead of Freedom 35's
        p["url"] = re.sub(r":(%s)$" % "|".join(F35_PORTS), f":{PORT}", p["url"].rstrip("/"))
    (MEM / "filesync.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    log(f"took over Freedom 35's sync: {len(cfg.get('peers', []))} paired PC(s), records copied, sync {'ON' if cfg.get('on') else 'paused'}")


# ------------------------------------------------------------------ Tailscale (private tunnel)
def ts_exe():
    exe = shutil.which("tailscale")
    guess = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    return exe or (str(guess) if guess.exists() else None)


def tailscale_ip():
    exe = ts_exe()
    if not exe:
        return None, None
    try:
        ip = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=8, **NOWIN).stdout.split()
        st = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=8, **NOWIN).stdout
        name = (json.loads(st).get("Self") or {}).get("DNSName", "").rstrip(".") if st else ""
        return (ip[0] if ip and ip[0].startswith("100.") else None), (name or None)
    except Exception:
        return None, None


def phone_link(key):
    """https link for the phone (port 8443, so it doesn't bump Freedom 35's link). Kept in this folder only - never synced."""
    exe = ts_exe()
    ip, name = tailscale_ip()
    lines = ["WiFile - your files from the phone", "====================================",
             "The phone needs Tailscale signed in to the SAME account.", ""]
    if exe and name:
        try:
            subprocess.run([exe, "serve", "--bg", "--https=8443", str(PORT)], capture_output=True, text=True, timeout=15, **NOWIN)
        except Exception:
            pass
        lines += ["PHONE: open this in Chrome, then menu > Add to home screen:", f"   https://{name}:8443/?key={key}", ""]
    if ip:
        lines += ["Backup link (any device on Tailscale):", f"   http://{ip}:{PORT}/?key={key}", ""]
    if not ip:
        lines += ["Tailscale isn't running on this PC yet - start it and restart WiFile."]
    lines += ["Keep this private - the key in the link is the lock."]
    (HERE / "WiFile phone link.txt").write_text("\n".join(lines), encoding="utf-8")
    return f"http://{ip}:{PORT}" if ip else ""


def pick_folder():
    """The normal Windows 'pick a folder' popup."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askdirectory(title="Pick a folder to share with your other PCs", mustexist=True)
        root.destroy()
        return p or ""
    except Exception as e:
        log(f"folder picker hiccup: {e}")
        return ""


# ------------------------------------------------------------------ the app window (drag folders onto it)
class Window:
    def __init__(self):
        self.win = None

    def make(self, show):
        try:
            import logging
            import webview
            lg = logging.getLogger("pywebview")
            fh = logging.FileHandler(LOG_DIR / "window.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            lg.addHandler(fh)
            lg.setLevel(logging.DEBUG)
        except Exception as e:
            log(f"(no app window - {e}; using the browser instead)")
            return False
        self.webview = webview
        self.win = webview.create_window("WiFile", f"http://127.0.0.1:{PORT}/", width=900, height=760,
                                         min_size=(420, 500), hidden=not show, background_color="#12161F")
        self.win.events.closing += self.on_close
        self.win.events.loaded += self.on_loaded
        self.hello = 0
        if show:
            threading.Thread(target=self.watchdog, daemon=True).start()
        return True

    def watchdog(self):
        """Page didn't show up in the window? Use the browser instead (and say why in the log)."""
        time.sleep(12)
        if not self.hello:
            log("app window never showed the page - opening it in the browser instead (see logs\\window.log)")
            try:
                self.win.hide()
            except Exception:
                pass
            webbrowser.open(f"http://127.0.0.1:{PORT}/")

    def on_close(self):
        if getattr(self, "quitting", False):
            return True
        self.win.hide()                                    # X just tucks it back in the tray
        return False

    def on_loaded(self):
        log("app window loaded the page")
        threading.Thread(target=self.bind_drop, daemon=True).start()

    def bind_drop(self):
        try:
            from webview.dom import DOMEventHandler
            doc = self.win.dom.document
            doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
            doc.events.drop += DOMEventHandler(self.on_drop, prevent_default=True)
            log("drag-and-drop ready")
        except Exception as e:
            log(f"drag-and-drop hiccup: {e}")

    def on_drop(self, e):
        got = []
        for f in (e.get("dataTransfer") or {}).get("files") or []:
            p = f.get("pywebviewFullPath")
            if not p:
                continue
            if not Path(p).is_dir():
                got.append(f"'{Path(p).name}' is a file - drop a FOLDER (or upload files with the button)")
                continue
            out = filesync.add_folder(p)
            got.append(f"Now sharing '{out['name']}'" if out.get("ok") else f"'{Path(p).name}': {out['error']}")
        if got:
            filesync.push()
            msg = json.dumps(" · ".join(got))
            self.win.evaluate_js(f"dropped({msg})")
            if TRAY.loop and filesync.CFG.get("on"):
                TRAY.run_async(filesync.sync_all())

    def show(self):
        if self.win:
            self.win.show()
            self.win.restore()
        else:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")


WIN = Window()


# ------------------------------------------------------------------ the little web server
KEY = ""
PUBLIC = {"/health"}
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Proto", "Forwarded", "Tailscale-User-Login", "Tailscale-User-Name")


@web.middleware
async def guard(request, handler):
    """This PC gets in free. Anything else (the other PC, your phone) needs the key."""
    relayed = any(h in request.headers for h in PROXY_HEADERS)
    if (request.remote in ("127.0.0.1", "::1") and not relayed) or request.path in PUBLIC:
        return await handler(request)
    given = request.query.get("key") or request.cookies.get("wifile_key") or request.headers.get("X-Crew-Key") or ""
    if not secrets.compare_digest(given, KEY):
        return web.Response(status=403, text="Nope. Wrong key, bud.")
    resp = await handler(request)
    if request.query.get("key") and not resp.prepared:
        resp.set_cookie("wifile_key", KEY, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
                        secure=request.headers.get("X-Forwarded-Proto") == "https")
    return resp


async def h_page(request):
    return web.FileResponse(HERE / "page.html", headers={"Cache-Control": "no-cache"})


async def h_hello(request):
    if request.query.get("app") == "1":
        WIN.hello = time.time()
    return web.json_response({"ok": True})


async def h_show(request):
    if request.remote in ("127.0.0.1", "::1"):
        WIN.show()
    return web.json_response({"ok": True})


async def h_health(request):
    return web.json_response({"app": "wifile", "version": VERSION, "me": filesync.CFG.get("me"), "on": filesync.CFG.get("on")})


class Hub:
    """filesync shouts status here - we just poke the tray."""
    def send(self, ev):
        TRAY.refresh()


# ------------------------------------------------------------------ the tray icon
class Tray:
    def __init__(self):
        self.icon = None
        self.loop = None
        self._last = None

    def image(self, color):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((2, 2, 62, 62), fill=color)
        d.arc((14, 14, 50, 50), 200, 340, fill="white", width=6)              # two arrows chasin' each other = sync
        d.arc((14, 14, 50, 50), 20, 160, fill="white", width=6)
        d.polygon([(46, 14), (54, 28), (40, 27)], fill="white")
        d.polygon([(18, 50), (10, 36), (24, 37)], fill="white")
        return im

    def state(self):
        st, cfg = filesync.STATUS, filesync.CFG
        if st.get("busy"):
            return "busy", (40, 120, 220), f"WiFile - syncin'... {st.get('done', 0)}/{st.get('todo', 0)}"
        if not cfg.get("on"):
            return "off", (120, 120, 120), "WiFile - paused"
        if not cfg.get("peers"):
            return "lonely", (200, 140, 30), "WiFile - not paired with another PC yet"
        last = ", ".join(f"{k} {v}" for k, v in st.get("last", {}).items())
        if st.get("errors"):
            return "err", (200, 50, 50), f"WiFile - hiccup: {st['errors'][-1][:70]}"
        return "ok", (40, 160, 70), f"WiFile - all synced{' (' + last + ')' if last else ''}"

    def refresh(self):
        if not self.icon:
            return
        key, color, tip = self.state()
        try:
            if key != self._last:
                self.icon.icon = self.image(color)
                self._last = key
            self.icon.title = tip[:127]
            self.icon.update_menu()
        except Exception:
            pass

    def run_async(self, coro):
        if self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    # menu actions
    def open_page(self, *_):
        WIN.show()

    def sync_now(self, *_):
        self.run_async(filesync.sync_all())

    def toggle(self, *_):
        filesync.CFG["on"] = not filesync.CFG.get("on")
        filesync.save_cfg()
        if filesync.CFG["on"]:
            self.run_async(filesync.sync_all())
        self.refresh()

    def copy_code(self, *_):
        code = filesync.pair_code()
        if code and IS_WIN:
            subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{code}'"], **NOWIN)
            if self.icon:
                self.icon.notify("Pair code copied. Paste it on the other PC's files page.", "WiFile")

    def phone(self, *_):
        f = HERE / "WiFile phone link.txt"
        if f.exists() and IS_WIN:
            os.startfile(str(f))

    def startup_file(self):
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "WiFile.vbs"

    def at_login(self, *_):
        return self.startup_file().exists()

    def toggle_login(self, *_):
        f = self.startup_file()
        if f.exists():                                    # never wiped - parked in todelete
            dest = HERE.parent / "todelete" / "WiFile startup"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest / f"WiFile {int(time.time())}.vbs"))
        else:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            f.write_text(f'CreateObject("WScript.Shell").Run """{pyw}"" ""{HERE / "sync_app.py"}"" --hidden", 0\n', encoding="utf-8")
        self.refresh()

    def quit(self, *_):
        log("quit from the tray")
        WIN.quitting = True
        if self.icon:
            self.icon.stop()
        os._exit(0)

    def run(self):
        import pystray
        from pystray import MenuItem as I, Menu
        menu = Menu(
            I("Open WiFile", self.open_page, default=True),
            I("Open in browser instead", lambda *_: webbrowser.open(f"http://127.0.0.1:{PORT}/")),
            I(lambda _: self.state()[2].replace("WiFile - ", "").capitalize(), None, enabled=False),
            Menu.SEPARATOR,
            I("Sync now", self.sync_now),
            I(lambda _: "Pause syncing" if filesync.CFG.get("on") else "Start syncing", self.toggle),
            I("Copy pair code", self.copy_code),
            I("Phone link", self.phone),
            I("Start with Windows", self.toggle_login, checked=self.at_login),
            Menu.SEPARATOR,
            I("Quit WiFile", self.quit))
        key, color, tip = self.state()
        self.icon = pystray.Icon("WiFile", self.image(color), tip, menu)
        self._last = key
        if WIN.win:
            self.icon.run_detached()                        # the app window owns the main thread
            # the app window goes black on some screens (big ultrawides, HDR) when it draws with the graphics card -
            # a page this simple doesn't need it
            os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disable-gpu --disable-gpu-compositing")
            try:
                WIN.webview.start(gui="edgechromium", private_mode=False, storage_path=str(MEM / "window"))
            except Exception as e:
                log(f"app window couldn't start ({e}) - using the browser")
                webbrowser.open(f"http://127.0.0.1:{PORT}/")
            while True:                                    # window loop ended (shouldn't) - keep the tray alive
                time.sleep(3600)
        self.icon.run()


TRAY = Tray()


def server_thread(ready):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    TRAY.loop = loop

    async def main():
        app = web.Application(client_max_size=64 * 1024 * 1024, middlewares=[guard])
        app.add_routes([web.get("/", h_page), web.get("/health", h_health), web.get("/show", h_show), web.get("/hello", h_hello)])
        filesync.routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log(f"WiFile up on port {PORT} as {filesync.CFG['me']}")
        ready.set()
        asyncio.get_running_loop().create_task(filesync.loop())
        while True:
            await asyncio.sleep(3600)
    loop.run_until_complete(main())


def already_running():
    s = socket.socket()
    try:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def main():
    global KEY
    if already_running():                                 # second click = pop the window back up
        try:
            from urllib.request import urlopen
            urlopen(f"http://127.0.0.1:{PORT}/show", timeout=3).read()
        except Exception:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    take_over()
    KEY = (HERE / "key.txt").read_text(encoding="utf-8").strip()
    my_url = phone_link(KEY)
    filesync.STATE_DIR = MEM
    filesync.CFG_FILE = MEM / "filesync.json"
    filesync.init(Hub(), log, documents, KEY, my_url)
    filesync.PICK_FOLDER = pick_folder
    if not filesync.CFG.get("folders"):
        doc = documents()
        if F35.exists():                                   # a PC that ran Freedom 35: keep the 4 folders it always shared
            filesync.CFG["folders"] = {"Claude": str(HERE.parent), "Shared": str(doc / "Shared"),
                                       "Phone": str(doc / "Phone"), "tpb pixel art": str(doc / "tpb pixel art")}
        else:
            filesync.CFG["folders"] = filesync.default_folders()
        filesync.save_cfg()
        for p in filesync.roots().values():
            p.mkdir(parents=True, exist_ok=True)
    ready = threading.Event()
    threading.Thread(target=server_thread, args=(ready,), daemon=True).start()
    ready.wait(15)
    if "--no-tray" in sys.argv:
        while True:
            time.sleep(3600)
    WIN.make(show="--hidden" not in sys.argv)          # first open shows the window; "Start with Windows" starts tucked away
    TRAY.run()


if __name__ == "__main__":
    main()
