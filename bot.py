import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import math
import os
import threading
from typing import Dict, Optional, Tuple

from telegram import (
    BotCommand,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
)

import config
import database

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Configuration Fallbacks ---
PREMIUM_CHANNEL_ID = getattr(
    config, "PREMIUM_CHANNEL_ID", int(os.getenv("PREMIUM_CHANNEL_ID", 0))
)
PREMIUM_GROUP_ID = getattr(
    config, "PREMIUM_GROUP_ID", int(os.getenv("PREMIUM_GROUP_ID", 0))
)
LOG_CHANNEL_ID = getattr(
    config, "LOG_CHANNEL_ID", int(os.getenv("LOG_CHANNEL_ID", 0))
)
SUPPORT_USERNAME = getattr(
    config, "SUPPORT_USERNAME", os.getenv("SUPPORT_USERNAME", "admin")
).lstrip("@")
UPGRADE_LINK = getattr(
    config, "UPGRADE_LINK", os.getenv("UPGRADE_LINK", "https://t.me")
)
PREMIUM_CHANNEL_LINK = getattr(
    config, "PREMIUM_CHANNEL_LINK", os.getenv("PREMIUM_CHANNEL_LINK", "")
)
PREMIUM_GROUP_LINK = getattr(
    config, "PREMIUM_GROUP_LINK", os.getenv("PREMIUM_GROUP_LINK", "")
)
ADMIN_USER_IDS = getattr(config, "ADMIN_USER_IDS", set())
BOT_TOKEN = config.BOT_TOKEN


# --- Dummy Web Server (Render Free Port Binding) ---
class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running healthy on Render!")

    def log_message(self, format, *args):
        return


def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health check server listening on port {port}")
    server.serve_forever()


# --- Database Helper Wrappers ---
def get_db_collection():
    if hasattr(database, "users_col"):
        return database.users_col
    if hasattr(database, "collection"):
        return database.collection
    return database.db["USERS"]


def db_get_user(user_id: int):
    if hasattr(database, "get_user"):
        return database.get_user(user_id)
    if hasattr(database, "get_user_by_id"):
        return database.get_user_by_id(user_id)
    return get_db_collection().find_one({"id": user_id})


def db_get_or_create_user(user_id: int, name: str = "User"):
    col = get_db_collection()
    user = col.find_one({"id": user_id})
    if user:
        col.update_one(
            {"id": user_id},
            {"$set": {"user_data.last_seen": datetime.now(timezone.utc)}},
        )
        return user

    now = datetime.now(timezone.utc)
    new_doc = {
        "id": user_id,
        "name": name,
        "premium": {
            "is_premium": False,
            "expiry_date": None,
            "purchase_date": None,
            "trial": False,
        },
        "user_data": {
            "is_verified": False,
            "is_subscribed": False,
            "join_date": now,
            "last_seen": now,
        },
        "files": {"todays_files": 0, "lifetime_files": 0},
        "refer": {"referral_points": 0, "invited_by": None},
        "ban_status": {"is_banned": False, "ban_reason": ""},
    }
    col.insert_one(new_doc)
    return new_doc


def db_is_premium_active(user_id: int) -> Tuple[bool, Optional[datetime]]:
    user = db_get_user(user_id)
    if not user:
        return False, None
    prem = user.get("premium", {})
    if not prem.get("is_premium", False):
        return False, None
    expiry = prem.get("expiry_date")
    if not expiry:
        return False, None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (expiry > now), expiry


def db_add_or_extend_premium(
    user_id: int, days: int, name: str = "User"
) -> Dict:
    col = get_db_collection()
    user = db_get_or_create_user(user_id, name)
    prem = user.get("premium", {}) if user else {}
    current_expiry = prem.get("expiry_date")
    is_active = prem.get("is_premium", False)
    now = datetime.now(timezone.utc)

    if is_active and current_expiry:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        new_expiry = (
            current_expiry if current_expiry > now else now
        ) + timedelta(days=days)
        extended = current_expiry > now
    else:
        new_expiry = now + timedelta(days=days)
        extended = False

    update_fields = {
        "premium.is_premium": True,
        "premium.purchase_date": now,
        "premium.expiry_date": new_expiry,
        "premium.trial": False,
        "premium.bot2_notified": False,  # Auto-send notifier trigger
    }
    col.update_one({"id": user_id}, {"$set": update_fields})
    return {"new_expiry": new_expiry, "extended": extended}


