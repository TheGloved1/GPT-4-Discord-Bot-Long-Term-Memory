from time import time
from uuid import uuid4
from src.memory import (
    timestamp_to_datetime
)
from datetime import datetime
from src.utils import split_into_shorter_messages, logger
from src.base import Message, Prompt, Conversation
import discord
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_NAME,
    EXAMPLE_CONVOS,
)
from typing import Optional, List
import json
import asyncio
from enum import Enum
from dataclasses import dataclass
import openai
from openai import OpenAI

client = OpenAI()


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


async def GenerateOpenAIResponse(channel: discord.TextChannel, message: discord.Message = None):
    MentionContent = message.content.removeprefix("<@938447947857821696> ")
    if bot.user.mentioned_in(message):
        message.content = message.content.removeprefix("<@938447947857821696> ")
    logger.info("Embedding Message!")
    vector = gpt3_embedding(message)
    timestamp = time()
    timestring = timestring = timestamp_to_datetime(timestamp)
    user = message.author.name
    extracted_message = "%s: %s - %s" % (user, timestring, MentionContent)
    info = {
        "speaker": user,
        "timestamp": timestamp,
        "uuid": str(uuid4()),
        "vector": vector,
        "message": extracted_message,
        "timestring": timestring,
    }
    filename = "log_%s_user" % timestamp
    save_json(f"./src/chat_logs/{filename}.json", info)
    history = load_convo()
    logger.info("Loading Memories!")
    thinkingText = "**```Loading Memories...```**"
    await interactive_response.edit(content=thinkingText)
    memories = fetch_memories(vector, history, 5)
    current_notes, vector = summarize_memories(memories)
    logger.info(current_notes)
    print(
        "-------------------------------------------------------------------------------"
    )
    add_notes(current_notes)
    if len(notes_history) >= 2:
        print(notes_history[-2])
    else:
        print(
            "The list does not have enough elements to access the second-to-last element."
        )
    message_notes = Message(user="memories", text=current_notes)
    context_notes = None
    if len(notes_history) >= 2:
        context_notes = Message(user="context", text=notes_history[-2])
    else:
        print("The list does not have enough elements create context")
    logger.info(
        f"Message to process - {message.author}: {message.content[:50]} - {channel.id} {channel.jump_url}"
    )
    thinkingText = "**```Reading Previous Messages...```**"
    await interactive_response.edit(content=thinkingText)
    if not channel.type == discord.ChannelType.text:
        logger.info("Public Thread Message Recieved!")
        channel_messages = [
            discord_message_to_message(msg)
            async for msg in message.channel.history(limit=MAX_MESSAGE_HISTORY)
        ]
    else:
        channel_messages = [discord_message_to_message(message)]
    if message.thread is None:
        logger.info("Thread Message Recieved!")
        logger.info(message.content)
    channel_messages = [x for x in channel_messages if x is not None]
    channel_messages.reverse()
    channel_messages.insert(0, message_notes)
    if context_notes:
        channel_messages.insert(0, context_notes)
    timestamp = time()
    timestring = timestring = timestamp_to_datetime(timestamp)
    prompt = Prompt(
        header=Message(
            "System", f"Instructions for {MY_BOT_NAME}: {BOT_INSTRUCTIONS}"
        ),
        examples=MY_BOT_EXAMPLE_CONVOS,
        convo=Conversation(
            channel_messages + [Message(f"{timestring} {MY_BOT_NAME}")]
        ),
    )
    rendered = prompt.render()
    mentions = re.findall(r"<@(\d+)>", rendered)
    for mention in mentions:
        user = await bot.fetch_user(mention)
        rendered = rendered.replace(f"<@{mention}>", user.name)
    rendered.replace(
        f"\n<|endoftext|>GlovedBot: **```Reading Previous Messages...```**", ""
    )
    logger.info(rendered)
    logger.info("Prompt Rendered!")
    thinkingText = "**```Creating Response...```** \n"
    await interactive_response.edit(content=thinkingText)
    completions = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "system", "content": rendered}],
        temperature=1.0,
        stream=streamMode,
    )
    if not streamMode:
        logger.info("Stream Mode Off")
        full_reply_content = completions.choices[0].message.content
        reply_content = [
            full_reply_content[i: i + 2000]
            for i in range(0, len(full_reply_content), 2000)
        ]
        await interactive_response.edit(content=reply_content[0])
        for msg in reply_content[1:]:
            interactive_response = await channel.send(msg)
            logger.info("Message character limit reached. Sending chunk.")
    else:
        logger.info("Stream Mode On")
        collected_chunks = []
        collected_messages = []
        full_reply_content_combined = ""
        logger.info("Getting chunks...")
        for chunk in completions:
            await asyncio.sleep(0.4)
            collected_chunks.append(chunk)
            chunk_message = chunk.choices[0].delta
            if chunk_message.content is not None:
                collected_messages.append(chunk_message)
            full_reply_content = "".join([m.content for m in collected_messages])
            if full_reply_content and not full_reply_content.isspace():
                await interactive_response.edit(
                    content=thinkingText + full_reply_content
                )
            if len(full_reply_content) > 1950:
                full_reply_content_combined = full_reply_content
                await interactive_response.edit(content=full_reply_content)
                interactive_response = await channel.send(thinkingText)
                collected_messages = []
                logger.info("Message character limit reached. Started new message.")
        logger.info("full_reply_content: " + full_reply_content)
        await interactive_response.edit(content=full_reply_content)
