#!/usr/bin/env python3
"""
Unfollow by Lykoon  v11 Pro  -  graphical app (opens in your browser).  Made by Lykoon Company.

Needs only Python 3.8+.  Nothing else to install by hand: the first start sets itself up
(a private environment and the browser engine, needs internet, a few minutes). Later starts take seconds.
Keep unfollow_bot.py in the same folder as this file.

Run:
    python unfollow_app.py               # Windows:  py unfollow_app.py      (or double-click Start-Windows.bat)
    python3 unfollow_app.py              # macOS / Linux                      (or Start-Mac.command / Start-Linux.sh)
    python3 unfollow_app.py --lan        # also control it from your phone (same Wi-Fi)

No files are written to your folder: the list and progress live in a temporary folder that is deleted
when you quit. Your logins are remembered in ~/.ig_unfollow, the private environment lives in ~/.lykoon.
"""
# ---- self-setup: runs once at start, so no venv / pip / playwright install is needed by hand ----
import os
import subprocess
import sys
from pathlib import Path

LYKOON_HOME = Path(os.environ.get("LYKOON_HOME", Path.home() / ".lykoon"))
VENV = LYKOON_HOME / "venv"
_CHK = ("from playwright.sync_api import sync_playwright as s\n"
        "p = s().start()\n"
        "import os\n"
        "print(1 if os.path.exists(p.chromium.executable_path) else 0)\n"
        "p.stop()\n")


def _vpy():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _say(msg):
    print("  " + msg, flush=True)


def _bootstrap():
    """Make sure we run inside our own private environment that has a working Playwright."""
    if os.environ.get("LYKOON_NO_VENV") == "1" or os.path.realpath(sys.prefix) == os.path.realpath(str(VENV)):
        return
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or newer is needed.")
    print("\n  Unfollow by Lykoon", flush=True)
    LYKOON_HOME.mkdir(parents=True, exist_ok=True)
    py = _vpy()
    if not py.exists():
        _say("First start: creating the private environment...")
        r = subprocess.run([sys.executable, "-m", "venv", str(VENV)], capture_output=True, text=True)
        if r.returncode != 0 or not py.exists():
            print((r.stdout + r.stderr).strip()[-600:])
            sys.exit("\n  Could not create the environment.\n  Linux (Debian / Ubuntu / Kali):  sudo apt install python3-venv  and run again.\n"
                     "  Windows / Mac: install Python from python.org (tick 'Add to PATH') and run again.")
    ok = subprocess.run([str(py), "-c", "import playwright.sync_api"], capture_output=True).returncode == 0
    if not ok:
        _say("Installing the browser engine (Playwright), one time...")
        r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "playwright"])
        if r.returncode != 0:
            sys.exit("\n  Install failed. Check your internet connection and run again.")
    if os.environ.get("LYKOON_SKIP_BROWSER") != "1":
        have = subprocess.run([str(py), "-c", _CHK], capture_output=True, text=True).stdout.strip() == "1"
        if not have:
            _say("Downloading the built-in browser (Chromium, about 150 MB), one time...")
            r = subprocess.run([str(py), "-m", "playwright", "install", "chromium"])
            if r.returncode != 0:
                _say("Chromium download failed. You can still pick Google Chrome or Edge in the app if installed.")
    try:
        code = subprocess.call([str(py), os.path.abspath(__file__)] + sys.argv[1:])
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    _bootstrap()
# ---- end self-setup ----

import argparse
import atexit
import contextlib
import csv
import io
import json
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # Windows consoles: never crash on odd characters
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
import unfollow_bot as bot  # noqa: E402

APP_NAME = "Unfollow by Lykoon"
APP_VERSION = "v12 Pro"
REPORT_KEEP = 40            # how many auto-saved reports to keep
HOME_DIR = Path(os.environ.get("IG_UNFOLLOW_HOME", Path.home() / ".ig_unfollow"))
MAX_BODY = 80 * 1024 * 1024
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02]")
RESULT_LINE = re.compile(r"^\s*[\u2714\u25cb\u2716+xo]\s+\d\d:\d\d:\d\d\s")
BOXY = re.compile(r"[\u256d\u256e\u2570\u256f\u2502\u2500]")

# A geometric wolf (Lykoon = "wolf") on a gradient badge. Original artwork.
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Lykoon">'
    '<defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14264a"/>'
    '<stop offset="1" stop-color="#0a1330"/></linearGradient></defs>'
    '<rect width="64" height="64" rx="17" fill="url(#lg)"/>'
    '<circle cx="46" cy="17" r="9" fill="#dcebff" opacity=".9"/><path d="M12 11 26 22H38L52 11 55 33 45 51 32 59 19 51 9 33Z" fill="#eaf3ff"/>'
    '<path d="M19 34 28 37 25 41ZM45 34 36 37 39 41Z" fill="#1d3f7a"/>'
    '<path d="M27.5 47H36.5L32 53Z" fill="#1d3f7a"/>'
    '<path d="M32 23V31" stroke="#62d8ff" stroke-width="2.4" stroke-linecap="round"/></svg>'
)


# ================================================================ browsers
def _exists_any(paths):
    return any(p and Path(p).exists() for p in paths)


