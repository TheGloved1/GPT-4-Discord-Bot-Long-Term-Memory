from numpy import not_equal
from src.constants import MAX_CHARS_PER_REPLY_MSG
from typing import Optional, List
from discord import ChannelType, Message as DiscordMessage
from src.base import Message
import logging

logger = logging.getLogger(__name__)


def discord_message_to_message(message: DiscordMessage) -> Optional[Message]:
    if message.content:
        return Message(user=message.author.name, text=message.content)
    return None


def split_into_shorter_messages(message: str) -> List[str]:
    return [
        message[i: i + MAX_CHARS_PER_REPLY_MSG]
        for i in range(0, len(message), MAX_CHARS_PER_REPLY_MSG)
    ]


def is_last_message_stale(
    interaction_message: DiscordMessage, last_message: DiscordMessage, bot_id: str
) -> bool:
    """
    Checks if the last message is stale.

    Args:
        interaction_message (DiscordMessage): The current interaction message.
        last_message (DiscordMessage): The last message in the channel.
        bot_id (str): The ID of the bot.

    Returns:
        bool: True if the last message is stale, False otherwise.
    """
    return (
        last_message
        and last_message.id != interaction_message.id
        and last_message.author
        and last_message.author.id != bot_id
    )

# def should_block(channel) -> bool:
#     if channel.name and channel.name not in ALLOWED_CHANNEL_NAMES:
#         # not allowed in this server
#         logger.info(f"Messages from {channel} not allowed")
#         return True
#     return False
