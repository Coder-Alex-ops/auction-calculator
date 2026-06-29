# Connect your Garmin to AI (free setup)

> **Paste this into Claude Code, not a regular Claude chat.** This guide installs
> software and runs commands on your computer, which a normal chat window cannot
> do. Claude Code is the free coding agent that runs in your terminal and can
> actually do these steps for you.
>
> New to Claude Code? Here is the setup walkthrough:
> [Claude Code setup video](https://www.skool.com/athlete-ai-community/classroom/4d559c78?md=9ff91bc6878648d382f7b1cac766d504)

Pull your own Garmin data (workouts plus sleep, HRV, resting HR, body battery,
stress, training readiness) into a folder your AI coach reads, or into your own
database. This is the recovery data Strava cannot give you.

This is built on the open-source **python-garminconnect** library by cyberjunky.
The script here is a thin wrapper around it. Full library and docs:
[github.com/cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)

Everything you need is in the `files/` folder next to this guide:

- `sync_garmin.py` - the pull script
- `requirements.txt` - what to install
- `garmin-sync.yml` - the optional cloud automation (Path A)

Pick one of two ways to run it:

- **Path A: GitHub Actions** runs it automatically in the cloud every morning.
  Best if you already use GitHub.
- **Path B: Local cron** runs it on your own computer on a schedule. No GitHub or
  server needed.

---

## Step 0: Get the files

Download the `files/` folder. Put `sync_garmin.py` and `requirements.txt` in a
folder on your computer, for example `garmin-ai/`. Open a terminal in that folder.

(If you are using your own app/repo to receive the data, drop the script wherever
you keep scripts and adjust the paths below.)

---

## Step 1: One-time setup (both paths)

1. Install Python 3.11+ from python.org, then install the library:

   ```bash
   pip install -r requirements.txt
   ```

   On Windows, if `python`/`pip` is not found, use the `py` launcher:
   `py -m pip install -r requirements.txt`.

2. Log in once. This is the only time you enter your password or a 2FA code:

   ```bash
   export GARMIN_EMAIL="you@example.com"
   export GARMIN_PASSWORD="your-password"
   python sync_garmin.py --login
   ```

   Windows PowerShell:

   ```powershell
   $env:GARMIN_EMAIL="you@example.com"
   $env:GARMIN_PASSWORD="your-password"
   py sync_garmin.py --login
   ```

   It saves a login token on your computer that lasts about a year, then prints a
   long base64 token bundle. Copy that bundle somewhere safe if you plan to use
   Path A.

3. Test it:

   ```bash
   python sync_garmin.py --days 3 --dry-run
   ```

   You should see your last 3 days of activities and wellness print out.

---

## What you get

By default the script writes a clean folder your AI can read:

```text
garmin/
  daily/2026-06-28.md          # one wellness note per day, plain English
  activities/2026-06-28-...md   # one note per workout
  data.json                    # the full store, updated each run
```

A daily note looks like this:

```text
# Garmin wellness 2026-06-28
- Resting HR: 48 bpm
- HRV (overnight): 72 ms
- Sleep: 7.7 h (score 84)
- Body battery: 28 -> 96
- Stress (avg): 31
- Steps: 11240
- Training readiness: 81
```

Point your AI coach at the `garmin/` folder and it has your recovery context every
morning. (Sleep and HRV only fill in on nights you actually wear the watch to bed.)

---

## Path A: GitHub Actions (cloud, automatic)

1. Put the script in a GitHub repo of your own. Copy `garmin-sync.yml` into a
   `.github/workflows/` folder in that repo.

2. In the repo: Settings > Secrets and variables > Actions, add:

   | Secret | Value |
   |--------|-------|
   | `GARMIN_TOKEN_B64` | the base64 bundle printed by `--login` |
   | `GARMIN_INGEST_URL` | your ingest endpoint, if you use one |
   | `SESSION_LOG_SECRET` | the shared secret your endpoint checks |

   If you only want the files mode and no database, change the workflow's last
   step to `--sink files` and skip the URL and secret.

3. Open the Actions tab and click Run workflow once to confirm a green run. After
   that it runs every morning on its own.

You only touch it again if your password changes or the yearly token expires; then
re-run `--login` and update the `GARMIN_TOKEN_B64` secret.

---

## Path B: Local cron (your computer, no GitHub)

After Step 1, schedule the script on your own machine.

**Mac / Linux:**

```bash
crontab -e
# run every morning at 6am:
0 6 * * * cd /path/to/garmin-ai && python sync_garmin.py --days 3 --sink files --out ./garmin
```

**Windows (Task Scheduler):** create a Basic Task that runs daily and calls:

```text
py C:\path\to\garmin-ai\sync_garmin.py --days 3 --sink files --out C:\path\to\garmin-ai\garmin
```

Your machine has to be on and awake at the scheduled time.

---

## Sending to a database instead of files

If you have your own endpoint that accepts the data, use `--sink supabase`:

```bash
export GARMIN_INGEST_URL="https://yoursite.com/api/garmin/ingest"
export GARMIN_INGEST_SECRET="your-shared-secret"
python sync_garmin.py --days 3 --sink supabase
```

It POSTs `{activities, wellness}` with an `Authorization: Bearer` header.

---

## Notes and limits

- This uses an unofficial login flow (Garmin has no public API). It works through
  the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
  library. If Garmin changes their login and it stops working, update the library:
  `pip install -U garminconnect`, then re-run `--login`.
- Read-only. The script never writes anything back to your Garmin account.
- Keep your token bundle private. It is a login credential.
