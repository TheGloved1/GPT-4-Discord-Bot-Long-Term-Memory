from venv import logger
import discord
from dotenv import load_dotenv
import os
import dacite
import yaml
from typing import Dict, List
from src.base import Config

load_dotenv()
logger.info(os.environ["DISCORD_BOT_TOKEN"])

# load config.yaml
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG: Config = dacite.from_dict(Config, yaml.safe_load(open(os.path.join(SCRIPT_DIR, "config.yaml"), "r")))

llm_model = "mistral"
BOT_NAME = CONFIG.name
BOT_INSTRUCTIONS = CONFIG.instructions
EXAMPLE_CONVOS = CONFIG.example_conversations
MY_GUILD = discord.Object(id=os.environ["GUILD_ID"])

GOOGLE_AI_KEY = os.environ["GOOGLE_AI_KEY"]
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OWNER_ID = os.environ["OWNER_ID"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]

# ALLOWED_CHANNEL_NAMES: List[str] = []
# channel_names = os.environ["ALLOWED_CHANNEL_NAMES"].split(",")
# for s in channel_names:
#     ALLOWED_CHANNEL_NAMES.append(str(s))


# Send Messages, Send Messages in Threads, Manage Messages, Read Message History
BOT_INVITE_URL = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=328565073920&scope=bot"

SECONDS_DELAY_RECEIVING_MSG = (
    0  # give a delay for the bot to respond so it can catch multiple messages
)
MAX_MESSAGE_HISTORY = 12
MAX_CHARS_PER_REPLY_MSG = (
    1500  # discord has a 2k limit, we just break message into 1.5k
)

MY_BOT_NAME = BOT_NAME
MY_BOT_EXAMPLE_CONVOS = EXAMPLE_CONVOS
