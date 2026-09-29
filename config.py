import os
import sys
from dotenv import load_dotenv

# Load .env file (local testing ke liye)
load_dotenv()

# Read variables directly from environment
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL_ID_RAW = os.getenv("LOG_CHANNEL_ID")
PREMIUM_CHANNEL_ID_RAW = os.getenv("PREMIUM_CHANNEL_ID")
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", "")

# MongoDB Config
DB_NAME = "cluster0"
COLLECTION_NAME = "USERS"

# Check kaun-kaun se variables gayab hain
missing_vars = []
if not BOT_TOKEN:
    missing_vars.append("BOT_TOKEN")
if not MONGO_URI:
    missing_vars.append("MONGO_URI")
if not LOG_CHANNEL_ID_RAW:
    missing_vars.append("LOG_CHANNEL_ID")
if not PREMIUM_CHANNEL_ID_RAW:
    missing_vars.append("PREMIUM_CHANNEL_ID")

if missing_vars:
    print(f"❌ CRITICAL ERROR: Render me ye keys nahi mili: {missing_vars}")
    print(
        "👉 Render Dashboard -> Environment me jaakar exact spelling check karein."
    )
    raise ValueError(f"Missing required environment variables: {missing_vars}")

# Convert IDs to Integer safely
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID_RAW.strip())
    PREMIUM_CHANNEL_ID = int(PREMIUM_CHANNEL_ID_RAW.strip())
except ValueError:
    raise ValueError(
        "LOG_CHANNEL_ID and PREMIUM_CHANNEL_ID must be numbers (e.g. -1001234567890)"
    )

# Parse Admin IDs safely
ADMIN_USER_IDS = []
for x in ADMIN_USER_IDS_RAW.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_USER_IDS.append(int(x))
