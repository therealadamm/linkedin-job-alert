# LinkedIn Job Alert -> Telegram

Checks LinkedIn for new postings matching **"Fresh Graduate" OR "Network Engineer"**
around Kuala Lumpur / Selangor (Malaysia only, physical roles preferred), and pings
you on Telegram the moment it finds something new. Runs on GitHub Actions, so it
works even when your PC is off.

## How it works

- A GitHub Actions workflow runs `linkedin_job_alert.py` every 2 hours, 8am–10pm
  Malaysia time (8 runs/day).
- The script queries LinkedIn's public job-search results (no login needed) for
  each keyword, filters out remote roles and non-Malaysia results, and skips
  anything it already sent you before (tracked in `seen_jobs.json`).
- New matches get pushed to your Telegram instantly.

**Heads up:** this uses LinkedIn's public search page, not an official API (LinkedIn
doesn't offer one for this use case). It's not bypassing any login or auth — but
LinkedIn can change their page layout or rate-limit requests without warning, so the
script may occasionally need a small fix. As a free, zero-risk backup, I'd also turn
on LinkedIn's own native job alerts: save a search with your filters on linkedin.com
and toggle "Email me about new jobs" — costs nothing and can't break.

## One-time setup (~10 minutes)

1. **Create a new GitHub repo** (private is fine — e.g. `linkedin-job-alert`).
2. **Upload these files** into it, keeping the folder structure (the
   `.github/workflows/job_alert.yml` path matters — that's what makes it a scheduled
   workflow).
3. **Add your secrets** — go to your repo's `Settings → Secrets and variables →
   Actions → New repository secret` and add two:
   - `TELEGRAM_BOT_TOKEN` → the bot token you already have
   - `TELEGRAM_CHAT_ID` → `1204609399`

   Don't put these values directly in the code — secrets keep them out of your
   commit history.
4. **Allow the workflow to commit**: `Settings → Actions → General → Workflow
   permissions` → select **"Read and write permissions"** → Save. (This lets the
   workflow update `seen_jobs.json` after each run so it remembers what it already
   sent you.)
5. **Test it**: go to the **Actions** tab → click "LinkedIn Job Alert" on the left →
   **"Run workflow"** → Run. After ~30-60 seconds, check the run logs and your
   Telegram. If nothing matched in the last 3 hours you won't get a message — that's
   expected, not a bug.

   To check Telegram delivery on its own (skip LinkedIn entirely), run locally:
   ```bash
   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=123456789 python3 linkedin_job_alert.py --test
   ```

6. Once a manual test message arrives, you're done — it'll now run automatically on
   schedule.

## Tuning it

All the knobs are constants at the top of `linkedin_job_alert.py`:

| Variable | What it controls |
|---|---|
| `KEYWORDS` | List of search terms (matched as OR — any job matching any term gets sent) |
| `LOCATION` / `DISTANCE_MILES` | Search center + radius (LinkedIn only accepts 5/10/25/50/75/100 mile steps) |
| `TIME_WINDOW_SECONDS` | How far back each run looks (kept wider than the run interval as a safety overlap) |

To change the check frequency, edit the `cron` line in
`.github/workflows/job_alert.yml`. GitHub Actions cron is in **UTC** — Malaysia time
is UTC+8, so subtract 8 hours when picking times.

## If it stops sending anything

Almost always means LinkedIn tweaked their page's HTML class names. Open the Actions
run logs — if you see `Found 0 new matching job(s)` for several days in a row when
you'd expect matches, the CSS selectors in `fetch_jobs()` likely need updating.
Happy to help patch it when that happens — just bring me the failing run log.
