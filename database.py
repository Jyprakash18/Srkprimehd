from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import config
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

try:
    client = MongoClient(config.MONGO_URI)
    db = client[config.DB_NAME]
    users_col = db[config.COLLECTION_NAME]
    client.admin.command("ping")
    logger.info("✅ Connected to MongoDB Atlas (cluster0.USERS)")
except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {e}")
    raise e


def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    return users_col.find_one({"id": telegram_id})


def get_or_create_user(
    telegram_id: int, name: str = "User"
) -> Optional[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    try:
        user = users_col.find_one_and_update(
            {"id": telegram_id},
            {
                "$setOnInsert": {
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
                        "join_date": now,
                        "last_seen": now,
                    },
                    "files": {"todays_files": 0, "lifetime_files": 0},
                    "refer": {"referral_points": 0, "invited_by": None},
                    "ban_status": {"is_banned": False, "ban_reason": ""},
                },
                "$set": {"user_data.last_seen": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return user
    except PyMongoError as e:
        logger.error(f"Error in get_or_create_user: {e}")
        return None


def is_premium_active(telegram_id: int) -> Tuple[bool, Optional[datetime]]:
    user = get_user(telegram_id)
    if not user:
        return False, None

    premium = user.get("premium", {})
    if not premium.get("is_premium", False):
        return False, None

    expiry = premium.get("expiry_date")
    if not expiry:
        return False, None

    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expiry > now:
        return True, expiry
    return False, expiry


def add_or_extend_premium(
    telegram_id: int, days: int, name: str = "User"
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    user = get_or_create_user(telegram_id, name)

    premium = user.get("premium", {}) if user else {}
    is_active = premium.get("is_premium", False)
    current_expiry = premium.get("expiry_date")

    if is_active and current_expiry:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)

        if current_expiry > now:
            new_expiry = current_expiry + timedelta(days=days)
            extended = True
        else:
            new_expiry = now + timedelta(days=days)
            extended = False
    else:
        new_expiry = now + timedelta(days=days)
        extended = False

    update_fields = {
        "premium.is_premium": True,
        "premium.purchase_date": now,
        "premium.expiry_date": new_expiry,
        "premium.trial": False,
    }

    updated_user = users_col.find_one_and_update(
        {"id": telegram_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )

    return {
        "user": updated_user,
        "new_expiry": new_expiry,
        "extended": extended,
    }


def revoke_premium(telegram_id: int) -> bool:
    res = users_col.update_one(
        {"id": telegram_id},
        {
            "$set": {
                "premium.is_premium": False,
                "premium.expiry_date": None,
                "premium.trial": False,
            }
        },
    )
    return res.modified_count > 0


def get_expired_active_users() -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    return list(
        users_col.find(
            {"premium.is_premium": True, "premium.expiry_date": {"$lte": now}}
        )
    )


def mark_user_expired(telegram_id: int):
    users_col.update_one(
        {"id": telegram_id},
        {"$set": {"premium.is_premium": False, "premium.expiry_date": None}},
    )


def count_all_users() -> int:
    return users_col.count_documents({})


def count_premium_users() -> int:
    now = datetime.now(timezone.utc)
    return users_col.count_documents(
        {"premium.is_premium": True, "premium.expiry_date": {"$gt": now}}
    )


def count_expired_users() -> int:
    now = datetime.now(timezone.utc)
    return users_col.count_documents(
        {
            "$or": [
                {
                    "premium.is_premium": True,
                    "premium.expiry_date": {"$lte": now},
                },
                {
                    "premium.is_premium": False,
                    "premium.purchase_date": {"$ne": None},
                },
            ]
        }
    )


def get_paginated_premium_users(page: int, per_page: int = 5) -> List[Dict]:
    now = datetime.now(timezone.utc)
    skip = (page - 1) * per_page
    return list(
        users_col.find(
            {"premium.is_premium": True, "premium.expiry_date": {"$gt": now}}
        )
        .skip(skip)
        .limit(per_page)
    )


def get_paginated_expired_users(page: int, per_page: int = 5) -> List[Dict]:
    now = datetime.now(timezone.utc)
    skip = (page - 1) * per_page
    return list(
        users_col.find(
            {
                "$or": [
                    {
                        "premium.is_premium": True,
                        "premium.expiry_date": {"$lte": now},
                    },
                    {
                        "premium.is_premium": False,
                        "premium.purchase_date": {"$ne": None},
                    },
                ]
            }
        )
        .skip(skip)
        .limit(per_page)
    )
