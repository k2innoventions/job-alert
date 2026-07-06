# Daily 3D Artist Job Alert

Every morning at 8 AM Eastern, this checks several job APIs for new
entry-level 3D / environment artist roles (Orlando area + remote), filters
them against `config.yaml`, and emails a digest of anything it hasn't sent
before. Runs free on GitHub Actions — no computer needs to be on.

## One-time setup (about 10 minutes)

### 1. Create a Gmail app password (for sending the email)
1. Go to https://myaccount.google.com/security and make sure **2-Step
   Verification** is ON (app passwords require it).
2. Go to https://myaccount.google.com/apppasswords
3. Create one named `job-alert`. Copy the 16-character password it shows —
   you'll paste it into GitHub in step 3. This is safer than a real password
   and can be revoked anytime.

### 2. (Optional but recommended) Get free Adzuna API keys
Adzuna aggregates the big general job boards. Without it, the script still
works using Remotive + company boards, but coverage is thinner.
1. Sign up at https://developer.adzuna.com (free)
2. Copy your **Application ID** and **Application Key**.

### 3. Create the GitHub repository
1. Create a free account at https://github.com if needed.
2. Create a **new private repository** called `job-alert`.
3. Upload all the files in this folder to it, keeping the folder structure
   (the `.github/workflows/daily.yml` path matters). Easiest way: on the
   repo page click **Add file → Upload files** and drag the folder contents
   in. If the `.github` folder won't drag, create the file manually:
   **Add file → Create new file**, name it `.github/workflows/daily.yml`,
   and paste the contents.

### 4. Add the secrets
In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add:

| Secret name          | Value                                        |
|----------------------|----------------------------------------------|
| `GMAIL_ADDRESS`      | the Gmail address that sends the digest      |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 1    |
| `RECIPIENT_EMAIL`    | Lindsey's email (where the digest goes)      |
| `ADZUNA_APP_ID`      | from step 2 (optional)                       |
| `ADZUNA_APP_KEY`     | from step 2 (optional)                       |

### 5. Test it
Go to the **Actions** tab → **Daily job alert** → **Run workflow**. Within a
minute or two you'll see a green check (and an email, if there were matches).
Open the run's log to see which sources worked. Any company board with a
wrong token is listed in the log and email footer — fix or remove it in
`config.yaml`.

That's it. It now runs every morning automatically.

## Tuning it

Everything lives in `config.yaml`:
- **include_keywords / exclude_keywords** — what job titles count as a match
- **locations** — add cities/states if she'd relocate
- **greenhouse_boards / lever_boards** — add any studio she cares about.
  Find the token in the careers page URL: `boards.greenhouse.io/TOKEN` or
  `jobs.lever.co/TOKEN`. Wrong tokens are harmless (they're skipped and
  reported).
- To get an email even on days with zero matches, uncomment the line noted
  in `job_alert.py`.
- To change the send time, edit the `cron:` line in
  `.github/workflows/daily.yml` (times are UTC).

## What this can't cover

Disney, Universal, and most large employers don't expose public job APIs, so
they can't be polled by a script politely. For those, use their own alert
systems — they're excellent and land in the same inbox:
- Disney Careers → create a profile and set up **job alerts** (search
  "environment artist", "3D", "Imagineering")
- Universal Orlando / Universal Creative → careers site talent network
- ArtStation Jobs, Hitmarker, GameJobs.co → each has email alerts and is
  where studios post art roles first

The script + those native alerts together give near-complete coverage.
