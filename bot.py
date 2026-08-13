import asyncio
import json
import os
import subprocess
import time

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

load_dotenv(override=True)  # .env always wins, even over stale shell exports

# --- All secrets come from environment variables. Never hardcode tokens here. ---
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AIPIPE_TOKEN = os.environ.get("AIPIPE_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
LOG_URL = os.environ["LOG_URL"]  # public wget-able URL where run.jsonl will end up

LOG_FILE = "run.jsonl"

# Tried in order. aipipe first; if it's down, fall through to OpenRouter's free
# model. Gemini is listed LAST and only two models deep — as of April 2026 Google
# requires a funded prepaid billing account (min $10) before generateContent works
# at all, even at zero usage, so it's not a true free fallback unless you've paid
# into it. Left in for anyone who has billing set up; otherwise it fails fast and
# falls through to OpenRouter.
PROVIDERS = [
    {
        "name": "aipipe",
        "base_url": "https://aipipe.org/openai/v1",
        "api_key": AIPIPE_TOKEN,
        "models": ["openai/gpt-4.1-nano"],
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": OPENROUTER_API_KEY,
        # OpenRouter's free lineup rotates — check https://openrouter.ai/models?max_price=0
        # before grading day and add/replace IDs here if this one has been retired.
        "models": [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "openai/gpt-oss-20b:free",
            "openrouter/free",
        ],
    },
    {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": GEMINI_API_KEY,
        "models": [
            "gemini-3.6-flash",
            "gemini-2.5-flash",
        ],
    },
]

_clients = {}


def _get_client(provider):
    if provider["name"] not in _clients:
        _clients[provider["name"]] = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"])
    return _clients[provider["name"]]


def call_llm(messages):
    """Tries each provider/model in order, returns (raw_text, provider_name, model_name).
    Raises the last error only if every single option failed."""
    last_error = None
    for provider in PROVIDERS:
        if not provider["api_key"]:
            continue  # skip providers with no key configured
        client = _get_client(provider)
        for model in provider["models"]:
            try:
                response = client.chat.completions.create(model=model, messages=messages)
                return response.choices[0].message.content.strip(), provider["name"], model
            except Exception as e:
                print(f"[WARN] {provider['name']}/{model} failed: {e}")
                last_error = e
                continue
    raise last_error if last_error else RuntimeError("No LLM providers configured — set AIPIPE_TOKEN or GEMINI_API_KEY")

# Per-chat short history so multi-turn questions work (answer only the LAST message,
# but earlier messages give context).
conversation_history = {}
MAX_HISTORY_MESSAGES = 6

# --- Auto-push run.jsonl to GitHub every N logged events ---
PUSH_EVERY_N = int(os.environ.get("PUSH_EVERY_N", "5"))
_event_count = 0


def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def _git_push_log_sync():
    """Runs in a background thread — commits and pushes run.jsonl.
    Never raises: git/network failures are printed and logged, not fatal to the bot."""
    try:
        add = subprocess.run(["git", "add", LOG_FILE], capture_output=True)
        if add.returncode != 0:
            print(f"[git] 'git add' failed: {add.stderr.decode()}")
            return

        commit = subprocess.run(["git", "commit", "-m", "update run log"], capture_output=True)
        if commit.returncode != 0:
            stderr = commit.stderr.decode()
            if "nothing to commit" in commit.stdout.decode() or "nothing to commit" in stderr:
                print(f"[git] nothing new to commit (event #{_event_count})")
            else:
                print(f"[git] 'git commit' failed: {stderr}")
            return

        push = subprocess.run(["git", "push", "origin", "HEAD:main"], capture_output=True)
        if push.returncode != 0:
            stderr = push.stderr.decode()
            print(f"[git] 'git push' FAILED: {stderr}")
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps({"type": "push_error", "error": stderr, "timestamp": time.time()}) + "\n")
            return

        print(f"[git] pushed {LOG_FILE} (event #{_event_count})")
    except Exception as e:
        print(f"[git] unexpected error during push: {e}")


async def maybe_push_log():
    """Call after logging an event. Pushes every PUSH_EVERY_N events,
    off the event loop so a slow/failed git push never blocks message handling."""
    global _event_count
    _event_count += 1
    if _event_count % PUSH_EVERY_N == 0:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _git_push_log_sync)


def extract_json(text: str) -> dict:
    """Best-effort: parse straight JSON, or pull the {...} out of extra text/markdown."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    log_event({"type": "incoming", "chat_id": chat_id, "text": user_text})

    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are a careful data analyst. The user's LAST message asks a data-analysis "
        "question and specifies exactly what JSON shape to reply with. Work out the "
        "real answer using public data you know (e.g. MOSPI statistics), general "
        "knowledge, or arithmetic on numbers given in the message. "
        "Reply with ONLY that exact JSON object and absolutely nothing else — no "
        "explanation, no markdown, no code fences, just the raw JSON. Do not add any "
        "keys beyond the ones the message asked for."
    )

    try:
        reply_text, used_provider, used_model = call_llm(
            [{"role": "system", "content": system_prompt}] + history[-MAX_HISTORY_MESSAGES:]
        )
        parsed = extract_json(reply_text)
        log_event({"type": "llm_used", "chat_id": chat_id, "provider": used_provider, "model": used_model})
    except Exception as e:
        # Fallback so we NEVER reply with non-JSON, even if every provider or parsing fails.
        print(f"[ERROR] All LLM providers or JSON parse failed: {e}")
        log_event({"type": "error", "chat_id": chat_id, "error": str(e)})
        parsed = {"answer": None}

    history.append({"role": "assistant", "content": json.dumps(parsed)})

    if not isinstance(parsed, dict):
        parsed = {"answer": parsed}
    parsed["log_url"] = LOG_URL
    final_reply = json.dumps(parsed)

    log_event({"type": "outgoing", "chat_id": chat_id, "text": final_reply})
    await update.message.reply_text(final_reply)
    await maybe_push_log()


def _configure_git():
    """Called once at startup so a freshly-deployed host (Render/Railway/etc.)
    can push run.jsonl with zero manual git setup. Reads GITHUB_TOKEN and
    GITHUB_REPO (e.g. "23f1002312/TDS-P1") from env vars and points the git
    remote at a token-authenticated URL. No-op if either is missing — e.g. on
    your laptop, where you've already configured git credentials by hand."""
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repo = os.environ.get("GITHUB_REPO", "")
    if not github_token or not github_repo:
        print("[git] GITHUB_TOKEN/GITHUB_REPO not set — skipping auto-config, "
              "assuming git is already set up on this host.")
        return
    remote_url = f"https://{github_token}@github.com/{github_repo}.git"
    try:
        # Works whether this host has an existing repo (Render clones one) or not.
        if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode != 0:
            subprocess.run(["git", "init"], check=True, capture_output=True)
        result = subprocess.run(["git", "remote", "set-url", "origin", remote_url], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["git", "remote", "add", "origin", remote_url], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", os.environ.get("GIT_USER_EMAIL", "bot@example.com")],
                        capture_output=True)
        subprocess.run(["git", "config", "user.name", os.environ.get("GIT_USER_NAME", "Data Analyst Bot")],
                        capture_output=True)
        print(f"[git] configured to push run.jsonl to {github_repo}")
    except subprocess.CalledProcessError as e:
        print(f"[git] auto-configuration failed (log pushing may not work): {e}")


def main():
    _configure_git()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running... (Ctrl+C to stop)")
    app.run_polling()


if __name__ == "__main__":
    main()
