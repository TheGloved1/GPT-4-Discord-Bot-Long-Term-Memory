import asyncio
from enum import Enum
from dataclasses import dataclass
import openai
from openai import OpenAI

client = OpenAI()
import json
from typing import Optional, List
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_NAME,
    EXAMPLE_CONVOS,
)
import discord
from src.base import Message, Prompt, Conversation
from src.utils import split_into_shorter_messages, logger
from datetime import datetime
from src.memory import (
    gpt3_response_embedding, 
    save_json,
    timestamp_to_datetime
    )

from uuid import uuid4
from time import time


MY_BOT_NAME = BOT_NAME
MY_BOT_EXAMPLE_CONVOS = EXAMPLE_CONVOS


class CompletionResult(Enum):
    OK = 0
    TOO_LONG = 1
    INVALID_REQUEST = 2
    OTHER_ERROR = 3


@dataclass
class CompletionData:
    status: CompletionResult
    reply_text: Optional[str]
    status_text: Optional[str]


async def generate_completion_response(
    messages: List[Message], user: str, message: discord.Message
) -> CompletionData:
    
    interactive_response = await message.channel.send("...")

    current_content = "..."

    try:
        timestamp = time()
        timestring = timestring = timestamp_to_datetime(timestamp)
        prompt = Prompt(
            header=Message(
                "System", f"Instructions for {MY_BOT_NAME}: {BOT_INSTRUCTIONS}"
            ),
            examples=MY_BOT_EXAMPLE_CONVOS,
            convo=Conversation(messages + [Message(f"{timestring} {MY_BOT_NAME}")]),
        )
        
        rendered = prompt.render()
        print(rendered)
        # response = openai.Completion.create(
        #     model="text-davinci-003",
        #     message=rendered,
        #     temperature=1.0,
        #     top_p=0.9,
        #     max_tokens=512,
        #     n=1,
        #     stop=["<|endoftext|>"])
            
        # You can rollback to using text-davincini-003 by swapping the active "response =" and "reply ="

        response = client.chat.completions.create(model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": rendered}],
        stream=True)

        # Below for "text-davinci-003" model
        # reply = response.choices[0].text.strip()

        for chunk in response:
            if 'choices' in chunk and len(chunk['choices'][0]['text']) > 0:
                text = chunk.choices[0].message['text'].strip()
                if text:
                    return CompletionData(
                        status=CompletionResult.OK, reply_text=text, status_text=None
                    )
                    # Add this text chunk to the current content
                    current_content += text

                    # Keep editing the same message with the new chunk appended
                    if len(current_content) <= 2000:
                        await interactive_response.edit(content=current_content)
                    else:
                        # If we exceed the max message length, send a new one
                        current_content = text  # Reset current_content with the current chunk
                        interactive_response = await message.channel.send(current_content)

                # Discord best practices recommend adding a sleep timer when editing messages frequently
                await asyncio.sleep(0.5)


                
        
    except openai.InvalidRequestError as e:
        if "This model's maximum context length" in e.user_message:
            return CompletionData(
                status=CompletionResult.TOO_LONG, reply_text=None, status_text=str(e)
            )
        else:
            logger.exception(e)
            return CompletionData(
                status=CompletionResult.INVALID_REQUEST,
                reply_text=None,
                status_text=str(e),
            )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
        )


async def process_response(
    user: str, channel: discord.TextChannel, response_data: CompletionData, message=discord.Message
):
    status = response_data.status
    reply_text = response_data.reply_text
    status_text = response_data.status_text
    if status is CompletionResult.OK:
        sent_message = None
        if not reply_text:
            sent_message = await channel.send(
                embed=discord.Embed(
                    description=f"**Invalid response** - empty response",
                    color=discord.Color.yellow(),
                )
            )
        else:
            shorter_response = split_into_shorter_messages(reply_text)
            for r in shorter_response:
                if (channel.type == discord.ChannelType.text):
                    sent_message = await message.reply(r)
                else:
                    sent_message = await channel.send(r)

    elif status is CompletionResult.INVALID_REQUEST:
        await channel.send(
            embed=discord.Embed(
                description=f"**Invalid request** - {status_text}",
                color=discord.Color.yellow(),
            )
        )
    else:
        await channel.send(
            embed=discord.Embed(
                description=f"**Error** - {status_text}",
                color=discord.Color.yellow(),
            )
        )
