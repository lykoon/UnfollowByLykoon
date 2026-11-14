#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unfollow by Lykoon  v10  (Playwright, runs on your own computer)

Needs only Python 3.8+. The first start sets itself up (no venv / pip needed).

Easiest way: just run this and the bot asks you everything
    python3 unfollow_bot.py

Prefer a graphical window? Run  python3 unfollow_app.py  instead.

You are in control (no hidden limits):
  * How many accounts to unfollow   ->  any number (or 'all')
  * Rest after each unfollow        ->  any number of seconds (0 is fine); type 12 and it rests exactly 12
  * Break                           ->  after every N unfollows rest M minutes (or seconds), 0 = no break
  * Daily / hourly limit            ->  none by default (0 = unlimited), you can set one if you want

Run directly (no questions):
  python3 unfollow_bot.py --yes --mode non_followers --limit all --gap 0
  python3 unfollow_bot.py --yes --mode all --limit 500 --gap 3 --tabs 4
  python3 unfollow_bot.py --stats | --dry-run | --export | --retry-failed | --benchmark

Files (in the same folder):
  following_usernames.txt  the bot's LIVE list (a name disappears once it is unfollowed)
  following_usernames.backup.txt  backup of the original list (created on first change)
  following.json / followers_*.json  Instagram export
  keep.txt       accounts that must never be unfollowed
  state.csv      record of every account (resume uses this)
  settings.json  settings from last time
  cooldown.txt   when it is safe to run again after Instagram blocked you
  debug/         screenshots on failure
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
import contextlib
import copy
import csv
import glob
import json
import os
import random
import re
import shutil
import time
from collections import Counter, deque
from pathlib import Path
from urllib.parse import urlparse

try:   # makes arrow keys / backspace / history work in input()
    HAVE_READLINE = bool(__import__("readline"))
except Exception:
    HAVE_READLINE = False

VERSION = "v12"
SETTINGS_VER = 6
STATE_FIELDS = ["ts", "username", "status", "detail"]
FINISHED = {"unfollowed", "already", "not_found"}
LIST_NAME = "following_usernames.txt"
BACKUP_NAME = "following_usernames.backup.txt"
SETTINGS_NAME = "settings.json"
# daily/hourly = 0 -> no limit (unlimited)
BUILTIN = dict(gap=[3.0, 3.0], break_every=60, break_min=1.0, hourly=0, daily=0, block="off", tabs=3, browser="chromium")
MAX_TABS = 6
BROWSERS = {"chromium": None, "chrome": "chrome", "msedge": "msedge"}   # naam -> Playwright channel
OVERHEAD = 2.0   # rough per-account click + page overhead (seconds), only for the ETA
MODE_TEXT = {
    "non_followers": "people you follow who do NOT follow you back",
    "followers": "people who follow you back (mutual)",
    "all": "everyone you follow",
}
BLOCK_HINTS = (
    "action blocked", "try again later", "we restrict certain activity", "we limit how often",
    "temporarily blocked", "please wait a few minutes",
)

# ---- "load only what is needed": all of this is blocked (applied AFTER login)
BLOCK_TYPES = {"image", "media", "font", "texttrack", "manifest", "eventsource"}
BLOCK_URL_PARTS = (
    "facebook.com/tr", "connect.facebook.net", "google-analytics.com", "googletagmanager.com",
    "/ajax/bz", "/logging/", "/api/v1/feed/timeline", "/api/v1/feed/reels_tray",
    "/api/v1/clips/", "/api/v1/discover/", "/api/v1/direct_v2/", "/api/v1/notifications/",
    "/api/v1/highlights/", "/api/v1/stories/",
)
# max level: everything above + this is blocked too. Only essentials from Instagram's own hosts get through.
IG_HOSTS = ("instagram.com", "cdninstagram.com", "fbcdn.net")
MAX_EXTRA_TYPES = {"ping"}
MAX_EXTRA_PARTS = (
    "/api/v1/feed/", "/api/v1/discover", "/api/v1/direct_v2", "/api/v1/notifications", "/api/v1/stories",
    "/api/v1/highlights", "/api/v1/reels", "/api/v1/fbsearch", "/api/v1/web/search", "/api/v1/news",
    "/api/v1/clips", "/api/v1/tags/", "/api/v1/locations/",
)
# security checks (captcha) must always load fully, including their images
CAPTCHA_HOSTS = ("google.com", "gstatic.com", "recaptcha.net", "hcaptcha.com", "arkoselabs.com", "funcaptcha.com",
                 "cloudflare.com", "challenges.cloudflare.com")
BLOCK_LEVELS = ["off", "normal", "max"]
LEVEL_TEXT = {
    "off": "load everything (slowest)",
    "normal": "block images/video/fonts/tracking",
    "max": "only essentials, everything else blocked",
}
POPUP_BUTTONS = re.compile(
    r"^(Not Now|Not now|Decline optional cookies|Allow all cookies|Only allow essential cookies)$")
CHROME_FLAGS = [
    "--mute-audio", "--disable-background-networking", "--disable-sync", "--no-first-run",
    "--disable-notifications",
    # stop background (hidden) tabs from being throttled, so prefetch stays fast
    "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
]


class BotError(Exception):
    """An error whose message should be shown to the user as is."""


PW_HELP = (
    "The automation engine could not start.\n"
    "Close the app, delete the folder ~/.lykoon (Windows: C:\\Users\\<you>\\.lykoon) and start it again.\n"
    "It sets itself up again, no venv or manual install needed."
)


@contextlib.contextmanager
def pw_guard():
    """Show clear instructions instead of a long traceback when the system Playwright (without node) is used."""
    try:
        yield
    except FileNotFoundError as e:
        if "node" in str(e) or "playwright" in str(e).lower():
            raise BotError(PW_HELP)
        raise
    except ModuleNotFoundError as e:
        if "playwright" in str(e):
            raise BotError(PW_HELP)
        raise


# ================================================================ terminal ka look
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STY = dict(bold="1", dim="2", red="31", green="32", yellow="33", blue="34", magenta="35", cyan="36",
           white="97", grey="90")
SYM_U = dict(ok="✔", bad="✖", skip="○", dot="●", prompt="❯", warn="▲", clock="◷", arrow="➜",
             tl="╭", tr="╮", bl="╰", br="╯", h="─", v="│", full="█", empty="░", ell="…")
SYM_A = dict(ok="+", bad="x", skip="o", dot="*", prompt=">", warn="!", clock="~", arrow="->",
             tl="+", tr="+", bl="+", br="+", h="-", v="|", full="#", empty=".", ell=".")
PROG = None   # live progress of the running job (shown while resting)
PUMP = None   # wait that keeps Playwright events running (instead of time.sleep), set by run()
STOP = None   # (GUI) threading.Event: once set, the bot stops at the next opportunity
HOOK = None   # (GUI) callable(kind, **data): reports the bot's state to the outside
FINISH = None  # (GUI) threading.Event: "that is enough" while reading lists: stop reading, keep what was read


def emit(kind, **data):
    if HOOK is not None:
        try:
            HOOK(kind, **data)
        except Exception:
            pass


class Enough(Exception):
    """Raised inside the list reader when the person pressed 'That is enough'."""


def finish_asked():
    return FINISH is not None and FINISH.is_set()


def stop_asked():
    return STOP is not None and STOP.is_set()


def use_color():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    except Exception:
        return False


def use_uni():
    enc = getattr(sys.stdout, "encoding", None)
    return enc is None or "utf" in str(enc).lower()


def S(name):
    return (SYM_U if use_uni() else SYM_A)[name]


def paint(text, *styles):
    text = str(text)
    if not styles or not use_color():
        return text
    return "\x1b[" + ";".join(STY[s] for s in styles) + "m" + text + "\x1b[0m"


def vlen(s):
    return len(ANSI.sub("", s))


def term_cols():
    return shutil.get_terminal_size((88, 24)).columns


def box_w():
    return max(58, min(term_cols() - 1, 80))


def clip(s, n, pad=True):
    """Visible lambai n tak (zyada ho to kaat do, kam ho to space se bhar do)."""
    v = vlen(s)
    if v <= n:
        return s + " " * (n - v) if pad else s
    plain = ANSI.sub("", s)
    return plain[: max(0, n - 1)] + S("ell")


def box(lines, title="", style="cyan", width=None):
    w = width or box_w()
    inn = w - 4

    def b(x):
        return paint(x, style)
    if title:
        t = " " + title + " "
        top = b(S("tl") + S("h")) + paint(t, "bold", style) + b(S("h") * max(0, w - 3 - vlen(t)) + S("tr"))
    else:
        top = b(S("tl") + S("h") * (w - 2) + S("tr"))
    out = [top]
    for ln in lines:
        out.append(b(S("v")) + " " + clip(ln, inn) + " " + b(S("v")))
    out.append(b(S("bl") + S("h") * (w - 2) + S("br")))
    return "\n".join(out)


def rule(title, style="cyan"):
    t = " " + title + " "
    print("\n" + paint(S("h") * 2, style) + paint(t, "bold", style) + paint(S("h") * max(0, box_w() - 2 - vlen(t)), style))


def kv(label, value, width=26):
    return paint(f"{label:<{width}}", "grey") + str(value)


def rl(s):
    """Tell readline that colour escape codes are invisible (otherwise the line wraps wrongly).
    Without readline the markers would show up as junk, so they are skipped then."""
    if not HAVE_READLINE:
        return s
    return ANSI.sub(lambda m: "\001" + m.group(0) + "\002", s)


def say(sym, style, text):
    print(" " + paint(S(sym), style) + " " + text)


def p_ok(t):
    say("ok", "green", t)


def p_warn(t):
    say("warn", "yellow", t)


def p_bad(t):
    say("bad", "red", t)


def p_info(t):
    say("dot", "cyan", t)


def banner():
    print(box([
        paint(S("dot") + " UNFOLLOW BY LYKOON", "bold", "cyan") + "  " + paint(VERSION, "grey"),
        paint("fast  " + S("dot") + "  clean  " + S("dot") + "  fully in your control", "grey"),
    ]))