def db_revoke_premium(user_id: int):
    col = get_db_collection()
    col.update_one(
        {"id": user_id},
        {
            "$set": {
                "premium.is_premium": False,
                "premium.expiry_date": None,
                "premium.trial": False,
                "premium.bot2_notified": False,
            }
        },
    )


# --- Helper Links & Membership Checks ---
_cached_invite_links: Dict[int, str] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def format_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%d-%m-%Y %H:%M UTC")


async def get_join_request_link(
    bot, chat_id: int, link_name: str, fallback_url: str = ""
) -> str:
    """Tries creating an official join request link; falls back to static link if unavailable."""
    if not chat_id:
        return fallback_url

    if chat_id in _cached_invite_links:
        return _cached_invite_links[chat_id]

    try:
        link_obj = await bot.create_chat_invite_link(
            chat_id=chat_id,
            name=link_name,
            creates_join_request=True,
        )
        _cached_invite_links[chat_id] = link_obj.invite_link
        return link_obj.invite_link
    except Exception as e:
        logger.warning(
            f"Auto link creation failed for {chat_id} ({e}). Using fallback link."
        )
        return (
            fallback_url
            or f"https://t.me/c/{str(chat_id)[4:] if str(chat_id).startswith('-100') else chat_id}"
        )


async def kick_and_unban_user(bot, chat_id: int, user_id: int):
    if not chat_id:
        return
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(
            f"Removed and unbanned expired user {user_id} from {chat_id}"
        )
    except Exception as e:
        logger.warning(f"Error kicking user {user_id} from {chat_id}: {e}")


async def build_premium_buttons(bot) -> InlineKeyboardMarkup:
    """Creates the 3 buttons matching Photo 1 exactly:

    1. Join SRK Prime Max ↗
    2. Join SRKPrime Request ↗
    3. Upgrade ↗
    """
    channel_link = await get_join_request_link(
        bot, PREMIUM_CHANNEL_ID, "SRK Prime Max", PREMIUM_CHANNEL_LINK
    )
    group_link = await get_join_request_link(
        bot, PREMIUM_GROUP_ID, "SRKPrime Request", PREMIUM_GROUP_LINK
    )

    buttons = []
    if channel_link:
        buttons.append(
            [InlineKeyboardButton("Join SRK Prime Max ↗", url=channel_link)]
        )
    if group_link:
        buttons.append(
            [InlineKeyboardButton("Join SRKPrime Request ↗", url=group_link)]
        )
    if UPGRADE_LINK:
        buttons.append([InlineKeyboardButton("Upgrade ↗", url=UPGRADE_LINK)])

    return InlineKeyboardMarkup(buttons)


# --- Core Command Handlers (Connected to Side Menu) ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_get_or_create_user(user.id, user.full_name)
    is_active, expiry = db_is_premium_active(user.id)

    if is_active and expiry:
        reply_markup = await build_premium_buttons(context.bot)
        status_msg = (
            f"🎉 <b>Premium Active</b>\n\n"
            f"👤 <b>User:</b> {user.first_name}\n"
            f"📅 <b>Expiry:</b> <code>{expiry.strftime('%d-%m-%Y')}</code>\n\n"
            f"Click the buttons below to access your communities:"
        )
        await update.message.reply_text(
            status_msg, parse_mode="HTML", reply_markup=reply_markup
        )
    else:
        upgrade_btn = (
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("Upgrade ↗", url=UPGRADE_LINK)]]
            )
            if UPGRADE_LINK
            else None
        )
        msg = (
            f"👋 Welcome <b>{user.first_name}</b>!\n\n"
            f"❌ <b>Your Premium Subscription is inactive.</b>\n\n"
            f"🔒 <b> Your Premium benefits are currently unavailable.:</b>\n"
            f"🚀 <b> Upgrade now to unlock all Premium features.:</b>\n"
            f"👇 <b> Tap Upgrade to continue.:</b>\n"
            f"Use the <b>Menu (bottom left)</b> to view available plans and subscribe."
        )
        await update.message.reply_text(
            msg, parse_mode="HTML", reply_markup=upgrade_btn
        )


