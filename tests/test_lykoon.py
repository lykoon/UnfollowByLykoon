"""Tests for Unfollow by Lykoon. They need no browser, no Instagram and no internet.

Run:   python -m unittest discover -s tests -v
"""
import csv
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

os.environ["LYKOON_NO_VENV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unfollow_app as appmod  # noqa: E402
import unfollow_bot as bot  # noqa: E402


class Pure(unittest.TestCase):
    def setUp(self):
        bot.set_platform("instagram")

    def test_norm_user(self):
        cases = {"@Name": "name", "https://www.instagram.com/Some.User/": "some.user", "instagram.com/_u/abc?x=1": "abc",
                 "  bob_1  ": "bob_1", "bad name": "", "a/b": "", "": "", "x" * 31: ""}
        for raw, want in cases.items():
            self.assertEqual(bot.norm_user(raw), want, raw)

    def test_norm_user_tiktok(self):
        bot.set_platform("tiktok")
        self.assertEqual(bot.norm_user("https://www.tiktok.com/@Some.One"), "some.one")
        self.assertEqual(bot.norm_user("a"), "")          # TikTok names have at least 2 characters

    def test_gap(self):
        self.assertEqual(bot.parse_gap_text("12"), [12.0, 12.0])
        self.assertEqual(bot.parse_gap_text("10-15"), [10.0, 15.0])
        self.assertEqual(bot.parse_gap_text("15-10"), [10.0, 15.0])
        self.assertEqual(bot.parse_gap_text("0"), [0.0, 0.0])
        self.assertEqual(bot.parse_gap_text("12s"), [12.0, 12.0])
        self.assertEqual(bot.parse_gap_text("10 to 15"), [10.0, 15.0])
        self.assertEqual(bot.parse_gap_text("1,5"), [1.5, 1.5])
        for bad in ("", "abc", "5000", "-3", "1e9", "abc5", "3 4 5"):
            with self.assertRaises(ValueError):
                bot.parse_gap_text(bad)

    def test_minutes_and_count(self):
        self.assertEqual(bot.parse_minutes("5"), 5)
        self.assertEqual(bot.parse_minutes("30s"), 0.5)
        self.assertEqual(bot.parse_minutes("1h"), 60)
        with self.assertRaises(ValueError):
            bot.parse_minutes("soon")
        self.assertEqual(bot.parse_count("all", 40), 40)
        self.assertEqual(bot.parse_count("500", 40), 40)
        self.assertEqual(bot.parse_count("7", 40), 7)
        for bad in ("0", "-3", "many"):
            with self.assertRaises(ValueError):
                bot.parse_count(bad, 40)

    def test_read_entries_survives_odd_files(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "following.json"
            f.write_text(json.dumps({"relationships_following": [
                {"string_list_data": [{"value": "Alice", "timestamp": 5}]},
                "junk", {"string_list_data": None}, {"string_list_data": ["x", {"href": "https://instagram.com/bob"}]}]}))
            self.assertEqual([n for n, _ in bot.read_entries(f)], ["Alice", "bob"])


class AppLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.app = appmod.App(Path(self.tmp.name) / "work", self.home)
        (Path(self.tmp.name) / "work").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_paste_list(self):
        r = self.app.set_list(dict(kind="paste", text="@Anna, bob\nbob https://instagram.com/cy/ bad name!", platform="instagram"))
        self.assertTrue(r["ok"])
        self.assertEqual(self.app.names, ["anna", "bob", "cy", "bad"])      # lower-cased, no duplicates, links understood
        self.assertEqual((r["count"], r["invalid_n"]), (4, 1))              # "name!" is not a valid username

    def test_account_modes(self):
        self.app.fetched = dict(me="me", following=["A", "b", "C"], followers=["a", "D"], partial={})
        self.app.fetched_platform = "instagram"
        got = {m: self.app.set_list(dict(kind="account", mode=m, platform="instagram")) for m in ("following", "mutual", "non_followers", "all")}
        self.assertEqual(got["following"]["count"], 3)
        self.assertEqual(got["mutual"]["count"], 1)
        self.assertEqual(got["non_followers"]["count"], 2)
        self.assertEqual(got["all"]["count"], 4)
        self.app.fetched["partial"] = {"followers": True}
        self.assertFalse(self.app.set_list(dict(kind="account", mode="non_followers", platform="instagram"))["ok"])

    def test_list_refused_while_busy(self):
        self.app.phase = "running"
        self.assertFalse(self.app.set_list(dict(kind="paste", text="a", platform="instagram"))["ok"])
        self.assertFalse(self.app.forget_login()["ok"])

    def test_start_needs_a_list_and_valid_numbers(self):
        self.assertFalse(self.app.start({})["ok"])
        self.app.names = ["anna", "bob"]
        for bad in (dict(rest="x"), dict(count="0"), dict(tabs="zzz"), dict(parallel="many"), dict(engine="warp"), dict(browser="ie")):
            r = self.app.start(bad)
            self.assertFalse(r["ok"], bad)
            self.assertEqual(self.app.phase, "idle")

    def test_protected_accounts_are_skipped(self):
        self.assertTrue(self.app.set_keep("@Anna\nhttps://instagram.com/bob/\nbad name!")["ok"])
        self.assertEqual(self.app.keep_set(), {"anna", "bob", "bad"})
        self.app.names = ["anna", "bob"]
        r = self.app.start({})
        self.assertFalse(r["ok"])
        self.assertIn("protected", r["error"])

    def test_double_start_is_refused(self):
        ev = threading.Event()
        self.app._worker = lambda *a, **k: ev.wait(5)      # a fake bot that just waits
        self.app.names = ["anna", "bob"]
        self.assertTrue(self.app.start(dict(rest="1"))["ok"])
        self.assertFalse(self.app.start(dict(rest="1"))["ok"])
        ev.set()
        self.app.thread.join(5)

    def _fake_run(self, rows):
        self.app.phase = "running"
        self.app.t0 = time.time() - 10
        for i, (u, st, det) in enumerate(rows, 1):
            self.app.on_event("result", n=i, user=u, status=st, detail=det, secs=1.5, per_min=1, eta=1, left=0,
                              stats=dict(unfollowed=1, already=0, not_found=0, failed=0))
        self.app.on_event("end", stats=dict(unfollowed=1, already=0, not_found=0, failed=1), left=0, reason="List finished.")

    def test_elapsed_stops_growing_when_finished(self):
        self._fake_run([("anna", "unfollowed", "")])
        a = self.app.snapshot()["elapsed"]
        time.sleep(0.25)
        self.assertEqual(self.app.snapshot()["elapsed"], a)

    def test_downloads(self):
        self._fake_run([("anna", "unfollowed", ""), ("bob", "failed", "=cmd|' /C calc'!A0"), ("cy", "already", ""), ("dan", "not_found", "")])
        self.app.full_log += ["12:00:00  hello"]
        self.app.names = ["anna", "bob"]
        name, data, ctype = self.app.make_download("results.csv")
        self.assertTrue(name.startswith("unfollow_results_") and name.endswith(".csv"))
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))                                   # BOM for Excel
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["#", "time", "username", "result", "details", "seconds"])
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[2][4].startswith("'="), "formula cells must be neutralised")
        self.assertEqual(self.app.make_download("unfollowed.txt")[1], b"anna\n")
        self.assertEqual(self.app.make_download("failed.txt")[1], b"bob\n")
        self.assertEqual(self.app.make_download("skipped.txt")[1], b"cy\ndan\n")
        self.assertEqual(self.app.make_download("list.txt")[1], b"anna\nbob\n")
        self.assertIn(b"hello", self.app.make_download("log.txt")[1])
        z = zipfile.ZipFile(io.BytesIO(self.app.make_download("all.zip")[1]))
        self.assertIn("results.csv", z.namelist())
        self.assertIsNone(self.app.make_download("../../etc/passwd"))
        self.assertIsNone(self.app.make_download("nothing.txt"))

    def test_fetched_lists_download(self):
        self.app.fetched = dict(me="me", following=["A", "b", "C"], followers=["a", "D"], partial={})
        self.assertEqual(self.app.make_download("non_followers.txt")[1], b"b\nC\n")
        self.assertEqual(self.app.make_download("mutual.txt")[1], b"A\n")
        self.app.fetched["partial"] = {"followers": True}
        self.assertIsNone(self.app.make_download("non_followers.txt"))      # would be wrong with half a followers list

    def test_report_is_autosaved_and_pruned(self):
        self._fake_run([("anna", "unfollowed", "")])
        self.app.save_report()
        files = list((self.home / "reports").glob("unfollow_*.csv"))
        self.assertEqual(len(files), 1)
        self.assertTrue(self.app.saved_report.endswith(".csv"))

    def test_cooldown_file(self):
        self.home.mkdir(parents=True)
        (self.home / "cooldown.txt").write_text(str(time.time() + 600))
        self.assertGreater(self.app.cooldown_left(), 500)
        self.app.names = ["anna"]
        self.assertIn("blocked", self.app.start({})["error"])
        self.app.clear_cooldown()
        self.assertEqual(self.app.cooldown_left(), 0)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv, cls.app, cls.url = appmod.serve("127.0.0.1", 18765, open_window=False, token="tok", home=Path(cls.tmp.name), quiet=True)
        cls.base = cls.url.split("/?")[0]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None, token="tok", raw=None, headers=None):
        h = {"X-Token": token} if token else {}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_token_is_required(self):
        self.assertEqual(self.call("/api/state", token=None)[0], 403)
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("/download/results.csv", token=None)[0], 403)
        self.assertEqual(self.call("/api/start", body={}, token="wrong")[0], 403)

    def test_page_and_state(self):
        st, body, _ = self.call("/")
        self.assertEqual(st, 200)
        self.assertIn(b"Unfollow", body)
        self.assertIn(b"PRO", body)
        st, body, _ = self.call("/api/state")
        s = json.loads(body)
        self.assertEqual(s["phase"], "idle")
        self.assertEqual(s["version"], appmod.APP_VERSION)
        self.assertIn("downloads", s)

    def test_bad_requests_never_crash(self):
        self.assertEqual(self.call("/api/list", raw=b"{nope")[0], 400)
        self.assertEqual(self.call("/api/list", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.call("/api/list", raw=b"{}", headers={"Content-Length": "abc"})[0], 400)
        self.assertEqual(self.call("/api/nothing", body={})[0], 404)
        st, body, _ = self.call("/api/start", body={"count": ["x"], "rest": None})
        self.assertIn(st, (200, 500))
        self.assertEqual(json.loads(body)["ok"], False)
        self.assertEqual(self.call("/api/state")[0], 200)                  # the server is still alive

    def test_downloads_over_http(self):
        self.assertEqual(self.call("/download/results.csv")[0], 404)       # nothing yet
        self.assertEqual(self.call("/api/list", body=dict(kind="paste", text="anna bob", platform="instagram"))[0], 200)
        st, body, h = self.call("/download/list.txt")
        self.assertEqual((st, body), (200, b"anna\nbob\n"))
        self.assertIn("attachment", h["Content-Disposition"])
        st, body, h = self.call("/download/all.zip")
        self.assertEqual(st, 200)
        self.assertIn("list.txt", zipfile.ZipFile(io.BytesIO(body)).namelist())

    def test_protected_list_roundtrip(self):
        st, body, _ = self.call("/api/keep", body=dict(text="@Anna\nbob"))
        self.assertEqual(json.loads(body)["count"], 2)
        self.assertEqual(json.loads(self.call("/api/keep")[1])["text"], "anna\nbob\n")


if __name__ == "__main__":
    unittest.main()
