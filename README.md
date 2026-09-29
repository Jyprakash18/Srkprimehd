Premium Subscription Bot
A Telegram bot to manage premium subscriptions using MongoDB.

Setup
Install dependencies:

bash
pip install -r requirements.txt
Create a .env file from .env.example and add your credentials.

Run the bot:

bash
python bot.py
Commands
/start: Start the bot.
/status: Check your premium status.
/verify USER_ID DAYS: (Admin) Verify payment.
/revoke USER_ID: (Admin) Revoke premium.
/status_user USER_ID: (Admin) Check another user's status.
How to Get IDs
BOT_TOKEN: Talk to @BotFather.
LOG_CHANNEL_ID: Add bot as admin to a private channel. Forward a message from that channel to @userinfobot.
PREMIUM_CHANNEL_ID: The username of your premium channel (without @).
ADMIN_USER_IDS: Forward your message to @userinfobot to get your numeric ID.
Step-by-Step Setup Guide
Step 1: Get Your Credentials
BOT_TOKEN:

Open Telegram and search for @BotFather.
Send /newbot.
Follow the prompts. You will get a token like 123456789:AAH....
ADMIN_USER_IDS:

Open Telegram and search for @userinfobot.
Send it any message (e.g., "hi").
It will reply with your Id. Copy that number (e.g., 123456789).

LOG_CHANNEL_ID:

Create a Private Channel (e.g., "Bot Logs").
Add your Bot as an Admin of this channel.
Forward any message from this channel to @userinfobot.
The bot will give you a Channel Id. It will look like -1001234567890. This is your LOG_CHANNEL_ID.
PREMIUM_CHANNEL_ID:

This is the username of your premium channel.
If your channel link is https://t.me/my_premium, then PREMIUM_CHANNEL_ID=my_premium.
Important: Your Bot must be an Admin of this premium channel to manage invite links (though for this code, we just send the public link).
MONGO_URI:

Go to MongoDB Atlas.
Connect -> Drivers.
Copy the URI. It looks like mongodb+srv://user:pass@cluster0....
Step 2: Configure Environment
Create a folder named premium_bot.
Inside it, create the files listed in the structure above.
Create a file named .env (not .env.example).
Paste your values into .env:
text
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
MONGO_URI=YOUR_MONGO_URI_HERE
LOG_CHANNEL_ID=-100YOUR_LOG_CHANNEL_ID
PREMIUM_CHANNEL_ID=your_premium_username
ADMIN_USER_IDS=YOUR_TELEGRAM_ID
Step 3: Run on VPS (Recommended)
If you are on a Linux VPS (like DigitalOcean, AWS, or Hostinger):

Install Python 3.11+:

bash
sudo apt update
sudo apt install python3.11 python3.11-venv
Create Virtual Environment:

bash
cd premium_bot
python3.11 -m venv venv
source venv/bin/activate
Install Dependencies:

bash
pip install -r requirements.txt
Run the Bot:

bash
python bot.py
Keep it Running (Systemd):

Create a file /etc/systemd/system/premium-bot.service:
ini
[Unit]
Description=Premium Telegram Bot
After=network.target

[Service]
User=your_username
WorkingDirectory=/home/your_username/premium_bot
ExecStart=/home/your_username/premium_bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
Enable it:
bash
sudo systemctl enable premium-bot
sudo systemctl start premium-bot


Step 4: Run on Android/Termux (Possible but less stable)
Install Termux from F-Droid.
Run:
bash
pkg update
pkg install python
git clone <your_repo_link> # Or copy files manually
cd premium_bot
pip install -r requirements.txt
python bot.py
Warning: Termux stops running when the screen is off. You need to use a "Wake Lock" app or keep the screen on. A VPS is much better for 24/7 operation.

Key Features Explained
Database Safety: The update_premium_status function in database.py uses {"$set": {"premium.is_premium": ...}}. This ensures that only the premium fields are touched. Your files, refer, and ban_status data remains untouched.
Expiry Logic: The background job runs every hour. It checks if expiry_date is in the past. If yes, it sets is_premium to false.
Extension Logic: In /verify, if a user already has an active premium, the new days are added to the current expiry date, not the current time. This prevents losing days.
Admin Security: Only IDs listed in ADMIN_USER_IDS can use /verify, /revoke, or /status_user.

How to Make Bot Admin in Premium Channel
Go to your Premium Channel.
Tap Edit -> Administrators.
Tap Add Admin.
Select your Bot.
Ensure "Invite Users" or "Manage Links" is checked if you plan to generate unique links later. For now, just being an admin is enough for the bot to post logs if needed.
This project is ready to run. Let me know if you encounter any specific error messages!
