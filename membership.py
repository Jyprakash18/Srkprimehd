import logging
from typing import Dict, Optional
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

_cached_invite_links: Dict[int, str] = {}


async def check_is_member(bot, chat_id: int, user_id: int) -> bool:
    """Checks whether the user is already inside the chat."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in {
            "member",
            "administrator",
            "creator",
            "restricted",
        }
    except BadRequest as e:
        if (
            "user not found" in str(e).lower()
            or "participant_id_invalid" in str(e).lower()
        ):
            return False
        logger.warning(
            f"Error checking membership for {user_id} in {chat_id}: {e}"
        )
        return False
    except Exception as e:
        logger.error(f"Error in check_is_member: {e}")
        return False


async def get_join_request_link(
    bot, chat_id: int, link_name: str
) -> Optional[str]:
    """Generates an official Telegram Join Request invite link."""
    if chat_id in _cached_invite_links:
        return _cached_invite_links[chat_id]

    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=chat_id, name=link_name, creates_join_request=True
        )
        _cached_invite_links[chat_id] = invite_link.invite_link
        return invite_link.invite_link
    except TelegramError as e:
        logger.error(
            f"Failed to create join request link for {chat_id}: {e}. Ensure Bot is ADMIN with 'Invite Users via Link' permission!"
        )
        return None


async def kick_and_unban_user(bot, chat_id: int, user_id: int):
    """Removes user from chat and unbans them immediately so they can rejoin in future."""
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"Removed expired user {user_id} from {chat_id}")
    except BadRequest as e:
        if (
            "user is not a member" in str(e).lower()
            or "user not found" in str(e).lower()
        ):
            return
        logger.warning(f"Could not remove user {user_id} from {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Error kicking user {user_id}: {e}")