def detect_browsers():
    home = Path.home()
    env = os.environ
    pw_dirs = [env.get("PLAYWRIGHT_BROWSERS_PATH"), home / ".cache" / "ms-playwright",
               home / "Library" / "Caches" / "ms-playwright"]
    if env.get("LOCALAPPDATA"):
        pw_dirs.append(Path(env["LOCALAPPDATA"]) / "ms-playwright")
    chromium = False
    for p in pw_dirs:
        try:
            if p and Path(p).is_dir() and any(x.name.startswith("chromium") for x in Path(p).iterdir()):
                chromium = True
        except OSError:
            pass
    roots = [env.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    roots = [Path(r) for r in roots if r]
    chrome = _exists_any(["/opt/google/chrome/chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                          "/Applications/Google Chrome.app"] + [r / "Google/Chrome/Application/chrome.exe" for r in roots])
    edge = _exists_any(["/opt/microsoft/msedge/msedge", "/usr/bin/microsoft-edge", "/Applications/Microsoft Edge.app"]
                       + [r / "Microsoft/Edge/Application/msedge.exe" for r in roots])
    return [
        dict(id="chromium", label="Chromium", sub="Built-in browser, the most reliable choice", found=chromium),
        dict(id="chrome", label="Google Chrome", sub="Use your installed Chrome", found=chrome),
        dict(id="msedge", label="Microsoft Edge", sub="Use your installed Edge", found=edge),
    ]


def find_window_browser():
    """Find a Chromium-family browser so the dashboard can open as a standalone app window."""
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "chrome"):
        p = shutil.which(n)
        if p:
            return p
    cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        r = os.environ.get(k)
        if r:
            cands += [str(Path(r) / "Google/Chrome/Application/chrome.exe"),
                      str(Path(r) / "Microsoft/Edge/Application/msedge.exe")]
    for p in cands:
        if Path(p).exists():
            return p
    return None


# ================================================================ app state
class Sink:
    """Captures the bot's print() output (nothing goes to the terminal) and feeds the dashboard log."""

    def __init__(self, app):
        self.app, self.buf = app, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.app.log_line(line)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


class App:
    def __init__(self, workdir, home):
        self.workdir, self.home = Path(workdir), Path(home)
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()
        self.thread = None
        self.names = []
        self.fetched = None                 # {'me','following','followers'} read from the user's own account (memory only)
        self.fetch = dict(state="idle", stage="", n=0, total=0, error="", me="", counts={}, wait=0, want_followers=True)
        self.fetch_thread = None
        self.fetched_platform = "instagram"
        self.run_platform = "instagram"
        self.run_engine = "turbo"
        self.run_parallel = 1
        self.finish_evt = threading.Event()
        self.kind = ""
        self.saved_report = ""
        self.reset()

    def reset(self):
        with self.lock:
            self.phase = "idle"          # idle | login | running | finished
            self.target = 0
            self.stats = dict(unfollowed=0, already=0, not_found=0, failed=0)
            self.rows, self.log = [], []
            self.history, self.full_log = [], []      # everything that happened in this run (for the downloads)
            self.t1 = None                            # moment the run ended (so "elapsed" stops growing)
            self.saved_report = ""
            self.rest = None
            self.per_min, self.eta, self.left = 0.0, None, 0
            self.t0 = None
            self.reason, self.error = "", ""
            self.info = {}
            self.stopping = False
            self.challenge = False

    def snapshot(self):
        with self.lock:
            rest = None
            if self.rest and time.time() - self.rest["ts"] < 3:
                left = max(0.0, self.rest["left"] - (time.time() - self.rest["ts"]))
                rest = dict(left=round(left, 1), total=self.rest["total"], label=self.rest["label"])
            return dict(phase=self.phase, target=self.target, stats=dict(self.stats), rows=self.rows[-120:],
                        log=self.log[-60:], rest=rest, per_min=round(self.per_min, 1), eta=self.eta,
                        left=self.left, elapsed=((self.t1 or time.time()) - self.t0) if self.t0 else 0,
                        reason=self.reason, error=self.error, info=self.info,
                        stopping=self.stopping, challenge=self.challenge, names=len(self.names), cooldown=self.cooldown_left(), platform=self.run_platform, engine=self.run_engine, parallel=self.run_parallel,
                        cooldowns={p: round(self.cooldown_left(p)) for p in bot.PLATFORMS},
                        fetch=dict(self.fetch), downloads=self.download_info(), saved_report=self.saved_report)

    @staticmethod
    def cd_name(platform):
        return "cooldown.txt" if platform == "instagram" else f"cooldown_{platform}.txt"

    def cooldown_left(self, platform="instagram"):
        try:
            return max(0.0, float((self.home / self.cd_name(platform)).read_text().strip()) - time.time())
        except Exception:
            return 0.0

    # ---- events from the bot
    def log_line(self, line):
        t = ANSI.sub("", line).rstrip()
        if not t.strip() or RESULT_LINE.match(t) or BOXY.search(t):
            return
        kind = "bad" if t.strip().startswith(("\u2716", "x ")) else "warn" if t.strip().startswith(("\u25b2", "! ")) else "info"
        with self.lock:
            entry = dict(t=time.strftime("%H:%M:%S"), kind=kind, text=t.strip(" \u2714\u25cf\u25b2\u2716*!+x"))
            self.log.append(entry)
            self.log = self.log[-60:]
            self.full_log.append(f"{entry['t']}  {entry['text']}")
            if len(self.full_log) > 5000:
                del self.full_log[:1000]

    def on_event(self, kind, **d):
        with self.lock:
            if kind == "phase":
                self.phase = d["name"]
                if d["name"] == "running":
                    self.target = d.get("target", self.target)
                    self.info = dict(browser=d.get("browser"), tabs=d.get("tabs"), level=d.get("level"))
                    self.t0 = time.time()
            elif kind == "fetch":
                f = self.fetch
                if d.get("stage") == "waiting":
                    f["wait"] = d.get("wait", 0)
                else:
                    f["wait"] = 0
                    f["stage"] = d.get("stage", f["stage"])
                    if f["state"] in ("login", "reading"):
                        f["state"] = "login" if f["stage"] == "login" else "reading"
                    if "n" in d:
                        f["n"], f["total"] = d["n"], d.get("total", 0)
            elif kind == "challenge":
                self.challenge = bool(d.get("on"))
            elif kind == "rest":
                self.rest = dict(left=d["left"], total=d["total"], label=d.get("label", ""), ts=time.time())
            elif kind == "result":
                self.rest = None
                self.stats = d["stats"]
                self.per_min, self.eta, self.left = d["per_min"], d["eta"], d["left"]
                self.rows.append(dict(n=d["n"], user=d["user"], status=d["status"], detail=d["detail"],
                                      secs=round(d["secs"], 1), t=time.strftime("%H:%M:%S")))
                self.history.append(self.rows[-1])
                self.rows = self.rows[-400:]
            elif kind == "end":
                self.stats, self.left, self.reason = d["stats"], d["left"], d["reason"]
                self.t1 = time.time()

    # ---- list preparation
    def busy(self):
        with self.lock:
            return self.phase in ("login", "running") or self.fetch["state"] in ("login", "reading")

    def set_list(self, payload):
        r = self._set_list(payload)
        if r.get("ok"):
            keep = self.keep_set()
            with self.lock:
                r["protected"] = sum(1 for n in self.names if n.lower() in keep)       # these will be skipped by the run
        return r

    def _set_list(self, payload):
        if self.busy():
            return dict(ok=False, error="Wait for the current job to finish first.")
        kind = payload.get("kind")
        plat = payload.get("platform", "instagram")
        if plat not in bot.PLATFORMS:
            return dict(ok=False, error="Unknown platform.")
        bot.set_platform(plat)                       # username rules differ per platform
        if kind == "paste":
            tokens = re.split(r"[\s,;]+", payload.get("text", ""))
            names, bad, seen = [], [], set()
            for tok in tokens:
                if not tok.strip():
                    continue
                n = bot.norm_user(tok)
                if not n:
                    bad.append(tok[:40])
                elif n not in seen:
                    seen.add(n)
                    names.append(n)
            with self.lock:
                self.names = names
            return dict(ok=True, count=len(names), invalid=bad[:8], invalid_n=len(bad), sample=names[:5])
        if kind == "account":
            with self.lock:
                got = self.fetched
            if not got or not got.get("following") or self.fetched_platform != plat:
                return dict(ok=False, error=f"Collect your {bot.PLATFORMS[plat]['label']} lists first (button above).")
            ing = got["following"]
            ers = got.get("followers")
            mode = payload.get("mode", "following")
            if mode != "following" and not ers:
                return dict(ok=False, error="Fetch your followers too to use this option.")
            if mode == "non_followers" and (got.get("partial") or {}).get("followers"):
                return dict(ok=False, error="You stopped reading followers early, so 'Not following me back' would be wrong. Collect again to read them all.")
            fset = {n.lower() for n in (ers or [])}
            iset = {n.lower() for n in ing}
            if mode == "following":
                chosen = list(ing)
            elif mode == "mutual":
                chosen = [n for n in ing if n.lower() in fset]
            elif mode == "non_followers":
                chosen = [n for n in ing if n.lower() not in fset]
            else:  # all = everyone connected to you (people you follow + people who follow you)
                chosen = list(ing) + [n for n in ers if n.lower() not in iset]
            with self.lock:
                self.names, self.kind = chosen, "account"
            return dict(ok=True, count=len(chosen), sample=chosen[:5], mode=mode, me=got.get("me", ""))
        if kind == "export":
            if plat != "instagram":
                return dict(ok=False, error="Export files only work for Instagram.")
            files = payload.get("files") or {}
            d = self.workdir / "export"
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
            for fn, text in files.items():
                base = os.path.basename(fn).lower()
                if base == "following.json":
                    (d / "following.json").write_text(text, encoding="utf-8")
                elif re.fullmatch(r"followers_\d+\.json", base):
                    (d / base).write_text(text, encoding="utf-8")
            if not (d / "following.json").exists():
                return dict(ok=False, error="following.json is missing. In your Instagram export look in connections/followers_and_following/.")
            try:
                ents = bot.read_entries(d / "following.json")
            except Exception:
                return dict(ok=False, error="Could not read following.json. Did you export in JSON format?")
            followers = bot.load_followers(d)
            if not followers:
                return dict(ok=False, error="Please also add followers_1.json (and followers_2.json, ... if you have them).")
            seen, names = set(), []
            for n, _ in ents:
                k = n.lower()
                if k not in seen:
                    seen.add(k)
                    names.append(n)
            non_f = [n for n in names if n.lower() not in followers]
            mutual = [n for n in names if n.lower() in followers]
            mode = payload.get("mode", "non_followers")
            chosen = {"non_followers": non_f, "mutual": mutual, "all": names}.get(mode, non_f)
            with self.lock:
                self.names = chosen
            shutil.rmtree(d, ignore_errors=True)
            return dict(ok=True, count=len(chosen), sample=chosen[:5], mode=mode,
                        counts=dict(non_followers=len(non_f), mutual=len(mutual), all=len(names)))
        return dict(ok=False, error="Please provide a list first.")

    # ---- start / stop
    def start_fetch(self, cfg):
        browser = cfg.get("browser", "chromium")
        plat = cfg.get("platform", "instagram")
        if browser not in bot.BROWSERS or plat not in bot.PLATFORMS:
            return dict(ok=False, error="Unknown browser or platform.")
        if not bot.PLATFORMS[plat]["lists"]:
            return dict(ok=False, error=f"Reading your lists is not available for {bot.PLATFORMS[plat]['label']}. Paste the profiles instead.")
        with self.lock:
            if self.phase in ("login", "running") or self.fetch["state"] in ("login", "reading"):
                return dict(ok=False, error="Something is already running.")
            want = bool(cfg.get("followers", True))
            self.fetch = dict(state="login", stage="login", n=0, total=0, error="", me="", counts={}, wait=0, want_followers=want, platform=plat)
            self.fetched = None
            self.fetched_platform = plat
        self.stop_evt.clear()
        self.finish_evt.clear()
        kinds = ("following", "followers") if want else ("following",)
        self.fetch_thread = threading.Thread(target=self._fetch_worker, args=(browser, kinds, plat), daemon=True)
        self.fetch_thread.start()
        return dict(ok=True)

    def _fetch_worker(self, browser, kinds, plat="instagram"):
        d = self.workdir / "fetch"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        self.home.mkdir(parents=True, exist_ok=True)
        out = {}
        err = ""
        try:
            args = bot.parse_args(["--dir", str(d), "--browser", browser, "--profile", str(self.home / "ig_profile"), "--no-menu", "--platform", plat])
            bot.resolve_defaults(args, d)
            bot.STOP, bot.HOOK, bot.FINISH = self.stop_evt, self.on_event, self.finish_evt
            with contextlib.redirect_stdout(Sink(self)):
                with bot.pw_guard():
                    bot.fetch_lists(args, d, kinds, out)
        except bot.BotError as e:
            err = str(e)
        except Exception as e:  # noqa: BLE001
            err = f"Unexpected error ({type(e).__name__}): {e}"
        finally:
            bot.STOP = bot.HOOK = bot.FINISH = None
            shutil.rmtree(d, ignore_errors=True)
            with self.lock:
                ing, ers = out.get("following") or [], out.get("followers")
                tot = out.get("totals") or {}
                part = dict(out.get("partial") or {})
                if not ers:
                    ers = None if not part.get("followers") else ers      # nothing read yet -> no followers list
                if ers is not None and not err and not part.get("followers") and tot.get("followers") and len(ers) < 0.97 * tot["followers"]:
                    err = (f"Only {len(ers):,} of about {tot['followers']:,} followers could be read, so Mutual / Not-following-back "
                           "would be wrong. Please fetch again.")
                    out["followers"] = ers = None
                if ers is not None and part.get("followers") and not ers:
                    ers = None
                if ing:
                    self.fetched = dict(me=out.get("me", ""), following=list(ing), followers=list(ers) if ers is not None and not err else None,
                                        partial=part)
                    fs = {n.lower() for n in (self.fetched["followers"] or [])}
                    counts = dict(following=len(ing))
                    if self.fetched["followers"] is not None:
                        iset = {n.lower() for n in ing}
                        counts.update(mutual=sum(1 for n in ing if n.lower() in fs),
                                      all=len(ing) + sum(1 for n in self.fetched["followers"] if n.lower() not in iset),
                                      followers=len(self.fetched["followers"]))
                        if not part.get("followers"):          # "Not following me back" is only correct with the FULL followers list
                            counts["non_followers"] = sum(1 for n in ing if n.lower() not in fs)
                    self.fetch.update(counts=counts, me=out.get("me", ""), partial=part)
                self.fetch.update(state="error" if err else "done", error=err, wait=0)
                if err and ing:
                    self.fetch["error"] = err + " (the part that was already read is kept)"

    def start(self, cfg):
        with self.lock:
            if self.phase in ("login", "running") or self.fetch["state"] in ("login", "reading"):
                return dict(ok=False, error="The bot is already running.")
            if not self.names:
                return dict(ok=False, error="Please add the accounts to unfollow first (step 2).")
            names = list(self.names)
        keep = self.keep_set()
        protected = [n for n in names if n.lower() in keep]
        if protected:
            names = [n for n in names if n.lower() not in keep]
            if not names:
                return dict(ok=False, error="Every account on this list is in your protected list, so nothing would be unfollowed.")
        try:
            count = bot.parse_count(cfg.get("count", "all") or "all", len(names))
            gap = bot.parse_gap_text(str(cfg.get("rest", "12")))
            be = int(cfg.get("break_every") or 0)
            if be < 0:
                raise ValueError("The break interval cannot be negative")
            bm = bot.parse_minutes(str(cfg.get("break_dur") or "0")) if be > 0 else 0.0
            tabs = max(1, min(bot.MAX_TABS, int(cfg.get("tabs") or 2)))
            parallel = max(1, min(30, int(cfg.get("parallel") or 1)))
            browser = cfg.get("browser", "chromium")
            block = cfg.get("block", "off")
            if browser not in bot.BROWSERS or block not in bot.BLOCK_LEVELS:
                raise ValueError("Unknown browser or page-load option")
        except (ValueError, TypeError) as e:
            return dict(ok=False, error=str(e) or "Some settings are invalid")
        plat = cfg.get("platform", "instagram")
        if plat not in bot.PLATFORMS:
            return dict(ok=False, error="Unknown platform.")
        engine = cfg.get("engine", "turbo")
        auto_speed = bool(cfg.get("auto_speed", True))
        if engine not in ("turbo", "classic"):
            return dict(ok=False, error="Unknown engine.")
        left = self.cooldown_left(plat)
        if left > 0 and not cfg.get("ignore_cooldown"):
            return dict(ok=False, cooldown=left,
                        error=f"{bot.PLATFORMS[plat]['label']} blocked an action earlier. Wait {bot.fmt_dur(left)} or clear the cooldown.")
        with self.lock:                      # check again and claim the run in ONE step: a double click cannot start two bots
            if self.phase in ("login", "running") or self.fetch["state"] in ("login", "reading"):
                return dict(ok=False, error="The bot is already running.")
            self.reset()
            self.run_platform = plat
            self.run_engine = engine if plat == "instagram" else "classic"
            self.run_parallel = parallel if self.run_engine == "turbo" else 1
            self.stop_evt.clear()
            self.phase, self.target = "login", count
        self.thread = threading.Thread(target=self._worker, args=(names, count, gap, be, bm, tabs, browser, block, plat, engine, parallel, auto_speed), daemon=True)
        self.thread.start()
        return dict(ok=True, count=count, protected=len(protected))

    def enough(self):
        """'That is enough': stop reading lists and keep what has been read so far."""
        with self.lock:
            if self.fetch["state"] != "reading":
                return dict(ok=False, error="Nothing to finish yet.")
        self.finish_evt.set()
        return dict(ok=True)

    def stop(self):
        with self.lock:
            if self.phase in ("login", "running"):
                self.stopping = True
        self.stop_evt.set()

    def _worker(self, names, count, gap, be, bm, tabs, browser, block, plat="instagram", engine="turbo", parallel=1, auto_speed=True):
        d = self.workdir / "run"
        cdn = self.cd_name(plat)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            (d / bot.LIST_NAME).write_text("\n".join(names) + "\n", encoding="utf-8")
            if (self.home / cdn).exists():
                shutil.copyfile(self.home / cdn, d / cdn)
            argv = ["--dir", str(d), "--mode", "all", "--limit", str(count), "--gap", *[repr(float(x)) for x in gap],
                    "--break-every", str(be), "--break-min", repr(float(bm)), "--tabs", str(tabs), "--browser", browser,
                    "--block", block, "--profile", str(self.home / "ig_profile"), "--no-menu", "--yes", "--max-fails", "3",
                    "--platform", plat, "--engine", engine, "--parallel", str(parallel)] + (["--auto-speed"] if auto_speed and engine == "turbo" else [])
            args = bot.parse_args(argv)
            bot.resolve_defaults(args, d)
            args.gap = list(gap)
            bot.set_platform(plat)
            L = bot.build_lists(args, d)
            if not L["pending"]:
                raise bot.BotError("Nothing left to do for this list.")
            bot.STOP, bot.HOOK = self.stop_evt, self.on_event
            with contextlib.redirect_stdout(Sink(self)):
                with bot.pw_guard():
                    bot.run(args, d, L)
        except bot.BotError as e:
            with self.lock:
                self.error = str(e)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.error = f"Unexpected error ({type(e).__name__}): {e}"
                self.log.append(dict(t=time.strftime("%H:%M:%S"), kind="bad", text=traceback.format_exc()[-600:]))
        finally:
            bot.STOP = bot.HOOK = None
            try:                                   # a block / cooldown must survive even if the run ended with an error
                if (d / cdn).exists():
                    shutil.copyfile(d / cdn, self.home / cdn)
            except OSError:
                pass
            shutil.rmtree(d, ignore_errors=True)
            with self.lock:
                if self.t0 and not self.t1:
                    self.t1 = time.time()
                self.phase, self.rest, self.stopping = "finished", None, False
                if not self.reason and not self.error and not self.rows:
                    self.reason = "Nothing was done."
            self.save_report()

    # ---- protected accounts (never unfollowed), saved on this computer
    def keep_path(self):
        return self.home / "keep.txt"

    def keep_text(self):
        try:
            return self.keep_path().read_text(encoding="utf-8")
        except OSError:
            return ""

    def keep_set(self):
        return {ln.strip().lower() for ln in self.keep_text().splitlines() if ln.strip()}

    @staticmethod
    def _norm_keep(tok):
        t = tok.strip().lower()
        t = re.sub(r"^(?:https?://)?(?:www\.|m\.)?(?:instagram\.com/(?:_u/)?|tiktok\.com/)", "", t)
        t = t.lstrip("@").split("?")[0].split("#")[0].strip("/")
        return t if re.fullmatch(r"[a-z0-9._]{1,30}", t) else ""

    def set_keep(self, text):
        seen, names, bad = set(), [], 0
        for tok in re.split(r"[\s,;]+", text or ""):
            if not tok.strip():
                continue
            n = self._norm_keep(tok)
            if not n:
                bad += 1
            elif n not in seen:
                seen.add(n)
                names.append(n)
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            self.keep_path().write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
        except OSError as e:
            return dict(ok=False, error=f"Could not save the protected list: {e}")
        return dict(ok=True, count=len(names), invalid=bad)

    # ---- downloads: results, lists, log
    @staticmethod
    def _cell(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # spreadsheets must never run a cell as a formula

    def _rows_csv(self, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["#", "time", "username", "result", "details", "seconds"])
        for r in rows:
            w.writerow([r.get("n"), r.get("t"), self._cell(r.get("user")), r.get("status"), self._cell(r.get("detail")), r.get("secs")])
        return "﻿" + buf.getvalue()                                   # BOM: Excel opens it with the right encoding

    def download_info(self):
        with self.lock:
            h = self.history
            got = self.fetched or {}
            return dict(results=len(h), failed=sum(1 for r in h if r["status"] == "failed"),
                        unfollowed=sum(1 for r in h if r["status"] == "unfollowed"), names=len(self.names),
                        lists=bool(got.get("following")))

    def _assemble(self):
        """Every download that exists right now: {name: bytes}"""
        with self.lock:
            hist, log, names, got = list(self.history), list(self.full_log), list(self.names), dict(self.fetched or {})
        out = {}

        def txt(key, lines):
            if lines:
                out[key] = ("\n".join(lines) + "\n").encode("utf-8")

        if hist:
            out["results.csv"] = self._rows_csv(hist).encode("utf-8")
        txt("unfollowed.txt", [r["user"] for r in hist if r["status"] == "unfollowed"])
        txt("skipped.txt", [r["user"] for r in hist if r["status"] in ("already", "not_found")])
        txt("failed.txt", [r["user"] for r in hist if r["status"] == "failed"])
        txt("log.txt", log)
        txt("list.txt", names)
        ing, ers = got.get("following") or [], got.get("followers")
        txt("following.txt", ing)
        if ers:
            fs = {n.lower() for n in ers}
            txt("followers.txt", ers)
            txt("mutual.txt", [n for n in ing if n.lower() in fs])
            if not (got.get("partial") or {}).get("followers"):                      # only correct with the FULL followers list
                txt("non_followers.txt", [n for n in ing if n.lower() not in fs])
        return out

    def make_download(self, name):
        """-> (filename, bytes, content type) or None when there is nothing to give yet"""
        name = (name or "").strip()
        stamp = time.strftime("%Y-%m-%d_%H%M")
        files = self._assemble()
        if name == "all.zip":
            if not files:
                return None
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for k, v in files.items():
                    z.writestr(k, v)
            return f"unfollow_everything_{stamp}.zip", buf.getvalue(), "application/zip"
        if name not in files:
            return None
        base, ext = name.rsplit(".", 1)
        ctype = "text/csv; charset=utf-8" if ext == "csv" else "text/plain; charset=utf-8"
        return f"unfollow_{base}_{stamp}.{ext}", files[name], ctype

    def save_report(self):
        """Auto-save the run's report, so the results are never lost when the app is closed."""
        try:
            with self.lock:
                hist, plat = list(self.history), self.run_platform
            if not hist:
                return
            rd = self.home / "reports"
            rd.mkdir(parents=True, exist_ok=True)
            path = rd / f"unfollow_{plat}_{time.strftime('%Y-%m-%d_%H%M%S')}.csv"
            path.write_bytes(self._rows_csv(hist).encode("utf-8"))
            for old in sorted(rd.glob("unfollow_*.csv"))[:-REPORT_KEEP]:
                old.unlink()
            with self.lock:
                self.saved_report = str(path)
        except Exception:  # noqa: BLE001
            pass

    def open_reports(self):
        rd = self.home / "reports"
        try:
            rd.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(rd))  # noqa: S606  (Windows only)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(rd)])
            else:
                subprocess.Popen(["xdg-open", str(rd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, error=f"Could not open the folder ({type(e).__name__}). It is: {rd}")
        return dict(ok=True, path=str(rd))

    def clear_cooldown(self, platform="instagram"):
        try:
            (self.home / self.cd_name(platform if platform in bot.PLATFORMS else "instagram")).unlink()
        except OSError:
            pass

    def forget_login(self):
        if self.busy():
            return dict(ok=False, error="Stop the bot first.")
        n = 0
        for p in self.home.glob("ig_profile*"):
            shutil.rmtree(p, ignore_errors=True)
            n += 1
        return dict(ok=True, removed=n)


# ================================================================ http
def make_handler(app, token, page, shutdown):
    class H(BaseHTTPRequestHandler):
        server_version = "UnfollowByLykoon"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def _authed(self):
            q = parse_qs(urlparse(self.path).query)
            got = self.headers.get("X-Token") or (q.get("t") or [""])[0]
            return secrets.compare_digest(got.encode(), token.encode())

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._authed():
                return self._send(403, "<h3>This link needs its access token. Open the full link printed in the terminal.</h3>",
                                  "text/html; charset=utf-8")
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/api/state":
                s = app.snapshot()
                s["browsers"] = detect_browsers()
                s["version"] = APP_VERSION
                return self._json(s)
            if path == "/api/keep":
                return self._json(dict(ok=True, text=app.keep_text()))
            if path.startswith("/download/"):
                got = app.make_download(path[len("/download/"):])
                if not got:
                    return self._json(dict(ok=False, error="Nothing to download yet."), 404)
                fname, data, ctype = got
                return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"'})
            self._json(dict(ok=False, error="not found"), 404)

        def do_POST(self):
            if not self._authed():
                return self._json(dict(ok=False, error="token"), 403)
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:
                return self._json(dict(ok=False, error="bad request"), 400)
            if n > MAX_BODY:
                return self._json(dict(ok=False, error="That file is too large"), 413)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(dict(ok=False, error="bad json"), 400)
            if not isinstance(body, dict):
                return self._json(dict(ok=False, error="bad json"), 400)
            path = urlparse(self.path).path
            try:
                return self._post(path, body)
            except Exception as e:  # noqa: BLE001
                return self._json(dict(ok=False, error=f"Server error ({type(e).__name__}): {e}"), 500)

        def _post(self, path, body):
            if path == "/api/list":
                return self._json(app.set_list(body))
            if path == "/api/start":
                return self._json(app.start(body))
            if path == "/api/fetch/start":
                return self._json(app.start_fetch(body))
            if path == "/api/fetch/enough":
                return self._json(app.enough())
            if path == "/api/stop":
                app.stop()
                return self._json(dict(ok=True))
            if path == "/api/new":
                if app.phase == "finished":
                    app.reset()
                return self._json(dict(ok=True))
            if path == "/api/cooldown/clear":
                app.clear_cooldown(body.get("platform", "instagram"))
                return self._json(dict(ok=True))
            if path == "/api/forget-login":
                return self._json(app.forget_login())
            if path == "/api/keep":
                return self._json(app.set_keep(body.get("text", "")))
            if path == "/api/open-reports":
                return self._json(app.open_reports())
            if path == "/api/quit":
                app.stop()
                self._json(dict(ok=True))
                threading.Thread(target=shutdown, daemon=True).start()
                return
            self._json(dict(ok=False, error="not found"), 404)

    return H


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def serve(host="127.0.0.1", port=8765, open_window=True, token=None, home=HOME_DIR, quiet=False):
    """Start the server. Returns (server, app, url).  (also used by the tests)"""
    workdir = tempfile.mkdtemp(prefix="lykoon_unfollow_")
    atexit.register(shutil.rmtree, workdir, True)
    app = App(workdir, home)
    token = token or secrets.token_urlsafe(9)
    page = (PAGE.replace("__LOGO__", LOGO_SVG)
                .replace("__FAVICON__", "data:image/svg+xml," + quote(LOGO_SVG))
                .replace("__NAME__", APP_NAME))
    srv = None
    for p in range(port, port + 30):
        try:
            holder = {}
            handler = make_handler(app, token, page, lambda: holder["srv"].shutdown())
            srv = ThreadingHTTPServer((host, p), handler)
            holder["srv"] = srv
            port = p
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("Could not find a free port.")
    shown = lan_ip() if host == "0.0.0.0" else "127.0.0.1"
    url = f"http://{shown}:{port}/?t={token}"
    local = f"http://127.0.0.1:{port}/?t={token}"
    if not quiet:
        print(f"\n  {APP_NAME} {APP_VERSION}  -  made by Lykoon Company")
        print(f"  Dashboard : {local}")
        if host == "0.0.0.0":
            print(f"  On phone  : {url}   (same Wi-Fi)")
        print("  To quit press Ctrl+C here (or use 'Quit app' in the dashboard).\n")
    if open_window:
        b = find_window_browser()
        try:
            if b:
                subprocess.Popen([b, f"--app={local}", "--window-size=1180,900"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                webbrowser.open(local)
        except Exception:
            webbrowser.open(local)
    return srv, app, local


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"{APP_NAME} (graphical app)")
    ap.add_argument("--lan", action="store_true", help="also allow control from a phone/another device on the same Wi-Fi")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the window automatically")
    a = ap.parse_args(argv)
    srv, app, url = serve("0.0.0.0" if a.lan else "127.0.0.1", a.port, not a.no_open)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        srv.server_close()
        if app.thread and app.thread.is_alive():
            app.thread.join(timeout=15)
        print("Goodbye. Temporary files removed.")


# ================================================================ dashboard page
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070b14"><meta name="apple-mobile-web-app-capable" content="yes">
<title>__NAME__</title><link rel="icon" href="__FAVICON__"><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Unbounded:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{color-scheme:dark;--bg:#070b14;--bg2:#0c1424;--card:#0e1626;--raise:#142038;--ink:#e8f0fb;--mute:#8a9ab4;--line:#1c2a44;--soft:#121c30;--acc:#62d8ff;--acc2:#3d8bff;--on:#04121f;--accsoft:rgba(98,216,255,.10);--ok:#37d9a0;--warn:#ffa24d;--bad:#ff6b6b;--glow:rgba(61,139,255,.35);--shadow:0 0 0 1px rgba(255,255,255,.02),0 18px 40px -24px rgba(0,0,0,.8)}
:root[data-theme=light]{color-scheme:light;--bg:#edf3f9;--bg2:#e2ebf5;--card:#ffffff;--raise:#f6f9fc;--ink:#0a1424;--mute:#5a6b85;--line:#d9e3ef;--soft:#eef3f9;--acc:#0a78c2;--acc2:#2f6bff;--on:#ffffff;--accsoft:rgba(10,120,194,.09);--ok:#0b9a6a;--warn:#c76a00;--bad:#d93a3a;--glow:rgba(10,120,194,.25);--shadow:0 1px 2px rgba(10,20,36,.05),0 16px 34px -22px rgba(10,20,36,.25)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;color:var(--ink);font:15px/1.55 Manrope,"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",Arial,sans-serif;min-height:100vh;background:radial-gradient(900px 480px at 85% -8%,rgba(61,139,255,.16),transparent 62%),radial-gradient(700px 420px at -10% 8%,rgba(98,216,255,.07),transparent 60%),var(--bg);background-attachment:fixed}
:root[data-theme=light] body{background:radial-gradient(900px 480px at 85% -8%,rgba(47,107,255,.10),transparent 62%),var(--bg)}
button,input,select,textarea{font:inherit;color:inherit}
h1,h2,h3,.num,.ring b,.tile b,.rest .t,.opt .c{font-family:Unbounded,"Bahnschrift","Segoe UI Variable Display","Segoe UI",system-ui,sans-serif}
.wrap{max-width:1140px;margin:0 auto;padding:0 20px calc(40px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:12;display:flex;align-items:center;gap:14px;margin:0 -20px 18px;padding:12px 20px;background:color-mix(in srgb,var(--bg) 78%,transparent);backdrop-filter:blur(14px) saturate(1.3);-webkit-backdrop-filter:blur(14px) saturate(1.3);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand svg{width:40px;height:40px;flex:none;border-radius:12px}
.brand h1{font-size:15px;line-height:1.15;margin:0;font-weight:600;letter-spacing:-.01em}.brand h1 span{color:var(--acc)}
.brand small{display:block;color:var(--mute);font-size:12px;margin-top:3px}
.tools{margin-left:auto;display:flex;align-items:center;gap:10px}
.pill{padding:7px 13px;border-radius:99px;background:var(--card);border:1px solid var(--line);font-size:12.5px;font-weight:700;display:flex;align-items:center;gap:8px;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mute)}
.pill.run .dot{background:var(--ok);box-shadow:0 0 0 0 var(--ok);animation:ping 1.6s infinite}.pill.login .dot{background:var(--warn);animation:blink 1.1s infinite}.pill.bad .dot{background:var(--bad)}.pill.done .dot{background:var(--acc)}
@keyframes ping{70%{box-shadow:0 0 0 7px transparent}100%{box-shadow:0 0 0 0 transparent}}@keyframes blink{50%{opacity:.25}}
.icon-btn{width:38px;height:38px;border-radius:12px;border:1px solid var(--line);background:var(--card);cursor:pointer;display:grid;place-items:center}
.icon-btn:hover{border-color:var(--acc);color:var(--acc)}.icon-btn svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}

/* hero: the moon and the wolf */
.hero{position:relative;overflow:hidden;display:flex;align-items:center;gap:20px;margin:0 0 22px;padding:38px 36px;min-height:230px;border-radius:28px;color:#eaf3ff;background:linear-gradient(165deg,#0f1c38 0%,#09122a 55%,#060c1c 100%);border:1px solid #1b2c4d;box-shadow:var(--shadow)}
.hero .aurora{position:absolute;inset:0;pointer-events:none;background:
 radial-gradient(1.5px 1.5px at 8% 22%,#fff,transparent),radial-gradient(1px 1px at 17% 64%,#cfe3ff,transparent),radial-gradient(1.5px 1.5px at 29% 18%,#fff,transparent),radial-gradient(1px 1px at 38% 78%,#bcd4ff,transparent),
 radial-gradient(1px 1px at 47% 30%,#fff,transparent),radial-gradient(1.5px 1.5px at 58% 70%,#fff,transparent),radial-gradient(1px 1px at 66% 14%,#cfe3ff,transparent),radial-gradient(1px 1px at 74% 52%,#fff,transparent),
 radial-gradient(1.5px 1.5px at 91% 24%,#fff,transparent),radial-gradient(1px 1px at 95% 80%,#bcd4ff,transparent),radial-gradient(1px 1px at 5% 88%,#fff,transparent),radial-gradient(1px 1px at 52% 90%,#cfe3ff,transparent);animation:twinkle 6s ease-in-out infinite alternate}
@keyframes twinkle{to{opacity:.55}}
.hero::before{content:"";position:absolute;right:-60px;top:-70px;width:340px;height:340px;border-radius:50%;background:radial-gradient(circle at 34% 32%,#ffffff 0%,#e4efff 28%,#a9c6f2 62%,#6f93cf 100%);box-shadow:0 0 90px 30px rgba(120,170,255,.28),0 0 220px 90px rgba(61,139,255,.18);opacity:.95}
.hero::after{content:"";position:absolute;right:30px;top:40px;width:56px;height:56px;border-radius:50%;background:rgba(80,110,170,.18);box-shadow:-70px 90px 0 -14px rgba(80,110,170,.16),40px 120px 0 -8px rgba(80,110,170,.14)}
.hero-in{position:relative;z-index:1;flex:1;min-width:0;max-width:600px}
.hero h2{font-size:clamp(23px,3.2vw,33px);line-height:1.18;letter-spacing:-.025em;margin:0 0 12px;font-weight:600}.hero h2 em{font-style:normal}
.hero p{color:#b8c9e4;max-width:520px;margin:0;font-size:15px}
.feats{display:flex;gap:8px;flex-wrap:wrap;margin-top:20px}.feats span{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;padding:6px 12px;border-radius:99px;border:1px solid rgba(160,190,240,.28);color:#d4e2f8;background:rgba(10,20,45,.55);backdrop-filter:blur(6px)}
.feats svg{width:14px;height:14px;stroke:#62d8ff;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.hero-art{position:absolute;z-index:1;right:46px;top:50%;transform:translateY(-50%);flex:none}.hero-art svg{width:132px;height:132px;border-radius:34px;box-shadow:0 20px 50px rgba(2,8,24,.55),0 0 0 1px rgba(255,255,255,.25);animation:floaty 7s ease-in-out infinite}
@media(max-width:760px){.hero{padding:26px 20px;min-height:0}.hero::before{right:-140px;top:-140px;opacity:.22}.hero::after{display:none}.hero-art{display:none}}
@keyframes floaty{50%{transform:translateY(-6px)}}

.grid{display:grid;gap:18px;grid-template-columns:minmax(0,1fr);align-items:start}
@media(min-width:960px){.grid{grid-template-columns:minmax(0,1fr) 344px}.sticky{position:sticky;top:78px}}
.stack{display:grid;gap:16px;grid-template-columns:minmax(0,1fr)}.grid>*,.stack>*{min-width:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);padding:22px;transition:border-color .2s}
.card>h3{margin:0 0 3px;font-size:14.5px;font-weight:600;display:flex;align-items:center;gap:12px;letter-spacing:-.005em}
.num{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:600;border:1.5px solid var(--acc);color:var(--acc);background:transparent;flex:none;transition:background .3s,color .3s}
.card.done .num{background:var(--acc);color:var(--on);font-size:0;border-color:var(--acc)}.card.done .num::after{content:"";width:11px;height:6px;border-left:2.4px solid var(--on);border-bottom:2.4px solid var(--on);transform:rotate(-45deg) translate(1px,-1px)}
.sub{color:var(--mute);font-size:13.5px;margin:4px 0 16px}
.seg{display:flex;background:var(--soft);padding:4px;border-radius:13px;gap:4px;margin-bottom:14px;border:1px solid var(--line)}
.seg button{flex:1;border:0;background:none;color:var(--mute);padding:9px 8px;border-radius:10px;cursor:pointer;font-weight:700;font-size:13.5px;min-width:0;line-height:1.25;transition:background .15s,color .15s}
.seg button.on{background:var(--raise);color:var(--ink);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc) 45%,var(--line))}
textarea,input[type=text],input[type=number],select{width:100%;border:1px solid var(--line);background:var(--soft);border-radius:12px;padding:11px 13px;outline:none;transition:border-color .15s,box-shadow .15s}
textarea{min-height:140px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.55}
textarea:focus,input:focus,select:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--accsoft)}
label.f{display:block;font-size:12.5px;font-weight:700;color:var(--mute);margin:0 0 6px}
.cols{display:grid;gap:12px;grid-template-columns:1fr 1fr}@media(max-width:520px){.cols{grid-template-columns:1fr}}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.chip{border:1px solid var(--line);background:var(--soft);padding:6px 13px;border-radius:99px;font-size:13px;cursor:pointer;font-weight:600}
.chip:hover{border-color:var(--acc);color:var(--acc)}.chip.on{background:var(--accsoft);border-color:var(--acc);color:var(--acc);font-weight:800}
.drop{border:1.5px dashed var(--line);border-radius:16px;padding:26px 14px;text-align:center;color:var(--mute);cursor:pointer;transition:.15s;background:var(--soft)}
.drop svg{width:30px;height:30px;stroke:var(--acc);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;margin-bottom:6px}
.drop.over,.drop:hover{border-color:var(--acc);background:var(--accsoft)}.drop b{color:var(--ink);display:block}
.msg{font-size:13.5px;margin-top:12px;color:var(--mute)}.msg b{color:var(--ink)}.msg.ok b.n{color:var(--ok)}.msg.err{color:var(--bad)}
.presets{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}
.preset,.bcard,.opt,.plat{border:1.5px solid var(--line);background:var(--soft);cursor:pointer;text-align:left;transition:border-color .15s,background .15s,transform .12s}
.preset{border-radius:14px;padding:11px 12px}.preset b{display:block;font-size:14px}.preset span{font-size:12px;color:var(--mute)}
.preset:hover,.bcard:hover,.opt:hover,.plat:hover{border-color:color-mix(in srgb,var(--acc) 60%,var(--line))}
.preset.on,.bcard.on,.opt.on,.plat.on{border-color:var(--acc);background:var(--accsoft)}
.bro{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.bcard{border-radius:14px;padding:12px 13px}
.bcard b{display:block;font-size:14.5px}.bcard span{font-size:12.5px;color:var(--mute)}.bcard em{display:inline-block;font-style:normal;font-size:11.5px;margin-top:6px;padding:2px 9px;border-radius:99px;background:var(--bg2);color:var(--mute)}.bcard em.ok{color:var(--ok)}
details{margin-top:16px}summary{cursor:pointer;color:var(--acc);font-weight:800;font-size:13.5px}
.btn{border:0;border-radius:14px;padding:14px 18px;font-weight:800;font-size:15.5px;cursor:pointer;width:100%;color:var(--on);background:linear-gradient(180deg,var(--acc),color-mix(in srgb,var(--acc) 78%,var(--acc2)));box-shadow:0 12px 28px -12px var(--glow);transition:transform .1s,filter .15s}
.btn:hover:not(:disabled){filter:brightness(1.08);transform:translateY(-1px)}.btn:active:not(:disabled){transform:none}.btn:disabled{opacity:.35;cursor:not-allowed;box-shadow:none}
.btn.stop{background:linear-gradient(180deg,#ff8f5a,#ff6b4a);color:#2a0b02;box-shadow:0 12px 28px -12px rgba(255,107,74,.5)}
.btn.ghost{background:var(--soft);color:var(--ink);box-shadow:none;border:1px solid var(--line);font-size:14.5px;padding:12px 14px}
.sumrow{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}.sumrow:last-of-type{border:0}.sumrow span{color:var(--mute)}.sumrow b{text-align:right;font-variant-numeric:tabular-nums}
#sumcard{border-color:color-mix(in srgb,var(--acc) 26%,var(--line))}#sumcard>h3{margin-bottom:8px}
.note{margin-top:14px;padding:11px 13px;border-radius:12px;background:var(--soft);font-size:12.5px;color:var(--mute)}
.warn{margin-top:12px;padding:11px 13px;border-radius:12px;background:rgba(255,162,77,.1);border:1px solid rgba(255,162,77,.25);color:var(--warn);font-size:13px}
.alert{margin-bottom:16px;padding:13px 16px;border-radius:16px;font-size:14px;border:1px solid transparent}.alert.bad{background:rgba(255,107,107,.09);color:var(--bad);border-color:rgba(255,107,107,.28)}.alert.ok{background:rgba(55,217,160,.09);color:var(--ok);border-color:rgba(55,217,160,.28)}.alert.info{background:var(--accsoft);color:var(--acc);border-color:color-mix(in srgb,var(--acc) 30%,transparent)}
.live{display:grid;grid-template-columns:150px 1fr;gap:22px;align-items:center}@media(max-width:520px){.live{grid-template-columns:1fr;justify-items:center;text-align:center}}
.ring{position:relative;width:150px;height:150px}.ring svg{width:150px;height:150px;transform:rotate(-90deg);filter:drop-shadow(0 0 12px var(--glow))}
.ring circle{fill:none;stroke-width:10;stroke-linecap:round}.ring .bg{stroke:var(--soft)}.ring .fg{stroke:url(#rg);transition:stroke-dashoffset .5s}
.ring div{position:absolute;inset:0;display:grid;place-content:center;text-align:center}.ring b{font-size:30px;font-weight:600;line-height:1}.ring span{font-size:12px;color:var(--mute);margin-top:4px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.tile{background:var(--soft);border:1px solid var(--line);border-radius:14px;padding:12px 6px;text-align:center}.tile b{display:block;font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}.tile span{font-size:11.5px;color:var(--mute)}
.tile.g b{color:var(--ok)}.tile.y b{color:var(--warn)}.tile.r b{color:var(--bad)}
.meta{display:flex;justify-content:space-between;font-size:13px;color:var(--mute);gap:8px;flex-wrap:wrap;margin-top:12px}
.rest{margin-top:16px;padding:13px 15px;border-radius:16px;background:var(--soft);border:1px solid var(--line);display:flex;align-items:center;gap:14px}
.rest .t{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;min-width:86px}.rest .l{font-size:13px;color:var(--mute);text-transform:capitalize}
.bar{flex:1;height:7px;border-radius:99px;background:var(--bg2);overflow:hidden}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc2),var(--acc));border-radius:99px;transition:width .25s}
.logs{margin-top:12px;font-size:13px;color:var(--mute)}.logs div{padding:2px 0}.logs .warn{background:none;border:0;color:var(--warn);margin:0;padding:2px 0}.logs .bad{color:var(--bad)}
.feedhead{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}.feedhead h3{margin:0;font-size:14.5px;font-weight:600}
.mini{display:flex;gap:4px}.mini button{border:0;background:none;color:var(--mute);font-size:12px;font-weight:700;padding:5px 9px;border-radius:8px;cursor:pointer}.mini button.on{background:var(--accsoft);color:var(--acc)}
.feed{list-style:none;margin:0;padding:0;max-height:520px;overflow:auto}
.feed li{display:flex;gap:11px;align-items:center;padding:10px 6px;border-bottom:1px solid var(--line);font-size:14px;border-radius:10px}.feed li:last-child{border:0}.feed li:hover{background:var(--soft)}
.feed .ic{flex:none;width:24px;height:24px;border-radius:50%;display:grid;place-items:center;background:var(--ok)}.feed .ic svg{width:13px;height:13px;stroke:#03140d;fill:none;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}
.feed .y .ic{background:var(--warn)}.feed .r .ic{background:var(--bad)}
.feed .u{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}.feed .s{color:var(--mute);font-size:12.5px;text-align:right;white-space:nowrap}
.empty{color:var(--mute);font-size:13.5px;text-align:center;padding:26px 6px}
footer{margin-top:34px;padding-top:20px;border-top:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap;color:var(--mute);font-size:13px}
footer svg{width:28px;height:28px;border-radius:9px}footer .by{font-weight:800;color:var(--ink)}footer a.mail{color:var(--acc);font-weight:700;text-decoration:none}footer a.mail:hover{text-decoration:underline}
footer .links{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}footer .links button{background:none;border:0;color:var(--acc);cursor:pointer;font-size:13px;padding:4px 8px;border-radius:8px;font-weight:700}footer .links button:hover{background:var(--accsoft)}
.modal{position:fixed;inset:0;background:rgba(2,5,12,.66);backdrop-filter:blur(5px);display:grid;place-items:center;padding:18px;z-index:20}
.modal .box{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:26px;max-width:440px;width:100%;box-shadow:0 30px 80px rgba(0,0,0,.5);animation:rise .22s both}
@keyframes rise{from{opacity:0;transform:translateY(8px)}}
.modal h3{margin:0 0 6px;font-size:17px;font-weight:600}.modal p{color:var(--mute);margin:0 0 16px;font-size:14px}.modal .row{display:grid;grid-template-columns:1fr 1.4fr;gap:10px;margin-top:18px}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:12px 18px;border-radius:14px;font-size:14px;font-weight:600;max-width:92vw;box-shadow:var(--shadow);z-index:30}
.acct{display:flex;gap:14px;align-items:flex-start;padding:14px;border-radius:16px;background:var(--accsoft);border:1px solid color-mix(in srgb,var(--acc) 22%,var(--line));margin-bottom:14px}
.acct-ic{flex:none;width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:var(--acc)}
.acct-ic svg{width:22px;height:22px;stroke:var(--on);fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.acct-tx b{display:block;font-size:14.5px}.acct-tx span{display:block;color:var(--mute);font-size:13px;margin-top:2px}
.chk{display:flex;gap:9px;align-items:flex-start;font-size:13.5px;margin:0 0 12px;cursor:pointer}.chk input{margin-top:3px;accent-color:var(--acc);width:16px;height:16px}.chk small{color:var(--mute)}
.fprog{margin-top:14px;padding:13px 14px;border-radius:14px;background:var(--soft);border:1px solid var(--line)}.fprog .bar{margin-top:10px}
.frow{display:flex;align-items:center;gap:12px;font-size:13.5px}.frow span{flex:1}.frow .btn{width:auto;padding:8px 14px;font-size:13px}
.opts{display:grid;gap:9px;margin-top:14px}
.opt{display:flex;align-items:center;gap:12px;border-radius:14px;padding:12px 14px}
.opt .k{flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:12.5px;background:var(--bg2);color:var(--acc)}.opt.on .k{background:var(--acc);color:var(--on)}
.opt .t{flex:1;min-width:0}.opt .t b{display:block;font-size:14.5px}.opt .t span{font-size:12.5px;color:var(--mute)}.opt .c{font-weight:600;font-size:16px;font-variant-numeric:tabular-nums}.opt.dis{opacity:.45;pointer-events:none}
.qlabel{font-weight:800;font-size:14px;margin:4px 0 10px}.opt .n{display:inline-block;margin-left:8px;font-size:11px;font-weight:800;color:var(--acc);background:var(--bg2);border-radius:99px;padding:2px 8px}
.plats{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.plat{display:flex;align-items:center;gap:13px;border-radius:16px;padding:14px 15px;color:inherit;font:inherit}
.plat .pi{flex:none;width:40px;height:40px;border-radius:12px;display:grid;place-items:center;background:var(--bg2)}.plat.on .pi{background:var(--acc)}
.plat .pi svg{width:22px;height:22px;stroke:var(--acc);fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}.plat.on .pi svg{stroke:var(--on)}
.plat b{display:block;font-size:15px}.plat small{display:block;font-size:12px;color:var(--mute);margin-top:1px}.plat .xp{display:inline-block;margin-left:7px;font-size:10.5px;font-weight:800;color:var(--warn);background:rgba(255,162,77,.13);border-radius:99px;padding:1px 8px;vertical-align:middle}
.turbo{display:flex;align-items:flex-start;gap:12px;padding:13px 14px;border-radius:14px;background:var(--accsoft);border:1px solid color-mix(in srgb,var(--acc) 22%,var(--line));margin-top:14px;font-size:13px}.turbo svg{flex:none;width:20px;height:20px;margin-top:1px;stroke:var(--acc);fill:none;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}.turbo b{display:block;font-size:14px}.turbo span{color:var(--mute)}
::selection{background:var(--acc);color:var(--on)}
*{scrollbar-width:thin;scrollbar-color:var(--line) transparent}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:10px}
@media(max-width:520px){.seg button{padding:8px 4px;font-size:12.5px}.wrap{padding-left:14px;padding-right:14px}header{margin-left:-14px;margin-right:-14px;padding-left:14px;padding-right:14px}.card{padding:17px}.hero{padding:24px 18px}.tiles{gap:6px}}
html,body{overflow-x:hidden}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
span.pl{display:inline!important;margin:0!important;font-size:inherit!important;color:inherit!important}
#parbox input{max-width:50%}@media(max-width:520px){#parbox input{max-width:100%}}
.livehead{display:flex;align-items:center;gap:10px;margin:0 0 18px}.livehead .lt{font-family:Unbounded,system-ui,sans-serif;font-weight:600;font-size:15px}
.livehead .lm{font-size:11.5px;font-weight:800;color:var(--acc);background:var(--accsoft);border:1px solid color-mix(in srgb,var(--acc) 30%,transparent);border-radius:99px;padding:3px 10px}
#live{background:linear-gradient(180deg,color-mix(in srgb,var(--acc2) 8%,var(--card)),var(--card) 55%);border-color:color-mix(in srgb,var(--acc) 24%,var(--line))}
.ring{width:164px;height:164px}.ring svg{width:164px;height:164px}.live{grid-template-columns:164px 1fr}
.logs div:first-child{color:var(--acc)}
/* ---- premium layer ---- */
.card{position:relative;background:linear-gradient(180deg,color-mix(in srgb,var(--card) 92%,#fff 8%),var(--card) 42%);backdrop-filter:blur(8px)}
:root[data-theme=light] .card{background:var(--card)}
.card::before{content:"";position:absolute;left:22px;right:22px;top:-1px;height:1px;background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--acc) 65%,transparent),transparent);opacity:.55;pointer-events:none}
#s1,#s2,#s3{margin-top:0}
#left{position:relative}
#s1::after,#s2::after,#s3::after{content:"";position:absolute;left:35px;top:-17px;width:2px;height:16px;background:linear-gradient(var(--acc),transparent);opacity:.5}
.num{box-shadow:0 0 0 4px var(--accsoft)}
.card.done{border-color:color-mix(in srgb,var(--acc) 30%,var(--line))}
.btn:not(:disabled):not(.stop):not(.ghost){position:relative;overflow:hidden;animation:glowp 2.6s ease-in-out infinite}
.btn:not(:disabled):not(.stop):not(.ghost)::after{content:"";position:absolute;top:0;left:-60%;width:40%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.38),transparent);transform:skewX(-18deg);transition:left .6s}
.btn:not(:disabled):not(.stop):not(.ghost):hover::after{left:130%}
@keyframes glowp{50%{box-shadow:0 14px 34px -10px var(--glow),0 0 0 4px var(--accsoft)}}
.preset b{font-family:Unbounded,system-ui,sans-serif;font-size:13px;font-weight:600}.preset.on{box-shadow:0 10px 24px -14px var(--glow)}
.plat.on{box-shadow:0 14px 30px -16px var(--glow)}
.tile b{background:linear-gradient(180deg,currentColor,color-mix(in srgb,currentColor 70%,transparent));-webkit-background-clip:text;background-clip:text}
.hero h2{text-shadow:0 2px 30px rgba(61,139,255,.35)}
.feats span:hover{border-color:var(--acc);color:#fff}
.sumrow b{color:var(--ink)}#sSpeed{color:var(--acc)}
.turbo b{font-family:Unbounded,system-ui,sans-serif;font-weight:600;font-size:13px;margin-bottom:3px}.turbo span{line-height:1.5;display:block}
.presets{grid-template-columns:repeat(4,1fr)}@media(max-width:640px){.presets{grid-template-columns:repeat(2,1fr)}}
.preset[data-p=max].on,.preset[data-p=max]:hover{border-color:var(--warn)}.preset[data-p=max].on{background:rgba(255,162,77,.1)}
.gauge{height:8px;border-radius:99px;background:var(--bg2);margin:14px 0 6px;overflow:hidden}.gauge i{display:block;height:100%;width:3%;border-radius:99px;transition:width .35s,background .35s;box-shadow:0 0 14px currentColor}
.gtxt{font-size:12.5px;font-weight:700;min-height:18px}
.accents{display:flex;gap:7px;align-items:center;padding:0 4px}.accents button{width:18px;height:18px;border-radius:50%;border:2px solid transparent;cursor:pointer;padding:0;outline-offset:2px}
.accents button[data-a=ice]{background:#62d8ff}.accents button[data-a=emerald]{background:#3ddc97}.accents button[data-a=violet]{background:#a78bfa}.accents button[data-a=amber]{background:#ffb454}
.accents button.on{border-color:var(--ink);transform:scale(1.12)}
@media(max-width:520px){.accents{display:none}}
:root[data-accent=emerald]{--acc:#3ddc97;--acc2:#12a36b;--accsoft:rgba(61,220,151,.1);--glow:rgba(18,163,107,.38)}
:root[data-accent=violet]{--acc:#b39bff;--acc2:#7c5cff;--accsoft:rgba(167,139,250,.12);--glow:rgba(124,92,255,.4)}
:root[data-accent=amber]{--acc:#ffc062;--acc2:#ff8a3d;--accsoft:rgba(255,180,84,.11);--glow:rgba(255,138,61,.38)}
:root[data-theme=light][data-accent=emerald]{--acc:#0b9a6a;--acc2:#12a36b;--accsoft:rgba(11,154,106,.09);--glow:rgba(11,154,106,.25)}
:root[data-theme=light][data-accent=violet]{--acc:#6d4af0;--acc2:#7c5cff;--accsoft:rgba(109,74,240,.09);--glow:rgba(109,74,240,.25)}
:root[data-theme=light][data-accent=amber]{--acc:#c26a00;--acc2:#ff8a3d;--accsoft:rgba(194,106,0,.09);--glow:rgba(194,106,0,.25)}
.fulllog{margin-top:12px}.logbar{display:flex;gap:10px;align-items:center;margin:10px 0}
#fulllog{max-height:260px;overflow:auto;background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:12px;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;margin:0;color:var(--mute)}
.hide{display:none!important}
/* ================================================================
   PREMIUM v2 — glass, glow, gradient edges, refined type
   (pure visual layer: no ids/classes used by the JS were changed)
   ================================================================ */
:root{
 --bg:#04060d;--bg2:#090f1e;--card:#0a1121;--raise:#111b33;--soft:#0e1729;
 --line:rgba(150,172,218,.15);--ink:#f0f4ff;--mute:#8f9dbb;
 --edge:linear-gradient(150deg,rgba(255,255,255,.26),rgba(255,255,255,.05) 30%,rgba(255,255,255,.015) 62%,color-mix(in srgb,var(--acc) 42%,transparent));
 --shadow:0 1px 0 rgba(255,255,255,.05) inset,0 34px 64px -34px rgba(0,0,0,.9),0 10px 26px -14px rgba(0,0,0,.65);
 --ease:cubic-bezier(.2,.8,.2,1)
}
:root[data-theme=light]{
 --edge:linear-gradient(150deg,#fff,rgba(10,20,36,.07) 55%,color-mix(in srgb,var(--acc) 30%,transparent));
 --shadow:0 1px 0 #fff inset,0 30px 50px -32px rgba(20,40,80,.35),0 6px 18px -10px rgba(20,40,80,.18)
}

/* ---------- atmosphere ---------- */
body{background:
 radial-gradient(1100px 640px at 90% -12%,color-mix(in srgb,var(--acc2) 24%,transparent),transparent 62%),
 radial-gradient(820px 540px at -10% 4%,color-mix(in srgb,var(--acc) 13%,transparent),transparent 60%),
 radial-gradient(900px 600px at 50% 125%,color-mix(in srgb,var(--acc2) 14%,transparent),transparent 62%),var(--bg);background-attachment:fixed}
:root[data-theme=light] body{background:
 radial-gradient(1000px 560px at 90% -10%,color-mix(in srgb,var(--acc2) 14%,transparent),transparent 62%),
 radial-gradient(800px 500px at -8% 6%,color-mix(in srgb,var(--acc) 8%,transparent),transparent 60%),var(--bg);background-attachment:fixed}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.045;mix-blend-mode:overlay;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.ambient{position:fixed;inset:0;overflow:hidden;pointer-events:none;z-index:0}
.ambient i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.32;will-change:transform}
.ambient i:nth-child(1){width:420px;height:420px;left:-120px;top:22%;background:var(--acc2);animation:drift 26s ease-in-out infinite alternate}
.ambient i:nth-child(2){width:360px;height:360px;right:-100px;top:48%;background:var(--acc);opacity:.18;animation:drift 32s ease-in-out -8s infinite alternate-reverse}
.ambient i:nth-child(3){width:300px;height:300px;left:42%;bottom:-140px;background:color-mix(in srgb,var(--acc2) 70%,#8b5cf6);opacity:.2;animation:drift 38s ease-in-out -14s infinite alternate}
:root[data-theme=light] .ambient i{opacity:.12}
@keyframes drift{to{transform:translate3d(70px,-60px,0) scale(1.15)}}
.wrap{position:relative;z-index:1}

/* ---------- header ---------- */
header{margin-bottom:24px;padding:14px 20px;background:linear-gradient(180deg,color-mix(in srgb,var(--bg) 88%,transparent),color-mix(in srgb,var(--bg) 62%,transparent));border-bottom:1px solid transparent;border-image:linear-gradient(90deg,transparent,var(--line) 20%,color-mix(in srgb,var(--acc) 35%,var(--line)) 50%,var(--line) 80%,transparent) 1}
.brand svg{width:46px;height:46px;border-radius:15px;box-shadow:0 0 0 1px rgba(255,255,255,.16),0 12px 30px -8px var(--glow)}
.brand h1{font-size:16px;letter-spacing:-.015em}
.brand h1 span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.brand small{font-size:12px;letter-spacing:.01em}
.pill{padding:8px 15px;background:color-mix(in srgb,var(--card) 70%,transparent);backdrop-filter:blur(10px);box-shadow:0 1px 0 rgba(255,255,255,.05) inset;letter-spacing:.01em}
.dot{box-shadow:0 0 10px currentColor}
.pill.run{border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}.pill.login{border-color:color-mix(in srgb,var(--warn) 45%,var(--line))}.pill.bad{border-color:color-mix(in srgb,var(--bad) 45%,var(--line))}
.icon-btn{border-radius:13px;width:40px;height:40px;background:color-mix(in srgb,var(--card) 70%,transparent);backdrop-filter:blur(10px);transition:transform .2s var(--ease),border-color .2s,color .2s,box-shadow .2s}
.icon-btn:hover{transform:translateY(-1px) rotate(-8deg);box-shadow:0 10px 22px -10px var(--glow)}

/* ---------- accent swatches ---------- */
.accents{gap:8px;padding:5px 8px;border-radius:99px;background:color-mix(in srgb,var(--card) 70%,transparent);border:1px solid var(--line)}
.accents button{width:16px;height:16px;border:0;box-shadow:inset 0 0 0 1px rgba(255,255,255,.35);transition:transform .2s var(--ease),box-shadow .2s}
.accents button:hover{transform:scale(1.2)}
.accents button.on{border:0;transform:scale(1.15);box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--ink),0 0 14px 2px var(--glow)}
.accents button[data-a=gold]{background:linear-gradient(135deg,#f8e2a0,#c58b2a)}
:root[data-accent=gold]{--acc:#f3cf80;--acc2:#d39a3a;--accsoft:rgba(243,207,128,.11);--glow:rgba(211,154,58,.42)}
:root[data-theme=light][data-accent=gold]{--acc:#a8741a;--acc2:#d39a3a;--accsoft:rgba(168,116,26,.09);--glow:rgba(168,116,26,.25)}

/* ---------- hero ---------- */
.hero{padding:50px 46px;min-height:272px;border-radius:34px;border:1px solid transparent;
 background:linear-gradient(160deg,#0f1e40 0%,#08122b 52%,#050a1a 100%) padding-box,linear-gradient(135deg,rgba(255,255,255,.34),rgba(255,255,255,.06) 32%,color-mix(in srgb,var(--acc) 60%,transparent)) border-box;
 box-shadow:0 50px 90px -50px var(--glow),0 30px 60px -36px rgba(0,0,0,.9)}
.hero::before{background:radial-gradient(circle at 34% 32%,#fff 0%,color-mix(in srgb,var(--acc) 10%,#fff) 26%,color-mix(in srgb,var(--acc) 38%,#a9c6f2) 60%,color-mix(in srgb,var(--acc2) 55%,#4a6aa8) 100%);box-shadow:0 0 100px 34px var(--glow),0 0 240px 100px color-mix(in srgb,var(--acc2) 22%,transparent)}
.hero .eyebrow{display:inline-flex;align-items:center;gap:9px;margin:0 0 18px;padding:6px 14px 6px 10px;border-radius:99px;font-size:11.5px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--acc);background:rgba(255,255,255,.05);border:1px solid color-mix(in srgb,var(--acc) 35%,transparent);backdrop-filter:blur(8px)}
.hero .eyebrow i{width:7px;height:7px;border-radius:50%;background:var(--acc);box-shadow:0 0 12px var(--acc),0 0 0 3px color-mix(in srgb,var(--acc) 22%,transparent)}
.hero h2{font-size:clamp(27px,3.8vw,42px);line-height:1.12;letter-spacing:-.03em;margin-bottom:16px;text-shadow:none;background:linear-gradient(180deg,#ffffff 20%,color-mix(in srgb,var(--acc) 40%,#c4d7ff));-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{font-size:15.5px;color:#b3c3e0}
.feats{gap:9px;margin-top:24px}
.feats span{padding:8px 14px;background:rgba(255,255,255,.045);border-color:rgba(190,210,255,.2);box-shadow:0 1px 0 rgba(255,255,255,.06) inset;transition:transform .25s var(--ease),border-color .2s,background .2s}
.feats span:hover{transform:translateY(-2px);background:color-mix(in srgb,var(--acc) 14%,transparent)}
.feats svg{stroke:var(--acc)}
.hero-art svg{width:140px;height:140px;border-radius:36px;box-shadow:0 30px 70px -10px var(--glow),0 0 0 1px rgba(255,255,255,.3),0 0 0 9px rgba(255,255,255,.04),0 0 0 10px rgba(255,255,255,.06)}
@media(max-width:760px){.hero{padding:30px 22px;min-height:0;border-radius:26px}}
@media(max-width:520px){.hero{padding:26px 18px}}

/* ---------- cards ---------- */
.card{border:1px solid transparent;border-radius:26px;padding:28px;backdrop-filter:none;
 background:linear-gradient(180deg,color-mix(in srgb,var(--card) 90%,#fff 10%),var(--card) 36%) padding-box,var(--edge) border-box;
 box-shadow:var(--shadow);animation:cardin .65s var(--ease) backwards}
:root[data-theme=light] .card{background:linear-gradient(var(--card),var(--card)) padding-box,var(--edge) border-box}
.card::before{left:14%;right:14%;opacity:.7}
.stack>.card:nth-child(1){animation-delay:.04s}.stack>.card:nth-child(2){animation-delay:.1s}.stack>.card:nth-child(3){animation-delay:.16s}.stack>.card:nth-child(4){animation-delay:.22s}.stack>.card:nth-child(5){animation-delay:.28s}
@keyframes cardin{from{opacity:0;transform:translateY(16px) scale(.985)}}
#s1::after,#s2::after,#s3::after{display:none}
.card>h3{font-size:16.5px;gap:13px;letter-spacing:-.012em}
.num{width:32px;height:32px;font-size:13px;border:1.5px solid color-mix(in srgb,var(--acc) 70%,transparent);color:var(--acc);background:color-mix(in srgb,var(--acc) 8%,transparent);box-shadow:0 0 0 5px var(--accsoft),0 8px 18px -8px var(--glow)}
.card.done .num{background:linear-gradient(135deg,var(--acc),var(--acc2));border-color:transparent;box-shadow:0 0 0 5px var(--accsoft),0 10px 22px -8px var(--glow)}
.card.done{border-color:transparent}
.sub{font-size:14px;margin:6px 0 18px;line-height:1.6}

/* ---------- inputs ---------- */
textarea,input[type=text],input[type=number],select{border-radius:14px;padding:12px 15px;background:color-mix(in srgb,var(--bg) 58%,var(--soft));box-shadow:inset 0 1px 3px rgba(0,0,0,.35)}
:root[data-theme=light] textarea,:root[data-theme=light] input[type=text],:root[data-theme=light] input[type=number],:root[data-theme=light] select{background:var(--soft);box-shadow:inset 0 1px 2px rgba(10,20,36,.06)}
textarea:focus,input:focus,select:focus{box-shadow:0 0 0 4px var(--accsoft),0 8px 22px -12px var(--glow)}
label.f{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px}
.chip{padding:7px 14px;background:color-mix(in srgb,var(--bg) 40%,var(--soft));transition:transform .18s var(--ease),border-color .18s,background .18s,color .18s}
.chip:hover{transform:translateY(-1px)}
.chip.on{background:linear-gradient(180deg,color-mix(in srgb,var(--acc) 22%,transparent),var(--accsoft));box-shadow:0 8px 18px -10px var(--glow)}
.drop{border-radius:20px;padding:34px 16px;border:1.5px dashed color-mix(in srgb,var(--acc) 35%,var(--line));background:radial-gradient(260px 120px at 50% 0%,var(--accsoft),transparent 80%),var(--soft)}
.drop svg{width:34px;height:34px;padding:9px;box-sizing:content-box;border-radius:14px;background:var(--accsoft);margin-bottom:10px}
.drop.over,.drop:hover{transform:translateY(-2px);box-shadow:0 18px 34px -22px var(--glow)}

/* ---------- segmented control ---------- */
.seg{padding:5px;border-radius:17px;background:color-mix(in srgb,var(--bg) 60%,var(--soft));box-shadow:inset 0 1px 3px rgba(0,0,0,.35)}
:root[data-theme=light] .seg{box-shadow:inset 0 1px 2px rgba(10,20,36,.06)}
.seg button{border-radius:13px;padding:11px 8px;transition:background .25s,color .25s,box-shadow .25s}
.seg button:hover:not(.on){color:var(--ink)}
.seg button.on{background:linear-gradient(180deg,color-mix(in srgb,var(--acc) 24%,var(--raise)),color-mix(in srgb,var(--acc2) 14%,var(--raise)));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc) 50%,transparent),0 8px 18px -10px var(--glow)}

/* ---------- selectable cards ---------- */
.preset,.bcard,.opt,.plat{position:relative;border:1px solid var(--line);border-radius:17px;background:linear-gradient(180deg,color-mix(in srgb,var(--soft) 90%,#fff 10%),var(--soft));transition:transform .25s var(--ease),border-color .2s,background .2s,box-shadow .25s}
:root[data-theme=light] .preset,:root[data-theme=light] .bcard,:root[data-theme=light] .opt,:root[data-theme=light] .plat{background:var(--soft)}
.preset:hover,.bcard:hover,.opt:hover,.plat:hover{transform:translateY(-2px);box-shadow:0 16px 30px -20px rgba(0,0,0,.75)}
.preset.on,.bcard.on,.opt.on,.plat.on{border-color:color-mix(in srgb,var(--acc) 75%,transparent);background:linear-gradient(180deg,color-mix(in srgb,var(--acc) 17%,var(--soft)),color-mix(in srgb,var(--acc2) 7%,var(--soft)));box-shadow:0 0 0 1px color-mix(in srgb,var(--acc) 35%,transparent),0 18px 36px -20px var(--glow)}
.preset{padding:14px 14px}.preset b{font-size:13.5px;margin-bottom:3px}.preset span{line-height:1.4;display:block}
.opt{padding:14px 16px}.opt .k{width:28px;height:28px;background:color-mix(in srgb,var(--acc) 12%,var(--bg2));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc) 30%,transparent)}
.opt.on .k{background:linear-gradient(135deg,var(--acc),var(--acc2));box-shadow:0 6px 14px -6px var(--glow)}
.opt .c{font-size:17px;color:var(--acc)}
.plat{padding:16px 17px}
.plat .pi{width:44px;height:44px;border-radius:14px;background:color-mix(in srgb,var(--acc) 10%,var(--bg2));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc) 22%,transparent)}
.plat.on .pi{background:linear-gradient(135deg,var(--acc),var(--acc2));box-shadow:0 10px 22px -8px var(--glow)}
.bcard em{border:1px solid var(--line)}.bcard em.ok{border-color:color-mix(in srgb,var(--ok) 40%,transparent);background:rgba(55,217,160,.08)}

/* ---------- account / turbo callouts ---------- */
.acct,.turbo{border-radius:18px;padding:16px;background:linear-gradient(135deg,color-mix(in srgb,var(--acc) 12%,transparent),color-mix(in srgb,var(--acc2) 4%,transparent));border:1px solid color-mix(in srgb,var(--acc) 26%,var(--line))}
.acct-ic{width:46px;height:46px;border-radius:15px;background:linear-gradient(135deg,var(--acc),var(--acc2));box-shadow:0 12px 24px -10px var(--glow)}
.turbo svg{padding:6px;box-sizing:content-box;width:18px;height:18px;border-radius:10px;background:var(--accsoft)}
.qlabel{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:8px 0 12px}
.note,.warn{border-radius:14px}
.turbo{margin-bottom:18px}
.alert{border-radius:18px;padding:15px 18px;backdrop-filter:blur(8px);box-shadow:0 14px 30px -22px rgba(0,0,0,.8)}

/* ---------- buttons ---------- */
.btn{border-radius:16px;letter-spacing:.01em;transition:transform .2s var(--ease),filter .2s,box-shadow .25s}
.btn:not(:disabled):not(.stop):not(.ghost){padding:15px 20px;background:linear-gradient(120deg,var(--acc) 0%,color-mix(in srgb,var(--acc) 55%,var(--acc2)) 55%,var(--acc2) 100%);box-shadow:0 1px 0 rgba(255,255,255,.45) inset,0 -2px 0 rgba(0,0,0,.12) inset,0 18px 36px -14px var(--glow)}
.btn:hover:not(:disabled){filter:brightness(1.07) saturate(1.05);transform:translateY(-2px)}
.btn:disabled:not(.stop){background:var(--raise);color:var(--mute);opacity:.75;border:1px solid var(--line);box-shadow:none}
.btn.stop{background:linear-gradient(135deg,#ff9a66,#ff5f4a);box-shadow:0 1px 0 rgba(255,255,255,.35) inset,0 18px 34px -14px rgba(255,95,74,.55)}
.btn.ghost{background:color-mix(in srgb,var(--bg) 40%,var(--soft));backdrop-filter:blur(6px)}
.btn.ghost:hover:not(:disabled){border-color:color-mix(in srgb,var(--acc) 55%,var(--line));color:var(--acc)}
summary{letter-spacing:.01em;transition:color .2s}summary:hover{filter:brightness(1.15)}
details[open]>summary{margin-bottom:4px}

/* ---------- summary card ---------- */
#sumcard{background:linear-gradient(180deg,color-mix(in srgb,var(--acc2) 15%,var(--card)),var(--card) 46%) padding-box,linear-gradient(150deg,color-mix(in srgb,var(--acc) 75%,transparent),rgba(255,255,255,.05) 38%,color-mix(in srgb,var(--acc2) 45%,transparent)) border-box;box-shadow:0 50px 90px -50px var(--glow),var(--shadow)}
:root[data-theme=light] #sumcard{background:linear-gradient(180deg,color-mix(in srgb,var(--acc2) 7%,var(--card)),var(--card) 50%) padding-box,linear-gradient(150deg,color-mix(in srgb,var(--acc) 65%,transparent),rgba(10,20,36,.06) 40%,color-mix(in srgb,var(--acc2) 40%,transparent)) border-box}
#sumcard>h3{font-family:Manrope,system-ui,sans-serif;font-size:11.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--acc);margin-bottom:10px}
#sumcard>h3::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,color-mix(in srgb,var(--acc) 45%,transparent),transparent)}
.sumrow{padding:11px 0;border-bottom:1px dashed var(--line)}
#sEta{font-family:Unbounded,system-ui,sans-serif;font-size:15px;font-weight:600;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
#sSpeed{color:var(--acc)}
.gauge{height:9px;background:color-mix(in srgb,var(--bg) 55%,var(--soft));box-shadow:inset 0 1px 2px rgba(0,0,0,.4)}
.gtxt{font-size:12px;letter-spacing:.02em}

/* ---------- live run ---------- */
#live{background:linear-gradient(180deg,color-mix(in srgb,var(--acc2) 12%,var(--card)),var(--card) 50%) padding-box,linear-gradient(150deg,color-mix(in srgb,var(--acc) 65%,transparent),rgba(255,255,255,.05) 40%,color-mix(in srgb,var(--acc2) 40%,transparent)) border-box;box-shadow:0 50px 100px -56px var(--glow),var(--shadow)}
.livehead .lt{font-size:16px}
.livehead .lm{font-size:11px;letter-spacing:.08em;text-transform:uppercase;background:linear-gradient(135deg,var(--acc),var(--acc2));color:var(--on);border:0}
.ring svg{filter:drop-shadow(0 0 18px var(--glow))}.ring circle{stroke-width:9}.ring .bg{stroke:color-mix(in srgb,var(--bg) 40%,var(--soft))}
.ring b{font-size:36px;letter-spacing:-.03em}
.tile{--tc:var(--acc);position:relative;overflow:hidden;border-radius:17px;padding:15px 6px 12px;background:linear-gradient(180deg,color-mix(in srgb,var(--tc) 9%,var(--soft)),var(--soft))}
.tile.g{--tc:var(--ok)}.tile.y{--tc:var(--warn)}.tile.r{--tc:var(--bad)}
.tile::before{content:"";position:absolute;left:18%;right:18%;top:0;height:2px;border-radius:0 0 4px 4px;background:var(--tc);box-shadow:0 0 14px var(--tc)}
.tile b{font-size:21px;-webkit-text-fill-color:currentColor}.tile span{letter-spacing:.03em;text-transform:uppercase;font-size:10.5px;font-weight:700}
.rest{border-radius:18px;background:linear-gradient(90deg,var(--accsoft),var(--soft))}
.bar{height:8px;background:color-mix(in srgb,var(--bg) 55%,var(--soft));box-shadow:inset 0 1px 2px rgba(0,0,0,.35)}
.bar i{box-shadow:0 0 14px var(--glow);background:linear-gradient(90deg,var(--acc2),var(--acc))}
.logs{padding:12px 14px;border-radius:14px;background:color-mix(in srgb,var(--bg) 45%,var(--soft));border:1px solid var(--line)}.logs:empty{display:none}
#fulllog{border-radius:14px;background:color-mix(in srgb,var(--bg) 60%,var(--soft))}

/* ---------- activity feed ---------- */
.feedhead h3{font-size:15px}
.mini{padding:3px;border-radius:12px;background:color-mix(in srgb,var(--bg) 50%,var(--soft));border:1px solid var(--line)}
.mini button{border-radius:9px;transition:background .2s,color .2s}.mini button.on{background:var(--accsoft);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc) 30%,transparent)}
.feed li{padding:11px 10px;animation:cardin .4s var(--ease) backwards;transition:background .2s}
.feed .ic{width:26px;height:26px;box-shadow:0 0 0 4px color-mix(in srgb,currentColor 0%,transparent),0 6px 14px -6px currentColor;color:var(--ok)}
.feed .y .ic{color:var(--warn)}.feed .r .ic{color:var(--bad)}
.feed .ic svg{stroke:#04110b}
.empty{padding:34px 8px;border:1px dashed var(--line);border-radius:16px;margin-top:6px}

/* ---------- modal / toast / footer ---------- */
.modal{background:radial-gradient(600px 400px at 50% 40%,color-mix(in srgb,var(--acc2) 14%,transparent),transparent),rgba(2,4,10,.72);backdrop-filter:blur(10px) saturate(1.2)}
.modal .box{border:1px solid transparent;border-radius:28px;padding:30px;background:linear-gradient(180deg,color-mix(in srgb,var(--card) 90%,#fff 10%),var(--card) 40%) padding-box,var(--edge) border-box;box-shadow:0 50px 110px -30px rgba(0,0,0,.8),0 0 80px -30px var(--glow);animation:modalin .35s var(--ease) both}
:root[data-theme=light] .modal .box{background:linear-gradient(var(--card),var(--card)) padding-box,var(--edge) border-box}
@keyframes modalin{from{opacity:0;transform:translateY(14px) scale(.96)}}
.modal h3{font-size:19px}
.toast{background:color-mix(in srgb,var(--raise) 94%,transparent);color:var(--ink);border:1px solid color-mix(in srgb,var(--acc) 40%,var(--line));border-radius:16px;padding:13px 20px;backdrop-filter:blur(12px);box-shadow:0 24px 50px -20px rgba(0,0,0,.8),0 0 30px -10px var(--glow);animation:toastin .35s var(--ease) both}
@keyframes toastin{from{opacity:0;transform:translate(-50%,14px)}}
footer{margin-top:44px;padding-top:24px;border-top:1px solid transparent;border-image:linear-gradient(90deg,transparent,var(--line) 20%,var(--line) 80%,transparent) 1}
footer svg{width:34px;height:34px;border-radius:11px;box-shadow:0 8px 20px -8px var(--glow)}
footer .links button{transition:background .2s,transform .2s var(--ease)}footer .links button:hover{transform:translateY(-1px)}
::selection{background:color-mix(in srgb,var(--acc) 80%,transparent)}
*{scrollbar-width:thin;scrollbar-color:color-mix(in srgb,var(--acc) 30%,var(--line)) transparent}
@media(max-width:520px){.card{padding:20px;border-radius:22px}.hero h2{font-size:26px}}
@media (prefers-reduced-motion:reduce){.ambient i{animation:none!important}}
.probadge{font-style:normal;margin-left:8px;padding:2px 8px;border-radius:99px;font:800 10px/1.5 Manrope,system-ui,sans-serif;letter-spacing:.14em;vertical-align:middle;color:var(--on);background:linear-gradient(135deg,var(--acc),var(--acc2));box-shadow:0 6px 16px -6px var(--glow);-webkit-text-fill-color:var(--on)}
.dls{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:12px}.dls>span{font-size:11.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-right:4px}
.dlbar{margin-top:18px;padding:15px 16px;border-radius:18px;background:linear-gradient(135deg,var(--accsoft),transparent);border:1px solid color-mix(in srgb,var(--acc) 24%,var(--line))}
.dlh{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}.dlh b{font-size:14px}.dlh span{font-size:12px;color:var(--ok)}
.dlrow{display:flex;flex-wrap:wrap;gap:8px;margin-top:11px}
#keepbox{margin-top:14px}
</style></head><body><div class="ambient" aria-hidden="true"><i></i><i></i><i></i></div><div class="wrap">
<header>
 <div class="brand">__LOGO__<div><h1>Unfollow <span>by Lykoon</span><em class="probadge">PRO</em></h1><small>Clean up who you follow, safely</small></div></div>
 <div class="tools"><div class="pill" id="pill"><i class="dot"></i><span id="pilltxt">Ready</span></div>
 <div class="accents" id="accents" role="group" aria-label="Accent colour"><button data-a="ice" title="Ice"></button><button data-a="emerald" title="Emerald"></button><button data-a="violet" title="Violet"></button><button data-a="amber" title="Amber"></button><button data-a="gold" title="Gold"></button></div>
  <button class="icon-btn" id="theme" title="Switch theme" aria-label="Switch theme"><svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button></div>
</header>
<div id="top"></div>
<div class="hero" id="hero"><div class="aurora"></div>
 <div class="hero-in"><div class="eyebrow"><i></i>Lykoon &middot; Pro</div><h2>Unfollow in bulk, safely and at your own pace.</h2>
  <p>Pick the accounts, set the exact pace, and watch it work live. You can stop at any moment.</p>
  <div class="feats">
   <span><svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2.5h6"/></svg>Exact timing</span>
   <span><svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z"/><path d="m9 12 2.2 2.2L15.5 10"/></svg>Auto-stop on block</span>
   <span><svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>Live progress</span>
   <span><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>Windows, Mac, Linux</span>
  </div></div>
 <div class="hero-art" aria-hidden="true">__LOGO__</div></div>

<div class="grid" id="main">
 <div class="stack" id="left">
  <section class="card" id="s0">
   <h3><span class="num">1</span>Which platform?</h3>
   <p class="sub">Choose where you want to unfollow people.</p>
   <div class="plats" id="plats"></div>
   <div class="warn hide" id="platwarn"></div>
  </section>
  <section class="card" id="s1">
   <h3><span class="num">2</span>Who should be unfollowed?</h3>
   <p class="sub" id="s1sub">Let the app read your own Instagram account automatically, or paste usernames, or drop your export files.</p>
   <div class="seg"><button id="tabAcct" class="on">My account (automatic)</button><button id="tabPaste">Paste usernames</button><button id="tabExport">Export files</button></div>
   <div id="paneA">
    <div class="acct">
     <div class="acct-ic"><svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M17 5v6M14 8h6"/></svg></div>
     <div class="acct-tx"><b>Build the lists straight from your <span class="pl">Instagram</span> account</b><span>A browser opens on this computer. Log in once and the app reads who you follow and who follows you. Nothing is typed, nothing is uploaded.</span></div>
    </div>
    <div class="qlabel">Which list do you want to collect?</div>
    <div class="opts" id="opts"></div>
    <button class="btn" id="fetchBtn" style="margin-top:14px">Collect this list</button>
    <div class="fprog hide" id="fprog"><div class="frow"><span id="ftext"></span><button class="btn ghost" id="fstop">Cancel</button></div><div class="bar"><i id="fbar"></i></div>
     <div class="frow" style="margin-top:12px"><button class="btn" id="fenough" style="padding:11px 14px;font-size:14.5px">That is enough of this list</button></div>
     <div class="msg" id="fnote" style="margin:8px 0 0">"Enough" ends the list being read now and moves on to the next one (followers). Press it again to finish. Mutual and All then use only the part that was read; "Not following me back" needs all followers.</div></div>
    <div class="msg err hide" id="ferr"></div>
   </div>
   <div id="paneP" class="hide"><textarea id="paste" spellcheck="false" placeholder="One username per line (or separated by spaces/commas).&#10;@name and profile links work too."></textarea></div>
   <div id="paneE" class="hide">
    <div class="drop" id="drop"><svg viewBox="0 0 24 24"><path d="M12 16V4m0 0L7 9m5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg><b>Drop following.json and followers_1.json here</b><small>or click to choose them (select all files at once)</small></div>
    <input type="file" id="files" multiple accept=".json" class="hide">
    <div id="modes" class="chips"></div>
   </div>
   <div class="msg" id="listmsg">No list yet.</div>
   <div class="dls hide" id="listdl"><span>Download</span><button class="chip" data-dl="list.txt">This list (.txt)</button><button class="chip hide" data-dl="following.txt">Following</button><button class="chip hide" data-dl="followers.txt">Followers</button><button class="chip hide" data-dl="non_followers.txt">Not following back</button><button class="chip hide" data-dl="mutual.txt">Mutual</button></div>
   <details id="keepbox"><summary>Protected accounts (never unfollow)</summary>
    <p class="sub" style="margin:8px 0 10px">Friends, family or brands you always want to keep following. They are skipped even when they are on your list. Saved on this computer.</p>
    <textarea id="keep" spellcheck="false" style="min-height:90px" placeholder="One username per line (or separated by spaces/commas)."></textarea>
    <div class="msg" id="keepmsg"></div></details>
  </section>

  <section class="card" id="s2">
   <h3><span class="num">3</span>Pace and safety</h3>
   <p class="sub">Slower is safer. <span class="pl">Instagram</span> may temporarily block an account that unfollows too fast; the bot stops by itself if that happens.</p>
   <div class="presets" id="presets">
    <button class="preset" data-p="safe"><b>Safe</b><span>4s rest, 1 at a time. About 12 per minute</span></button>
    <button class="preset" data-p="balanced"><b>Balanced</b><span>3s rest, 2 at a time. About 30 per minute</span></button>
    <button class="preset" data-p="fast"><b>Fast</b><span>2s rest, 4 at a time, no breaks. About 85 per minute</span></button>
    <button class="preset" data-p="max"><b>Max</b><span>1s rest, 6 at a time, no breaks. About 210 per minute</span></button>
   </div>
   <div class="turbo" id="turbobox"><svg viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg><div><b>Turbo mode: what is it?</b><span>Normally the bot opens every profile and clicks Unfollow, about 4 to 6 seconds each. Turbo sends the same message the Unfollow button sends, straight to Instagram, in under a second. So your speed is set only by the rest time and how many you do at once. If Instagram ever refuses it, the bot switches to the normal way by itself.</span></div></div>
   <div class="cols">
    <div><label class="f" for="count">How many accounts</label><input type="text" id="count" value="all" inputmode="numeric" placeholder="a number or all">
     <div class="chips"><button class="chip" data-c="20">20</button><button class="chip" data-c="100">100</button><button class="chip" data-c="500">500</button><button class="chip" data-c="all">All</button></div></div>
    <div><label class="f" for="rest">Rest after each unfollow (seconds)</label><input type="text" id="rest" value="3" inputmode="decimal">
     <div class="chips"><button class="chip" data-r="0">0</button><button class="chip" data-r="3">3</button><button class="chip" data-r="6">6</button><button class="chip" data-r="12">12</button><button class="chip" data-r="10-15">10-15</button></div></div>
   </div>
   <p class="sub" style="margin:10px 0 0">Rest is exact: <b>12</b> = 12 seconds, <b>0</b> = none, <b>10-15</b> = random between 10 and 15.</p>
   <div class="warn hide" id="fastwarn">Very short rests raise the risk of an "Action Blocked" message from <span class="pl">Instagram</span>. The bot slows down (Auto speed) or stops by itself if it sees one.</div>
   <div class="cols" style="margin-top:16px">
    <div><label class="f" for="be">Take a break every (unfollows)</label><input type="number" id="be" value="60" min="0"></div>
    <div><label class="f" for="bd">Break length (5 = minutes, 30s, 2m)</label><input type="text" id="bd" value="1"></div>
   </div>
   <div id="parbox" style="margin-top:16px"><label class="f" for="par">Unfollow this many at the same time (1-30)</label><input type="number" id="par" value="2" min="1" max="30">
    <div class="chips"><button class="chip" data-par="1">1</button><button class="chip" data-par="2">2</button><button class="chip" data-par="3">3</button><button class="chip" data-par="5">5</button><button class="chip" data-par="10">10</button></div>
    <label class="chk" style="margin-top:14px"><input type="checkbox" id="auto" checked><span><b>Auto speed</b><br><small>If Instagram pushes back, pause, slow down and keep going instead of stopping. After 4 pushbacks it stops.</small></span></label>
    <p class="sub" style="margin:8px 0 0">The rest is taken after every round, so 3 at a time with a 6 second rest means 3 unfollows every 6 seconds. More at once is faster but riskier.</p></div>
   <details><summary>Advanced</summary>
    <div class="cols" style="margin-top:12px"><div id="engbox"><label class="f" for="eng">Method (Instagram)</label><select id="eng"><option value="classic">Classic (opens each profile)</option><option value="turbo">Turbo (fastest)</option></select></div>
    <div><label class="f" for="tabs">Preload tabs (1-6, classic)</label><input type="number" id="tabs" value="3" min="1" max="6"></div>
    <div><label class="f" for="block" id="blockLbl">Page loading</label><select id="block"><option value="off">Full (most reliable)</option><option value="normal">Light</option><option value="max">Minimal (fastest, may fail)</option></select></div></div>
   </details>
  </section>

  <section class="card" id="s3">
   <h3><span class="num">4</span>Which browser should it use?</h3>
   <p class="sub">The browser opens on this computer. The first time you will log in to <span class="pl">Instagram</span> in it, and the login is remembered.</p>
   <div class="bro" id="bro"></div>
  </section>

  <section class="card hide" id="live">
   <div class="livehead"><span class="lt" id="liveT">Unfollowing</span><span class="lm" id="liveM"></span></div>
   <div class="live">
    <div class="ring"><svg viewBox="0 0 150 150"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--acc2)"/><stop offset="1" style="stop-color:var(--acc)"/></linearGradient></defs><circle class="bg" cx="75" cy="75" r="64"/><circle class="fg" id="ringfg" cx="75" cy="75" r="64" stroke-dasharray="402.1" stroke-dashoffset="402.1"/></svg><div><b id="bigdone">0</b><span>of <span id="bigtarget">0</span></span></div></div>
    <div><div class="tiles">
     <div class="tile g"><b id="tU">0</b><span>Unfollowed</span></div><div class="tile y"><b id="tS">0</b><span>Skipped</span></div>
     <div class="tile r"><b id="tF">0</b><span>Failed</span></div><div class="tile"><b id="tL">-</b><span>Left in list</span></div></div>
     <div class="meta"><span id="mSpeed"></span><span id="mEta"></span><span id="mTime"></span></div></div>
   </div>
   <div class="rest hide" id="restbox"><div class="t" id="restT">0s</div><div class="l" id="restL">rest</div><div class="bar"><i id="restBar"></i></div></div>
   <div class="logs" id="logs"></div>
   <div class="dlbar hide" id="dlbar"><div class="dlh"><b id="dlTitle">Download results</b><span id="dlSaved"></span></div><div class="dlrow">
    <button class="chip" data-dl="results.csv">Report (.csv)</button><button class="chip" data-dl="unfollowed.txt">Unfollowed (.txt)</button><button class="chip hide" id="dlFailed" data-dl="failed.txt">Failed (.txt)</button><button class="chip" data-dl="log.txt">Full log (.txt)</button><button class="chip on" data-dl="all.zip">Everything (.zip)</button><button class="chip hide" id="retryFailed">Retry failed ones</button></div></div>
   <details class="fulllog"><summary>Show full log</summary><div class="logbar"><button class="chip" id="copylog">Copy log</button><span class="msg" style="margin:0">Send this text if something does not work.</span></div><pre id="fulllog"></pre></details>
   <div style="margin-top:18px"><button class="btn stop" id="stop">Stop</button><button class="btn ghost hide" id="again">Start a new run</button></div>
  </section>
 </div>

 <aside class="stack sticky" id="right">
  <section class="card" id="sumcard">
   <h3 style="margin-bottom:10px">Summary</h3>
   <div class="sumrow"><span>Platform</span><b id="sPlat">Instagram</b></div>
   <div class="sumrow"><span>Accounts</span><b id="sAcc">0</b></div>
   <div class="sumrow"><span>Method</span><b id="sMode">Turbo</b></div>
   <div class="sumrow"><span>Rest</span><b id="sRest">12 s</b></div>
   <div class="sumrow"><span>Break</span><b id="sBreak">-</b></div>
   <div class="sumrow"><span>Browser</span><b id="sBro">Chromium</b></div>
   <div class="sumrow"><span>Speed</span><b id="sSpeed">-</b></div>
   <div class="gauge" id="gauge"><i id="gfill"></i></div><div class="gtxt" id="gtxt"></div>
   <div class="sumrow"><span>Estimated time</span><b id="sEta">-</b></div>
   <div style="margin-top:16px"><button class="btn" id="go" disabled>Start unfollowing</button></div>
   <div class="note" id="sNote">Add your list in step 2 to begin.</div>
  </section>
  <section class="card hide" id="feedcard">
   <div class="feedhead"><h3>Activity</h3><div class="mini" id="filters"><button class="on" data-f="all">All</button><button data-f="unfollowed">Done</button><button data-f="skipped">Skipped</button><button data-f="failed">Failed</button></div></div>
   <ul class="feed" id="feed"></ul><div class="empty" id="feedempty">Results will appear here as they happen.</div>
  </section>
 </aside>
</div>

<footer>__LOGO__<span><span class="by">Made by Lykoon Company</span> &middot; <a class="mail" href="mailto:lykoonofficial@gmail.com">lykoonofficial@gmail.com</a><br>&copy; 2026 Lykoon &middot; <span id="ver"></span> &middot; Run reports are saved in ~/.ig_unfollow/reports; your saved logins are remembered.</span>
 <span class="links"><button id="reports">Reports folder</button><button id="cd" class="hide">Clear cooldown</button><button id="forget">Forget saved logins</button><button id="quit">Quit app</button></span></footer>
</div>
<div class="modal hide" id="modal"><div class="box"><h3>Start unfollowing?</h3><p id="mtext"></p><div class="sumrow"><span>Accounts</span><b id="mAcc"></b></div><div class="sumrow"><span>Rest</span><b id="mRest"></b></div><div class="sumrow"><span>Browser</span><b id="mBro"></b></div>
 <div class="row"><button class="btn ghost" id="mNo">Cancel</button><button class="btn" id="mYes">Yes, start</button></div></div></div>
<script>
const $=id=>document.getElementById(id);const TOKEN=new URLSearchParams(location.search).get('t')||'';
const PI={instagram:{label:'Instagram',lists:true,exp:false,rest:3,sub:'Let the app read your own Instagram account automatically, or paste usernames, or drop your export files.'},tiktok:{label:'TikTok',lists:true,exp:true,rest:3,sub:'Let the app read your own TikTok account automatically, or paste usernames.'}};
const PW={tiktok:'TikTok support is experimental. TikTok changes its pages often, so some accounts may fail, and it sometimes shows a slider captcha: solve it in the browser window and the bot continues by itself.'};
const PICON={instagram:'<rect x="3.5" y="3.5" width="17" height="17" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r=".6"/>',tiktok:'<path d="M14 4v10.5a3.5 3.5 0 1 1-3.5-3.5"/><path d="M14 4c.4 2.4 2 3.9 4.5 4.1"/>'};
let S=null,listOK=false,exportFiles={},exportMode='non_followers',acctMode='mutual',lastFetch='',tab='acct',browser='chromium',filter='all',BROS=[],LS={},lostN=0;
try{LS=JSON.parse(localStorage.getItem('lykoon')||'{}')}catch(e){}
if(LS.v!==11){for(const k of ['rest','be','bd','par','eng','tabs','count','block'])delete LS[k];LS.v=11}
let platform=PI[LS.platform]?LS.platform:'instagram';
const PRESETS={safe:{rest:'4',be:'40',bd:'1',par:'1'},balanced:{rest:'3',be:'60',bd:'1',par:'2'},fast:{rest:'2',be:'0',bd:'0',par:'4'},max:{rest:'1',be:'0',bd:'0',par:'6'}};
function engineNow(){return platform==='instagram'?$('eng').value:'classic'}
function turboOn(){return platform==='instagram'&&$('eng').value==='turbo'}
function save(){try{localStorage.setItem('lykoon',JSON.stringify({v:11,rest:$('rest').value,be:$('be').value,bd:$('bd').value,tabs:$('tabs').value,block:$('block').value,eng:$('eng').value,par:$('par').value,auto:$('auto').checked,accent:document.documentElement.dataset.accent||'',browser,platform,count:$('count').value,theme:document.documentElement.dataset.theme||''}))}catch(e){}}
function toast(t){document.querySelectorAll('.toast').forEach(x=>x.remove());const e=document.createElement('div');e.className='toast';e.textContent=t;document.body.appendChild(e);setTimeout(()=>e.remove(),4200)}
async function rawApi(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'X-Token':TOKEN,'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json()}
async function api(p,b){try{return await rawApi(p,b)}catch(e){return{ok:false,error:'Lost connection to the app. Is it still running?'}}}
const fmt=s=>{if(s==null)return'-';s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h?`${h}h ${m}m`:m?`${m}m ${s%60}s`:`${s}s`};
const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
for(const k of ['rest','be','bd','tabs','block','count'])if(LS[k]!=null&&LS[k]!=='')$(k).value=LS[k];if(LS.eng)$('eng').value=LS.eng;if(LS.auto===false)$('auto').checked=false;if(LS.accent)document.documentElement.dataset.accent=LS.accent;if(LS.par)$('par').value=LS.par;if(LS.browser)browser=LS.browser;if(LS.theme)document.documentElement.dataset.theme=LS.theme;
$('theme').onclick=()=>{const d=document.documentElement,dark=d.dataset.theme?d.dataset.theme==='dark':true;d.dataset.theme=dark?'light':'dark';save()};
function parseRest(){const m=$('rest').value.trim().replace(/,/g,'.').match(/^(\d+(?:\.\d+)?)\s*(?:s|sec)?\s*(?:(?:-|\u2013|to|\s)\s*(\d+(?:\.\d+)?)\s*(?:s|sec)?)?$/i);if(!m)return null;const x=parseFloat(m[1]);return m[2]!=null?(x+parseFloat(m[2]))/2:x}
function parseMin(t){const m=t.trim().toLowerCase().replace(/\s/g,'').match(/^(\d+(?:\.\d+)?)(s|sec|m|min|h)?$/);if(!m)return 0;const v=+m[1],u=m[2]||'m';return u[0]=='s'?v/60:u=='h'?v*60:v}
function markPreset(){let hit='';for(const [k,p] of Object.entries(PRESETS))if($('rest').value.trim()===p.rest&&$('be').value.trim()===p.be&&$('bd').value.trim()===p.bd)hit=k;document.querySelectorAll('.preset').forEach(b=>b.classList.toggle('on',b.dataset.p===hit))}
function selCount(){const n=Math.max(0,(window.N||0)-(window.P||0)),c=$('count').value.trim().toLowerCase();return(c==='all'||!c)?n:Math.min(n,Math.max(0,parseInt(c)||0))}
function presetText(){const T=turboOn();document.querySelectorAll('.preset').forEach(b=>{const p=PRESETS[b.dataset.p],r=parseFloat(p.rest),n=parseInt(p.par),sp=T?n*60/(r+0.7):60/(r+2),sh=sp>=10?Math.round(sp):sp.toFixed(1);b.querySelector('span').textContent=p.rest+'s rest'+(T?', '+n+' at a time':'')+(p.be==='0'?', no breaks':'')+'. About '+sh+' per minute'})}
function summary(){if(S)drawDl(S);presetText();const rest=parseRest(),c=selCount();markPreset();$('s1').classList.toggle('done',listOK);$('s2').classList.toggle('done',rest!=null&&c>0);{const bb=BROS.find(x=>x.id===browser);$('s3').classList.toggle('done',!!(bb&&bb.found))}$('s0').classList.add('done');$('sPlat').textContent=PI[platform].label;$('fastwarn').classList.toggle('hide',!(rest!=null&&(rest<2||(turboOn()&&(parseInt($('par').value)||1)>=5))));
 const be=parseInt($('be').value)||0,bm=parseMin($('bd').value);const par=Math.max(1,Math.min(30,parseInt($('par').value)||1)),T=turboOn();$('turbobox').classList.toggle('hide',!T);$('parbox').classList.toggle('hide',!T);$('engbox').classList.toggle('hide',platform!=='instagram');$('sMode').textContent=T?'Turbo'+(par>1?' x'+par:''):'Classic';{const sp=rest==null?0:(T?par*60/((rest||0)+0.7):60/((rest||0)+2));$('sSpeed').textContent=sp?(sp>=10?Math.round(sp):sp.toFixed(1))+' per min':'-';{const f=Math.min(100,Math.max(3,Math.log10(Math.max(sp,1))/Math.log10(400)*100)),g=$('gfill');g.style.width=f+'%';const lv=sp<15?['Gentle','var(--ok)']:sp<45?['Balanced','var(--acc)']:sp<120?['Fast: higher risk of a block','var(--warn)']:['Very aggressive: a block is likely','var(--bad)'];g.style.background=lv[1];$('gtxt').textContent=sp?lv[0]:'';$('gtxt').style.color=lv[1]}}
 const sec=(T?Math.ceil(c/par)*((rest||0)+0.6):c*((rest||0)+2))+(be>0&&c>1?Math.floor((c-1)/be)*bm*60:0);
 const b=BROS.find(x=>x.id===browser);$('sAcc').textContent=listOK?c:'0';$('sRest').textContent=rest==null?'?':$('rest').value.trim()+' s';$('sBreak').textContent=be>0?`every ${be} (${$('bd').value.trim()})`:'none';
 $('sBro').textContent=b?b.label:browser;$('sEta').textContent=listOK&&c>0?'~'+fmt(sec):'-';
 $('go').disabled=!(listOK&&c>0&&rest!=null);$('sNote').textContent=!listOK?'Add your list in step 2 to begin.':c>0?'The browser window will open. Log in to '+PI[platform].label+' there if asked.':'Choose how many accounts to unfollow.'}
async function sendList(){let r;if(tab==='acct'){const f=S&&S.fetch,c=(f&&f.counts)||{};if(c[acctMode]==null){listOK=false;window.N=0;$('listmsg').className='msg';$('listmsg').textContent='Pick a list above and press the button to collect it.';return summary()}r=await api('/api/list',{kind:'account',mode:acctMode,platform})}
 else if(tab==='paste'){const t=$('paste').value;if(!t.trim()){listOK=false;window.N=0;$('listmsg').className='msg';$('listmsg').textContent='No list yet.';return summary()}r=await api('/api/list',{kind:'paste',text:t,platform})}
 else{if(!Object.keys(exportFiles).length){listOK=false;window.N=0;return summary()}r=await api('/api/list',{kind:'export',files:exportFiles,mode:exportMode,platform})}
 const m=$('listmsg');if(!r.ok){listOK=false;window.N=0;m.className='msg err';m.textContent=r.error;return summary()}
 listOK=r.count>0;window.N=r.count;window.P=r.protected||0;m.className='msg ok';m.innerHTML=`<b class="n">${r.count}</b> accounts ready`+(r.protected?` &middot; ${r.protected} protected (will be skipped)`:'')+(r.invalid_n?` &middot; ${r.invalid_n} invalid names skipped (${esc(r.invalid.slice(0,3).join(', '))})`:'')+(r.sample.length?`<br>First: ${esc(r.sample.join(', '))}`:'');
 if(r.counts){$('modes').innerHTML='';for(const [k,l] of [['non_followers','Not following me back'],['mutual','Mutual'],['all','Everyone']]){const b=document.createElement('button');b.className='chip'+(exportMode===k?' on':'');b.textContent=`${l} (${r.counts[k]})`;b.onclick=()=>{exportMode=k;sendList()};$('modes').appendChild(b)}}
 summary()}
let pt;$('paste').addEventListener('input',()=>{clearTimeout(pt);pt=setTimeout(sendList,350)});
function setTab(t){tab=t;$('tabAcct').classList.toggle('on',t==='acct');$('paneA').classList.toggle('hide',t!=='acct');$('tabPaste').classList.toggle('on',t==='paste');$('tabExport').classList.toggle('on',t==='export');$('paneP').classList.toggle('hide',t!=='paste');$('paneE').classList.toggle('hide',t!=='export');sendList()}
$('tabAcct').onclick=()=>setTab('acct');$('tabPaste').onclick=()=>setTab('paste');$('tabExport').onclick=()=>setTab('export');
async function readFiles(fl){for(const f of fl){if(/\.json$/i.test(f.name))exportFiles[f.name]=await f.text()}const n=Object.keys(exportFiles);$('drop').querySelector('b').textContent=`${n.length} file${n.length==1?'':'s'} loaded`;$('drop').querySelector('small').textContent=n.join(', ');sendList()}
$('drop').onclick=()=>$('files').click();$('files').onchange=e=>readFiles(e.target.files);
['dragover','dragenter'].forEach(ev=>$('drop').addEventListener(ev,e=>{e.preventDefault();$('drop').classList.add('over')}));['dragleave','drop'].forEach(ev=>$('drop').addEventListener(ev,e=>{e.preventDefault();$('drop').classList.remove('over')}));
$('drop').addEventListener('drop',e=>readFiles(e.dataTransfer.files));
document.querySelectorAll('[data-c]').forEach(b=>b.onclick=()=>{$('count').value=b.dataset.c;summary();save()});
document.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>{$('rest').value=b.dataset.r;summary();save()});
document.querySelectorAll('.preset').forEach(b=>b.onclick=()=>{const p=PRESETS[b.dataset.p];$('rest').value=p.rest;$('be').value=p.be;$('bd').value=p.bd;$('par').value=p.par;summary();save()});
for(const k of ['rest','be','bd','tabs','block','count','par','eng','auto'])$(k).addEventListener('input',()=>{summary();save()});
document.querySelectorAll('#accents button').forEach(b=>b.onclick=()=>{document.documentElement.dataset.accent=b.dataset.a;save();markAcc()});
$('copylog').onclick=async()=>{const t=$('fulllog').textContent;try{await navigator.clipboard.writeText(t);toast('Log copied')}catch(e){const r=document.createRange();r.selectNodeContents($('fulllog'));const sl=getSelection();sl.removeAllRanges();sl.addRange(r);toast('Select and copy the log (Ctrl+C)')}};
function markAcc(){const x=document.documentElement.dataset.accent||'ice';document.querySelectorAll('#accents button').forEach(b=>b.classList.toggle('on',b.dataset.a===x))}markAcc();
document.querySelectorAll('[data-par]').forEach(b=>b.onclick=()=>{$('par').value=b.dataset.par;summary();save()});
function drawPlats(){const box=$('plats');box.innerHTML='';for(const k of ['instagram','tiktok']){const p=PI[k],e=document.createElement('button');e.className='plat'+(platform===k?' on':'');e.innerHTML=`<span class="pi"><svg viewBox="0 0 24 24">${PICON[k]}</svg></span><span><b>${p.label}${p.exp?'<span class="xp">Experimental</span>':''}</b><small>${k==='tiktok'?'Account or paste':'Account, paste or export'}</small></span>`;e.onclick=()=>{if(busyNow())return toast('Wait for the current job to finish first.');setPlatform(k,true)};box.appendChild(e)}}
function busyNow(){const f=S&&S.fetch,ph=S&&S.phase;return !!((f&&(f.state==='login'||f.state==='reading'))||(ph&&ph!=='idle'&&ph!=='done'&&ph!=='ended'))}
function setPlatform(k,user){platform=k;const p=PI[k];if(user)$('rest').value=String(p.rest);
 drawPlats();document.querySelectorAll('.pl').forEach(e=>e.textContent=p.label);$('s1sub').textContent=p.sub;
 $('tabAcct').classList.toggle('hide',!p.lists);$('tabExport').classList.toggle('hide',k!=='instagram');
 if((tab==='acct'&&!p.lists)||(tab==='export'&&k!=='instagram'))tab='paste';
 $('block').classList.toggle('hide',k!=='instagram');const bl=$('blockLbl');if(bl)bl.classList.toggle('hide',k!=='instagram');
 const w=$('platwarn');w.classList.toggle('hide',!PW[k]);w.textContent=PW[k]||'';
 lastFetch='';listOK=false;window.N=0;if(S)drawFetch(S);setTab(tab);save()}
function drawBro(){const box=$('bro');box.innerHTML='';for(const b of BROS){const e=document.createElement('button');e.className='bcard'+(browser===b.id?' on':'');e.innerHTML=`<b>${b.label}</b><span>${b.sub}</span><br><em class="${b.found?'ok':''}">${b.found?'Detected':'Not detected'}</em>`;e.onclick=()=>{browser=b.id;drawBro();summary();save()};box.appendChild(e)}}
$('go').onclick=()=>{const b=BROS.find(x=>x.id===browser);$('mtext').textContent=b&&!b.found&&b.id!=='chromium'?`${b.label} was not detected on this computer, so the start may fail.`:'The browser window will open. If you are not logged in to '+PI[platform].label+' yet, log in there and the bot continues by itself.';
 $('mAcc').textContent=selCount();$('mRest').textContent=$('rest').value.trim()+' s';$('mBro').textContent=b?b.label:browser;$('modal').classList.remove('hide')};
$('mNo').onclick=()=>$('modal').classList.add('hide');
$('mYes').onclick=async()=>{$('modal').classList.add('hide');save();try{if('Notification' in window&&Notification.permission==='default')Notification.requestPermission()}catch(e){}const r=await api('/api/start',{count:$('count').value,rest:$('rest').value,break_every:$('be').value,break_dur:$('bd').value,browser,tabs:$('tabs').value,block:$('block').value,platform,engine:engineNow(),parallel:$('par').value,auto_speed:$('auto').checked});if(!r.ok)toast(r.error);else if(r.protected)toast(r.protected+' protected account'+(r.protected>1?'s were':' was')+' skipped.');poll()};
$('fetchBtn').onclick=async()=>{$('ferr').classList.add('hide');const r=await api('/api/fetch/start',{browser,platform,followers:acctMode!=='following'});if(!r.ok)toast(r.error);poll()};
$('fenough').onclick=async()=>{$('fenough').disabled=true;const r=await api('/api/fetch/enough',{});if(!r.ok)toast(r.error)};
$('fstop').onclick=async()=>{$('fstop').disabled=true;await api('/api/stop',{})};
const OPTS=[['mutual','1','Mutual','They follow you and you follow them',1],['non_followers','2','Not following me back','You follow them, they do not follow you',1],['following','3','People I follow','Everyone you follow (no followers needed, fastest)',0],['all','4','All','Everyone connected to you: people you follow plus people who follow you',1]];
function drawFetch(s){let f=s.fetch||{};if(f.platform&&f.platform!==platform&&f.state!=='login'&&f.state!=='reading')f={state:'idle'};const PL=PI[platform].label,busy=f.state==='login'||f.state==='reading';
 $('fprog').classList.toggle('hide',!busy);$('fetchBtn').classList.toggle('hide',busy);
 if(busy){const nm={following:'people you follow',followers:'your followers'}[f.stage];$('fstop').disabled=false;$('fenough').classList.toggle('hide',!(f.state==='reading'&&f.n>0));$('fnote').classList.toggle('hide',!(f.state==='reading'&&f.n>0));if(f.state!=='reading'||!f.n)$('fenough').disabled=false;else if(f.n&&$('fenough').dataset.s!==f.stage){$('fenough').dataset.s=f.stage;$('fenough').disabled=false};
  $('ftext').textContent=f.wait?`${PL} asked to slow down. Waiting ${Math.round(f.wait)}s, then continuing...`:f.stage==='login'?`Log in to ${PL} in the browser window. Reading starts by itself.`:nm?`Reading ${nm}: ${f.n.toLocaleString()}${f.total?' of '+f.total.toLocaleString():''}`:'Starting...';
  $('fbar').style.width=(f.stage==='login'||!f.total?(f.stage==='login'?8:35):Math.min(100,f.n/f.total*100))+'%'}
 const e=$('ferr');e.classList.toggle('hide',!(f.state==='error'&&f.error));e.textContent=f.error||'';
 const c=f.counts||{},has=!!c.following;
 const avail=k=>c[k]!=null,need=OPTS.find(o=>o[0]===acctMode)[4];
 $('fetchBtn').textContent=!has?'Collect this list':avail(acctMode)?'Collect again':need?'Collect followers too':'Collect this list';
 const key=JSON.stringify(c)+acctMode+busy;const box=$('opts');
 if(box.dataset.k!==key){box.dataset.k=key;
  box.innerHTML=OPTS.map(([k,n,t,d])=>`<button class="opt${acctMode===k?' on':''}${busy?' dis':''}" data-m="${k}"><span class="k">${n}</span><span class="t"><b>${t}</b><span>${d}</span></span>${avail(k)?`<span class="c">${c[k].toLocaleString()}</span>`:(has&&!busy?`<span class="n">${k==='non_followers'&&f.partial&&f.partial.followers?'needs all followers':'needs followers'}</span>`:'')}</button>`).join('')+((f.partial&&(f.partial.followers||f.partial.following))?`<div class="msg" style="margin:2px 0 0">You stopped early, so these lists use only the part that was read${f.partial.followers?' (Not following me back is hidden because it needs all followers)':''}.</div>`:'');
  box.querySelectorAll('.opt').forEach(b=>b.onclick=()=>{acctMode=b.dataset.m;box.dataset.k='';drawFetch(S);sendList()})}
 const done=(f.state==='done'||f.state==='error')&&has;const sig=done?JSON.stringify(c):'';if(sig!==lastFetch){lastFetch=sig;if(done&&tab==='acct')sendList()}}
$('stop').onclick=async()=>{$('stop').disabled=true;$('stop').textContent='Stopping...';await api('/api/stop',{})};
$('again').onclick=async()=>{await api('/api/new',{});poll()};
$('quit').onclick=async()=>{if(confirm('Quit the app?')){await api('/api/quit',{});document.body.innerHTML='<p style="padding:60px 20px;text-align:center;font-size:16px">The app has been closed. You can close this window.</p>'}};
$('forget').onclick=async()=>{if(!confirm('Forget all saved logins (Instagram and TikTok)? You will have to log in again next time.'))return;const r=await api('/api/forget-login',{});toast(r.ok?'Saved login removed.':r.error)};
$('cd').onclick=async()=>{await api('/api/cooldown/clear',{platform});poll()};
document.querySelectorAll('#filters button').forEach(b=>b.onclick=()=>{filter=b.dataset.f;document.querySelectorAll('#filters button').forEach(x=>x.classList.toggle('on',x===b));if(S)drawFeed(S)});
const CHECK='<svg viewBox="0 0 24 24"><path d="M5 12.5 10 17.5 19 7"/></svg>',DASH='<svg viewBox="0 0 24 24"><path d="M6 12h12"/></svg>',CROSS='<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7 7 17"/></svg>';
const ST={unfollowed:[CHECK,'','Unfollowed'],already:[DASH,'y','Not following'],not_found:[DASH,'y','Not found'],failed:[CROSS,'r','Failed']};
function drawFeed(s){let rows=s.rows.slice().reverse();if(filter==='unfollowed')rows=rows.filter(r=>r.status==='unfollowed');else if(filter==='skipped')rows=rows.filter(r=>r.status==='already'||r.status==='not_found');else if(filter==='failed')rows=rows.filter(r=>r.status==='failed');
 $('feedempty').classList.toggle('hide',rows.length>0);$('feed').innerHTML=rows.map(r=>{const [ic,cl,w]=ST[r.status]||ST.failed;return `<li class="${cl}"><span class="ic">${ic}</span><span class="u">${esc(r.user)}</span><span class="s">${r.status==='failed'?esc(r.detail).slice(0,38):w} &middot; ${r.secs}s</span></li>`}).join('')}
function render(s){S=s;lostN=0;$('ver').textContent=s.version;if(!BROS.length||$('bro').childElementCount===0){BROS=s.browsers;if(!BROS.some(b=>b.id===browser))browser='chromium';drawBro();summary()}
 const ph=s.phase,busy=ph==='login'||ph==='running',blocked=/^BLOCK/.test(s.reason);
 $('s0').classList.toggle('hide',ph!=='idle');$('s1').classList.toggle('hide',ph!=='idle');$('s2').classList.toggle('hide',ph!=='idle');$('s3').classList.toggle('hide',ph!=='idle');$('hero').classList.toggle('hide',ph!=='idle');$('sumcard').classList.toggle('hide',ph!=='idle');
 $('live').classList.toggle('hide',ph==='idle');$('feedcard').classList.toggle('hide',ph==='idle');const cdl=(s.cooldowns&&s.cooldowns[platform])||0;$('cd').classList.toggle('hide',!(cdl>0));
 $('liveT').textContent=(ph==='login'?'Waiting for login on ':ph==='finished'?'Run on ':'Unfollowing on ')+PI[s.platform||platform].label;$('liveM').textContent=(s.engine==='turbo'?'Turbo'+(s.parallel>1?' x'+s.parallel:''):'Classic');
 $('pill').className='pill '+(ph==='running'?(s.challenge?'login':'run'):ph==='login'?'login':ph==='finished'?(s.error||blocked?'bad':'done'):'');
 $('pilltxt').textContent=ph==='idle'?'Ready':ph==='login'?'Waiting for login':ph==='running'?(s.stopping?'Stopping':s.challenge?'Needs you':'Running'):'Finished';
 let top='';if(cdl>0&&ph==='idle')top+=`<div class="alert bad"><b>Cooldown active.</b> ${PI[platform].label} blocked an action earlier. Wait <b>${fmt(cdl)}</b> before running again, or clear the cooldown from the footer.</div>`;
 if(s.challenge&&busy)top+=`<div class="alert info"><b>Security check needed.</b> ${PI[s.platform||platform].label} is showing a captcha in the browser window. Solve it there and the bot continues by itself.</div>`;
 if(ph==='login')top+=`<div class="alert info"><b>A browser window has opened.</b> Log in to ${PI[s.platform||platform].label} there. The bot starts automatically as soon as you are logged in.</div>`;
 if(ph==='finished'){if(s.error)top+=`<div class="alert bad"><b>Something went wrong.</b> ${esc(s.error)}</div>`;else if(/^(BLOCK|Stopped after|Logged out|Login|Browser)/.test(s.reason))top+=`<div class="alert bad">${esc(s.reason)}</div>`;else top+=`<div class="alert ok"><b>Finished.</b> ${esc(s.reason||'The run completed.')} ${s.stats.unfollowed} unfollowed in ${fmt(s.elapsed)}.</div>`}
 $('top').innerHTML=top;
 const st=s.stats,done=st.unfollowed,tg=Math.max(1,s.target);$('bigdone').textContent=done;$('bigtarget').textContent=s.target;$('ringfg').style.strokeDashoffset=402.1*(1-Math.min(1,done/tg));
 $('tU').textContent=done;$('tS').textContent=st.already+st.not_found;$('tF').textContent=st.failed;$('tL').textContent=s.left||'-';
 $('mSpeed').textContent=s.per_min?`${s.per_min} per min`:'';$('mEta').textContent=busy&&s.eta!=null?`about ${fmt(s.eta)} left`:'';$('mTime').textContent=s.elapsed?`elapsed ${fmt(s.elapsed)}`:'';
 const rb=$('restbox');if(s.rest&&busy){rb.classList.remove('hide');$('restT').textContent=s.rest.left.toFixed(1)+'s';$('restL').textContent=s.rest.label||'rest';$('restBar').style.width=(100-s.rest.left/Math.max(.1,s.rest.total)*100)+'%'}else rb.classList.add('hide');
 $('fulllog').textContent=s.log.map(l=>(l.t||'')+'  '+l.text).join('\n');$('logs').innerHTML=s.log.slice(-4).map(l=>`<div class="${l.kind}">${esc(l.text)}</div>`).join('');
 $('stop').classList.toggle('hide',!busy);$('again').classList.toggle('hide',ph!=='finished');if(!busy){$('stop').disabled=false;$('stop').textContent='Stop'}
 drawFeed(s);drawFetch(s);drawDl(s);setTitle(s);notifyDone(s)}
let timer,pollGen=0;async function poll(){const g=++pollGen;clearTimeout(timer);try{const s=await rawApi('/api/state');if(g!==pollGen)return;render(s)}catch(e){if(g!==pollGen)return;if(++lostN===3)toast('Lost connection to the app. Is it still running?')}timer=setTimeout(poll,S&&(S.phase==='running'||S.phase==='login'||(S.fetch&&(S.fetch.state==='login'||S.fetch.state==='reading')))?600:2000)}
const BASE_TITLE=document.title;let prevPhase='idle';
function setTitle(s){const ph=s.phase;document.title=ph==='running'?`▶ ${s.stats.unfollowed}/${s.target} · ${BASE_TITLE}`:ph==='login'?`… Login · ${BASE_TITLE}`:ph==='finished'?`✔ Done · ${BASE_TITLE}`:BASE_TITLE}
function notifyDone(s){if(prevPhase!=='finished'&&prevPhase!=='idle'&&s.phase==='finished'){try{if('Notification' in window&&Notification.permission==='granted')new Notification(BASE_TITLE,{body:`Finished: ${s.stats.unfollowed} unfollowed`+(s.stats.failed?`, ${s.stats.failed} failed`:'')+'.'})}catch(e){}}prevPhase=s.phase}
async function dl(name){const r=await fetch('/download/'+name,{headers:{'X-Token':TOKEN}}).catch(()=>null);if(!r||!r.ok){let m='Nothing to download yet.';try{m=(await r.json()).error||m}catch(e){}return toast(m)}
 const cd=r.headers.get('Content-Disposition')||'',m=/filename="([^"]+)"/.exec(cd),b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=m?m[1]:name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1500);toast('Downloaded '+a.download)}
document.querySelectorAll('[data-dl]').forEach(b=>b.onclick=()=>dl(b.dataset.dl));
function drawDl(s){const d=s.downloads||{},fin=s.phase==='finished',has=d.results>0;$('dlbar').classList.toggle('hide',!has);
 if(has){$('dlTitle').textContent=fin?'Download your results':'Download so far';$('dlSaved').textContent=s.saved_report?'Report auto-saved on this computer':'';$('dlFailed').classList.toggle('hide',!(d.failed>0));$('retryFailed').classList.toggle('hide',!(fin&&d.failed>0))}
 const c=(s.fetch&&s.fetch.counts)||{};$('listdl').classList.toggle('hide',!(s.phase==='idle'&&listOK));
 for(const [k,key] of [['following.txt','following'],['followers.txt','followers'],['non_followers.txt','non_followers'],['mutual.txt','mutual']])document.querySelector('#listdl [data-dl="'+k+'"]').classList.toggle('hide',c[key]==null)}
$('retryFailed').onclick=async()=>{const r=await fetch('/download/failed.txt',{headers:{'X-Token':TOKEN}}).catch(()=>null);if(!r||!r.ok)return toast('There are no failed accounts.');const t=await r.text();await api('/api/new',{});$('paste').value=t;setTab('paste');toast('Failed accounts loaded. Check the pace, then start again.');poll()};
let kt;function keepInfo(){const n=($('keep').value.match(/[^\s,;]+/g)||[]).length;$('keepmsg').textContent=n?n+' protected':''}
$('keep').addEventListener('input',()=>{keepInfo();clearTimeout(kt);kt=setTimeout(async()=>{const r=await api('/api/keep',{text:$('keep').value});if(r.ok){$('keepmsg').textContent=r.count?r.count+' protected account'+(r.count>1?'s':'')+' saved'+(r.invalid?' · '+r.invalid+' invalid skipped':''):'';if(listOK)sendList()}else toast(r.error)},600)});
(async()=>{const r=await api('/api/keep');if(r.ok){$('keep').value=r.text;keepInfo()}})();
if(!['127.0.0.1','localhost'].includes(location.hostname))$('reports').classList.add('hide');
$('reports').onclick=async()=>{const r=await api('/api/open-reports',{});toast(r.ok?'Opened: '+r.path:r.error)};
setPlatform(platform,false);summary();poll();
</script></body></html>
"""

if __name__ == "__main__":
    main()