def fmt_dur(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def fmt_gap(g):
    if g[1] == 0:
        return "0 (no rest)"
    return f"{g[0]:g}-{g[1]:g}" if g[0] != g[1] else f"{g[0]:g}"


def fmt_cap(n, cap):
    return f"{n} (no limit)" if not cap else f"{n}/{cap}"


class Progress:
    """Live progress bar shown with every result line and while resting."""

    def __init__(self, target):
        self.target = max(1, target)
        self.t0 = time.time()
        self.s = dict(unfollowed=0, already=0, not_found=0, failed=0)

    def done(self):
        return self.s["unfollowed"]

    def eta(self):
        d = self.done()
        if d <= 0:
            return None
        return (time.time() - self.t0) / d * max(0, self.target - d)

    def per_min(self):
        el = time.time() - self.t0
        return self.done() / el * 60 if el > 3 and self.done() else 0.0

    def bar(self, width=16):
        frac = min(1.0, self.done() / self.target)
        f = int(round(frac * width))
        return paint(S("full") * f, "green") + paint(S("empty") * (width - f), "grey"), frac

    def line(self, tail=""):
        bar, frac = self.bar()
        skipped = self.s["already"] + self.s["not_found"]
        eta = self.eta()
        out = (" " + bar + " " + paint(f"{self.done()}/{self.target}", "bold") + f" {int(frac * 100):>3}%   "
               + paint(S("ok"), "green") + str(self.done()) + " "
               + paint(S("skip"), "yellow") + str(skipped) + " "
               + paint(S("bad"), "red") + str(self.s["failed"])
               + paint(f"   {self.per_min():.0f}/min", "grey")
               + (paint(f"   ETA {fmt_dur(eta)}", "grey") if eta is not None else ""))
        if tail:
            out += "   " + tail
        return out


def pause(sec):
    """Wait, while Playwright events (routes, prefetch tabs) keep running."""
    if sec <= 0:
        return
    if PUMP is not None:
        try:
            PUMP(int(sec * 1000) or 1)
            return
        except Exception:
            pass
    time.sleep(sec)


def nap(sec, label=""):
    """Wait. In a terminal it shows a live progress bar + countdown."""
    if sec <= 0:
        return
    live = False
    try:
        live = sys.stdout.isatty() and PROG is not None
    except Exception:
        pass
    end = time.time() + sec
    while True:
        left = end - time.time()
        if left <= 0:
            break
        if stop_asked():
            break
        emit("rest", left=left, total=sec, label=label.strip())
        if live:
            tail = paint(S("clock") + f" {label}{left:5.1f}s", "cyan")
            print("\r\x1b[K" + clip(PROG.line(tail), term_cols() - 1, pad=False), end="", flush=True)
        pause(min(0.2 if live else 0.25, left))
    if live:
        print("\r\x1b[K", end="", flush=True)


def ding():
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass


def result_line(n, target, user, status, detail, secs):
    ts = time.strftime("%H:%M:%S")
    sym, sty, word = {
        "unfollowed": ("ok", "green", "UNFOLLOWED"),
        "already": ("skip", "yellow", "already not following"),
        "not_found": ("skip", "yellow", "account not found"),
        "failed": ("bad", "red", "FAIL: " + detail),
    }[status]
    left = " " + paint(S(sym), sty) + " " + paint(ts, "grey") + f"  {n:>4}  " + clip(user, 28)
    right = paint(word, sty if status != "unfollowed" else "bold")
    if status == "unfollowed":
        right = paint(word, "bold", "green")
    return left + " " + right + paint(f"  {secs:.1f}s", "grey")


# ================================================================ data
def read_entries(fn):
    """Instagram export json -> [(username, timestamp)]"""
    with open(fn, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out = []
    for e in data:
        if not isinstance(e, dict):
            continue
        for s in e.get("string_list_data") or []:
            if not isinstance(s, dict):
                continue
            name = s.get("value") or e.get("title") or (s.get("href") or "").rstrip("/").split("/")[-1]
            if name:
                out.append((name.strip(), int(s.get("timestamp") or 0)))
    return out


def load_ts_map(d):
    fj = d / "following.json"
    if not fj.exists():
        return {}
    try:
        return {n.lower(): ts for n, ts in read_entries(fj)}
    except Exception:
        return {}


def load_followers(d):
    names = set()
    for fn in glob.glob(str(d / "followers_*.json")):
        for name, _ in read_entries(fn):
            names.add(name.lower())
    return names


def norm_user(s):
    """'@Name', 'instagram.com/name/', 'tiktok.com/@name' -> 'name' ('' if invalid)"""
    s = s.strip().lower()
    s = re.sub(r"^(?:https?://)?(?:www\.|m\.|web\.|mbasic\.)?(?:instagram\.com/(?:_u/)?|tiktok\.com/)", "", s)
    s = s.lstrip("@").split("?")[0].split("#")[0].strip("/")
    if "/" in s:
        return ""
    return s if re.fullmatch(PLAT["user_re"], s) else ""


def read_keep(d):
    p = d / "keep.txt"
    if not p.exists():
        return set()
    out = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        u = norm_user(ln.split("#")[0])
        if u:
            out.add(u)
    return out


def keep_add(d, text):
    cur = read_keep(d)
    added, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        u = norm_user(tok)
        if not u:
            bad.append(tok)
        elif u not in cur and u not in added:
            added.append(u)
    if added:
        p = d / "keep.txt"
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        p.write_text(old + "\n".join(added) + "\n", encoding="utf-8")
    return added, bad


def keep_remove(d, text):
    p = d / "keep.txt"
    if not p.exists():
        return []
    drop = {norm_user(t) for t in re.split(r"[\s,;]+", text.strip()) if t}
    drop.discard("")
    kept, removed = [], []
    for ln in p.read_text(encoding="utf-8").splitlines():
        u = norm_user(ln.split("#")[0])
        if u and u in drop:
            removed.append(u)
        else:
            kept.append(ln)
    p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


class LiveList:
    """following_usernames.txt = the bot's live list. A name is REMOVED from it as soon as it is unfollowed."""

    def __init__(self, d):
        self.d = d
        self.path = d / LIST_NAME
        self.backup = d / BACKUP_NAME
        self.names, self.index = [], set()
        if self.path.exists():
            self._read()
        else:
            self.rebuild()

    def _read(self):
        seen, out = set(), []
        for ln in self.path.read_text(encoding="utf-8").splitlines():
            n = ln.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
        self.names, self.index = out, seen

    def _write(self):
        if self.path.exists() and not self.backup.exists():
            shutil.copyfile(self.path, self.backup)       # backup of the original list before the first change
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(self.names) + ("\n" if self.names else ""), encoding="utf-8")
        os.replace(tmp, self.path)                        # atomic: the list stays safe even if we are stopped midway

    def rebuild(self):
        """Build a new list from following.json. Returns how many names."""
        fj = self.d / "following.json"
        seen, out = set(), []
        if fj.exists():
            for n, _ in read_entries(fj):
                if n.lower() not in seen:
                    seen.add(n.lower())
                    out.append(n)
        self.names, self.index = out, seen
        if out:
            self._write()
        return len(out)

    def remove(self, user):
        k = user.lower()
        if k in self.index:
            self.index.discard(k)
            self.names = [n for n in self.names if n.lower() != k]
            self._write()

    def clean(self, state):
        """Remove finished accounts (per state.csv) from the list. Returns how many were removed."""
        drop = {n.lower() for n in self.names if state.last.get(n.lower()) in FINISHED}
        if not drop:
            return 0
        self.names = [n for n in self.names if n.lower() not in drop]
        self.index -= drop
        self._write()
        return len(drop)

    def count(self):
        return len(self.names)


class State:
    """state.csv (+ old done.txt / failed.txt if present)"""

    def __init__(self, d):
        self.path = d / "state.csv"
        self.rows = []
        for fn, base in (("done.txt", "already"), ("failed.txt", "failed")):
            p = d / fn
            if not p.exists():
                continue
            for ln in p.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                user, _, detail = ln.partition("|")
                st = base
                if "page_not_found" in detail:
                    st = "not_found"
                elif base == "already" and "unfollowed" in detail and "not_following" not in detail:
                    st = "unfollowed"
                self.rows.append({"ts": 0, "username": user.strip().lower(), "status": st, "detail": detail.strip()})
        if self.path.exists():
            with open(self.path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        r["ts"] = int(r.get("ts") or 0)
                    except ValueError:
                        r["ts"] = 0
                    r["username"] = (r.get("username") or "").lower()
                    r["status"] = r.get("status") or ""
                    self.rows.append(r)
        self.last = {r["username"]: r["status"] for r in self.rows}

    def add(self, user, status, detail=""):
        row = {"ts": int(time.time()), "username": user.lower(), "status": status, "detail": detail}
        new = not self.path.exists()
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=STATE_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        self.rows.append(row)
        self.last[row["username"]] = status

    def unfollow_times(self, window):
        now = time.time()
        return sorted(r["ts"] for r in self.rows if r["status"] == "unfollowed" and now - r["ts"] < window)

    def counts(self):
        c = {}
        for st in self.last.values():
            c[st] = c.get(st, 0) + 1
        return c

    def fail_reasons(self):
        return Counter(r["detail"] for r in self.rows
                       if r["status"] == "failed" and self.last.get(r["username"]) == "failed")


def next_free(times, window, cap):
    """How long to wait so the window stays under the cap (0 = now)."""
    if len(times) < cap:
        return 0
    return times[len(times) - cap] + window


def quota(state, daily, hourly):
    """daily/hourly = 0 means no limit. Returns: room, day count, hour count, wait until."""
    day, hour = state.unfollow_times(86400), state.unfollow_times(3600)
    room = float("inf")
    free_at = 0
    if daily:
        room = min(room, daily - len(day))
        free_at = max(free_at, next_free(day, 86400, daily))
    if hourly:
        room = min(room, hourly - len(hour))
        free_at = max(free_at, next_free(hour, 3600, hourly))
    return max(0, room), len(day), len(hour), free_at


def build_lists(args, d, live=None, state=None):
    live = live or LiveList(d)
    state = state or State(d)
    followers = load_followers(d)
    keep = read_keep(d)
    mode = args.mode or "all"
    if not live.names:
        raise BotError("The bot list (following_usernames.txt) is empty or missing.\n"
                       "Rebuild it from following.json with menu option 6.")
    if mode != "all" and not followers:
        raise BotError("followers_*.json not found! Extract it from your Instagram export zip (unzip -j ...).")

    # do not count finished accounts (removing them from the file happens during the run)
    names = [n for n in live.names if state.last.get(n.lower()) not in FINISHED]
    if args.order in ("oldest", "newest"):
        ts = load_ts_map(d)
        if args.order == "oldest":
            names.sort(key=lambda n: (ts.get(n.lower(), 0) == 0, ts.get(n.lower(), 0)))
        else:
            names.sort(key=lambda n: -ts.get(n.lower(), 0))
    elif args.order == "random":
        random.shuffle(names)

    mutual = [n for n in names if n.lower() in followers]
    non_f = [n for n in names if n.lower() not in followers]

    def eligible(n):
        k = n.lower()
        if k in keep:
            return False
        st = state.last.get(k)
        return (st == "failed") if args.retry_failed else (st != "failed")

    elig = [n for n in names if eligible(n)]
    e_mut = [n for n in elig if n.lower() in followers]
    e_non = [n for n in elig if n.lower() not in followers]
    pending = {"followers": e_mut, "non_followers": e_non, "all": elig}[mode]
    total = {"followers": mutual, "non_followers": non_f, "all": names}[mode]
    return dict(live=live, state=state, followers=followers, keep=keep, names=names,
                mutual=mutual, non_f=non_f, e_mut=e_mut, e_non=e_non, elig=elig,
                pending=pending, total=total)


# ================================================================ settings / args
def load_settings(d):
    try:
        data = json.loads((d / SETTINGS_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(d, args):
    data = dict(v=SETTINGS_VER, gap=list(args.gap), break_every=args.break_every, break_min=args.break_min,
                hourly=args.hourly, daily=args.daily, block=args.block, tabs=args.tabs, tabs_ok=1, browser=args.browser)
    try:
        (d / SETTINGS_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def norm_gap(vals):
    """[12] -> exactly 12 | [12, 20] -> random between 12 and 20 | [0] -> no rest."""
    nums = [float(v) for v in vals]
    if not nums or len(nums) > 2:
        raise ValueError("Write it like:  12   or   0   or   10-15")
    if any(n < 0 for n in nums):
        raise ValueError("Seconds cannot be negative")
    lo = nums[0]
    hi = nums[1] if len(nums) == 2 else lo
    if lo > hi:
        lo, hi = hi, lo
    if hi > 3600:
        raise ValueError("Rest is too long. Use less than 3600 seconds (1 hour)")
    return [lo, hi]


def parse_gap_text(s):
    t = str(s).strip().lower().replace(",", ".")
    if re.match(r"^-\s*\d", t):
        raise ValueError("Seconds cannot be negative")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec)?\s*(?:(?:-|\u2013|to|\s)\s*(\d+(?:\.\d+)?)\s*(?:s|sec)?)?", t)
    if not m:
        raise ValueError("Enter a number:  12   or   0   or   10-15")
    return norm_gap([g for g in m.groups() if g])


def parse_minutes(s):
    """'5' -> 5 minute | '30s' -> 0.5 minute | '2m' | '1h'. Return: minute (float)."""
    t = s.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(s|sec|m|min|h)?", t)
    if not m:
        raise ValueError("Write it like:  5 (minutes)   or   30s   or   2m")
    v, u = float(m.group(1)), m.group(2) or "m"
    return v / 60 if u in ("s", "sec") else (v * 60 if u == "h" else v)


def parse_count(raw, n):
    """'all' / number -> int (1..n)"""
    raw = str(raw).strip().lower()
    if raw in ("all", "sab", "max", "poore"):
        return n
    try:
        v = int(raw)
    except ValueError:
        raise ValueError("Enter a number (e.g. 50) or 'all'")
    if v < 1:
        raise ValueError("At least 1 is required")
    return min(v, n)


def resolve_limit(args, n):
    raw = args.limit
    if raw is None:
        raw = 10
    return parse_count(raw, n) if n > 0 else 0


def resolve_defaults(args, d):
    saved = load_settings(d)
    fresh = saved.get("v") == SETTINGS_VER     # do not reuse limits/block from an older settings version

    def pick(name, cast, keep_old=False):
        v = getattr(args, name)
        if v is not None:
            return v
        if name == "tabs" and saved.get("tabs_ok") != 1:
            return BUILTIN[name]          # ignore the old saved value (3 tabs)
        if fresh or keep_old:
            try:
                return cast(saved[name])
            except Exception:
                pass
        return BUILTIN[name]

    if args.block is None:
        sb = saved.get("block") if fresh else None
        args.block = sb if sb in BLOCK_LEVELS else BUILTIN["block"]
    args.break_every = max(0, int(pick("break_every", int, True)))
    args.break_min = max(0.0, float(pick("break_min", float, True)))
    args.daily = max(0, int(pick("daily", int)))
    args.hourly = max(0, int(pick("hourly", int)))
    if args.daily and args.hourly > args.daily:
        args.hourly = args.daily
    args.tabs = min(MAX_TABS, max(1, int(pick("tabs", int))))
    if args.browser is None:
        sbr = saved.get("browser") if fresh else None
        args.browser = sbr if sbr in BROWSERS else BUILTIN["browser"]
    if args.gap is None:
        try:
            args.gap = norm_gap(saved["gap"])
        except Exception:
            args.gap = list(BUILTIN["gap"])
    else:
        try:
            args.gap = norm_gap(args.gap)
        except ValueError as e:
            raise BotError(str(e))


# ================================================================ helpers
def cooldown_name():
    return "cooldown.txt" if PLAT["name"] == "instagram" else f"cooldown_{PLAT['name']}.txt"


def cooldown_left(d):
    p = d / cooldown_name()
    try:
        return float(p.read_text().strip()) - time.time()
    except Exception:
        return 0


def rest_seconds(args):
    lo, hi = args.gap
    return lo if lo == hi else random.uniform(lo, hi)


def estimate_seconds(n, gap, break_every, break_min, hourly=0):
    per = (gap[0] + gap[1]) / 2 + OVERHEAD
    total = n * per
    if break_every > 0 and n > 1:
        total += ((n - 1) // break_every) * break_min * 60
    if hourly > 0 and n > 1:
        total = max(total, ((n - 1) // hourly) * 3600)
    return total


def print_summary(args, L, state, d):
    mode = args.mode or "all"
    room, day_n, hour_n, free_at = quota(state, args.daily, args.hourly)
    c = state.counts()
    rows = [kv("In the bot list now", f"{len(L['names'])} accounts")]
    if L["followers"]:
        rows.append(kv("They follow you", f"{len(L['mutual'])}  (mutual)"))
        rows.append(kv("They do NOT follow you", len(L["non_f"])))
    rows.append(kv("Mode", f"{mode}  ->  total {len(L['total'])}, remaining {len(L['pending'])}"))
    rows.append(kv("Done so far", f"unfollowed {c.get('unfollowed', 0)}  already {c.get('already', 0)}  "
                               f"not_found {c.get('not_found', 0)}  failed {c.get('failed', 0)}"))
    rows.append(kv("Keep list", f"{len(L['keep'])} accounts"))
    rows.append(kv("Last 24 hours", fmt_cap(day_n, args.daily)))
    rows.append(kv("Last hour", fmt_cap(hour_n, args.hourly)))
    left = cooldown_left(d)
    if left > 0:
        rows.append(paint(f"COOLDOWN active: {fmt_dur(left)} left (Instagram blocked you earlier)", "yellow"))
    if L["pending"] and args.daily:
        rows.append(kv("Remaining at this pace", f"~{-(-len(L['pending']) // args.daily)} days"))
    print(box(rows, "STATS"))


# ================================================================ browser
def alert_text(page):
    """Only the dialog/alert text (not the whole page, otherwise someone's bio could be mistaken for a 'block')."""
    try:
        return " ".join(page.locator('[role="dialog"], [role="alert"]').all_inner_texts()).lower()
    except Exception:
        return ""


def body_text(page):
    try:
        return page.inner_text("body", timeout=5000).lower()
    except Exception:
        return ""


def is_block(text):
    return any(h in text for h in BLOCK_HINTS)


def make_router(level="normal", stats=None):
    types = BLOCK_TYPES | (MAX_EXTRA_TYPES if level == "max" else set())
    parts = BLOCK_URL_PARTS + (MAX_EXTRA_PARTS if level == "max" else ())

    def handler(route):
        try:
            req = route.request
            url = req.url
            host = urlparse(url).hostname or ""
            if host.endswith(CAPTCHA_HOSTS) or "/challenge" in url:
                route.continue_()
                return
            try:
                if "/challenge" in req.frame.page.url:      # whole page is a security check: let everything load
                    route.continue_()
                    return
            except Exception:
                pass
            block = req.resource_type in types or any(s in url for s in parts)
            if not block and level == "max":
                block = not (urlparse(url).hostname or "").endswith(IG_HOSTS)   # block third parties (ads/tracking)
            if stats is not None:
                stats["blocked" if block else "allowed"] += 1
            if block:
                route.abort()
            else:
                route.continue_()
        except Exception:
            pass  # page closed, no problem
    return handler


def install_blocking(ctx, level, stats=None):
    """Remove all earlier routes and apply blocking for the new level."""
    try:
        ctx.unroute_all(behavior="ignoreErrors")
    except Exception:
        pass
    if level != "off":
        ctx.route("**/*", make_router(level, stats))


def logged_in(ctx, page):
    try:
        return bool(PLAT["logged_in"](ctx, page))
    except Exception:
        return False


def wait_login(ctx, page, timeout=600):
    page.goto(PLAT["origin"] + "/", wait_until="domcontentloaded")
    t0, warned = time.time(), False
    while time.time() - t0 < timeout and not stop_asked():
        if logged_in(ctx, page):
            page.wait_for_timeout(1500)
            return True
        if not warned:
            p_info(f"Log in to {PLAT['label']} in the browser window. I will continue by myself once you are logged in...")
            warned = True
        page.wait_for_timeout(2000)
    return False


# ================================================================ read your own lists (following / followers)
IG_ORIGIN = "https://www.instagram.com"
FETCH_BACKOFF = 45                      # seconds (x attempt number) to wait when Instagram says "slow down"
IG_APP_ID = "936619743392459"          # the public web-app id Instagram's own website sends
FETCH_JS = """async ([url, appid]) => {
  let r; try { r = await fetch(url, {credentials: 'include', headers: {'X-IG-App-ID': appid, 'X-Requested-With': 'XMLHttpRequest'}}); }
  catch (e) { return {status: 0, j: null}; }
  let t = ''; try { t = await r.text(); } catch (e) {}
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  return {status: r.status, j: j, t: t.slice(0, 90)};
}"""


def _ig_get(page, path, tries=4, backoff=None):
    """GET an Instagram web API path from inside the logged-in page. Returns parsed json or raises BotError."""
    for attempt in range(tries):
        if stop_asked():
            raise BotError("Stopped by you.")
        if finish_asked():
            raise Enough()
        res = page.evaluate(FETCH_JS, [IG_ORIGIN + path, IG_APP_ID])
        st, j = res.get("status"), res.get("j")
        if st == 200 and isinstance(j, dict):
            return j
        if st in (401, 403) or (isinstance(j, dict) and j.get("message") == "login_required"):
            raise BotError("Instagram says you are not logged in. Log in again and retry.")
        wait = (backoff or FETCH_BACKOFF) * (attempt + 1)            # 429 / "please wait a few minutes" / network hiccup: back off, then try again
        hint = re.sub(r"\s+", " ", res.get("t") or "")[:70]
        p_warn(f"Instagram asked to slow down (HTTP {st}{': ' + hint if hint else ''}). Waiting {wait:g}s before retrying...")
        emit("fetch", stage="waiting", wait=wait)
        end = time.time() + wait
        while time.time() < end and not stop_asked() and not finish_asked():
            page.wait_for_timeout(500)
    raise BotError("Instagram keeps refusing to send the list right now (rate limit). Wait 15-30 minutes and try again.")


def read_connections(ctx, page, kinds=("following", "followers"), out=None):
    """Read the logged-in account's own lists. Returns {'me': username, 'following': [...], 'followers': [...]}.
    `out` (a dict) is filled as we go, so a partial result survives an error."""
    out = {} if out is None else out
    if not page.url.startswith(IG_ORIGIN):
        page.goto(IG_ORIGIN + "/", wait_until="domcontentloaded")
    uid = next((c["value"] for c in ctx.cookies() if c["name"] == "ds_user_id" and c.get("value")), "")
    if not uid:
        raise BotError("Could not read your account id. Make sure you are logged in, then retry.")
    totals = {}
    try:
        u = _ig_get(page, f"/api/v1/users/{uid}/info/", tries=1).get("user", {})
        out["me"] = u.get("username", "")
        totals = dict(following=u.get("following_count") or 0, followers=u.get("follower_count") or 0)
        out["totals"] = totals
    except BotError as e:
        if "not logged in" in str(e):
            raise
    for kind in kinds:
        names, seen, max_id = [], set(), ""
        out[kind] = names
        try:
            while True:
                j = _ig_get(page, f"/api/v1/friendships/{uid}/{kind}/?count=200" + (f"&max_id={max_id}" if max_id else ""))
                for u in j.get("users", []):
                    n = (u.get("username") or "").strip()
                    if n and n.lower() not in seen:
                        seen.add(n.lower())
                        names.append(n)
                emit("fetch", stage=kind, n=len(names), total=totals.get(kind, 0))
                max_id = j.get("next_max_id") or ""
                if not max_id:
                    break
                if finish_asked():
                    raise Enough()
                page.wait_for_timeout(int(random.uniform(600, 1200)))    # gentle pace, like a person scrolling
        except Enough:
            out.setdefault("partial", {})[kind] = True      # "enough" for THIS list: keep what we have and go on to the next one
            if FINISH is not None:
                FINISH.clear()
    return out


def fetch_lists(args, d, kinds=("following", "followers"), out=None):
    """Open the browser, wait for login, read the lists, close the browser."""
    from playwright.sync_api import sync_playwright
    out = {} if out is None else out
    set_platform(getattr(args, "platform", "instagram"))
    with sync_playwright() as p:
        ctx = launch_ctx(p, args, d, dict(args=CHROME_FLAGS))
        try:
            page0 = ctx.pages[0] if ctx.pages else ctx.new_page()
            emit("fetch", stage="login")
            if not wait_login(ctx, page0):
                raise BotError("Login did not happen (or it was cancelled).")
            emit("fetch", stage="reading")
            if PLAT["name"] == "tiktok":
                read_tiktok_lists(ctx, page0, kinds, out)
            elif PLAT["name"] == "instagram":
                read_connections(ctx, page0, kinds, out)
            else:
                raise BotError(f"Reading your lists is not available for {PLAT['label']}. Paste the profiles instead.")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return out


def dismiss_popups(page):
    try:
        b = page.get_by_role("button", name=POPUP_BUTTONS)
        if b.count() > 0:
            b.first.click(timeout=2000)
            page.wait_for_timeout(300)
    except Exception:
        pass


def buttons(scope):
    return dict(
        following=scope.get_by_role("button", name="Following"),
        requested=scope.get_by_role("button", name="Requested"),
        follow=scope.get_by_role("button", name="Follow", exact=True),
        follow_back=scope.get_by_role("button", name="Follow Back"),
    )


def pick_scope(page):
    """Look inside the profile header first (to avoid buttons of suggested accounts)."""
    try:
        h = page.locator("main header")
        if h.count() > 0:
            s = h.first
            if sum(x.count() for x in buttons(s).values()) > 0:
                return s
    except Exception:
        pass
    return page


def wait_until(page, fn, timeout_ms, step_ms=40):
    """Poll fn() every step_ms until it is true (Playwright events keep running). True/False."""
    end = time.time() + timeout_ms / 1000.0
    while True:
        try:
            if fn():
                return True
        except Exception as e:
            reraise_if_closed(e)
        if time.time() >= end:
            return False
        page.wait_for_timeout(step_ms)


def unfollow_option(page, timeout=6000):
    d = page.get_by_role("dialog")
    cand = (
        d.get_by_role("button", name="Unfollow", exact=True)
        .or_(d.get_by_role("menuitem", name="Unfollow", exact=True))
        .or_(d.get_by_text("Unfollow", exact=True))
        .or_(page.get_by_role("menuitem", name="Unfollow", exact=True))
        .or_(page.get_by_text("Unfollow", exact=True))
    ).first
    return cand if wait_until(page, cand.is_visible, timeout, 30) else None


def reraise_if_closed(e):
    """If the browser/page was closed, do not record 'failed', pass it up."""
    if "closed" in str(e).lower():
        raise e


def challenge_present(page):
    """True if the site is showing a security check (captcha / challenge page)."""
    try:
        extra = PLAT.get("challenge")
        if extra and extra(page):
            return True
        u = page.url.lower()
        if "/challenge" in u or "accounts/suspended" in u:
            return True
        return any(k in f.url.lower() for f in page.frames for k in ("recaptcha", "hcaptcha", "arkoselabs", "funcaptcha"))
    except Exception:
        return False


def solve_challenge(page, timeout=600):
    """Pause while the person solves the captcha in the browser window. True = solved."""
    p_warn(f"{PLAT['label']} is asking for a security check (captcha). Solve it in the browser window; the bot continues by itself.")
    emit("challenge", on=True)
    t0 = time.time()
    try:
        while time.time() - t0 < timeout and not stop_asked():
            if not challenge_present(page):
                page.wait_for_timeout(1200)
                return True
            page.wait_for_timeout(1000)
        return False
    finally:
        emit("challenge", on=False)


def profile_url(user):
    return PLAT["profile_url"](user)


def process(page, user, preloaded=False, _again=True):
    """Return (status, detail) for the current platform. status: unfollowed|already|not_found|failed|blocked|logged_out"""
    return PLAT["process"](page, user, preloaded, _again)


def process_instagram(page, user, preloaded=False, _again=True):
    if not (preloaded and user.lower() in page.url.lower()):
        try:
            page.goto(profile_url(user), wait_until="commit", timeout=30000)
        except Exception as e:
            reraise_if_closed(e)
            return "failed", f"page open error ({type(e).__name__})"
    if "accounts/login" in page.url:
        return "logged_out", ""
    if challenge_present(page):
        if not solve_challenge(page):
            return "failed", "security check (captcha) was not solved"
        if _again:
            return process(page, user, False, False)

    any_state = (
        page.get_by_role("button", name="Following")
        .or_(page.get_by_role("button", name="Requested"))
        .or_(page.get_by_role("button", name="Follow", exact=True))
        .or_(page.get_by_role("button", name="Follow Back"))
    )
    if not wait_until(page, lambda: any_state.count() > 0, 12000, 40):
        if "accounts/login" in page.url:
            return "logged_out", ""
        low = body_text(page)
        if is_block(alert_text(page)) or "action blocked" in low:
            return "blocked", ""
        if re.search(r"this page isn.t available|page not found", low):
            return "not_found", ""
        if _again and challenge_present(page):
            if solve_challenge(page):
                return process(page, user, False, False)
            return "failed", "security check (captcha) was not solved"
        snippet = re.sub(r"\s+", " ", low).strip()[:80]
        return "failed", "profile buttons did not load" + (f" | page text: {snippet}" if snippet else " | page was empty")

    b = buttons(pick_scope(page))
    if b["following"].count() > 0:
        target = b["following"].first
    elif b["requested"].count() > 0:
        target = b["requested"].first
    else:
        return "already", ""

    opt = None
    for attempt in range(2):
        try:
            target.click(timeout=6000)
        except Exception as e:
            reraise_if_closed(e)
            if is_block(alert_text(page)):
                return "blocked", ""
            if attempt == 0:                     # maybe a popup was in the way
                dismiss_popups(page)
                continue
            return "failed", f"Following click error ({type(e).__name__})"
        opt = unfollow_option(page)
        if opt:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    if not opt:
        if is_block(alert_text(page)):
            return "blocked", ""
        return "failed", "Unfollow option not found"
    try:
        opt.click(timeout=6000)
    except Exception as e:
        reraise_if_closed(e)
        return "failed", f"Unfollow click error ({type(e).__name__})"

    done_loc = b["follow"].or_(b["follow_back"])
    conf = page.get_by_role("dialog").get_by_role("button", name="Unfollow")
    t_click, confirmed = time.time(), False
    while time.time() - t_click < 9.5:
        if done_loc.count() > 0:                       # unfollow ho gaya (button 'Follow' ban gaya)
            break
        if not confirmed and time.time() - t_click > 0.8 and conf.count() > 0:
            try:                                       # private account: confirm again
                conf.first.click(timeout=4000)
                confirmed = True
            except Exception as e:
                reraise_if_closed(e)
                return "failed", f"confirm click error ({type(e).__name__})"
        page.wait_for_timeout(40)

    if is_block(alert_text(page)):
        return "blocked", ""
    if b["following"].count() + b["requested"].count() == 0 and done_loc.count() > 0:
        return "unfollowed", ""
    return "failed", "still shows Following after the click"


def safe_process(page, user, preloaded):
    try:
        return process(page, user, preloaded)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if "closed" in str(e).lower():
            return "browser_closed", ""
        return "failed", f"unexpected error ({type(e).__name__})"


# ================================================================ platforms (Instagram, TikTok)
# TikTok is EXPERIMENTAL: its pages change often and are not tuned like Instagram's.
# Every selector lives in this section, so a change on their side is a small fix.
TT_BLOCK_HINTS = ("too many attempts", "visiting too frequently", "maximum number of attempts", "action too frequent",
                  "you're doing that too fast", "you’re doing that too fast", "try again later")


def _first_visible(loc):
    """The first visible element of a locator list (several buttons may share the same text)."""
    try:
        for i in range(min(loc.count(), 8)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    except Exception:
        pass
    return None


def _goto_profile(page, user, preloaded):
    if not (preloaded and user.lower() in page.url.lower()):
        try:
            page.goto(profile_url(user), wait_until="commit", timeout=30000)
        except Exception as e:
            reraise_if_closed(e)
            return f"page open error ({type(e).__name__})"
    return ""


def process_tiktok(page, user, preloaded=False, _again=True):
    err = _goto_profile(page, user, preloaded)
    if err:
        return "failed", err
    if "/login" in page.url:
        return "logged_out", ""
    if challenge_present(page):
        if not solve_challenge(page):
            return "failed", "security check (captcha) was not solved"
        if _again:
            return process_tiktok(page, user, False, False)
    rel = page.get_by_role("button", name=re.compile(r"^(following|friends|requested)$", re.I))
    foll = page.get_by_role("button", name=re.compile(r"^(follow|follow back)$", re.I))
    if not wait_until(page, lambda: rel.count() + foll.count() > 0, 12000, 60):
        low = body_text(page)
        if "couldn't find this account" in low or "couldn’t find this account" in low or "account doesn't exist" in low:
            return "not_found", ""
        if any(h in low for h in TT_BLOCK_HINTS):
            return "blocked", ""
        if "/login" in page.url:
            return "logged_out", ""
        if _again and challenge_present(page):
            if solve_challenge(page):
                return process_tiktok(page, user, False, False)
            return "failed", "security check (captcha) was not solved"
        return "failed", "profile buttons did not load"
    target = _first_visible(rel)
    if target is None:
        return "already", ""
    try:
        target.click(timeout=6000)
    except Exception as e:
        reraise_if_closed(e)
        return "failed", f"Following click error ({type(e).__name__})"
    conf = page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^unfollow$", re.I))
    t0, confirmed = time.time(), False
    while time.time() - t0 < 9.5:
        if rel.count() == 0 and foll.count() > 0:
            return "unfollowed", ""
        if not confirmed and conf.count() > 0:
            try:
                conf.first.click(timeout=4000)
                confirmed = True
            except Exception as e:
                reraise_if_closed(e)
                return "failed", f"confirm click error ({type(e).__name__})"
        page.wait_for_timeout(50)
    if any(h in (alert_text(page) + " " + body_text(page)[:600]) for h in TT_BLOCK_HINTS):
        return "blocked", ""
    return "failed", "still shows Following after the click"


def _ig_logged_in(ctx, page):
    if any(c["name"] == "sessionid" and c.get("value") for c in ctx.cookies(PLAT["origin"])):
        return True
    return page.locator('a[href="/direct/inbox/"]').count() > 0


def _tt_logged_in(ctx, page):
    if any(c["name"] in ("sessionid", "sid_tt", "sessionid_ss") and c.get("value") for c in ctx.cookies(PLAT["origin"])):
        return True
    return page.locator('[data-e2e="profile-icon"], [data-e2e="nav-profile"]').count() > 0


def _tt_challenge(page):
    try:
        return page.locator('#captcha_container, .captcha_verify_container, [class*="captcha-verify"]').count() > 0
    except Exception:
        return False


PLATFORMS = {
    "instagram": dict(name="instagram", label="Instagram", origin="https://www.instagram.com", user_re=r"[a-z0-9._]{1,30}", blocking=True,
                      lists=True, logged_in=_ig_logged_in, process=process_instagram, challenge=None,
                      profile_url=lambda u: f"{PLATFORMS['instagram']['origin']}/{u}/"),
    "tiktok": dict(name="tiktok", label="TikTok", origin="https://www.tiktok.com", user_re=r"[a-z0-9._]{2,24}", blocking=False,
                   lists=True, logged_in=_tt_logged_in, process=process_tiktok, challenge=_tt_challenge,
                   profile_url=lambda u: f"{PLATFORMS['tiktok']['origin']}/@{u}"),
}
PLAT = PLATFORMS["instagram"]


def set_platform(name="instagram", *_ignored):
    global PLAT
    PLAT = PLATFORMS.get(name, PLATFORMS["instagram"])
    return PLAT


# ---- TikTok: read the lists from the Following / Followers pop-up on your own profile page
def _tt_num(text):
    m = re.match(r"\s*([\d.,]+)\s*([kmb]?)", (text or "").lower())
    if not m:
        return 0
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(v * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)])


def read_tiktok_lists(ctx, page, kinds=("following", "followers"), out=None):
    out = {} if out is None else out
    origin = PLAT["origin"]
    if not page.url.startswith(origin):
        page.goto(origin + "/", wait_until="domcontentloaded")
    link = page.locator('[data-e2e="nav-profile"]').first
    try:
        link.click(timeout=10000)
        wait_until(page, lambda: "/@" in page.url, 10000, 100)
    except Exception:
        raise BotError("Could not open your TikTok profile. Make sure you are logged in, then retry.")
    m = re.search(r"/@([^/?#]+)", page.url)
    out["me"] = m.group(1) if m else ""
    totals = {}
    for kind in ("following", "followers"):
        try:
            totals[kind] = _tt_num(page.locator(f'[data-e2e="{kind}-count"]').first.inner_text(timeout=4000))
        except Exception:
            totals[kind] = 0
    out["totals"] = dict(totals)
    for kind in kinds:
        names, seen = [], set()
        out[kind] = names
        opened = False
        for sel in (f'[data-e2e="{kind}"]', f'[data-e2e="{kind}-count"]', f'text=/^{kind}$/i'):
            try:
                page.locator(sel).first.click(timeout=5000)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            raise BotError(f"Could not open your {kind} list on TikTok. TikTok may have changed its page.")
        pop = page.locator('[role="dialog"], [data-e2e="follow-info-popup"]').first
        if not wait_until(page, lambda: pop.count() > 0, 8000, 100):
            raise BotError(f"The {kind} list did not open on TikTok.")
        idle = 0
        try:
            while idle < 5:
                if stop_asked():
                    raise BotError("Stopped by you.")
                if finish_asked():
                    raise Enough()
                hrefs = pop.locator('a[href^="/@"]').evaluate_all("els => els.map(e => e.getAttribute('href'))")
                before = len(names)
                for h in hrefs:
                    n = re.sub(r"^/@", "", (h or "").split("?")[0]).strip("/").lower()
                    if n and "/" not in n and n not in seen and n != out["me"].lower():
                        seen.add(n)
                        names.append(n)
                emit("fetch", stage=kind, n=len(names), total=totals.get(kind, 0))
                idle = idle + 1 if len(names) == before else 0
                if totals.get(kind) and len(names) >= totals[kind]:
                    break
                pop.locator('a[href^="/@"]').last.evaluate(
                    "el => { let p = el.parentElement; while (p && p.scrollHeight <= p.clientHeight + 5) p = p.parentElement; if (p) p.scrollTop = p.scrollHeight; }")
                page.wait_for_timeout(int(random.uniform(700, 1300)))
        except Enough:
            out.setdefault("partial", {})[kind] = True
            if FINISH is not None:
                FINISH.clear()
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    return out


def launch_ctx(p, args, d, extra=None):
    """Open the browser: the built-in Chromium (default) or your Google Chrome / Edge (--browser chrome)."""
    kw = dict(headless=False, locale="en-US", viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    kw.update(extra or {})
    ch = BROWSERS.get(args.browser)
    if ch:
        kw["channel"] = ch
    prof = args.profile if not ch else f"{args.profile}_{args.browser}"    # every browser has its own login profile
    if PLAT["name"] != "instagram":
        prof += "_" + PLAT["name"]                                         # ... and every platform too
    try:
        return p.chromium.launch_persistent_context(str(d / prof), **kw)
    except Exception as e:
        msg = str(e)
        if ch and ("Executable doesn't exist" in msg or "is not found" in msg or "not found at" in msg):
            raise BotError(f"{args.browser} was not found on this computer.\n"
                           f"Either install it (in the venv: playwright install {ch}) or use the built-in Chromium: --browser chromium")
        raise


# ================================================================ TURBO engine (Instagram): one request per unfollow, no page load
IDS_MIN_BULK = 60                  # fewer names than this: look the ids up one by one instead of reading the whole following list
VARIANTS = ["v4", "v3", "v2", "v1"]    # unfollow request shapes; the bot finds the one that works by itself
BLOCK_RE = re.compile(r"feedback_required|spam|wait a few minutes|try again later|rate.?limit|too many", re.I)
CHALLENGE_RE = re.compile(r"challenge_required|checkpoint_required|/challenge", re.I)

JS_UNFOLLOW = r"""
async (a) => {
  const base = {'X-CSRFToken': a.csrf, 'X-IG-App-ID': a.app, 'X-Requested-With': 'XMLHttpRequest', 'X-ASBD-ID': '129477',
                'X-IG-WWW-Claim': (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('www-claim-v2')) || '0'};
  const HP = Object.assign({'Content-Type': 'application/x-www-form-urlencoded'}, base);
  const VARS = {
    v4: (id) => ['/api/v1/web/friendships/' + id + '/unfollow/', ''],
    v3: (id) => ['/web/friendships/' + id + '/unfollow/', ''],
    v2: (id) => ['/api/v1/friendships/destroy/' + id + '/', 'container_module=profile&user_id=' + id],
    v1: (id) => ['/api/v1/friendships/destroy/' + id + '/', ''],
  };
  const mk = VARS[a.variant] || VARS.v4;
  const tpl = a.tpl || null;
  const TH = tpl ? Object.assign({}, tpl.headers, {'X-CSRFToken': a.csrf}) : HP;
  const isHtml = (t) => /^\s*<(!doctype|html)/i.test(t.slice(0, 40));
  async function one(item) {
    const u = item[0]; let id = item[1] || null; const t0 = performance.now();
    const ms = () => Math.round(performance.now() - t0);
    try {
      if (!id) {
        const lr = await fetch('/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u), {headers: base, credentials: 'include'});
        const lt = await lr.text();
        let uo = null;
        try { uo = JSON.parse(lt).data.user; id = (uo && uo.id) || null; } catch (e) { id = null; }
        if (uo && uo.followed_by_viewer === false && uo.requested_by_viewer !== true) {
          return {u: u, id: id, status: 200, text: '{"already":true}', ms: ms(), url: lr.url};      // you do not follow them: nothing to do
        }
        if (!id) {
          const lok = lr.status === 200 && !isHtml(lt);
          return {u: u, status: lok ? 404 : lr.status, text: lt.slice(0, 600), ms: ms(), url: lr.url};
        }
      }
      const spec = tpl ? [tpl.url.split('{id}').join(id), tpl.body == null ? '' : tpl.body.split('{id}').join(id)] : mk(id);
      const r = await fetch(spec[0], {method: tpl ? tpl.method : 'POST', headers: TH, body: spec[1] === '' ? undefined : spec[1], credentials: 'include'});
      const t = await r.text();
      return {u: u, id: id, status: r.status, text: t.slice(0, 600), ms: ms(), url: r.url};
    } catch (e) { return {u: u, id: id, status: 0, text: String(e), ms: ms(), url: ''}; }
  }
  return await Promise.all(a.items.map(one));
}
"""


def turbo_classify(status, text, url=""):
    """API answer -> (status, detail). status: unfollowed | not_found | failed | blocked | challenge | logged_out | html"""
    t = text or ""
    if re.match(r"\s*<(!doctype|html)", t[:40], re.I):
        return ("logged_out", "") if "accounts/login" in (url or "") else ("html", f"HTML answer (HTTP {status})")
    if status == 200 and '"already":true' in t:
        return "already", "you were not following this account"
    if status == 200 and re.search(r'"status"\s*:\s*"ok"', t):
        try:
            fs = json.loads(t).get("friendship_status") or {}
        except ValueError:
            fs = {}
        if fs.get("following") is True:
            return "failed", "Instagram said ok but you still follow this account"
        return "unfollowed", ""
    if CHALLENGE_RE.search(t):
        m = re.search(r'"url"\s*:\s*"([^"]*challenge[^"]*)"', t)
        return "challenge", (m.group(1).replace("\\/", "/") if m else "")
    if status == 429 or BLOCK_RE.search(t):
        return "blocked", re.sub(r"\s+", " ", t)[:120]
    if status in (401, 403) and re.search(r"login_required|not.?logged", t, re.I):
        return "logged_out", ""
    if status == 404 or re.search(r"user.?not.?found", t, re.I):
        return "not_found", ""
    if status == 0:
        return "failed", "network error: " + t[:60]
    return "failed", f"HTTP {status}: " + re.sub(r"\s+", " ", t)[:90]


def turbo_ids(page, uid, want):
    """Read your following list (username -> id) so each unfollow is a single request. Returns (map, complete)."""
    idmap, max_id, expected = {}, "", None
    try:
        expected = int(_ig_get(page, f"/api/v1/users/{uid}/info/", tries=1)["user"]["following_count"])
    except Exception:
        pass
    emit("phase", name="ids", n=0, total=expected or 0)
    while True:
        if stop_asked():
            raise BotError("Stopped by you.")
        j = _ig_get(page, f"/api/v1/friendships/{uid}/following/?count=200" + (f"&max_id={max_id}" if max_id else ""), tries=2, backoff=15)
        for u in j.get("users", []):
            n, pk = (u.get("username") or "").lower(), u.get("pk") or u.get("pk_id") or u.get("id")
            if n and pk:
                idmap[n] = str(pk)
        emit("phase", name="ids", n=len(idmap), total=expected or 0)
        max_id = j.get("next_max_id") or ""
        if not max_id or all(w in idmap for w in want):
            break
        page.wait_for_timeout(int(random.uniform(250, 500)))
    return idmap, bool(expected) and len(idmap) >= expected - 3


def learn_unfollow(ctx, args, user, pk, uid):
    """Unfollow ONE account the normal way (like a person) and record the exact request Instagram's own page sends.
    That request becomes the template for every other account, so TURBO always matches what Instagram uses today.
    Returns (status, detail, template_or_None, seconds)."""
    page1 = ctx.new_page()
    seen = []

    def on_req(r):
        try:
            if r.method == "POST" and r.url.startswith(IG_ORIGIN) and re.search(r"unfollow|destroy|friendships", r.url + " " + (r.headers.get("x-fb-friendly-name") or ""), re.I):
                seen.append(r)
        except Exception:
            pass

    page1.on("request", on_req)
    t0 = time.time()
    try:
        st, detail = process_instagram(page1, user)
        if not pk and st == "unfollowed":
            try:
                pk = str(_ig_get(page1, f"/api/v1/users/web_profile_info/?username={user}")["data"]["user"]["id"])
            except Exception:
                pk = None
        tpl = None
        if seen and st == "unfollowed":
            best = [q for q in seen if re.search(r"destroy|unfollow", q.url + " " + (q.headers.get("x-fb-friendly-name") or ""), re.I)] or seen
            r = best[-1]
            body = r.post_data
            path = re.sub(r"^https?://[^/]+", "", r.url)
            blob = path + " " + (body or "")
            ids = [pk] if pk and pk in blob else sorted({x for x in re.findall(r"\d{5,}", blob) if x != str(uid)}, key=len)
            if len(ids) == 1:
                hs = {k: v for k, v in r.headers.items() if (k.startswith("x-") or k == "content-type") and k != "x-csrftoken"}
                tpl = dict(method="POST", url=path.replace(ids[0], "{id}"), body=(body.replace(ids[0], "{id}") if body else None), headers=hs)
        return st, detail, tpl, time.time() - t0
    finally:
        try:
            page1.close()
        except Exception:
            pass


def run_turbo(args, d, L):
    """TURBO: returns None when finished, or 'fallback' when the request method does not work (then the classic engine continues)."""
    global PROG, PUMP
    from playwright.sync_api import sync_playwright
    state, live, pending = L["state"], L["live"], L["pending"]
    target = resolve_limit(args, len(pending))
    conc = max(1, min(30, int(getattr(args, "parallel", 1) or 1)))
    PROG = prog = Progress(target)
    stats = prog.s
    consec_fail = since_break = processed = chal_n = 0
    stop_reason, result = "", None
    variant = getattr(args, "unf_variant", None) or VARIANTS[0]
    blocks_n = soft_n = 0
    tried = set()
    tpl, learn_try, turbo_ok, diag, learn_n = None, not getattr(args, "no_learn", False), 0, [], 0
    with sync_playwright() as p:
        ctx = launch_ctx(p, args, d, dict(args=CHROME_FLAGS))
        page0 = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            emit("phase", name="login")
            if not wait_login(ctx, page0):
                p_bad("Stopped by you." if stop_asked() else "Login did not happen (10 minutes passed). Stopping.")
                return None
            install_blocking(ctx, "off")                     # blocking page resources breaks Instagram's own API calls
            PUMP = page0.wait_for_timeout
            if not page0.url.startswith(IG_ORIGIN):
                page0.goto(IG_ORIGIN + "/", wait_until="domcontentloaded")

            def ck():
                m = {x["name"]: x["value"] for x in ctx.cookies(IG_ORIGIN)}
                return m.get("csrftoken", ""), m.get("ds_user_id", "")

            csrf, uid = ck()
            if not (csrf and uid):
                raise BotError("Login cookies were not found. Log in to Instagram in the browser window and run again.")
            idmap, complete = {}, False
            if len(pending) >= IDS_MIN_BULK:
                try:
                    idmap, complete = turbo_ids(page0, uid, {u.lower() for u in pending[:target + 50]})
                except BotError as e:
                    if "Stopped" in str(e):
                        raise
                    p_warn(f"Could not read the id list ({e}); looking ids up one by one.")
            p_ok(f"TURBO on  {paint(f'{conc} at a time, rest after each round', 'grey')}")
            print()
            emit("phase", name="running", target=target, browser=args.browser, tabs=conc, level="turbo")

            def record(u, st, detail, secs):
                nonlocal processed, consec_fail, chal_n
                processed += 1
                if st in FINISHED:
                    state.add(u, st, detail)
                    live.remove(u)
                    stats[st] += 1
                    consec_fail = chal_n = 0
                else:
                    state.add(u, "failed", detail)
                    stats["failed"] += 1
                    consec_fail += 1
                print(result_line(processed, target, u, st, detail, secs))
                emit("result", n=processed, user=u, status=st, detail=detail, secs=secs, stats=dict(stats), target=target,
                     per_min=prog.per_min(), eta=prog.eta(), left=live.count())

            def send(items):
                nonlocal csrf, uid
                csrf, uid = ck()
                for _ in range(3):                       # the page may reload under us: try again
                    try:
                        return page0.evaluate(JS_UNFOLLOW, dict(items=[list(i) for i in items], csrf=csrf, app=IG_APP_ID, variant=variant, tpl=tpl))
                    except Exception as e:
                        reraise_if_closed(e)
                        page0.wait_for_timeout(1200)
                return []

            dq = deque(pending)
            while dq and stats["unfollowed"] < target and not stop_reason:
                if stop_asked():
                    stop_reason = "Stopped by you. Progress is saved."
                    break
                chunk, need = [], target - stats["unfollowed"]
                while dq and len(chunk) < min(conc, need):
                    u = dq.popleft()
                    pk = idmap.get(u.lower())
                    if pk is None and complete:          # not in your full live list: you do not follow them any more
                        processed += 1
                        state.add(u, "already", "not in your following list")
                        live.remove(u)
                        stats["already"] += 1
                        print(result_line(processed, target, u, "already", "", 0))
                        emit("result", n=processed, user=u, status="already", detail="", secs=0, stats=dict(stats), target=target,
                             per_min=prog.per_min(), eta=prog.eta(), left=live.count())
                        continue
                    chunk.append((u, pk))
                if not chunk:
                    continue
                learned = 0
                if learn_try:
                    learn_try = False
                    u, pk = chunk.pop(0)
                    learned = 1
                    st, detail, tpl, secs = learn_unfollow(ctx, args, u, pk, uid)
                    if st in ("blocked", "logged_out"):
                        dq.appendleft(u)
                        stop_reason = f"Logged out of {PLAT['label']}. Run again and log in." if st == "logged_out" else \
                            f"BLOCKED: {PLAT['label']} restricted this action. Stopped for {args.cooldown_h:g} hours. Increase the rest time before running again."
                        if st == "blocked":
                            (d / cooldown_name()).write_text(str(time.time() + args.cooldown_h * 3600))
                            ding()
                        break
                    record(u, st, detail, secs)
                    if tpl is None and st in ("already", "not_found", "failed") and learn_n < 3:
                        learn_try, learn_n = True, learn_n + 1
                    p_ok("Learned the exact request Instagram uses right now, using it for the rest." if tpl else
                         "Could not record Instagram's own request; trying the built-in request shapes.")
                    if not chunk:
                        t = rest_seconds(args)
                        if t > 0 and dq:
                            nap(t, "rest ")
                        since_break += 1
                        continue
                try:
                    res = send(chunk)
                except Exception as e:
                    if "closed" in str(e).lower():
                        stop_reason = "Browser was closed. Progress is saved, run again to continue."
                        break
                    raise
                retry, html_first, blocked, challenge, logged_out = [], None, "", None, False
                soft_bad = False
                for (u, _), r in zip(chunk, res or [None] * len(chunk)):
                    if r is None:
                        retry.append(u)
                        continue
                    st, detail = turbo_classify(r["status"], r["text"], r.get("url", ""))
                    if r.get("id"):
                        idmap[u.lower()] = str(r["id"])
                    if st == "html":
                        html_first = html_first or (u, r.get("id"))
                        if len(diag) < 3:
                            diag.append(f"{u}: HTTP {r.get('status')} {r.get('url', '')[-60:]} -> web page instead of data")
                        retry.append(u)
                    elif st == "blocked":
                        blocked = detail or "blocked"
                        retry.append(u)
                    elif st == "challenge":
                        challenge = detail or IG_ORIGIN + "/"
                        retry.append(u)
                    elif st == "logged_out":
                        logged_out = True
                        retry.append(u)
                    else:
                        secs = (r.get("ms") or 0) / 1000.0
                        if st == "unfollowed":
                            turbo_ok += 1
                        if st == "failed" and turbo_ok == 0 and soft_n < 8:       # nothing worked yet: probably the wrong request shape
                            soft_n += 1
                            soft_bad = True
                            if len(diag) < 3:
                                diag.append(f"{u}: HTTP {r.get('status')} {re.sub(chr(10), ' ', (r.get('text') or ''))[:110]}")
                            retry.append(u)
                            continue
                        record(u, st, detail, secs)
                dq.extendleft(reversed(retry))
                if soft_bad:
                    if tpl is not None:
                        p_warn("The recorded request did not work. Trying the built-in request shapes...")
                        tpl = None
                    else:
                        tried.add(variant)
                        nxt = [v for v in VARIANTS if v not in tried]
                        if nxt:
                            p_warn(f"Request shape '{variant}' did not work, trying '{nxt[0]}'...")
                            variant = nxt[0]
                        else:
                            result = "fallback"

                if logged_out:
                    stop_reason = f"Logged out of {PLAT['label']}. Run again and log in."
                elif blocked and getattr(args, "auto_speed", False) and blocks_n < 4:
                    blocks_n += 1
                    old = f"{conc} at a time, rest {args.gap[0]:g}-{args.gap[1]:g}s"
                    conc = max(1, conc // 2)
                    args.gap = (max(args.gap[0] * 1.6, 2.0), max(args.gap[1] * 1.6, 3.0))
                    wait = args.block_wait * 60 * blocks_n
                    p_warn(f"Instagram pushed back ({blocked[:50]}). Pausing {fmt_dur(wait)}, then slowing down "
                           f"(was {old}; now {conc} at a time, rest {args.gap[0]:g}-{args.gap[1]:g}s). Slowdown {blocks_n}/4.")
                    emit("phase", name="running", target=target, browser=args.browser, tabs=conc, level="turbo")
                    nap(wait, "pushback ")
                    blocked = ""
                elif blocked:
                    (d / cooldown_name()).write_text(str(time.time() + args.cooldown_h * 3600))
                    ding()
                    stop_reason = (f"BLOCKED: {PLAT['label']} restricted this action ({blocked[:60]}). Stopped for {args.cooldown_h:g} hours. "
                                   f"Increase the rest time before running again.")
                elif challenge:
                    chal_n += 1
                    if chal_n > 3:
                        stop_reason = "The security check keeps coming back. Open Instagram in the browser, finish it, then run again."
                    else:
                        page0.goto(challenge if challenge.startswith("http") else IG_ORIGIN + challenge, wait_until="domcontentloaded")
                        if not solve_challenge(page0):
                            stop_reason = "Security check was not completed. Run again to continue."
                        else:
                            page0.goto(IG_ORIGIN + "/", wait_until="domcontentloaded")
                elif html_first and tpl is not None:
                    p_warn("Instagram answered the recorded request with a web page. Trying the built-in request shapes...")
                    tpl = None
                elif html_first:                         # the request was not answered like an API: find a shape that works
                    p_warn(f"'{variant}' returned a web page, trying other request shapes...")
                    ok = None
                    for v in [x for x in VARIANTS if x != variant]:
                        variant = v
                        r = send([(html_first[0], html_first[1])])
                        r = r[0] if r else None
                        if r and turbo_classify(r["status"], r["text"], r.get("url", ""))[0] in ("unfollowed", "not_found", "already", "blocked", "challenge"):
                            ok = v
                            break
                    if ok:
                        p_ok(f"Request shape '{ok}' works, continuing with it.")
                        args.unf_variant = ok
                    else:
                        result = "fallback"
                        break
                if stop_reason or result:
                    break
                if consec_fail >= min(args.max_fails, 3) and turbo_ok == 0 and not result:
                    if tpl is not None:
                        p_warn("The recorded request failed. Trying the built-in request shapes...")
                        tpl, consec_fail = None, 0
                    else:
                        result = "fallback"
                        break
                if consec_fail >= args.max_fails:
                    ding()
                    stop_reason = f"Stopped after {args.max_fails} failures in a row. See the FAIL lines above for the reason."
                    break

                done_all = stats["unfollowed"] >= target or not dq
                if not done_all and not blocked and not challenge:
                    t = rest_seconds(args)
                    if t > 0:
                        nap(t, "rest ")
                    since_break += len(chunk) + learned
                    if args.break_every > 0 and args.break_min > 0 and since_break >= args.break_every:
                        br = args.break_min * 60
                        print(" " + paint(S("clock"), "cyan") + f" {since_break} unfollows done, break: {fmt_dur(br)}")
                        nap(br, "break ")
                        since_break = 0
            else:
                if not stop_reason and not result:
                    stop_reason = "List finished." if stats["unfollowed"] < target else ""
        except KeyboardInterrupt:
            stop_reason = "Ctrl+C: stopped. Progress is saved (state.csv + list)."
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            PROG = None
            PUMP = None
    if result == "fallback":
        p_warn("The fast method did not work on this account, so the classic page-by-page method continues (slower).")
        for x in diag:
            p_warn("  Instagram said -> " + x)
        return "fallback"
    ding()
    el = time.time() - prog.t0
    emit("end", stats=dict(stats), elapsed=el, left=live.count(), reason=stop_reason)
    print("\n" + box([
        kv("Unfollowed", paint(str(stats["unfollowed"]), "bold", "green")),
        kv("Already not following", stats["already"]),
        kv("Account not found", stats["not_found"]),
        kv("Fail", paint(str(stats["failed"]), "red") if stats["failed"] else 0),
        kv("Time", f"{fmt_dur(el)}   ({stats['unfollowed'] / el * 60 if el > 1 else 0:.0f} unfollow/min)"),
        kv("Left in the bot list", f"{live.count()} accounts"),
    ], "RESULT (TURBO)", "green"))
    if stop_reason:
        (p_bad if stop_reason.startswith(("BLOCK", "Stopped after", "Logged out", "Login", "Browser")) else p_info)(stop_reason)
    return None



def run(args, d, L):
    global PROG, PUMP
    from playwright.sync_api import sync_playwright  # lazy: --stats/--dry-run do not need it

    set_platform(getattr(args, "platform", "instagram"))
    if PLAT["name"] == "instagram" and getattr(args, "engine", "turbo") == "turbo":
        if run_turbo(args, d, L) != "fallback":
            return
        L = build_lists(args, d, L["live"], L["state"])        # the fast method failed: carry on page by page
    state, live, pending = L["state"], L["live"], L["pending"]
    target = resolve_limit(args, len(pending))
    shots_left = args.shots
    (d / "debug").mkdir(exist_ok=True)
    capped = bool(args.daily or args.hourly)

    PROG = prog = Progress(target)
    stats = prog.s
    consec_fail = since_break = processed = 0
    K = max(1, args.tabs) if args.prefetch else 1
    stop_reason = ""

    with sync_playwright() as p:
        ctx = launch_ctx(p, args, d, dict(args=CHROME_FLAGS))
        page0 = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            emit("phase", name="login")
            if not wait_login(ctx, page0):
                p_bad("Stopped by you." if stop_asked() else "Login did not happen (10 minutes passed). Stopping.")
                return
            level = args.block if PLAT["blocking"] else "off"      # request blocking is only tuned for Instagram
            install_blocking(ctx, level)           # AFTER login, so captcha/checkpoint pages are not blocked
            p_ok(f"Logged in   {paint('browser: ' + args.browser + '  page load: ' + level + ' (' + LEVEL_TEXT[level] + ')  tabs: ' + str(K), 'grey')}")
            print()
            emit("phase", name="running", target=target, browser=args.browser, tabs=K, level=level)
            tabs = [page0] + [ctx.new_page() for _ in range(K - 1)]
            PUMP = page0.wait_for_timeout
            loaded = {}                              # user -> kab navigate kiya (prefetch)

            def preload(i):
                if i >= len(pending) or pending[i] in loaded:
                    return
                try:
                    tabs[i % K].goto(profile_url(pending[i]), wait_until="commit", timeout=20000)
                    loaded[pending[i]] = time.time()
                except Exception:
                    pass

            for idx, user in enumerate(pending):
                if stats["unfollowed"] >= target:
                    break
                if stop_asked():
                    stop_reason = "Stopped by you. Progress is saved."
                    break

                # ---- (agar lagayi ho to) ghante / din ki limit
                if capped:
                    room, day_n, hour_n, free_at = quota(state, args.daily, args.hourly)
                    if room == 0:
                        if args.daily and day_n >= args.daily:
                            stop_reason = f"Daily limit ({args.daily}) reached. Next slot in {fmt_dur(free_at - time.time())}."
                            break
                        wait = free_at - time.time() + random.uniform(2, 10)
                        p_warn(f"Hourly limit ({args.hourly}) reached, waiting {fmt_dur(wait)} before continuing...")
                        nap(wait, "limit: ")
                        loaded.clear()

                page = tabs[idx % K]
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                t_loaded = loaded.pop(user, None)
                fresh = t_loaded is not None and time.time() - t_loaded < 180
                t_start = time.time()
                status, detail = safe_process(page, user, fresh)
                if status == "failed" and detail.startswith("profile buttons"):
                    nap(2, "retrying: ")     # try once more with a completely fresh page
                    status, detail = safe_process(page, user, False)
                    while status == "failed" and detail.startswith("profile buttons") and level != "off":
                        # the page may be incomplete because of blocking: lower one level and retry this same account
                        level = BLOCK_LEVELS[BLOCK_LEVELS.index(level) - 1]
                        install_blocking(ctx, level)
                        p_warn(f"Page did not load fully, reduced blocking: now '{level}'. Trying again...")
                        loaded.clear()
                        status, detail = safe_process(page, user, False)
                        if not (status == "failed" and detail.startswith("profile buttons")):
                            args.block = level           # it worked at this level, remember it
                            save_settings(d, args)
                processed += 1
                secs = time.time() - t_start

                if status == "browser_closed":
                    stop_reason = "Browser was closed. Progress is saved, run again to continue."
                    break
                if status == "blocked":
                    until = time.time() + args.cooldown_h * 3600
                    (d / cooldown_name()).write_text(str(until))
                    ding()
                    stop_reason = (f"BLOCKED: {PLAT['label']} restricted this action. Stopped for {args.cooldown_h:g} hours. "
                                   f"Increase the rest time before running again (menu option 7 can also clear the cooldown).")
                    break
                if status == "logged_out":
                    stop_reason = f"Logged out of {PLAT['label']}. Run again and log in."
                    break

                if status in FINISHED:
                    state.add(user, status, detail)
                    live.remove(user)               # <-- name is DELETED from the bot list
                    stats[status] += 1
                    consec_fail = 0
                else:
                    state.add(user, "failed", detail)
                    stats["failed"] += 1
                    consec_fail += 1
                    if shots_left > 0:
                        shots_left -= 1
                        try:
                            page.screenshot(path=str(d / "debug" / f"fail_{user}.png"))
                        except Exception:
                            pass

                print(result_line(processed, target, user, status, detail, secs))
                emit("result", n=processed, user=user, status=status, detail=detail, secs=secs,
                     stats=dict(stats), target=target, per_min=prog.per_min(), eta=prog.eta(), left=live.count())

                if consec_fail >= args.max_fails:
                    ding()
                    stop_reason = (f"Stopped after {args.max_fails} failures in a row. Instagram may be slowing or blocking pages for a while "
                                   f"(wait 30-60 minutes) or the layout changed. Check the screenshots in debug/.")
                    break

                done_all = stats["unfollowed"] >= target or idx + 1 >= len(pending)

                # ---- load the next accounts' pages in background tabs during the rest
                if K > 1 and not done_all:
                    for j in range(1, K):
                        preload(idx + j)

                # ---- rest / break (jitna aap ne kaha, bilkul utna)
                if status == "unfollowed" and not done_all:
                    t = rest_seconds(args)
                    if t > 0:
                        nap(t, "rest ")
                    since_break += 1
                    if args.break_every > 0 and args.break_min > 0 and since_break >= args.break_every:
                        br = args.break_min * 60
                        print(" " + paint(S("clock"), "cyan") + f" {since_break} unfollows done, break: {fmt_dur(br)}")
                        nap(br, "break ")
                        since_break = 0
                        loaded.clear()
            else:
                if pending and stats["unfollowed"] < target:
                    stop_reason = "List finished."
        except KeyboardInterrupt:
            stop_reason = "Ctrl+C: stopped. Progress is saved (state.csv + list)."
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            PROG = None
            PUMP = None

    ding()
    el = time.time() - prog.t0
    emit("end", stats=dict(stats), elapsed=el, left=live.count(), reason=stop_reason)
    print("\n" + box([
        kv("Unfollowed", paint(str(stats["unfollowed"]), "bold", "green")),
        kv("Already not following", stats["already"]),
        kv("Account not found", stats["not_found"]),
        kv("Fail", paint(str(stats["failed"]), "red") if stats["failed"] else 0),
        kv("Time", f"{fmt_dur(el)}   ({stats['unfollowed'] / el * 60 if el > 1 else 0:.0f} unfollow/min)"),
        kv("Left in the bot list", f"{live.count()} accounts"),
    ], "RESULT", "green"))
    if stop_reason:
        (p_bad if stop_reason.startswith(("BLOCK", "Stopped after", "Logged out", "Login", "Browser")) else p_info)(stop_reason)


# ================================================================ questions (wizard) / menu
def ask(text, default=None, parse=None, dshow=None):
    """Ask until the answer is valid. Empty Enter = default."""
    tail = ""
    if default is not None:
        tail = " " + paint("[" + str(dshow if dshow is not None else default) + "]", "grey")
    while True:
        raw = input(rl(" " + paint(S("prompt"), "bold", "cyan") + " " + text + tail + paint(" : ", "grey"))).strip()
        if raw == "":
            if default is not None:
                return default
            p_warn("An answer is required.")
            continue
        if parse is None:
            return raw
        try:
            return parse(raw)
        except ValueError as e:
            p_warn(str(e))


def yn(text, default=False):
    d = "Y/n" if default else "y/N"
    raw = input(rl(" " + paint(S("prompt"), "bold", "cyan") + " " + text + " " + paint("[" + d + "]", "grey")
                   + paint(" : ", "grey"))).strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes", "haan", "ha", "han")


def _nonneg_int(r):
    try:
        v = int(r)
    except ValueError:
        raise ValueError("Enter a number (0 or more)")
    if v < 0:
        raise ValueError("Enter a number (0 or more)")
    return v


def _ranged_int(r, lo, hi):
    try:
        v = int(r)
    except ValueError:
        raise ValueError(f"Enter a number between {lo} and {hi}")
    if not lo <= v <= hi:
        raise ValueError(f"Enter a number between {lo} and {hi}")
    return v


def wizard(args, d):
    """Questions before starting. True = go."""
    base = copy.copy(args)
    base.mode = "all"
    L0 = build_lists(base, d)
    has_f = bool(L0["followers"])
    n_non, n_mut, n_all = len(L0["e_non"]), len(L0["e_mut"]), len(L0["elig"])
    if n_all == 0:
        p_warn("Nothing left (everything is done, or the rest are in keep/failed).")
        return False

    rule("STEP 1/5   Who do you want to unfollow?")
    num = lambda n: paint(f"{n:>6}", "bold")        # noqa: E731
    print(f"   {paint('[1]', 'cyan')} People you follow who do NOT follow you back  {num(n_non) if has_f else '     ?'} accounts")
    print(f"   {paint('[2]', 'cyan')} People who follow you back (mutual)            {num(n_mut) if has_f else '     ?'} accounts")
    print(f"   {paint('[3]', 'cyan')} Everyone you follow                                {num(n_all)} accounts")
    if not has_f:
        p_warn("followers_*.json not found, so only [3] is available.")
    dflt = {"non_followers": "1", "followers": "2", "all": "3"}.get(args.mode)

    def p_mode(raw):
        m = {"1": "non_followers", "2": "followers", "3": "all"}.get(raw)
        if not m:
            raise ValueError("Enter 1, 2 or 3")
        if m != "all" and not has_f:
            raise ValueError("No followers file, only 3 is available")
        return m
    mode = ask("Choose (1/2/3)", default=args.mode, parse=p_mode, dshow=dflt)
    args.mode = mode
    avail = {"non_followers": n_non, "followers": n_mut, "all": n_all}[mode]
    if avail == 0:
        p_warn("Nothing left in this mode.")
        return False

    rule(f"STEP 2/5   How many accounts to unfollow?   ({avail} available in this mode)")
    try:
        d_count = parse_count(10 if args.limit is None else args.limit, avail)
    except ValueError:
        d_count = min(10, avail)
    print(paint("   No limit: enter any number, or 'all' for everyone.", "grey"))
    n = ask("Number or all", default=d_count, parse=lambda r: parse_count(r, avail))
    args.limit = n

    rule("STEP 3/5   How many seconds to rest after each unfollow?")
    print(paint("   Exactly what you type:  12 = exactly 12 sec,  0 = no rest,  10-15 = random between 10 and 15.", "grey"))
    args.gap = ask("Seconds", default=args.gap, parse=parse_gap_text, dshow=fmt_gap(args.gap))

    rule("STEP 4/5   Break (longer rest)")
    print(paint("   After how many unfollows should it take a break, and for how long? Type 0 for no break.", "grey"))
    args.break_every = ask("Break after every how many unfollows (0 = none)", default=args.break_every,
                           parse=_nonneg_int)
    if args.break_every > 0:
        args.break_min = ask("How long is the break?  (5 = 5 minutes, 30s = 30 seconds)", default=args.break_min,
                             parse=parse_minutes, dshow=f"{args.break_min:g} minutes")

    rule("STEP 5/5   More settings")
    cap_txt = "no limit" if not (args.daily or args.hourly) else f"{args.hourly or 'none'}/hour, {args.daily or 'none'}/day"
    print(paint(f"   Now: day/hour limit = {cap_txt} | tabs = {args.tabs} | page load = {args.block} | browser = {args.browser}", "grey"))
    if yn("Change these settings?", False):
        args.daily = ask("Max unfollows per day (0 = no limit)", default=args.daily, parse=_nonneg_int)
        args.hourly = ask("Max unfollows per hour (0 = no limit)", default=args.hourly, parse=_nonneg_int)
        if args.daily and args.hourly > args.daily:
            args.hourly = args.daily
        args.tabs = ask(f"Tabs (1-{MAX_TABS}, more = faster, 2 is fine)", default=args.tabs,
                        parse=lambda r: _ranged_int(r, 1, MAX_TABS))

        def p_block(r):
            r = r.strip().lower()
            if r not in BLOCK_LEVELS:
                raise ValueError("Enter max, normal or off")
            return r
        args.block = ask("Page load (max = fastest / normal / off = load everything)", default=args.block, parse=p_block)

        def p_browser(r):
            r = r.strip().lower()
            if r == "google chrome":
                r = "chrome"
            if r not in BROWSERS:
                raise ValueError("Enter chromium, chrome or msedge")
            return r
        args.browser = ask("Browser (chromium = the bot's own / chrome = Google Chrome / msedge)", default=args.browser,
                           parse=p_browser)

    L = build_lists(args, d)
    state = L["state"]
    room, day_n, hour_n, free_at = quota(state, args.daily, args.hourly)
    est = estimate_seconds(n, args.gap, args.break_every, args.break_min, args.hourly)
    per = (args.gap[0] + args.gap[1]) / 2 + OVERHEAD
    rows = [
        kv("Who", MODE_TEXT[mode]),
        kv("How many", f"{n} accounts"),
        kv("Rest", fmt_gap(args.gap) + ("" if args.gap[1] == 0 else " seconds after each unfollow")),
        kv("Break", f"every {args.break_every} unfollows, {args.break_min:g} minutes" if args.break_every and args.break_min else "none"),
        kv("Limit", "no limit" if not (args.daily or args.hourly) else
           f"{args.hourly or 'none'}/hour, {args.daily or 'none'}/day  (last 24h {day_n})"),
        kv("Browser / tabs / load", f"{args.browser} / {args.tabs} / {args.block}"),
        kv("Estimate", f"~{fmt_dur(est)}"),
        kv("First 5", ", ".join(L["pending"][:5])),
    ]
    print("\n" + box(rows, "READY", "green"))
    if args.daily and n > max(0, args.daily - day_n):
        p_warn(f"Because of the daily limit only ~{max(0, args.daily - day_n)} can be done today, the rest on the next day.")
    if per < 3:
        p_warn("At this speed Instagram can show 'Action Blocked' at any time. "
               "If that happens the bot stops by itself and does not continue.")
    if not yn("Start?", False):
        p_info("Cancel.")
        return False
    save_settings(d, args)
    return True


def benchmark(args, d):
    """Look only (no clicks). Opens one profile per blocking level and measures the time."""
    from playwright.sync_api import sync_playwright

    base = copy.copy(args)
    base.mode = "all"
    L = build_lists(base, d)
    user = args.user or (L["pending"][0] if L["pending"] else (L["names"][0] if L["names"] else None))
    if not user:
        raise BotError("No account found to test with.")
    rule(f"SPEED TEST   '{user}' will be opened 3 ways (nothing will be clicked)")
    rows = []
    with sync_playwright() as p:
        ctx = launch_ctx(p, args, d)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not wait_login(ctx, page):
                p_bad("Login did not happen.")
                return
            try:
                cdp = ctx.new_cdp_session(page)
                cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})   # barabar muqabla
            except Exception:
                pass
            counter = {"n": 0}
            page.on("request", lambda r: counter.__setitem__("n", counter["n"] + 1))
            any_state = (
                page.get_by_role("button", name="Following")
                .or_(page.get_by_role("button", name="Requested"))
                .or_(page.get_by_role("button", name="Follow", exact=True))
                .or_(page.get_by_role("button", name="Follow Back"))
            )
            for level in ("max", "normal", "off"):
                stats = {"allowed": 0, "blocked": 0}
                install_blocking(ctx, level, stats)
                page.goto("about:blank")
                counter["n"] = 0
                t0 = time.time()
                ok = True
                try:
                    page.goto(profile_url(user), wait_until="commit", timeout=30000)
                    any_state.first.wait_for(timeout=20000)
                except Exception as e:
                    reraise_if_closed(e)
                    ok = False
                secs = time.time() - t0
                rows.append((level, ok, secs, counter["n"], stats["blocked"]))
                word = paint("buttons loaded", "green") if ok else paint("NO BUTTONS", "red")
                print(f"   {level:<7} {clip(word, 17)} {secs:5.1f}s   requests {counter['n']:>3} (blocked {stats['blocked']})")
                nap(3)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    good = [r for r in rows if r[1]]
    if not good:
        p_bad("No level loaded the buttons. Check your login, internet connection or Instagram throttling.")
        return
    fastest = min(r[2] for r in good)
    # pick the most-blocking mode unless it is really slower (more than 25% + 0.3s)
    best = next(r for r in good if r[2] <= fastest * 1.25 + 0.3)
    args.block = best[0]
    save_settings(d, args)
    p_ok(f"Selected: '{best[0]}' ({best[2]:.1f}s). Saved to settings, the bot will use it from now on.")


def execute(args, d):
    """Build the lists, run the checks, then start the browser."""
    L = build_lists(args, d)
    state = L["state"]
    if not L["pending"]:
        p_info("Nothing left in this mode.")
        return
    left = cooldown_left(d)
    if left > 0 and not args.ignore_cooldown:
        p_warn(f"Cooldown active: run again in {fmt_dur(left)}  (clear it with menu option 7, or use --ignore-cooldown; risky).")
        return
    room, day_n, hour_n, free_at = quota(state, args.daily, args.hourly)
    if args.daily and day_n >= args.daily:
        p_warn(f"Daily limit reached. Run again in {fmt_dur(free_at - time.time())}.")
        return
    removed = L["live"].clean(state)
    if removed:
        p_info(f"{removed} already-finished accounts were removed from the bot list.")
    with pw_guard():
        run(args, d, L)


def show_stats(args, d):
    L = build_lists(args, d)
    print_summary(args, L, L["state"], d)
    reasons = L["state"].fail_reasons()
    if reasons:
        print(box([f"{c} x {r}" for r, c in reasons.most_common(5)], "WHY THEY FAILED", "yellow"))


def export_lists(d, L):
    if not L["followers"]:
        p_warn("followers_*.json not found, cannot export.")
        return
    (d / "non_followers.txt").write_text("\n".join(L["non_f"]) + "\n", encoding="utf-8")
    (d / "mutual.txt").write_text("\n".join(L["mutual"]) + "\n", encoding="utf-8")
    p_ok(f"non_followers.txt ({len(L['non_f'])}) and mutual.txt ({len(L['mutual'])}) were created.")


def keep_menu(d):
    while True:
        keep = sorted(read_keep(d))
        shown = ", ".join(keep[:12]) + (" ..." if len(keep) > 12 else "")
        rule(f"KEEP LIST   {len(keep)} accounts" + (f"   {shown}" if keep else ""))
        c = input(rl(" " + paint(S("prompt"), "bold", "cyan") + " [a] add   [r] remove   [Enter] back : ")).strip().lower()
        if c == "a":
            added, bad = keep_add(d, input(rl(" " + paint(S("prompt"), "bold", "cyan") + " Usernames (separate with space/comma, links work too) : ")))
            p_ok(f"{len(added)} added." + (f" Invalid: {', '.join(bad)}" if bad else ""))
        elif c == "r":
            removed = keep_remove(d, input(rl(" " + paint(S("prompt"), "bold", "cyan") + " Usernames to remove : ")))
            p_ok(f"{len(removed)} removed.")
        else:
            return


def rebuild_menu(d, assume_yes=False):
    if not (d / "following.json").exists():
        p_warn("following.json not found. Extract a fresh Instagram export zip:")
        print("   unzip -j ../<export>.zip 'connections/followers_and_following/*.json' -d .")
        return
    if not assume_yes and not yn("The bot list will be rebuilt from following.json (a backup of the old one is kept). Continue?", False):
        p_info("Cancel.")
        return
    live = LiveList(d)
    n = live.rebuild()
    gone = live.clean(State(d))
    p_ok(f"List rebuilt: {n} accounts ({gone} of them were already done and were removed).")


def reset_menu(d):
    left = cooldown_left(d)
    rule("COOLDOWN / SETTINGS")
    print("   Cooldown: " + (paint(f"{fmt_dur(left)} left", "yellow") if left > 0 else "none")
          + f"   |   settings.json: {'present' if (d / SETTINGS_NAME).exists() else 'missing'}")
    c = input(rl(" " + paint(S("prompt"), "bold", "cyan") + " [c] clear cooldown   [s] reset settings   [Enter] back : ")).strip().lower()
    if c == "c":
        try:
            (d / "cooldown.txt").unlink()
        except FileNotFoundError:
            pass
        p_ok("Cooldown cleared. (If Instagram really blocked you, wait a while before running.)")
    elif c == "s":
        try:
            (d / SETTINGS_NAME).unlink()
        except FileNotFoundError:
            pass
        p_ok("Settings reset.")


def menu(args, d):
    banner()
    while True:
        base = copy.copy(args)
        base.mode = "all"
        L = build_lists(base, d)
        st = L["state"]
        c = st.counts()
        room, day_n, hour_n, free_at = quota(st, args.daily, args.hourly)
        left = cooldown_left(d)
        rows = [
            paint(f"In list {len(L['names'])}", "bold") + paint(f"   |   unfollowed so far {c.get('unfollowed', 0)}"
                                                                    f"   |   last 24h {day_n}", "grey"),
        ]
        if left > 0:
            rows.append(paint(f"COOLDOWN: {fmt_dur(left)} left", "yellow"))
        rows += [
            "",
            paint(" 1 ", "bold", "cyan") + "Start unfollowing",
            paint(" 2 ", "bold", "cyan") + "Full stats",
            paint(" 3 ", "bold", "cyan") + f"Retry failed accounts ({c.get('failed', 0)})",
            paint(" 4 ", "bold", "cyan") + "Keep list (never unfollow these)",
            paint(" 5 ", "bold", "cyan") + "Lists export (non_followers.txt / mutual.txt)",
            paint(" 6 ", "bold", "cyan") + "Rebuild the bot list from a new Instagram export",
            paint(" 7 ", "bold", "cyan") + "Clear cooldown / reset settings",
            paint(" 8 ", "bold", "cyan") + "Speed test (find the fastest page-load mode)",
            paint(" 0 ", "bold", "cyan") + "Exit",
        ]
        print("\n" + box(rows, "MENU"))
        try:
            ch = input(rl(" " + paint(S("prompt"), "bold", "cyan") + " Choose : ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        try:
            if ch == "1" or ch == "3":
                a = copy.copy(args)
                a.retry_failed = (ch == "3")
                if ch == "3" and not c.get("failed", 0):
                    p_info("There are no failed accounts.")
                    continue
                if wizard(a, d):
                    execute(a, d)
            elif ch == "2":
                show_stats(base, d)
            elif ch == "4":
                keep_menu(d)
            elif ch == "5":
                export_lists(d, L)
            elif ch == "6":
                rebuild_menu(d)
            elif ch == "7":
                reset_menu(d)
            elif ch == "8":
                with pw_guard():
                    benchmark(args, d)
            elif ch in ("0", "q", "exit"):
                print("Bye.")
                return
            else:
                p_warn("Choose a number from 0 to 8.")
        except (EOFError, KeyboardInterrupt):
            print("\nBack to menu.")
        except BotError as e:
            p_bad(str(e))


# ================================================================ main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=f"Unfollow by Lykoon {VERSION}. Run without flags to get the menu and questions.",
        epilog="Made by Lykoon Company - lykoonofficial@gmail.com")
    ap.add_argument("--mode", choices=["followers", "non_followers", "all"], default=None,
                    help="followers = mutual | non_followers = people who do not follow you back | all")
    ap.add_argument("--limit", default=None, help="how many ACTUAL unfollows (a number or all)")
    ap.add_argument("--gap", type=float, nargs="+", default=None, metavar="SEC",
                    help="rest after each unfollow: --gap 12 (exactly 12 sec)  --gap 0 (none)  --gap 10 15 (random)")
    ap.add_argument("--break-every", dest="break_every", type=int, default=None,
                    help="take a break after this many unfollows (0 = none)")
    ap.add_argument("--break-min", dest="break_min", type=float, default=None, help="break length in minutes (0.5 = 30 sec)")
    ap.add_argument("--daily", type=int, default=None, help="max unfollows in 24 hours (0 = no limit, default)")
    ap.add_argument("--hourly", type=int, default=None, help="max unfollows in 1 hour (0 = no limit, default)")
    ap.add_argument("--engine", choices=["turbo", "classic"], default="turbo",
                    help="Instagram: turbo (default, one request per unfollow, fastest) or classic (open each profile)")
    ap.add_argument("--parallel", type=int, default=1, help="TURBO: how many unfollows at once per round (1-30), the rest is taken after each round")
    ap.add_argument("--auto-speed", action="store_true", help="TURBO: when Instagram pushes back, pause and slow down instead of stopping")
    ap.add_argument("--block-wait", type=float, default=4.0, metavar="MIN", help="--auto-speed: first pause in minutes (grows with each slowdown)")
    ap.add_argument("--no-learn", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--unf-variant", choices=["v1", "v2", "v3", "v4"], default=None, help=argparse.SUPPRESS)
    ap.add_argument("--tabs", type=int, default=None, help=f"parallel tabs that preload the next profiles (1-{MAX_TABS}, default 3)")
    ap.add_argument("--platform", choices=["instagram", "tiktok"], default="instagram",
                    help="which site to unfollow on (tiktok is experimental)")
    ap.add_argument("--browser", choices=list(BROWSERS), default=None,
                    help="chromium = the bot's own (default) | chrome = your Google Chrome | msedge = Microsoft Edge")
    ap.add_argument("--order", choices=["file", "oldest", "newest", "random"], default="file")
    ap.add_argument("--block", choices=BLOCK_LEVELS, default=None,
                    help="off = load everything (default, most reliable) | normal | max = essentials only (fast, but can fail on real Instagram)")
    ap.add_argument("--no-prefetch", dest="prefetch", action="store_false",
                    help="do not preload the next profile (1 tab)")
    ap.add_argument("--dir", default=".", help="data folder (default: current folder)")
    ap.add_argument("--profile", default="ig_profile", help="browser session folder")
    ap.add_argument("--cooldown-h", type=float, default=12, help="hours to wait after a block")
    ap.add_argument("--max-fails", type=int, default=3)
    ap.add_argument("--shots", type=int, default=5, help="how many screenshots to take on failure")
    ap.add_argument("--retry-failed", action="store_true", help="retry ONLY the failed accounts")
    ap.add_argument("--ignore-cooldown", action="store_true")
    ap.add_argument("--stats", action="store_true", help="only show the stats")
    ap.add_argument("--dry-run", action="store_true", help="show the list, do nothing")
    ap.add_argument("--benchmark", action="store_true", help="measure the speed of 3 page-load modes (nothing is clicked)")
    ap.add_argument("--user", default=None, help="account to use for --benchmark (default: first in the list)")
    ap.add_argument("--export", action="store_true", help="create non_followers.txt / mutual.txt")
    ap.add_argument("--rebuild-list", action="store_true", help="rebuild the bot list from following.json")
    ap.add_argument("--no-menu", action="store_true", help="run directly without the menu/questions")
    ap.add_argument("-y", "--yes", action="store_true", help="start without asking (with --no-menu)")
    return ap.parse_args(argv)


def direct_run(args, d):
    if args.mode is None and args.retry_failed:
        args.mode = "all"
    if args.mode is None:
        raise BotError("--mode is required: followers / non_followers / all")
    L = build_lists(args, d)
    print_summary(args, L, L["state"], d)
    if not L["pending"]:
        p_info("Nothing left in this mode.")
        return
    n = resolve_limit(args, len(L["pending"]))
    p_info(f"First 5: {', '.join(L['pending'][:5])}")
    if not args.yes and not yn(f"'{args.mode}': {n} accounts will be unfollowed. Start?", False):
        p_info("Cancel.")
        return
    execute(args, d)


def main(argv=None):
    args = parse_args(argv)
    d = Path(args.dir).resolve()
    try:
        resolve_defaults(args, d)
        if args.stats:
            show_stats(args, d)
        elif args.dry_run:
            L = build_lists(args, d)
            print_summary(args, L, L["state"], d)
            p_info("DRY-RUN: these accounts would go first (nothing was done):")
            for u in L["pending"][:15]:
                print("    ", u)
        elif args.export:
            export_lists(d, build_lists(args, d))
        elif args.benchmark:
            with pw_guard():
                benchmark(args, d)
        elif args.rebuild_list:
            rebuild_menu(d, assume_yes=args.yes)
        elif args.yes or args.no_menu or args.retry_failed:
            direct_run(args, d)
        else:
            menu(args, d)
    except BotError as e:
        p_bad(str(e))
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Progress is saved.")
