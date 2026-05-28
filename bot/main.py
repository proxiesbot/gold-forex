from __future__ import annotations

import json
import logging
import os
from textwrap import shorten

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", os.getenv("APP_API_KEY", "")).strip()
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
APP_ENV = os.getenv("APP_ENV", "local")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USERS_RAW = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
ALLOWED_USERS = {int(x.strip()) for x in ALLOWED_USERS_RAW.split(",") if x.strip().isdigit()}
LAST_STRATEGY: dict[int, dict] = {}
LAST_RUN: dict[int, dict] = {}

if not ALLOWED_USERS:
    logger.warning("telegram_allowed_users_empty bot access is unrestricted")


def is_valid_token(token: str | None) -> bool:
    if not token or ":" not in token:
        return False
    prefix, suffix = token.split(":", 1)
    return prefix.isdigit() and len(suffix) >= 20


def _trim(text: str, width: int = 3200) -> str:
    return shorten((text or "").replace("\n\n\n", "\n\n"), width=width, placeholder="...")


def _is_allowed(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not ALLOWED_USERS:
        return True
    return user.id in ALLOWED_USERS


async def _guard(update: Update) -> bool:
    if _is_allowed(update):
        return True
    await update.message.reply_text("هذا البوت غير متاح لك حاليًا.")
    return False


async def _backend_json(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            headers = {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else None
            resp = await client.request(method, f"{BACKEND_URL}{path}", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return 503, {"ok": False, "error": {"code": "BACKEND_UNREACHABLE", "message": "Backend API is unreachable", "details": {"reason": exc.__class__.__name__}}}
        try:
            data = resp.json()
        except ValueError:
            data = {"ok": False, "error": {"code": "INVALID_BACKEND_RESPONSE", "message": "Backend returned a non-JSON response", "details": {}}}
        return resp.status_code, data


def _extract_error(data: dict) -> str:
    if data.get("error"):
        return data["error"].get("message", "حدث خطأ غير متوقع")
    return "حدث خطأ غير متوقع"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    await help_command(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    await update.message.reply_text(
        _trim(
            "أوامر البوت:\n"
            "/help - عرض المساعدة\n"
            "/status - حالة الـ API والبيئة وآخر عدد runs\n"
            "/interpret <وصف>\n"
            "/scan <وصف>\n"
            "/correct <تصحيح>\n"
            "/save <اسم>\n"
            "/list\n"
            "/show <strategy_id>\n"
            "/run_saved <strategy_id>\n"
            "/runs\n"
            "/prefs\n"
            "/templates"
        )
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    try:
        _, health = await _backend_json("GET", "/health")
        _, version = await _backend_json("GET", "/version")
        _, system = await _backend_json("GET", "/api/system/status")
        _, runs = await _backend_json("GET", "/api/research/runs")
        runs_count = len(runs.get("items", []))
        provider_names = ", ".join(system.get("market_data", {}).get("providers", [])[:3]) or "-"
        cache_entries = system.get("market_data", {}).get("cache_entries", 0)
        msg = f"API: {'up' if health.get('ok') else 'down'}\nEnvironment: {version.get('env', APP_ENV)}\nVersion: {version.get('version', '-')}\nSaved runs: {runs_count}\nProviders: {provider_names}\nCache entries: {cache_entries}"
    except Exception:
        msg = f"API: unreachable\nEnvironment: {APP_ENV}"
    await update.message.reply_text(msg)


async def interpret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("مثال: /interpret حلل الذهب اليوم")
        return
    code, data = await _backend_json("POST", "/api/strategy/interpret", {"text": text})
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    chat_id = update.effective_chat.id
    LAST_STRATEGY[chat_id] = data.get("understood", {})
    reply = data.get("preview_text", "")
    if data.get("warnings"):
        reply += "\n" + " | ".join(data["warnings"][:2])
    await update.message.reply_text(_trim(reply))


async def correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    text = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    strategy = LAST_STRATEGY.get(chat_id)
    if not strategy:
        await update.message.reply_text("لا يوجد فهم سابق. ابدأ بـ /interpret أولًا.")
        return
    if not text:
        await update.message.reply_text("مثال: /correct لا، بدي bearish فقط")
        return
    code, data = await _backend_json("POST", "/api/strategy/correct", {"strategy": strategy, "correction_text": text})
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    LAST_STRATEGY[chat_id] = data.get("understood", {})
    await update.message.reply_text(_trim(data.get("preview_text", "")))


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("مثال: /scan scan gold 1h last 90 days")
        return
    code, parsed = await _backend_json("POST", "/api/strategy/interpret", {"text": text})
    if code >= 400:
        await update.message.reply_text(_extract_error(parsed))
        return
    strategy = parsed["understood"]
    code, data = await _backend_json("POST", "/api/research/run", {"strategy": strategy})
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    chat_id = update.effective_chat.id
    LAST_STRATEGY[chat_id] = strategy
    LAST_RUN[chat_id] = data
    await update.message.reply_text(_trim(_format_run(data)))


async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    name = " ".join(context.args).strip()
    strategy = LAST_STRATEGY.get(update.effective_chat.id)
    if not strategy:
        await update.message.reply_text("لا يوجد strategy في الجلسة الحالية.")
        return
    if not name:
        await update.message.reply_text("مثال: /save gold-idea")
        return
    code, data = await _backend_json("POST", "/api/strategy/save", {"name": name, "strategy": strategy})
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    saved = data.get("saved", {})
    await update.message.reply_text(f"Saved: {saved.get('name')} ({saved.get('id')})")


async def list_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    _, data = await _backend_json("GET", "/api/strategy/list")
    items = data.get("items", [])[:10]
    if not items:
        await update.message.reply_text("لا يوجد استراتيجيات محفوظة بعد.")
        return
    lines = [f"- {item['name']} | {item['primary_timeframe']}->{item['execution_timeframe']} | {'/'.join(item['steps'][:4])}" for item in items]
    await update.message.reply_text(_trim("\n".join(lines)))


async def show_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    strategy_id = " ".join(context.args).strip()
    if not strategy_id:
        await update.message.reply_text("مثال: /show gold-idea")
        return
    code, data = await _backend_json("GET", f"/api/strategy/{strategy_id}")
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    LAST_STRATEGY[update.effective_chat.id] = data.get("strategy", {})
    text = json.dumps(data.get("strategy", {}), ensure_ascii=False, indent=2)
    await update.message.reply_text(_trim(text))


async def run_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    strategy_id = " ".join(context.args).strip()
    if not strategy_id:
        await update.message.reply_text("مثال: /run_saved gold-idea")
        return
    code, detail = await _backend_json("GET", f"/api/strategy/{strategy_id}")
    if code >= 400:
        await update.message.reply_text(_extract_error(detail))
        return
    strategy = detail.get("strategy")
    code, data = await _backend_json("POST", "/api/research/run", {"strategy": strategy})
    if code >= 400:
        await update.message.reply_text(_extract_error(data))
        return
    LAST_STRATEGY[update.effective_chat.id] = strategy
    LAST_RUN[update.effective_chat.id] = data
    await update.message.reply_text(_trim(_format_run(data)))


async def runs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    _, data = await _backend_json("GET", "/api/research/runs")
    items = data.get("items", [])[:10]
    if not items:
        await update.message.reply_text("لا يوجد runs محفوظة بعد.")
        return
    lines = [f"- {item['id']} | qualified {item['total_qualified']} | avg quality {item['avg_quality_score']}" for item in items]
    await update.message.reply_text(_trim("\n".join(lines)))


async def prefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    _, data = await _backend_json("GET", "/api/preferences")
    await update.message.reply_text(_trim(json.dumps(data, ensure_ascii=False, indent=2)))


async def templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    _, data = await _backend_json("GET", "/api/templates")
    items = data.get("items", [])
    if not items:
        await update.message.reply_text("لا توجد قوالب حاليًا.")
        return
    lines = [f"- {item['label']}: {item['example_prompt']}" for item in items[:5]]
    await update.message.reply_text(_trim("\n\n".join(lines)))


def _format_run(data: dict) -> str:
    msg = [data.get("human_summary") or data.get("strategy_summary", "")]
    stats = data.get("stats", {})
    if stats:
        msg.append(f"Trades: {stats.get('trades_count', 0)} | Win rate: {stats.get('win_rate', 0)}% | Avg R: {stats.get('average_r', 0)}")
    if data.get("warnings"):
        msg.append("Warnings: " + " | ".join(data["warnings"][:2]))
    for setup in data.get("setups", [])[:3]:
        msg.append(f"- {setup['source_direction']} {setup['source_type']} | {setup['outcome_label']} | Q {setup['quality_score']}")
    return "\n".join(msg)


def build_app():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("interpret", interpret))
    app.add_handler(CommandHandler("correct", correct))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("save", save))
    app.add_handler(CommandHandler("list", list_saved))
    app.add_handler(CommandHandler("show", show_saved))
    app.add_handler(CommandHandler("run_saved", run_saved))
    app.add_handler(CommandHandler("runs", runs))
    app.add_handler(CommandHandler("prefs", prefs))
    app.add_handler(CommandHandler("templates", templates))
    return app


if __name__ == "__main__":
    if not TOKEN:
        logger.warning("telegram_bot_disabled missing_token")
        raise SystemExit(0)
    if not is_valid_token(TOKEN):
        logger.error("telegram_bot_disabled invalid_token_format")
        raise SystemExit(1)
    build_app().run_polling()
