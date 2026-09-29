from datetime import datetime, timezone
import logging
import os
from config import COLLECTION_NAME, DB_NAME, MONGO_URI, PREMIUM_CHANNEL_ID
from pymongo import MongoClient

logger = logging.getLogger(__name__)

# Initialize Client
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]


def get_user_by_id(telegram_id: int):
    """Fetches user document by Telegram ID."""
    return collection.find_one({"id": telegram_id})


def create_user_if_not_exists(telegram_id: int, name: str = "Unknown"):
    """Creates a new user document if it doesn't exist,

    preserving the existing schema and updating last_seen.
    """
    existing = get_user_by_id(telegram_id)
    now_utc = datetime.now(timezone.utc)

    if existing:
        collection.update_one(
            {"id": telegram_id}, {"$set": {"user_data.last_seen": now_utc}}
        )
        return existing

    new_user = {
        "id": telegram_id,
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
            "join_date": now_utc,
            "last_seen": now_utc,
        },
        "files": {"todays_files": 0, "lifetime_files": 0},
        "refer": {"referral_points": 0, "invited_by": None},
        "ban_status": {"is_banned": False, "ban_reason": ""},
    }

    try:
        collection.insert_one(new_user)
        logger.info(f"Created new user document for ID: {telegram_id}")
        return collection.find_one({"id": telegram_id})
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return None


def update_premium_status(
    telegram_id: int,
    is_premium: bool,
    expiry_date: datetime = None,
    purchase_date: datetime = None,
    trial: bool = False,
):
    """Updates ONLY the premium fields.

    Properly handles clearing the expiry date when revoking.
    """
    update_fields = {"premium.is_premium": is_premium, "premium.trial": trial}

    # Jab premium active ho raha ho YA revoke ho raha ho (None set karna ho)
    if expiry_date is not None or not is_premium:
        update_fields["premium.expiry_date"] = expiry_date

    if purchase_date is not None:
        update_fields["premium.purchase_date"] = purchase_date

    try:
        result = collection.update_one(
            {"id": telegram_id}, {"$set": update_fields}
        )
        if result.matched_count == 0:
            logger.warning(
                f"User ID {telegram_id} not found for premium update."
            )
        return result
    except Exception as e:
        logger.error(f"Error updating premium status: {e}")
        return None


def check_premium_status(telegram_id: int) -> bool:
    """Checks if a user's premium is active and unexpired."""
    user = get_user_by_id(telegram_id)
    if not user:
        return False

    premium = user.get("premium", {})
    if not premium.get("is_premium"):
        return False

    expiry = premium.get("expiry_date")
    if not expiry:
        return False

    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expiry:
        # Auto-expire if time has passed
        update_premium_status(telegram_id, is_premium=False)
        return False

    return True


# Alias so both function names work across files
check_premium_expiry = check_premium_status


def get_premium_channel_link() -> str:
    """Returns valid Telegram channel link."""
    # Check if a custom invite link was provided in environment
    custom_link = os.getenv("PREMIUM_CHANNEL_LINK")
    if custom_link:
        return custom_link.strip()

    ch_id = str(PREMIUM_CHANNEL_ID).strip()
    if ch_id.startswith("-100"):
        return f"https://t.me/c/{ch_id[4:]}"
    if ch_id.startswith("@"):
        return f"https://t.me/{ch_id[1:]}"

    return f"https://t.me/{ch_id}"
