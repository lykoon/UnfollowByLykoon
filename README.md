# Unfollow by Lykoon · Pro (v12 Final)

Clean up who you follow, safely. A local dashboard (opens in your browser) that unfollows accounts in bulk at **your** pace, with live progress, automatic safety stops and downloadable reports.

> Runs entirely on your own computer. Nothing is uploaded anywhere. Your logins stay in `~/.ig_unfollow`.

## Features

- **Instagram** (Classic = opens each profile, the default; Turbo = one request per unfollow, much faster) and **TikTok** (experimental)
- Build your list automatically from your own account (*Mutual*, *Not following me back*, *People I follow*, *All*), or paste usernames, or drop your Instagram export files
- **Protected accounts**: a "never unfollow" list that is always skipped
- Exact pacing: rest time, breaks, how many at the same time, speed presets (Safe / Balanced / Fast / Max)
- **Auto speed**: if Instagram pushes back, the bot pauses, slows down and continues instead of stopping
- Auto-stop and cooldown when Instagram blocks an action
- **Downloads**: results report (`.csv`), unfollowed / failed / skipped lists, full log, your lists (following, followers, mutual, not-following-back) or everything as one `.zip`
- Reports are also auto-saved to `~/.ig_unfollow/reports/`
- **Retry failed ones** with one click
- Premium dashboard: dark / light theme, 5 accent colours, live ring + stats, activity feed, desktop notification and progress in the tab title
- Control it from your phone on the same Wi-Fi (`--lan`)

## Quick start

You only need **Python 3.8+**. The first start sets itself up (a private environment + browser engine, about 150 MB, a few minutes). Later starts take seconds.

| System  | Start |
|---------|-------|
| Windows | double-click `Start-Windows.bat` |
| macOS   | double-click `Start-Mac.command` (first time: right-click → Open) |
| Linux   | `./Start-Linux.sh` or `python3 unfollow_app.py` |

Phone control: `python3 unfollow_app.py --lan`

Other options: `--port 8765`, `--no-open` (do not open the window automatically).

Linux without the venv module: `sudo apt install python3-venv`

Chromebook: turn on *Linux development environment* in the ChromeOS settings, run `sudo apt install -y python3 python3-venv unzip`, then `./Start-Linux.sh` and open the printed link in Chrome. (Intel/AMD Chromebooks; 4 GB RAM or more recommended.)

## How to use

1. Choose the platform.
2. Build the list (automatic / paste / export files). Optionally fill in **Protected accounts**.
3. Pick the pace. Start with ~10 accounts, rest 12 s, 1 at a time, then raise the speed slowly.
4. Pick the browser and press **Start unfollowing**. Log in once in the window that opens.
5. When it finishes, use **Download your results**.

Speed guide: with **Classic** (default) the presets give roughly Safe 10 · Balanced 12 · Fast 15 · Max 20 per minute. With **Turbo** they give roughly 10 / 30 / 85 / 210 per minute. The dashboard shows the live estimate. Faster means a higher risk of a temporary block. If Turbo cannot work on your account it tells you and carries on with Classic by itself.

Tip: if something does not work, open **Show full log** in the dashboard, press **Copy log** and include it when you report a problem.

## Files

| File | What it is |
|------|------------|
| `unfollow_app.py` | the dashboard + local server |
| `unfollow_bot.py` | the engine (also usable from the terminal: `python3 unfollow_bot.py --help`) |
| `tests/` | automated tests (no browser or internet needed) |

Reset everything: close the app and delete `~/.lykoon` (Windows: `C:\Users\<you>\.lykoon`). Saved logins: `~/.ig_unfollow` (or use **Forget saved logins** in the footer).

## Development

```bash
python -m unittest discover -s tests -v
ruff check .
```

## Roman Urdu (short)

1. Python install karo (3.8 ya naya). 2. `Start-Windows.bat` (ya Mac/Linux wali file) chalao. 3. Dashboard khulega: list banao, speed chuno, Start dabao, aur browser mein ek baar login karo. 4. Kaam khatam hone par **Download your results** se report (.csv / .txt / .zip) utaar lo. Pehle chhoti list (10 accounts) aur slow speed se test karo.

## Disclaimer

This is an independent tool, not affiliated with Instagram, Meta or TikTok. Automating actions may go against a platform's terms and can lead to temporary blocks or restrictions. Use it only on your own account, at a gentle pace, and at your own risk.

## Contact

Questions, bugs or ideas: [lykoonofficial@gmail.com](mailto:lykoonofficial@gmail.com)

## License

MIT © 2026 Lykoon Company · lykoonofficial@gmail.com
