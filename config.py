import os
import sys
from dotenv import load_dotenv

load_dotenv()


def get_env_or_exit(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(f"❌ CRITICAL ERROR: Environment variable '{key}' is missing!")
        sys.exit(1)
    return val.strip()


BOT_TOKEN = get_env_or_exit("BOT_TOKEN")
MONGO_URI = get_env_or_exit("MONGO_URI")

try:
    PREMIUM_CHANNEL_ID = int(get_env_or_exit("PREMIUM_CHANNEL_ID"))
    PREMIUM_GROUP_ID = int(get_env_or_exit("PREMIUM_GROUP_ID"))
    # Yahan LOG_CHANNEL_ID add karein:
    LOG_CHANNEL_ID = int(get_env_or_exit("LOG_CHANNEL_ID"))
except ValueError:
    print(
        "❌ CRITICAL ERROR: Channel aur Group IDs numbers hone chahiye (e.g. -100xxxxxxxxxx)!"
    )
    sys.exit(1)

raw_admins = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set()
for aid in raw_admins.split(","):
    aid = aid.strip()
    if aid.isdigit():
        ADMIN_USER_IDS.add(int(aid))

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "telegram").lstrip("@")
UPGRADE_LINK = os.getenv(
    "UPGRADE_LINK", f"https://t.me/{SUPPORT_USERNAME}"
).strip()
PORT = int(os.environ.get("PORT", 8080))

DB_NAME = "cluster0"
COLLECTION_NAME = "USERS"
