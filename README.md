# Data Analyst Telegram Bot — Setup Guide

## 1. Create the Telegram bot
1. In Telegram, message **@BotFather**.
2. Send `/newbot`, give it any display name, then a username ending in `bot`
   (5–32 chars, letters/digits/underscore only, e.g. `my_data_bot`).
3. Copy the token BotFather gives you — this is `TELEGRAM_BOT_TOKEN`.

## 2. Get your aipipe token
1. Go to https://aipipe.org/login and sign in with your student email.
2. Copy the token starting with `eyJ...` — this is `AIPIPE_TOKEN`.

## 2b. Add an OpenRouter fallback (tried second, after aipipe)
`bot.py` falls back to OpenRouter's free tier if aipipe is down. Its free-model
lineup rotates over time, so treat the model ID in the code as a starting
point, not a permanent fixture:

1. You already have an OpenRouter API key — set it as `OPENROUTER_API_KEY` in `.env`.
2. Before grading day, check https://openrouter.ai/models?max_price=0 to
   confirm `openai/gpt-oss-20b:free` (the default in `bot.py`) is still free.
   If it's been retired, swap in whatever current free `:free`-suffixed model
   ID you find there — it's just a string in the `PROVIDERS` list.

Leaving `OPENROUTER_API_KEY` blank just skips this fallback.

## 2c. Gemini as a last-resort fallback — heads up, it now needs paid billing
As of April 2026, Google requires a **funded prepaid billing account (min $10)**
before any Gemini API call succeeds — even at zero usage. Without it you'll see
`429 "Your prepayment credits are depleted"` on every call, which is a Google
billing-policy thing, not a bug in this code. For a course project, it's
probably not worth paying for — the fallback chain puts Gemini **last**, after
the free OpenRouter option, so it's harmless to leave unconfigured or even
misconfigured; it just fails fast and falls through.

If you still want it: create a key at https://aistudio.google.com/apikey (a
personal Gmail account avoids the "Failed to create project" error some
institutional accounts hit), then add a Cloud Billing account with a card and
at least $10 prepaid credit. Set the key as `GEMINI_API_KEY` in `.env`.
Leaving it blank just skips this fallback.

**Before grading day**, run a real end-to-end test with aipipe deliberately
broken (e.g. a wrong `AIPIPE_TOKEN`) to confirm OpenRouter actually kicks in
and answers correctly — don't assume the whole chain works untested.

## 3. Set up locally
```bash
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# now edit .env and fill in your real values
```

Load the `.env` file before running (simplest: `pip install python-dotenv` and add
`from dotenv import load_dotenv; load_dotenv()` at the top of `bot.py`), or just
export the three variables in your shell:

```bash
export TELEGRAM_BOT_TOKEN=...
export AIPIPE_TOKEN=...
export LOG_URL=...
python bot.py
```

## 4. Test it
Message your bot on Telegram from a **brand-new chat** (not one you'd already
tested from — the grader will be a fresh conversation too):

```
What is 15% of 200? Reply with ONLY this JSON: {"answer": <number>, "log_url": "..."}
```

You should get back exactly one JSON line, nothing else.

## 5. Test against the real grading pipeline
```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
```
Add a few practice questions to its `evals/questions.json`, then follow its
README (`generate.py` → `collect.py` → `grade.py`) pointed at your bot's
username, to sanity-check shape and timing before deploying.

## 6. Push to a public GitHub repo
```bash
git init
git add bot.py requirements.txt .gitignore README.md
git commit -m "Initial data analyst bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```
Make sure the repo is **Public** — private repos fail the registration check.

## 7. Make `run.jsonl` public (for `log_url`)
`bot.py` now auto-commits and pushes `run.jsonl` every `PUSH_EVERY_N` messages
(default 5, override with the `PUSH_EVERY_N` env var). It runs in a background
thread so a slow or failed push never delays your reply to Telegram, and a
push error gets logged instead of crashing the bot.

For this to work, **wherever you host the bot needs push credentials**. As of
this update, `bot.py` handles this automatically at startup — just add two
more environment variables on your host (Render/Railway/etc.), alongside the
LLM keys:

- `GITHUB_TOKEN` — a personal access token (`repo` scope). Create one at
  github.com → Settings → Developer settings → Personal access tokens.
- `GITHUB_REPO` — `23f1002312/TDS-P1` (just `owner/repo`, no URL).

On startup, `bot.py` points its git remote at a token-authenticated URL and
sets a commit identity automatically — no manual `git config`/`git remote`
commands needed on the host. This only runs when both variables are present,
so it's a no-op on your laptop where you haven't set them (git there is
already configured however you set it up manually).

If you'd rather not push from the deployed process at all, the manual/periodic
approach from before still works fine — just run
`git add run.jsonl && git commit -m log && git push` yourself now and then.
Then your `log_url` is:
```
https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/run.jsonl
```
Test it in an incognito window or with `wget <url>` — it must show raw text,
not a GitHub webpage. If you see a webpage, you copied the normal link instead
of the "Raw" one.

Alternative: upload `run.jsonl` to a public GCS bucket and use its
`https://storage.googleapis.com/BUCKET/run.jsonl` URL instead.

## 8. Deploy so it's online 24/7
Pick one:
- **Render.com** — new **Background Worker** (not Web Service, since this bot
  doesn't listen on a port). Connect your repo, start command `python bot.py`,
  add `TELEGRAM_BOT_TOKEN`, `AIPIPE_TOKEN`, `LOG_URL` as environment variables
  in the dashboard.
- **Railway.app** — same idea: connect repo, add env vars, deploy as a worker.
- **A VPS** — run under `systemd` or `tmux`/`screen` so it survives reboots
  and disconnects.

After deploying, message the bot from your **phone**, not your laptop, to
confirm the reply is coming from the live server.

## 9. Register in the exam
Submit exactly:
```
https://github.com/YOUR_USERNAME/YOUR_REPO, your_bot_username
```

## Checklist before you submit
- [ ] Repo is public
- [ ] No real tokens committed anywhere in git history
- [ ] Bot replies to a message from a brand-new chat
- [ ] Reply is *exactly* one JSON object, no extra text, no extra keys
- [ ] `log_url` works in an incognito tab / via `wget` with no login
- [ ] Bot tested from your phone after deployment (not just locally)
- [ ] Bot username ends in `bot`
