import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import os
import threading
from config import ADMIN_USER_IDS, BOT_TOKEN, LOG_CHANNEL_ID, PREMIUM_CHANNEL_ID
import database
from database import (
    check_premium_status,
    create_user_if_not_exists,
    get_premium_channel_link,
    get_user_by_id,
    update_premium_status,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Httpx logs ko mute karein taaki Bot Token logs me leak na ho
logging.getLogger("httpx").setLevel(logging.WARNING)


# --- Dummy Web Server (Render Free Port Binding) ---
class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

    def log_message(self, format, *args):
        return  # Server ke access logs band rakhein


def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health-check dummy server listening on port {port}")
    server.serve_forever()


# --- Helper Functions ---


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def format_date(dt: datetime | None) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def get_days_remaining(expiry_date: datetime | None) -> int:
    if not expiry_date:
        return 0
    now = datetime.now(timezone.utc)
    if expiry_date.tzinfo is None:
        expiry_date = expiry_date.replace(tzinfo=timezone.utc)
    delta = expiry_date - now
    return max(0, delta.days)


# --- Command Handlers ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name

    logger.info(f"User {user_id} started the bot.")
    db_user = create_user_if_not_exists(user_id, username)

    if not db_user:
        await update.message.reply_text(
            "⚠️ Database error. Please try again later."
        )
        return

    is_active = check_premium_status(user_id)

    if is_active:
        expiry = db_user.get("premium", {}).get("expiry_date")
        days_left = get_days_remaining(expiry)
        text = (
            "✅ <b>Premium Active</b>\n\n"
            f"📅 Expiry: {format_date(expiry)}\n"
            f"⏳ Remaining: {days_left} days\n\n"
            "Click the button below to join the premium channel."
        )
        channel_link = get_premium_channel_link()
        keyboard = []
        if channel_link:
            keyboard.append(
                [InlineKeyboardButton("Join Premium Channel", url=channel_link)]
            )

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=reply_markup
        )
    else:
        text = (
            "👋 <b>Welcome!</b>\n\n"
            "Your premium subscription is not active.\n"
            "Please contact admin to verify your payment."
        )
        await update.message.reply_text(text, parse_mode="HTML")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    db_user = get_user_by_id(user_id)
    if not db_user:
        await update.message.reply_text(
            "User not found. Please use /start first."
        )
        return

    is_active = check_premium_status(user_id)
    premium = db_user.get("premium", {})
    expiry = premium.get("expiry_date")

    if is_active:
        days_left = get_days_remaining(expiry)
        text = (
            "📊 <b>Account Status</b>\n\n"
            "✅ Premium: Active\n"
            f"📅 Expiry: {format_date(expiry)}\n"
            f"⏳ Remaining: {days_left} days"
        )
        channel_link = get_premium_channel_link()
        keyboard = []
        if channel_link:
            keyboard.append(
                [InlineKeyboardButton("Join Premium Channel", url=channel_link)]
            )

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=reply_markup
        )
    else:
        text = "📊 <b>Account Status</b>\n\n❌ Premium: Inactive/Expired"
        await update.message.reply_text(text, parse_mode="HTML")


# --- Admin Commands ---


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Access Denied. Admins only.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /verify USER_ID DAYS")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid format. USER_ID and DAYS must be numbers.")
        return

    if days <= 0:
        await update.message.reply_text("❌ DAYS must be greater than 0.")
        return

    target_user = create_user_if_not_exists(target_id, "Unknown")
    if not target_user:
        await update.message.reply_text("⚠️ Could not create/find target user in DB.")
        return

    current_expiry = target_user.get("premium", {}).get("expiry_date")
    now = datetime.now(timezone.utc)

    if current_expiry:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        if current_expiry > now:
            new_expiry = current_expiry + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)

    result = update_premium_status(
        target_id,
        is_premium=True,
        expiry_date=new_expiry,
        purchase_date=now,
        trial=False,
    )

    if result:
        channel_link = get_premium_channel_link()
        buttons = []
        if channel_link:
            buttons.append(
                [InlineKeyboardButton("Join Premium Channel", url=channel_link)]
            )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "✅ <b>Premium Activated!</b>\n\n"
                    f"📅 Expiry Date: {format_date(new_expiry)}\n"
                    f"⏳ Duration: {days} days\n\n"
                    "Please join the premium channel using the link below."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        except Exception as e:
            logger.warning(f"Could not send PM to {target_id}: {e}")

        # Log Channel update
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=(
                    f"✅ <b>Payment Verified</b>\n"
                    f"Admin: <code>{user.id}</code>\n"
                    f"User ID: <code>{target_id}</code>\n"
                    f"Duration: {days} days\n"
                    f"New Expiry: {format_date(new_expiry)}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"LOG_CHANNEL failed: {e}")

        await update.message.reply_text(
            f"✅ Verified for ID {target_id}.\nExpiry: {format_date(new_expiry)}"
        )
    else:
        await update.message.reply_text("⚠️ Failed to update database.")


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Access Denied. Admins only.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /revoke USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid User ID.")
        return

    result = update_premium_status(target_id, is_premium=False, expiry_date=None, trial=False)
    if result:
        await update.message.reply_text(f"❌ Revoked premium for ID {target_id}.")
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"❌ Premium Revoked for User ID: {target_id}",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("⚠️ User not found or no changes made.")


async def status_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Access Denied. Admins only.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /status_user USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid User ID.")
        return

    target_user = get_user_by_id(target_id)
    if not target_user:
        await update.message.reply_text("User not found in DB.")
        return

    premium = target_user.get("premium", {})
    is_active = check_premium_status(target_id)
    expiry = premium.get("expiry_date")

    text = (
        f"📊 <b>Status for ID: {target_id}</b>\n\n"
        f"Name: {target_user.get('name', 'Unknown')}\n"
        f"Premium: {'✅ Active' if is_active else '❌ Inactive'}\n"
        f"Expiry: {format_date(expiry)}\n"
        f"Purchase: {format_date(premium.get('purchase_date'))}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# --- Background Auto-Expiry Task ---


async def check_expiries_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running premium expiry check...")
    try:
        users = database.collection.find({"premium.is_premium": True})
        now = datetime.now(timezone.utc)
        for user in users:
            user_id = user.get("id")
            expiry = user.get("premium", {}).get("expiry_date")
            if not expiry:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if now > expiry:
                logger.info(f"Expiring premium for user {user_id}")
                update_premium_status(user_id, is_premium=False)
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="⌛ <b>Your Premium Subscription has expired.</b>\nContact admin to renew.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error in check_expiries_job: {e}")


# --- Main ---


def main():
    # 1. Start Background HTTP Server for Render Free Tier Port Binding
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    # 2. Setup Asyncio Event Loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 3. Build Bot Application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("status_user", status_user))

    if app.job_queue:
        app.job_queue.run_repeating(check_expiries_job, interval=3600, first=30)

    logger.info("Bot is polling Telegram...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
