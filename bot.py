import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import math
import os
import threading
from typing import Dict, Optional, Tuple

from telegram import (
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, TelegramError
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

# Httpx logs ko mute karein taaki Bot Token logs me disclose na ho
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
    logger.info(f"Health-check dummy server listening on port {port}")
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
    if hasattr(database, "get_or_create_user"):
        return database.get_or_create_user(user_id, name)
    if hasattr(database, "create_user_if_not_exists"):
        return database.create_user_if_not_exists(user_id, name)

    col = get_db_collection()
    user = col.find_one({"id": user_id})
    if user:
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


def db_add_or_extend_premium(user_id: int, days: int, name: str = "User") -> Dict:
    col = get_db_collection()
    user = db_get_or_create_user(user_id, name)
    prem = user.get("premium", {}) if user else {}
    current_expiry = prem.get("expiry_date")
    is_active = prem.get("is_premium", False)
    now = datetime.now(timezone.utc)

    if is_active and current_expiry:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        new_expiry = (current_expiry if current_expiry > now else now) + timedelta(days=days)
        extended = (current_expiry > now)
    else:
        new_expiry = now + timedelta(days=days)
        extended = False

    update_fields = {
        "premium.is_premium": True,
        "premium.purchase_date": now,
        "premium.expiry_date": new_expiry,
        "premium.trial": False,
    }
    col.update_one({"id": user_id}, {"$set": update_fields})
    return {"new_expiry": new_expiry, "extended": extended}


def db_revoke_premium(user_id: int):
    col = get_db_collection()
    col.update_one(
        {"id": user_id},
        {"$set": {"premium.is_premium": False, "premium.expiry_date": None, "premium.trial": False}},
    )


# --- Helpers & Membership Checks ---
_cached_invite_links: Dict[int, str] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def format_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%d-%m-%Y %H:%M UTC")


async def check_is_member(bot, chat_id: int, user_id: int) -> bool:
    if not chat_id:
        return False
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in {"member", "administrator", "creator", "restricted"}
    except (BadRequest, TelegramError):
        return False
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False


async def get_join_request_link(bot, chat_id: int, link_name: str) -> Optional[str]:
    if not chat_id:
        return None
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
        logger.error(f"Failed to create join request link for {chat_id}: {e}")
        return None


async def kick_and_unban_user(bot, chat_id: int, user_id: int):
    if not chat_id:
        return
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"Removed and unbanned expired user {user_id} from {chat_id}")
    except BadRequest as e:
        if "user not found" in str(e).lower() or "not a member" in str(e).lower():
            return
    except Exception as e:
        logger.warning(f"Error removing user {user_id} from {chat_id}: {e}")