async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Corresponds to Side Menu: 'Show premium plans' (/plans or /start)"""
    user = update.effective_user
    plans_text = (
        "💎 <b>SRK Premium Subscription Plans</b>\n\n"
        "• <b>30 Days:</b> ₹99\n"
        "• <b>90 Days:</b> ₹149\n"
        "• <b>180 Days:</b> ₹269\n"
        "• <b>365 Days:</b> ₹499\n\n"
        "💳 <b>How to Purchase:</b>\n"
        f"1. Send payment to Admin.\n"
        f"2. Send receipt with your User ID (<code>{user.id}</code>) to support.\n"
        "3. Once verified, your access is approved instantly!"
    )
    btn = (
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("Upgrade ↗", url=UPGRADE_LINK)]]
        )
        if UPGRADE_LINK
        else None
    )
    await update.message.reply_text(
        plans_text, parse_mode="HTML", reply_markup=btn
    )


async def myplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Corresponds to Side Menu: 'View current plan' (/myplan)"""
    user = update.effective_user
    is_active, expiry = db_is_premium_active(user.id)
    if is_active and expiry:
        days_left = max(0, (expiry - datetime.now(timezone.utc)).days)
        text = (
            f"📋 <b>Current Subscription</b>\n\n"
            f"• <b>Status:</b> Active ✅\n"
            f"• <b>Expiry Date:</b> <code>{format_date(expiry)}</code>\n"
            f"• <b>Remaining Time:</b> {days_left} days"
        )
        buttons = await build_premium_buttons(context.bot)
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=buttons
        )
    else:
        text = (
            "📋 <b>Current Subscription</b>\n\n"
            "• <b>Status:</b> Inactive / Expired ❌\n\n"
            "Select <b>Show premium plans</b> from Menu to buy."
        )
        await update.message.reply_text(text, parse_mode="HTML")


async def renew_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Corresponds to Side Menu: 'Renew premium' (/renew)"""
    user = update.effective_user
    renew_text = (
        "🔄 <b>Renew Premium Subscription</b>\n\n"
        "If you renew while your subscription is still active, "
        "<b>your new days will be added on top of your current expiry date!</b>\n\n"
        f"Contact @{SRKSupports} with your User ID (<code>{user.id}</code>) to renew."
    )
    btn = (
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("Upgrade ↗", url=UPGRADE_LINK)]]
        )
        if UPGRADE_LINK
        else None
    )
    await update.message.reply_text(
        renew_text, parse_mode="HTML", reply_markup=btn
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Corresponds to Side Menu: 'Get support' (/help)"""
    user = update.effective_user
    text = (
        f"🆘 <b>Customer Support</b>\n\n"
        f"For any queries, issues, or payments, contact our administrator:\n"
        f"👉 @{SRKSupports}\n\n"
        f"Your Telegram User ID: <code>{user.id}</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# --- Automatic Join Request System ---


async def handle_chat_join_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    join_req: ChatJoinRequest = update.chat_join_request
    user = join_req.from_user
    chat = join_req.chat

    logger.info(
        f"Join request from {user.id} ({user.first_name}) for {chat.title}"
    )
    is_active, expiry = db_is_premium_active(user.id)

    if is_active:
        try:
            await context.bot.approve_chat_join_request(
                chat_id=chat.id, user_id=user.id
            )
            exp_str = expiry.strftime("%d-%m-%Y") if expiry else "N/A"
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"✅ <b>Premium Access Approved!</b>\n\n"
                    f"You have been granted access to <b>{chat.title}</b>.\n"
                    f"📅 Expiry: <code>{exp_str}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Error approving join request: {e}")
    else:
        try:
            await context.bot.decline_chat_join_request(
                chat_id=chat.id, user_id=user.id
            )
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"❌ <b>Join Request Declined</b>\n\n"
                    f"Your request to join <b>{chat.title}</b> was declined because "
                    f"your premium subscription is not active or has expired.\n\n"
                    f"Run /start to view available plans and purchase access."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Error notifying declined user: {e}")


# --- Admin Commands ---


