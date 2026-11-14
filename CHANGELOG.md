# Changelog

## v12 Pro (Final)
**Fixed**
- The speed shown on the Safe / Balanced / Fast / Max cards always described Turbo, even when Classic was selected. The cards now follow the chosen method
- The rest box silently accepted wrong input: `-3` became 3, `1e9` became a random 1-9 s rest, `abc5` became 5. Now only `12`, `12s`, `10-15`, `10 to 15` (and a comma as decimal point) are accepted; anything else shows a clear message. The dashboard preview uses the same rule
- The banner said "Turbo Engine" although Classic is the default

**Added**
- Contact e-mail (lykoonofficial@gmail.com) in the dashboard footer, `--help` and README
- Chromebook (Linux) instructions, troubleshooting tip (Show full log / Copy log), more tests

## v11.1 Pro
- Much faster defaults: every preset now does 10+ unfollows per minute (Safe 4 s rest, Balanced 3 s, Fast 2 s, Max 1 s); breaks are shorter (1 min every 60)
- Method list: **Classic** is now first (and the default), Turbo second
- TikTok starts with a 3 s rest instead of 15 s; 3 preload tabs by default so the next profiles are ready in time
- Old slow settings saved in the browser are reset once, so the new speed applies right away

## v11 Pro
**New**
- Downloads: results report (.csv), unfollowed / failed / skipped lists, full log, your own lists, everything as one .zip
- Reports are auto-saved to `~/.ig_unfollow/reports/` (last 40 kept) + "Reports folder" button
- Protected accounts ("never unfollow"), saved on the computer
- "Retry failed ones" button after a run
- Desktop notification when a run finishes, live progress in the browser tab title
- PRO badge, refreshed premium dashboard, Gold accent

**Fixed**
- "Elapsed" time kept growing after a run had finished
- Double click on Start could launch the bot twice
- A block / cooldown was forgotten when the run ended with an error
- Changing the list or forgetting logins while a job was running could confuse the platform setting
- Several dashboard refresh loops could pile up after pressing Start / Stop
- Bad numbers in the settings (for example "many" for parallel) could crash the request instead of showing a message
- The local server crashed the request on broken input (bad JSON, bad Content-Length); it now answers with a clear error
- Odd entries inside an Instagram export file crashed the list reader
- Pressing Stop while waiting for login showed "Login did not happen (10 minutes passed)"
- Wrong step numbers in hints (step 1 → step 2); unknown result states no longer break the activity feed
- A connection loss on a button press now shows a message instead of failing silently