# --- User Command & Callback Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_get_or_create_user(user.id, user.full_name)
    is_active, expiry = db_is_premium_active(user.id)

    # Membership Checks
    in_channel = await check_is_member(context.bot, PREMIUM_CHANNEL_ID, user.id)
    in_group = await check_is_member(context.bot, PREMIUM_GROUP_ID, user.id)

    # Join Links
    channel_link = await get_join_request_link(context.bot, PREMIUM_CHANNEL_ID, "SRK Prime Max")
    group_link = await get_join_request_link(context.bot, PREMIUM_GROUP_ID, "SRKPrime Request")

    keyboard = [
        [InlineKeyboardButton("⭐ Show Premium Plans", callback_data="show_plans")],
        [InlineKeyboardButton("📋 View Current Plan", callback_data="view_plan")],
        [InlineKeyboardButton("🔄 Renew Premium", callback_data="renew_plan")],
    ]

    # Dynamic Channel/Group Buttons
    if is_active:
        # Channel Button
        if in_channel:
            keyboard.append([InlineKeyboardButton("✅ SRK Prime Max — Already Joined", callback_data="already_joined_channel")])
        elif channel_link:
            keyboard.append([InlineKeyboardButton("🔴 Join SRK Prime Max", url=channel_link)])

        # Group Button
        if in_group:
            keyboard.append([InlineKeyboardButton("✅ SRKPrime Request — Already Joined", callback_data="already_joined_group")])
        elif group_link:
            keyboard.append([InlineKeyboardButton("🔴 Join SRKPrime Request", url=group_link)])
    else:
        keyboard.append([InlineKeyboardButton("🔴 Join SRK Prime Max", callback_data="need_premium")])
        keyboard.append([InlineKeyboardButton("🔴 Join SRKPrime Request", callback_data="need_premium")])

    keyboard.append([InlineKeyboardButton("🆘 Get Support", url=f"https://t.me/{SUPPORT_USERNAME}")])

    if is_active and expiry:
        status_msg = (
            f"🎉 <b>Premium Active</b>\n\n"
            f"👤 User: <b>{user.first_name}</b>\n"
            f"📅 Expiry: <code>{expiry.strftime('%d-%m-%Y')}</code>\n\n"
            f"Click the buttons below to access your communities:"
        )
    else:
        status_msg = (
            f"👋 Welcome <b>{user.first_name}</b>!\n\n"
            f"❌ <b>Your Premium Subscription is currently inactive.</b>\n\n"
            f"Subscribe today to unlock instant access to <b>SRK Prime Max</b> and <b>SRKPrime Request</b>."
        )

    await update.message.reply_text(status_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_active, expiry = db_is_premium_active(user.id)
    if is_active and expiry:
        now = datetime.now(timezone.utc)
        days_left = max(0, (expiry - now).days)
        text = (
            "📊 <b>Account Status</b>\n\n"
            "✅ Premium: Active\n"
            f"📅 Expiry: <code>{format_date(expiry)}</code>\n"
            f"⏳ Remaining: {days_left} days"
        )
    else:
        text = "📊 <b>Account Status</b>\n\n❌ Premium: Inactive/Expired"
    await update.message.reply_text(text, parse_mode="HTML")


async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "show_plans":
        plans_text = (
            "💎 <b>SRK Premium Subscription Plans</b>\n\n"
            "• <b>30 Days:</b> ₹99\n"
            "• <b>60 Days:</b> ₹180\n"
            "• <b>90 Days:</b> ₹250\n"
            "• <b>365 Days:</b> ₹899\n\n"
            "💳 <b>How to Purchase:</b>\n"
            f"Send payment screenshot along with your User ID (<code>{user.id}</code>) to admin support."
        )
        await query.message.reply_text(plans_text, parse_mode="HTML")

    elif data == "view_plan":
        is_active, expiry = db_is_premium_active(user.id)
        if is_active and expiry:
            days_left = max(0, (expiry - datetime.now(timezone.utc)).days)
            text = (
                f"📋 <b>Current Subscription</b>\n\n"
                f"• Status: Active ✅\n"
                f"• Expiry Date: <code>{format_date(expiry)}</code>\n"
                f"• Remaining: {days_left} days"
            )
        else:
            text = "📋 <b>Current Subscription</b>\n\n• Status: Inactive / Expired ❌"
        await query.message.reply_text(text, parse_mode="HTML")

    elif data == "renew_plan":
        renew_text = (
            "🔄 <b>Renew Premium Subscription</b>\n\n"
            "If you renew while your subscription is still active, "
            "<b>your new days will be added on top of your current expiry date!</b>\n\n"
            f"Contact @{SUPPORT_USERNAME} with your User ID (<code>{user.id}</code>) to renew."
        )
        await query.message.reply_text(renew_text, parse_mode="HTML")

    elif data in ("already_joined_channel", "already_joined_group"):
        await query.answer("✅ You have already joined this community!", show_alert=True)

    elif data == "need_premium":
        await query.answer("❌ Premium Required! Please purchase a plan first.", show_alert=True)


# --- Automatic Join Request System ---
async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_req: ChatJoinRequest = update.chat_join_request
    user = join_req.from_user
    chat = join_req.chat

    logger.info(f"Join request from {user.id} ({user.first_name}) for {chat.title}")
    is_active, expiry = db_is_premium_active(user.id)

    if is_active:
        try:
            await context.bot.approve_chat_join_request(chat_id=chat.id, user_id=user.id)
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
            logger.info(f"Approved {user.id} in {chat.title}")
        except Exception as e:
            logger.error(f"Error approving join request: {e}")
    else:
        try:
            await context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)
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
            logger.info(f"Declined {user.id} in {chat.title}")
        except Exception as e:
            logger.warning(f"Error notifying declined user: {e}")


