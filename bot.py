import logging
import asyncio
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from config import BOT_TOKEN, ADMIN_USER_IDS, LOG_CHANNEL_ID, PREMIUM_CHANNEL_ID
from database import (
    get_user_by_id,
    create_user_if_not_exists,
    update_premium_status,
    check_premium_status,
    get_premium_channel_link
)

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

def format_date(dt: datetime) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M UTC")

def get_days_remaining(expiry_date: datetime) -> int:
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

    # Ensure user exists in DB
    db_user = create_user_if_not_exists(user_id, username)
    
    if not db_user:
        await update.message.reply_text("⚠️ Database error. Please try again later.")
        return

    # Check Premium Status
    is_active = check_premium_status(user_id)
    
    if is_active:
        expiry = db_user["premium"]["expiry_date"]
        days_left = get_days_remaining(expiry)
        text = (
            "✅ <b>Premium Active</b>\n\n"
            f"📅 Expiry: {format_date(expiry)}\n"
            f"⏳ Remaining: {days_left} days\n\n"
            "Click the button below to join the premium channel."
        )
        # Create Button
        keyboard = [[
            InlineKeyboardButton("Join Premium Channel", url=get_premium_channel_link())
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
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
        await update.message.reply_text("User not found. Please use /start first.")
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
        keyboard = [[
            InlineKeyboardButton("Join Premium Channel", url=get_premium_channel_link())
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        text = (
            "📊 <b>Account Status</b>\n\n"
            "❌ Premium: Inactive/Expired"
        )
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
        days = int(context.args<sup data-citation="1">1</sup>)
    except ValueError:
        await update.message.reply_text("Invalid format. USER_ID and DAYS must be numbers.")
        return

    # Ensure target user exists in DB
    target_user = create_user_if_not_exists(target_id, "Unknown")
    if not target_user:
        await update.message.reply_text("⚠️ Could not create/find target user in DB.")
        return

    # Check if they already have active premium to extend it
    current_expiry = target_user.get("premium", {}).get("expiry_date")
    now = datetime.now(timezone.utc)
    
    # If current expiry is in the future, extend from there. Otherwise, start from now.
    if current_expiry and current_expiry > now:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        new_expiry = current_expiry + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)

    # Update DB
    result = update_premium_status(
        target_id, 
        is_premium=True, 
        expiry_date=new_expiry, 
        purchase_date=now, 
        trial=False
    )

    if result and result.modified_count > 0:
        # Send message to user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "✅ <b>Premium Activated!</b>\n\n"
                    f"📅 Expiry Date: {format_date(new_expiry)}\n"
                    f"⏳ Duration: {days} days\n\n"
                    "Please join the premium channel to access files."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not send message to user {target_id}: {e}")

        # Log to Admin Channel
        log_text = (
            f"✅ <b>Payment Verified</b>\n"
            f"User ID: {target_id}\n"
            f"Duration: {days} days\n"
            f"New Expiry: {format_date(new_expiry)}"
        )
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
        
        await update.message.reply_text(f"✅ Verified for ID {target_id}. Expiry: {format_date(new_expiry)}")
    else:
        await update.message.reply_text("⚠️ Failed to update user in database.")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Access Denied. Admins only.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /revoke USER_ID")
        return

    try:
        target_id = int(context.args<sup data-citation="0">0</sup>)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")
        return

    result = update_premium_status(
        target_id, 
        is_premium=False, 
        expiry_date=None, 
        trial=False
    )

    if result and result.modified_count > 0:
        await update.message.reply_text(f"❌ Revoked premium for ID {target_id}.")
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=f"❌ Premium Revoked for User ID: {target_id}"
        )
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
        target_id = int(context.args<sup data-citation="0">0</sup>)
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

# --- Background Task for Expiry ---

async def check_expiries_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs periodically to check all users.
    Note: For a small bot, iterating all users is fine. 
    For very large bots, use a specific index or queue.
    """
    logger.info("Running premium expiry check...")
    # We fetch all users with is_premium: true
    users = database.collection.find({"premium.is_premium": True})
    
    for user in users:
        user_id = user.get("id")
        expiry = user.get("premium", {}).get("expiry_date")
        
        if not expiry:
            continue
            
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
            
        if datetime.now(timezone.utc) > expiry:
            logger.info(f"Expiring premium for user {user_id}")
            update_premium_status(user_id, is_premium=False)
            
    logger.info("Premium expiry check completed.")

# --- Main ---

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("status_user", status_user))

    # Add Background Job
    app.job_queue.run_repeating(
        check_expiries_job,
        interval=3600,  # Every hour
        first=30        # Start after 30 seconds
    )

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
