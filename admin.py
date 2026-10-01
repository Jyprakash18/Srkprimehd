import math
from typing import Tuple
import config
import database
import membership
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_USER_IDS


def format_dt(dt) -> str:
    if not dt:
        return "N/A"
    return dt.strftime("%d-%m-%Y %H:%M UTC")


async def admin_panel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 Total Users", callback_data="admin_count_users"
            ),
            InlineKeyboardButton(
                "⭐ Premium Users", callback_data="admin_prem_page_1"
            ),
        ],
        [
            InlineKeyboardButton(
                "⏰ Expired Users", callback_data="admin_exp_page_1"
            )
        ],
    ]
    await update.message.reply_text(
        "🛠 **Admin Management Dashboard**\n\nCommands:\n"
        "• `/addpremium USER_ID DAYS`\n"
        "• `/removepremium USER_ID`\n"
        "• `/user USER_ID`\n"
        "• `/premium_users`\n"
        "• `/expired_users`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def add_premium_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ **Format:** `/addpremium <USER_ID> <DAYS>`\nExample: `/addpremium 5672857559 30`",
            parse_mode="Markdown",
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ User ID aur Days dono numbers hone chahiye."
        )
        return

    if days <= 0:
        await update.message.reply_text("❌ Days 0 se zyada hone chahiye.")
        return

    result = database.add_or_extend_premium(target_id, days)
    new_expiry = result["new_expiry"]
    extended = result["extended"]

    ch_link = await membership.get_join_request_link(
        context.bot, config.PREMIUM_CHANNEL_ID, "SRK Prime Max"
    )
    gp_link = await membership.get_join_request_link(
        context.bot, config.PREMIUM_GROUP_ID, "SRKPrime Request"
    )

    dm_btns = []
    if ch_link:
        dm_btns.append(
            [InlineKeyboardButton("🔴 Join SRK Prime Max", url=ch_link)]
        )
    if gp_link:
        dm_btns.append(
            [InlineKeyboardButton("🔴 Join SRKPrime Request", url=gp_link)]
        )

    dm_text = (
        "🎉 **Premium Activated!**\n\n"
        f"⏳ **Duration:** {days} days\n"
        f"📅 **Expiry:** `{new_expiry.strftime('%d-%m-%Y')}`\n\n"
        "Niche diye buttons par click karke Join Request bhejein, bot automatic approve kar dega!"
    )

    dm_sent = True
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=dm_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(dm_btns) if dm_btns else None,
        )
    except Exception:
        dm_sent = False

    await update.message.reply_text(
        f"✅ **Premium {'Extended' if extended else 'Activated'}!**\n\n"
        f"👤 User: `{target_id}`\n"
        f"⏳ Days: {days}\n"
        f"📅 Expiry: `{format_dt(new_expiry)}`\n"
        f"📬 User Notified: {'Haan (DM sent)' if dm_sent else 'Nahi (Bot start nahi kiya tha)'}",
        parse_mode="Markdown",
    )


async def remove_premium_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ **Format:** `/removepremium <USER_ID>`"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID number honi chahiye.")
        return

    database.revoke_premium(target_id)
    await membership.kick_and_unban_user(
        context.bot, config.PREMIUM_CHANNEL_ID, target_id
    )
    await membership.kick_and_unban_user(
        context.bot, config.PREMIUM_GROUP_ID, target_id
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="⚠️ Aapka Premium Subscription cancel kar diya gaya hai.",
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ User `{target_id}` ka premium band ho gaya aur dono jagah se kick kar diya gaya.",
        parse_mode="Markdown",
    )