# --- Admin Commands ---
async def add_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not is_admin(admin.id):
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: <code>/addpremium USER_ID DAYS</code>", parse_mode="HTML")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ USER_ID and DAYS must be integers.")
        return

    if days <= 0:
        await update.message.reply_text("❌ Days must be > 0.")
        return

    res = db_add_or_extend_premium(target_id, days)
    new_expiry = res["new_expiry"]
    extended = res["extended"]

    ch_link = await get_join_request_link(context.bot, PREMIUM_CHANNEL_ID, "SRK Prime Max")
    gp_link = await get_join_request_link(context.bot, PREMIUM_GROUP_ID, "SRKPrime Request")

    dm_btns = []
    if ch_link:
        dm_btns.append([InlineKeyboardButton("🔴 Join SRK Prime Max", url=ch_link)])
    if gp_link:
        dm_btns.append([InlineKeyboardButton("🔴 Join SRKPrime Request", url=gp_link)])

    dm_text = (
        "🎉 <b>Premium Activated!</b>\n\n"
        f"⏳ Duration: <b>{days} days</b>\n"
        f"📅 Expiry: <code>{new_expiry.strftime('%d-%m-%Y')}</code>\n\n"
        "Click the buttons below to submit your join requests. The bot will approve you automatically!"
    )

    dm_sent = True
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=dm_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(dm_btns) if dm_btns else None,
        )
    except Exception:
        dm_sent = False

    # Log Channel
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
        f"📬 User DM: {'Sent' if dm_sent else 'Failed (User has not started bot)'}",
        parse_mode="HTML",
    )


async def remove_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not is_admin(admin.id):
        return

    if len(context.args) < 1:
        await update.message.reply_text("⚠️ Usage: <code>/removepremium USER_ID</code>", parse_mode="HTML")
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

    if LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"❌ <b>[LOG] Premium Revoked</b> for User ID: <code>{target_id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(f"✅ User <code>{target_id}</code> premium revoked and kicked.", parse_mode="HTML")


