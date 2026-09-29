from pymongo import MongoClient
from datetime import datetime, timezone
from config import MONGO_URI, DB_NAME, COLLECTION_NAME, PREMIUM_CHANNEL_ID
import logging

logger = logging.getLogger(__name__)

# Initialize Client
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

def get_user_by_id(telegram_id: int):
    """Fetches user document by Telegram ID."""
    return collection.find_one({"id": telegram_id})

def create_user_if_not_exists(telegram_id: int, name: str = "Unknown"):
    """
    Creates a new user document if it doesn't exist.
    It uses the exact structure you provided.
    """
    existing = get_user_by_id(telegram_id)
    if existing:
        return existing

    now_utc = datetime.now(timezone.utc)
    
    new_user = {
        "id": telegram_id,
        "name": name,
        "premium": {
            "is_premium": False,
            "expiry_date": None,
            "purchase_date": None,
            "trial": False
        },
        "user_data": {
            "is_verified": False,
            "is_subscribed": False,
            "join_date": now_utc,
            "last_seen": now_utc
        },
        "files": {
            "todays_files": 0,
            "lifetime_files": 0
        },
        "refer": {
            "referral_points": 0,
            "invited_by": None
        },
        "ban_status": {
            "is_banned": False,
            "ban_reason": ""
        }
    }

    try:
        collection.insert_one(new_user)
        logger.info(f"Created new user document for ID: {telegram_id}")
        return collection.find_one({"id": telegram_id})
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return None

def update_premium_status(telegram_id: int, is_premium: bool, expiry_date: datetime = None, purchase_date: datetime = None, trial: bool = False):
    """
    Updates ONLY the premium fields.
    Uses $set with dot notation to avoid overwriting other fields.
    """
    update_fields = {
        "premium.is_premium": is_premium
    }
    
    if expiry_date is not None:
        update_fields["premium.expiry_date"] = expiry_date
    if purchase_date is not None:
        update_fields["premium.purchase_date"] = purchase_date
    if trial is not None:
        update_fields["premium.trial"] = trial

    try:
        result = collection.update_one(
            {"id": telegram_id},
            {"$set": update_fields}
        )
        if result.matched_count == 0:
            logger.warning(f"User ID {telegram_id} not found for premium update.")
        return result
    except Exception as e:
        logger.error(f"Error updating premium status: {e}")
        return None

def check_premium_expiry(telegram_id: int):
    """
    Checks if a user's premium is expired.
    Returns True if active, False if expired or not premium.
    """
    user = get_user_by_id(telegram_id)
    if not user:
        return False
    
    premium = user.get("premium", {})
    if not premium.get("is_premium"):
        return False
        
    expiry = premium.get("expiry_date")
    if not expiry:
        return False
    
    # Ensure timezone aware comparison
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
        
    if datetime.now(timezone.utc) > expiry:
        # Mark as expired in DB
        update_premium_status(telegram_id, is_premium=False)
        return False
        
    return True

def get_premium_channel_link():
    """
    Returns the premium channel link.
    In a real production bot, you might generate a unique invite link per user,
    but for simplicity and reliability, we use the public channel link or a static invite.
    """
    return f"https://t.me/{PREMIUM_CHANNEL_ID}"