async def add_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not is_admin(admin.id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: <code>/addpremium USER_ID DAYS</code>", parse_mode="HTML"
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ USER_ID and DAYS must be integers."
        )
        return

    if days <= 0:
        await update.message.reply_text("❌ Days must be > 0.")
        return

    res = db_add_or_extend_premium(target_id, days)
    new_expiry = res["new_expiry"]
    extended = res["extended"]

    # Directly send notification with buttons
    buttons = await build_premium_buttons(context.bot)
    dm_text = (
        "🎉 <b>Premium Activated!</b>\n\n"
        f"⏳ Duration: <b>{days} days</b>\n"
        f"📅 Expiry: <code>{new_expiry.strftime('%d-%m-%Y')}</code>\n\n"
        "Click the buttons below to join your communities:"
    )

    dm_sent = True
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=dm_text,
            parse_mode="HTML",
            reply_markup=buttons,
        )
        col = get_db_collection()
        col.update_one(
            {"id": target_id}, {"$set": {"premium.bot2_notified": True}}
        )
    except Exception as e:
        logger.error(f"Failed to send DM to {target_id}: {e}")
        dm_sent = False

    if LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=(
                    f"📝 <b>[LOG] Premium Activated</b>\n"
                    f"Admin: <code>{admin.id}</code>\n"
                    f"Target: <code>{target_id}</code>\n"
                    f"Days: {days}\n"
                    f"New Expiry: <code>{format_date(new_expiry)}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(
        f"✅ <b>Premium {'Extended' if extended else 'Activated'}</b>\n\n"
        f"👤 User: <code>{target_id}</code>\n"
        f"⏳ Days: {days}\n"
        f"📅 Expiry: <code>{format_date(new_expiry)}</code>\n"
        f"📬 User Auto-Sent: {'✅ Delivered' if dm_sent else '❌ Failed (Bot not started or blocked)'}",
        parse_mode="HTML",
    )


async def remove_premium_cmd(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    admin = update.effective_user
    if not is_admin(admin.id):
        return

    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ Usage: <code>/removepremium USER_ID</code>", parse_mode="HTML"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be integer.")
        return

    db_revoke_premium(target_id)
    await kick_and_unban_user(context.bot, PREMIUM_CHANNEL_ID, target_id)
    await kick_and_unban_user(context.bot, PREMIUM_GROUP_ID, target_id)

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="⚠️ Your Premium Subscription has been revoked by an administrator.",
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ User <code>{target_id}</code> premium revoked.", parse_mode="HTML"
    )


async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ Usage: <code>/user USER_ID</code>", parse_mode="HTML"
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be integer.")
        return

    user = db_get_user(target_id)
    if not user:
        await update.message.reply_text(
            f"❌ User <code>{target_id}</code> not found.", parse_mode="HTML"
        )
        return

    is_active, expiry = db_is_premium_active(target_id)
    prem = user.get("premium", {})
    msg = (
        f"👤 <b>User Audit:</b> <code>{target_id}</code>\n\n"
        f"• Name: {user.get('name', 'N/A')}\n"
        f"• Premium: {'Active ✅' if is_active else 'Inactive ❌'}\n"
        f"• Expiry: <code>{format_date(expiry)}</code>\n"
        f"• Purchase Date: <code>{format_date(prem.get('purchase_date'))}</code>\n"
        f"• Lifetime Files: {user.get('files', {}).get('lifetime_files', 0)}\n"
        f"• Banned: {user.get('ban_status', {}).get('is_banned', False)}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# Yeh function bot.py me Admin commands ke paas add karein:
async def test_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not is_admin(admin.id):
        return

    await update.message.reply_text(
        f"⏳ Log channel test kar raha hu...\nTarget ID: <code>{LOG_CHANNEL_ID}</code>",
        parse_mode="HTML",
    )

    try:
        sent = await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=f"✅ <b>Test Successful!</b>\nLog channel is properly connected.\nAdmin: <code>{admin.id}</code>",
            parse_mode="HTML",
        )
        await update.message.reply_text(
            f"🎉 <b>Success!</b> Log channel me message bhej diya gaya hai (Msg ID: {sent.message_id}).",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Log Channel send failed: {e}")
        await update.message.reply_text(
            f"❌ <b>Failed!</b> Telegram ne ye error diya:\n<code>{e}</code>\n\n"
            "👉 <b>Solution:</b> Check karein ki Bot Log Channel me Admin hai ya nahi aur 'Post Messages' on hai ya nahi.",
            parse_mode="HTML",
        )

# --- Automatic Notification Background Job (Bot 1 -> Bot 2 Sync) ---


async def auto_notify_new_premium_job(context: ContextTypes.DEFAULT_TYPE):
    """Automatically scans MongoDB for newly activated users (from Bot 1 or Admin)

    and delivers the activation message with buttons if not already notified.
    """
    col = get_db_collection()
    now = datetime.now(timezone.utc)

    # Search for active premium users where bot2_notified is False or not set
    unnotified_users = list(
        col.find({
            "premium.is_premium": True,
            "premium.expiry_date": {"$gt": now},
            "premium.bot2_notified": {"$ne": True},
        }).limit(15)
    )

    if not unnotified_users:
        return

    buttons = await build_premium_buttons(context.bot)

    for u in unnotified_users:
        uid = u.get("id")
        expiry = u.get("premium", {}).get("expiry_date")
        if not uid or not expiry:
            continue

        exp_str = expiry.strftime("%d-%m-%Y")
        msg = (
            "🎉 <b>Premium Activated!</b>\n\n"
            f"Your subscription is active until <code>{exp_str}</code>.\n\n"
            "Click the buttons below to access your communities:"
        )

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=msg,
                parse_mode="HTML",
                reply_markup=buttons,
            )
            col.update_one(
                {"id": uid}, {"$set": {"premium.bot2_notified": True}}
            )
            logger.info(
                f"✅ Auto-sent premium notification to User {uid} successfully!"
            )
        except (Forbidden, BadRequest) as e:
            # User hasn't started the bot or blocked it
            col.update_one(
                {"id": uid}, {"$set": {"premium.bot2_notified": True}}
            )
            logger.warning(
                f"Could not auto-send to {uid} (User hasn't started bot yet): {e}"
            )
        except Exception as e:
            logger.error(f"Unexpected error auto-notifying {uid}: {e}")