async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) < 1:
        await update.message.reply_text("⚠️ Usage: <code>/user USER_ID</code>", parse_mode="HTML")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be integer.")
        return

    user = db_get_user(target_id)
    if not user:
        await update.message.reply_text(f"❌ User <code>{target_id}</code> not found.", parse_mode="HTML")
        return

    is_active, expiry = db_is_premium_active(target_id)
    prem = user.get("premium", {})
    in_ch = await check_is_member(context.bot, PREMIUM_CHANNEL_ID, target_id)
    in_gp = await check_is_member(context.bot, PREMIUM_GROUP_ID, target_id)

    msg = (
        f"👤 <b>User Audit:</b> <code>{target_id}</code>\n\n"
        f"• Name: {user.get('name', 'N/A')}\n"
        f"• Premium: {'Active ✅' if is_active else 'Inactive ❌'}\n"
        f"• Expiry: <code>{format_date(expiry)}</code>\n"
        f"• Purchase Date: <code>{format_date(prem.get('purchase_date'))}</code>\n"
        f"• Channel Member: {'Joined ✅' if in_ch else 'Not Joined ❌'}\n"
        f"• Group Member: {'Joined ✅' if in_gp else 'Not Joined ❌'}\n"
        f"• Lifetime Files: {user.get('files', {}).get('lifetime_files', 0)}\n"
        f"• Banned: {user.get('ban_status', {}).get('is_banned', False)}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def users_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    col = get_db_collection()
    now = datetime.now(timezone.utc)
    total = col.count_documents({})
    active = col.count_documents({"premium.is_premium": True, "premium.expiry_date": {"$gt": now}})
    expired = col.count_documents({
        "$or": [
            {"premium.is_premium": True, "premium.expiry_date": {"$lte": now}},
            {"premium.is_premium": False, "premium.purchase_date": {"$ne": None}},
        ]
    })
    await update.message.reply_text(
        f"👥 <b>DATABASE STATS</b>\n\n• Total: {total}\n• Active Premium: {active}\n• Expired: {expired}",
        parse_mode="HTML",
    )


# --- Pagination for Admin ---
def get_paginated_premium_users(page: int, per_page: int = 5):
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    skip = (page - 1) * per_page
    cursor = col.find({"premium.is_premium": True, "premium.expiry_date": {"$gt": now}}).skip(skip).limit(per_page)
    return list(cursor)


def get_paginated_expired_users(page: int, per_page: int = 5):
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    skip = (page - 1) * per_page
    cursor = col.find({
        "$or": [
            {"premium.is_premium": True, "premium.expiry_date": {"$lte": now}},
            {"premium.is_premium": False, "premium.purchase_date": {"$ne": None}},
        ]
    }).skip(skip).limit(per_page)
    return list(cursor)


async def render_premium_page(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    total = col.count_documents({"premium.is_premium": True, "premium.expiry_date": {"$gt": now}})
    per_page = 5
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    users = get_paginated_premium_users(page, per_page)
    if not users:
        return "⭐ <b>No active premium users found.</b>", InlineKeyboardMarkup([])

    text = f"⭐ <b>ACTIVE PREMIUM USERS (Page {page}/{total_pages})</b>\n\n"
    start_i = (page - 1) * per_page
    for i, u in enumerate(users, start=start_i + 1):
        exp = u.get("premium", {}).get("expiry_date")
        exp_s = exp.strftime("%d-%m-%Y") if exp else "N/A"
        text += f"{i}. <b>{u.get('name', 'User')}</b>\n   ID: <code>{u.get('id')}</code>\n   Expiry: <code>{exp_s}</code>\n\n"

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"admin_prem_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"admin_prem_{page + 1}"))

    return text, InlineKeyboardMarkup([nav] if nav else [])


async def render_expired_page(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    total = col.count_documents({
        "$or": [
            {"premium.is_premium": True, "premium.expiry_date": {"$lte": now}},
            {"premium.is_premium": False, "premium.purchase_date": {"$ne": None}},
        ]
    })
    per_page = 5
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    users = get_paginated_expired_users(page, per_page)
    if not users:
        return "⏰ <b>No expired users found.</b>", InlineKeyboardMarkup([])

    text = f"⏰ <b>EXPIRED USERS (Page {page}/{total_pages})</b>\n\n"
    start_i = (page - 1) * per_page
    for i, u in enumerate(users, start=start_i + 1):
        exp = u.get("premium", {}).get("expiry_date")
        exp_s = exp.strftime("%d-%m-%Y") if exp else "Revoked"
        text += f"{i}. <b>{u.get('name', 'User')}</b>\n   ID: <code>{u.get('id')}</code>\n   Expired on: <code>{exp_s}</code>\n\n"

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"admin_exp_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"admin_exp_{page + 1}"))

    return text, InlineKeyboardMarkup([nav] if nav else [])


async def premium_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text, markup = await render_premium_page(1)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def expired_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text, markup = await render_expired_page(1)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("Access Denied.", show_alert=True)
        return

    data = query.data
    if data.startswith("admin_prem_"):
        page = int(data.split("_")[-1])
        text, markup = await render_premium_page(page)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    elif data.startswith("admin_exp_"):
        page = int(data.split("_")[-1])
        text, markup = await render_expired_page(page)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# --- Background Auto-Expiry Job ---
async def check_expiries_job(context: ContextTypes.DEFAULT_TYPE):
    col = get_db_collection()
    now = datetime.now(timezone.utc)
    expired_users = list(col.find({"premium.is_premium": True, "premium.expiry_date": {"$lte": now}}))

    if not expired_users:
        return

    logger.info(f"Processing {len(expired_users)} expired subscriptions...")
    for u in expired_users:
        uid = u.get("id")
        if not uid:
            continue

        # 1. Update DB
        col.update_one(
            {"id": uid},
            {"$set": {"premium.is_premium": False, "premium.expiry_date": None}},
        )

        # 2. Kick from Channel & Group
        await kick_and_unban_user(context.bot, PREMIUM_CHANNEL_ID, uid)
        await kick_and_unban_user(context.bot, PREMIUM_GROUP_ID, uid)

        # 3. Inform User
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

        # 4. Inform Log Channel
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"⌛ <b>[EXPIRED]</b> Premium expired for User: <code>{uid}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass


# --- Main Entrypoint ---
def main():
    # 1. Background Port Server for Render Free Web Service
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    # 2. Python 3.12 / 3.14 Event Loop compatibility
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 3. Build Bot Application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # User Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(
        CallbackQueryHandler(
            user_callback_handler,
            pattern="^(show_plans|view_plan|renew_plan|already_joined_channel|already_joined_group|need_premium)$",
        )
    )

    # Join Request Handler (Core Feature)
    app.add_handler(ChatJoinRequestHandler(handle_chat_join_request))

    # Admin Commands & Aliases
    app.add_handler(CommandHandler(["verify", "addpremium"], add_premium_cmd))
    app.add_handler(CommandHandler(["revoke", "removepremium"], remove_premium_cmd))
    app.add_handler(CommandHandler(["status_user", "user"], user_info_cmd))
    app.add_handler(CommandHandler("users", users_count_cmd))
    app.add_handler(CommandHandler("premium_users", premium_users_cmd))
    app.add_handler(CommandHandler("expired_users", expired_users_cmd))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))

    # Background auto-expiry check every 10 minutes (600s)
    if app.job_queue:
        app.job_queue.run_repeating(check_expiries_job, interval=600, first=20)
    else:
        logger.warning("JobQueue not initialized.")

    logger.info("🤖 Bot 2 is live and listening!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
