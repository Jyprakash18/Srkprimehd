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


# Mandatory Tokens & MongoDB
BOT_TOKEN = get_env_or_exit("BOT_TOKEN")
MONGO_URI = get_env_or_exit("MONGO_URI")

# Channel aur Group IDs (Auto-Generate Join Links ke liye dono zaroori hain)
try:
    PREMIUM_CHANNEL_ID = int(get_env_or_exit("PREMIUM_CHANNEL_ID"))
    PREMIUM_GROUP_ID = int(get_env_or_exit("PREMIUM_GROUP_ID"))
except ValueError:
    print(
        "❌ CRITICAL ERROR: PREMIUM_CHANNEL_ID aur PREMIUM_GROUP_ID valid numbers hone chahiye (e.g. -100xxxxxxxxxx)!"
    )
    sys.exit(1)

# Admins List
raw_admins = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set()
for aid in raw_admins.split(","):
    aid = aid.strip()
    if aid.isdigit():
        ADMIN_USER_IDS.add(int(aid))

# Support & Upgrade Link
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "telegram").lstrip("@")
UPGRADE_LINK = os.getenv(
    "UPGRADE_LINK", f"https://t.me/{SUPPORT_USERNAME}"
).strip()

# Port for Render Free Service
PORT = int(os.environ.get("PORT", 8080))

DB_NAME = "cluster0"
COLLECTION_NAME = "USERS"