# --- Background Auto-Expiry Job ---


async def check_expiries_job(context: ContextTypes.DEFAULT_TYPE):
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    expired_users = list(
        col.find(
            {"premium.is_premium": True, "premium.expiry_date": {"$lte": now}}
        )
    )

    if not expired_users:
        return

    logger.info(f"Processing {len(expired_users)} expired subscriptions...")
    for u in expired_users:
        uid = u.get("id")
        if not uid:
            continue

        col.update_one(
            {"id": uid},
            {
                "$set": {
                    "premium.is_premium": False,
                    "premium.expiry_date": None,
                    "premium.bot2_notified": False,
                }
            },
        )

        await kick_and_unban_user(context.bot, PREMIUM_CHANNEL_ID, uid)
        await kick_and_unban_user(context.bot, PREMIUM_GROUP_ID, uid)

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "⌛ <b>Your Premium Subscription has expired.</b>\n\n"
                    "Access to the channel and group has ended. Run /start to renew anytime."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


# --- Side Menu Setup On Bot Startup ---


async def setup_bot_commands(application):
    """Sets the native Side Menu commands exactly matching Photo 1."""
    commands = [
        BotCommand("start", "Show premium plans"),
        BotCommand("myplan", "View current plan"),
        BotCommand("renew", "Renew premium"), 
        BotCommand("help", "Get support"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("✅ Side Menu (/menu) commands registered successfully!")


# --- Main Entrypoint ---


def main():
    # 1. Background Port Server for Render Free Tier
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    # 2. Asyncio event loop fix
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 3. Build Application with Side Menu hook
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(setup_bot_commands)
        .build()
    )

    # Core User Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["plans", "show_plans"], plans_command))
    app.add_handler(CommandHandler(["myplan", "status"], myplan_command))
    app.add_handler(CommandHandler("renew", renew_command))
    app.add_handler(CommandHandler("help", help_command))

    # Join Request Handler
    app.add_handler(ChatJoinRequestHandler(handle_chat_join_request))

    # Admin Handlers
    app.add_handler(CommandHandler(["verify", "addpremium"], add_premium_cmd))
    app.add_handler(
        CommandHandler(["revoke", "removepremium"], remove_premium_cmd)
    )
    app.add_handler(CommandHandler(["status_user", "user"], user_info_cmd))
    app.add_handler(CommandHandler("testlog", test_log_cmd))
   
    # Background Tasks
    if app.job_queue:
        # Check for Bot 1 newly activated users every 30 seconds to auto-send
        app.job_queue.run_repeating(
            auto_notify_new_premium_job, interval=30, first=5
        )
        # Check expired users every 10 minutes
        app.job_queue.run_repeating(check_expiries_job, interval=600, first=20)

    logger.info("🤖 Bot 2 is live and listening!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