async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) < 1:
        await update.message.reply_text("⚠️ **Format:** `/user <USER_ID>`")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID number honi chahiye.")
        return

    user = database.get_user(target_id)
    if not user:
        await update.message.reply_text(
            f"❌ User `{target_id}` database me nahi mila."
        )
        return

    is_active, expiry = database.is_premium_active(target_id)
    prem = user.get("premium", {})
    in_ch = await membership.check_is_member(
        context.bot, config.PREMIUM_CHANNEL_ID, target_id
    )
    in_gp = await membership.check_is_member(
        context.bot, config.PREMIUM_GROUP_ID, target_id
    )

    msg = (
        f"👤 **User Audit:** `{target_id}`\n\n"
        f"• **Name:** {user.get('name', 'N/A')}\n"
        f"• **Premium:** {'Active ✅' if is_active else 'Inactive ❌'}\n"
        f"• **Expiry:** `{format_dt(expiry)}`\n"
        f"• **Purchase Date:** `{format_dt(prem.get('purchase_date'))}`\n"
        f"• **Channel Member:** {'Joined ✅' if in_ch else 'Not Joined ❌'}\n"
        f"• **Group Member:** {'Joined ✅' if in_gp else 'Not Joined ❌'}\n"
        f"• **Lifetime Files:** {user.get('files', {}).get('lifetime_files', 0)}\n"
        f"• **Banned:** {user.get('ban_status', {}).get('is_banned', False)}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def users_count_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    total = database.count_all_users()
    prem = database.count_premium_users()
    exp = database.count_expired_users()

    await update.message.reply_text(
        f"👥 **DATABASE STATS**\n\n"
        f"• Total Users: {total}\n"
        f"• Active Premium Users: {prem}\n"
        f"• Expired Users: {exp}",
        parse_mode="Markdown",
    )


# --- Pagination Logic ---


async def render_premium_page(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    total = database.count_premium_users()
    per_page = 5
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    users = database.get_paginated_premium_users(page, per_page)
    if not users:
        return "⭐ **Koi active premium user nahi mila.**", InlineKeyboardMarkup(
            []
        )

    text = f"⭐ **ACTIVE PREMIUM USERS (Page {page}/{total_pages})**\n\n"
    start_i = (page - 1) * per_page
    for i, u in enumerate(users, start=start_i + 1):
        exp = u.get("premium", {}).get("expiry_date")
        exp_s = exp.strftime("%d-%m-%Y") if exp else "N/A"
        text += f"{i}. **{u.get('name', 'User')}**\n   ID: `{u.get('id')}`\n   Expiry: `{exp_s}`\n\n"

    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                "◀ Prev", callback_data=f"admin_prem_page_{page - 1}"
            )
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                "Next ▶", callback_data=f"admin_prem_page_{page + 1}"
            )
        )

    return text, InlineKeyboardMarkup([nav] if nav else [])


async def render_expired_page(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    total = database.count_expired_users()
    per_page = 5
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    users = database.get_paginated_expired_users(page, per_page)
    if not users:
        return "⏰ **Koi expired user nahi mila.**", InlineKeyboardMarkup([])

    text = f"⏰ **EXPIRED USERS (Page {page}/{total_pages})**\n\n"
    start_i = (page - 1) * per_page
    for i, u in enumerate(users, start=start_i + 1):
        exp = u.get("premium", {}).get("expiry_date")
        exp_s = exp.strftime("%d-%m-%Y") if exp else "Revoked"
        text += f"{i}. **{u.get('name', 'User')}**\n   ID: `{u.get('id')}`\n   Expired: `{exp_s}`\n\n"

    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                "◀ Prev", callback_data=f"admin_exp_page_{page - 1}"
            )
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                "Next ▶", callback_data=f"admin_exp_page_{page + 1}"
            )
        )

    return text, InlineKeyboardMarkup([nav] if nav else [])


async def premium_users_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return
    text, markup = await render_premium_page(1)
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=markup
    )


async def expired_users_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return
    text, markup = await render_expired_page(1)
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=markup
    )


async def admin_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer("Access Denied.", show_alert=True)
        return

    data = query.data
    if data == "admin_count_users":
        total = database.count_all_users()
        prem = database.count_premium_users()
        exp = database.count_expired_users()
        await query.answer(
            f"Total: {total} | Active: {prem} | Expired: {exp}", show_alert=True
        )
    elif data.startswith("admin_prem_page_"):
        page = int(data.split("_")[-1])
        text, markup = await render_premium_page(page)
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=markup
        )
    elif data.startswith("admin_exp_page_"):
        page = int(data.split("_")[-1])
        text, markup = await render_expired_page(page)
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=markup
        )
