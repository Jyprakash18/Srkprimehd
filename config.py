import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Load variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
PREMIUM_CHANNEL_ID = os.getenv("PREMIUM_CHANNEL_ID")
ADMIN_USER_IDS = [int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]

# MongoDB Config
DB_NAME = "cluster0"
COLLECTION_NAME = "USERS"

# Check if critical variables are missing
if not BOT_TOKEN or not MONGO_URI:
    raise ValueError("Missing BOT_TOKEN or MONGO_URI in .env file")
