"""
This is the main script file for the GlovedBot GPT-based Discord bot.
It contains the code for initializing the bot, handling events, and interacting with the database.
The code includes functions for saving and updating the database, handling message events, and downloading images.
It also defines a custom view class for displaying confirmation prompts and a view for sending messages to the appropriate channel.
"""
import os
import json
import re
import aiohttp
import discord
import elevenlabs
from discord import (
    Interaction,
    Message as DiscordMessage,
    FFmpegPCMAudio,
    ActivityType,
    Activity,
    NotFound,
)
from discord.utils import get as discord_get
import logging
import asyncio
from uuid import uuid4
from time import time
from openai import OpenAI
import google.generativeai as genai
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from PIL import Image
from src.base import Message, Conversation, Prompt
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_NAME,
    DISCORD_BOT_TOKEN,
    EXAMPLE_CONVOS,
    MAX_MESSAGE_HISTORY,
    OPENAI_API_KEY,
    OWNER_ID,
    ELEVENLABS_API_KEY,
    MY_BOT_EXAMPLE_CONVOS,
    MY_BOT_NAME,
    BOT_INVITE_URL,
    GOOGLE_AI_KEY,
    MISTRAL_API_KEY,
    # llm_model,
)
from src.utils import (
    logger,
    discord_message_to_message,
)
from src import completion
from src.memory import (
    gpt3_embedding,
    save_json,
    load_convo,
    add_notes,
    notes_history,
    fetch_memories,
    summarize_memories,
    timestamp_to_datetime,
)

logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
)

try:
    with open("database.json", "r") as f:
        database = json.load(f)
    logger.info(f"Database loaded!")
except FileNotFoundError:
    logger.info("Database not found. Creating new database...")
    database = {
        "Guilds": {}
    }
    with open("database.json", "w") as f:
        json.dump(database, f, indent=4)
    logger.info("Database created!")


llm_model = "mistral"
intents = discord.Intents.all()
intents.message_content = True
bot = discord.Bot(auto_sync_commands=True, intents=intents)
logger.info(f"LLM: {llm_model}")
mistral = MistralClient(api_key=MISTRAL_API_KEY)
logger.info(f'Mistral API Key: "{MISTRAL_API_KEY}"')
genai.configure(api_key=GOOGLE_AI_KEY)
logger.info(f'Google AI API Key: "{GOOGLE_AI_KEY}"')
openai = OpenAI(api_key=OPENAI_API_KEY)
logger.info(f'OpenAI API Key: "{openai.api_key}"')
elevenlabs.set_api_key(ELEVENLABS_API_KEY)
logger.info(f'ElevenLabs API Key: "{elevenlabs.get_api_key()}"')
images_folder = "images"
edit_mask = f"{images_folder}/mask.png"
logger.info(f'Edit Mask Path: "{edit_mask}"')
disconnect_time = None
current_messages = {}
streamMode = False
logger.info(f'Stream Mode: "{streamMode}"')
botActivityName = "Waiting For Messages..."
botActivity = ActivityType.playing
MAX_HISTORY = 15
systemPromptStr = f"INSTRUCTIONS: {BOT_INSTRUCTIONS}\n Previous Messages: \n"

# ---------------------------------------------Database-------------------------------------------------


async def save_database_loop():
    """
    Continuously saves the database to a JSON file every 2 minutes.
    """
    while True:
        with open("database.json", "w") as f:
            json.dump(database, f, indent=4)
        await asyncio.sleep(120)


def save_database():
    """
    Save the database to a JSON file.
    This function saves the contents of the `database` variable to a JSON file named 'database.json'.
    The file is written with an indentation of 4 spaces.
    """
    with open("database.json", "w") as f:
        json.dump(database, f, indent=4)
    logger.info("Database saved!")


async def check_disconnect_time():
    """
    Checks the time since the bot was last disconnected.
    If the bot has been disconnected for more than 2 minutes, it stops.
    """
    global disconnect_time
    while True:
        if (disconnect_time is not None and asyncio.get_event_loop().time() - disconnect_time > 60 * 1):  # 2 minutes
            logger.info("Bot was disconnected for too long, stopping...")
            await bot.close()
            break
        await asyncio.sleep(60)


def clean_discord_message(input_string):
    """
    Cleans a Discord message by removing any text between < and > brackets.

    Args:
        input_string (str): The input string to be cleaned.

    Returns:
        str: The cleaned string with text between brackets removed.
    """
    bracket_pattern = re.compile(r'<[^>]+>')
    cleaned_content = bracket_pattern.sub('', input_string)
    return cleaned_content


@bot.event
async def on_disconnect():
    """
    Saves the database, records the disconnect time, and logs the event.
    """
    global disconnect_time
    save_database()
    disconnect_time = asyncio.get_event_loop().time()
    logger.info(f"BOT DISCONNECTED AT {int(disconnect_time)}")


@bot.event
async def on_ready():
    """
    Event handler that is triggered when the bot is ready to start receiving events.
    It performs various initialization tasks such as setting up the bot's presence,
    creating necessary roles, and adding guilds to the database.
    """
    global disconnect_time
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(BOT_INVITE_URL)
    bot.loop.create_task(check_disconnect_time())
    completion.MY_BOT_NAME = bot.user.name
    completion.MY_BOT_EXAMPLE_CONVOS = []
    for c in EXAMPLE_CONVOS:
        messages = []
        for m in c.messages:
            if m.user == BOT_NAME:
                messages.append(Message(user=bot.user.name, text=m.text))
            else:
                messages.append(m)
        completion.MY_BOT_EXAMPLE_CONVOS.append(Conversation(messages=messages))
    bot.loop.create_task(save_database_loop())
    logger.info("Database Autosave Started!")
    for guild in bot.guilds:
        guild_id = str(guild.id)
        logger.info(f"Guild: {guild.name} (ID: {guild_id})")
        if guild_id not in database["Guilds"]:
            logger.info(f"{guild.name} not found in database. Adding...")
            database["Guilds"][guild_id] = {
                "name": guild.name,
                "images": {},
                "user_threads": {},
            }
        if ("name" not in database["Guilds"][guild_id]) or (guild.name not in database["Guilds"][guild_id]["name"]):
            database["Guilds"][guild_id]["name"] = guild.name
        try:
            role = discord_get(guild.roles, name=f"{bot.user.name} Admin")
            owner = await guild.fetch_member(OWNER_ID)
            if owner is None:
                logger.info(f"Owner not found in guild!")
                return
            if role is not None:
                logger.info(f"Role ({role.name}) found in guild! Refreshing role...")
                await role.delete()
                role = await guild.create_role(name=f"{bot.user.name} Admin", permissions=discord.Permissions(administrator=True))
                logger.info(f"Recreated Role ({role.name})!")
                if owner is not None:
                    logger.info(f"Found {owner.name} (ID: {OWNER_ID}) in guild!")
                    await owner.add_roles(role)
                    logger.info(f"Added role ({bot.user.name} Admin) to {owner.name}!")
                else:
                    logger.info(f"Owner not found in guild!")
            if role is None:
                role = await guild.create_role(name=f"{bot.user.name} Admin", permissions=discord.Permissions(administrator=True))
                logger.info(f"Role ({role.name}) Not Found. Created Role!")
                if owner is not None:
                    logger.info(f"Found {owner.name} (ID: {OWNER_ID}) in guild!")
                    await owner.add_roles(role)
                    logger.info(f"Added role ({bot.user.name} Admin) to {owner.name}!")
                else:
                    logger.info(f"Owner not found in guild!")
        except Exception:
            logger.info(f"Failed to add or get role for Owner!")
    logger.info(f"{bot.user.name} is ready!")
    await bot.change_presence(activity=Activity(type=botActivity, name=botActivityName))
    logger.info(f'Presence set to "{botActivity.name} {botActivityName}"!')


@bot.event
async def on_connect():
    """
    Event handler called when the bot connects to Discord.
    """
    logger.info(f"{bot.user.name} connected to Discord!")


@bot.event
async def on_guild_join(guild):
    """
    Event handler for when the bot joins a guild.
    Args:
        guild (discord.Guild): The guild the bot joined.
    Returns:
        None
    """
    logger.info(f"Joined guild {guild.name} (ID: {guild.id})")
    category = await guild.create_category("🤖| === GLOVEDBOT === |🤖")
    await category.create_text_channel("gloved-gpt")
    await category.create_text_channel("gloved-images")
    logger.info(f"Created channels in new guild {guild.name} (ID: {guild.id})")


async def download_image(url: str, images_folder: str, filename: str):
    """
    Downloads an image from the given URL and saves it to the specified folder with the given filename.
    Parameters:
    - url (str): The URL of the image to download.
    - images_folder (str): The folder where the downloaded image will be saved.
    - filename (str): The name of the downloaded image file.
    Returns:
    None
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            image_data = await resp.read()
    with open(os.path.join(images_folder, f"{filename}"), "wb") as f:
        f.write(image_data)
    logger.info(f"Downloaded image from {url} and saved to {images_folder}/{filename}")


async def check_admin_permissions(ctx):
    """
    Check if the author of the context has administrator permissions in the guild.

    Parameters:
    - ctx: The context object representing the current command invocation.

    Returns:
    - True if the author has administrator permissions.
    - False otherwise.
    """
    author = ctx.author
    if not author.guild_permissions.administrator:
        await ctx.respond("You don't have permission to do this!")
        return False
    return True


async def messageInNamedChannel(message: DiscordMessage, name: str):
    """
    Checks if a message is in a channel with a specific name.
    Parameters:
    - message (DiscordMessage): The message to check.
    - name (str): The name of the channel to check against.
    Returns:
    - bool: True if the message is in the channel with the specified name, False otherwise.
    """
    if message.channel.name:
        if message.channel.name == name:
            return True
    else:
        return False


async def updateDatabase(key, value):
    """
    Updates the database with the given key-value pair.
    Args:
        key: The key to update in the database.
        value: The value to associate with the key.
    Returns:
        None
    """
    database[key] = value
    await save_database()
    logger.info(f"Updated database with {key}: {value}")


def checkVoice(message: DiscordMessage, prefix: str):
    """
    Checks if a message contains the voice prefix.
    Args:
        message (DiscordMessage): The message to check.
    Returns:
        bool: True if the message contains the voice prefix, False otherwise.
    """
    return message.content.startswith(prefix)


class ConfirmView(discord.ui.View):
    """
    A custom view class for displaying a confirmation prompt with "Confirm" and "Cancel" buttons.
    """

    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: Interaction):
        """
        Callback function for the "Confirm" button.
        Args:
            button (discord.ui.Button): The button that was clicked.
            interaction (Interaction): The interaction object representing the user's interaction with the button.
        Returns:
            None
        """
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, button: discord.ui.Button, interaction: Interaction):
        """
        Callback function for the "Cancel" button.
        Args:
            button (discord.ui.Button): The button that was clicked.
            interaction (Interaction): The interaction object representing the user's interaction with the button.
        Returns:
            None
        """
        self.value = False
        self.stop()


@bot.event
async def on_message(message: DiscordMessage):
    """
    Event handler for when a message is received.

    Args:
    - message (DiscordMessage): The message object.

    Returns:
    - None
    """
    global current_messages
    old_message_id = None
    old_message = None
    OriginalMessage = message
    OriginalMessageID = int(OriginalMessage.id)
    OriginalChannel = OriginalMessage.channel
    OriginalChannelID = int(OriginalChannel.id)
    if (message.author == bot.user) or message.author.bot or message.author.system or message.mention_everyone:
        return
    channel = OriginalChannel
    if message.channel.id in current_messages:
        old_message_id = current_messages[message.channel.id]
        old_message = await message.channel.fetch_message(old_message_id)
        if old_message:
            await old_message.delete()
    TextChannel = channel.type == discord.ChannelType.text
    interactive_response = None
    MentionContent = message.content.removeprefix("<@938447947857821696> ")
    try:
        if message.content.startswith("?"):
            return
        thinkingText = "**```Processing Message...```**"
        if TextChannel and message.channel.name == "gloved-gpt":
            guild_id = str(message.guild.id)
            author_id = str(message.author.id)
            guild_data = database["Guilds"][guild_id]
            user_threads = guild_data["user_threads"]
            logger.info("gloved-gpt Channel Message Recieved!")
            if author_id not in guild_data["user_threads"]:
                user_threads[author_id] = {"threads": [], "counter": 1}
                logger.info(f"Added user {author_id} to database")
                thread_name = f"{message.author.name} - 1"
            else:
                user_data = guild_data["user_threads"][author_id]
                threads = user_data["threads"]
                counter = user_data["counter"]
                thread_number = (counter % 3) + 1
                thread_name = f"{message.author.name} - {thread_number}"
                user_data["counter"] += 1
                if user_data["counter"] > 3:
                    user_data["counter"] = 1
            threads = user_threads[author_id]["threads"]
            for thread in threads:
                try:
                    await bot.fetch_channel(thread["thread_id"])
                    logger.info(f'Discord thread (ID: {thread["thread_id"]}) found in database!')
                except NotFound:
                    logger.info(f'Discord thread (ID: {thread["thread_id"]}) not found! Removing from database...')
                    threads.remove(thread)
            user_threads[author_id]["threads"] = threads
            if len(threads) >= 3:
                view = ConfirmView()
                confirmMessage = await message.reply(
                    "You have reached the limit of 3 threads. Are you sure you want to archive your oldest thread and create a new one?",
                    view=view,
                )
                await view.wait()
                if view.value is True:
                    oldest_thread = threads.pop(0)
                    oldest_thread_id = oldest_thread["thread_id"]
                    oldest_message_id = oldest_thread["message_id"]
                    oldest_thread_channel = await message.guild.fetch_channel(oldest_thread_id)
                    await oldest_thread_channel.delete()
                    oldest_message = await message.channel.fetch_message(oldest_message_id)
                    await oldest_message.delete()
                    await confirmMessage.delete()
                    logger.info(f"Removed thread {oldest_thread_id} from database")
                else:
                    await confirmMessage.delete()
                    return
            createdThread = await message.create_thread(name=thread_name)
            threads.append({"thread_id": createdThread.id, "message_id": message.id})
            user_threads[author_id]["threads"] = threads
            save_database()
            interactive_response = await createdThread.send(thinkingText)
            logger.info("Thread Created!")
        elif isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message) or (message.channel.type in {discord.ChannelType.public_thread} and message.channel.parent.name == "gloved-gpt"):
            logger.info("Message is DM or User Thread. Processing...")
            interactive_response = await channel.send(thinkingText)
        else:
            return
        try:
            message = await channel.fetch_message(message.id)
        except NotFound:
            interactive_response.delete()
            return
        channel = await bot.fetch_channel(interactive_response.channel.id)
        current_messages[channel.id] = str(message.id)
        current_messages[message.channel.id] = interactive_response.id
        await bot.change_presence(
            activity=Activity(type=ActivityType.playing, name=f"Generating messages...")
        )
        message = await OriginalChannel.fetch_message(OriginalMessageID)
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
        if not TextChannel:
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
        rendered.replace(f"\n<|endoftext|>GlovedBot: **```Reading Previous Messages...```**", "")
        logger.info(rendered)
        logger.info("Prompt Rendered!")
        thinkingText = "**```Creating Response...```** \n"
        await interactive_response.edit(content=thinkingText)
        completions = None
        if llm_model != "mistral":
            return
        completions = mistral.chat(
            model="mistral-medium",
            messages=[ChatMessage(role="user", content=rendered)],
            temperature=0.7,
            max_tokens=150,
        )
        full_reply_content = completions.choices[0].message.content
        full_reply_content_combined = ""
        reply_content = [
            full_reply_content[i: i + 2000]
            for i in range(0, len(full_reply_content), 2000)
        ]
        await interactive_response.edit(content=reply_content[0])
        for msg in reply_content[1:]:
            interactive_response = await channel.send(msg)
            logger.info("Message character limit reached. Sending chunk.")
        if llm_model == "openai":
            completions = openai.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "system", "content": rendered}],
                temperature=1.0,
                stream=streamMode,
            )
            if not streamMode:
                logger.info("Stream Mode Off")
                full_reply_content = completions.choices[0].message.content
                full_reply_content_combined = ""
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
                        await interactive_response.edit(content=thinkingText + full_reply_content)
                    if len(full_reply_content) > 1950:
                        full_reply_content_combined = full_reply_content
                        await interactive_response.edit(content=full_reply_content)
                        interactive_response = await channel.send(thinkingText)
                        collected_messages = []
                        logger.info("Message character limit reached. Started new message.")
        else:
            logger.info("No model found! Stopping...")
            return
        logger.info("full_reply_content: " + full_reply_content)
        await interactive_response.edit(content=full_reply_content)
        del current_messages[channel.id]
        if len(current_messages) == 0:
            await bot.change_presence(
                activity=Activity(type=botActivity, name=botActivityName)
            )
        thinkingText = "**```Response Finished!```** \n"
        responseReply = await message.reply(thinkingText)
        logger.info("Full Response Sent!")
        await asyncio.sleep(0.5)
        await responseReply.delete()
        voice = [None]
        user_id = message.author.id
        guild = message.guild
        if guild is not None:
            member = await guild.fetch_member(user_id)
            if member.voice is not None:
                logger.info("User is in a voice channel!")
                voice[0] = member.voice
        if voice[0] is not None:
            user_id = message.author.id
            voice_channel = voice[0].channel
            logger.info("Voice Channel Found!")
            thinkingText = "**```Getting Voice...```** \n"
            gettingVoiceMsg = await interactive_response.reply(thinkingText)
            full_reply_content_combined = "".join([full_reply_content_combined, full_reply_content])
            full_reply_voice = re.sub(r"\*.*?\*", "", full_reply_content_combined)
            logger.info(f"Creating TTS for: {full_reply_voice}")
            try:
                audio = elevenlabs.generate(
                    text=full_reply_voice,
                    voice="Roetpv5aIoWbL37AfGp3",
                    model="eleven_multilingual_v2",
                )
                await gettingVoiceMsg.delete()
                with open("voice.mp3", "wb") as f:
                    f.write(audio)
                voice_client = await voice_channel.connect()
                await asyncio.sleep(0.5)
                voice_client.play(FFmpegPCMAudio("voice.mp3", options=f'-filter:a "volume=2.0"'))
                logger.info("TTS Generated and Saved!")
                logger.info("Playing Voice...")
                while voice_client.is_playing():
                    await asyncio.sleep(1)
                await voice_client.disconnect()
                logger.info("Voice Played!")
                os.remove("voice.mp3")
            except Exception as e:
                logger.error(f"Error generating or playing voice: {e}")
        else:
            logger.info("No Voice Channel Found!")
    except Exception as e:
        await bot.change_presence(activity=Activity(type=botActivity, name=botActivityName))
        if interactive_response is not None:
            logger.info("Error Occurred! Deleting Response...")
            await interactive_response.delete()
        logger.exception(e)
        await message.reply(f"Error: {str(e)}", delete_after=10)
        if not TextChannel and not message.channel.name == "gloved-gpt":
            return
        try:
            thread = await bot.fetch_channel(OriginalMessage.thread.id)
            message = await thread.fetch_message(OriginalMessage.id)
            await thread.delete()
            await message.delete()
            logger.info("Message Thread Deleted!")
        except Exception:
            return
    logger.info("Full Response Sent! Finished Message Event!")

logger.info("Registered Events!")


@bot.slash_command()
async def resetchannel(ctx):
    """
    Resets the current channel.

    This function clones the current channel, deletes the original channel, and sends a message to the new channel indicating that it has been reset.

    Parameters:
    - ctx: The context object representing the command invocation.

    Returns:
    None
    """
    channel = ctx.channel
    if not channel.type == discord.ChannelType.text:
        return
    channel_position = channel.position
    new_channel = await channel.clone(reason="Channel reset")
    await new_channel.edit(position=channel_position)
    await channel.delete(reason="Channel reset by command")
    await new_channel.send("Channel has been reset. Not a trace left, like my last user's dignity.")
    return


@bot.slash_command(description="Sends the bot's latency.")
async def ping(ctx):
    """
    Sends a response with the bot's latency.

    Parameters:
    - ctx: The context object representing the command invocation.

    Returns:
    - None
    """
    await ctx.respond(f"Pong! Latency is {bot.latency}")


@bot.slash_command(description="Purges messages from the current channel.")
async def purge(ctx, limit: discord.Option(int, description="The number of messages to purge (default: 10)", default=10)):
    """
    Purges messages from the current channel.

    Parameters:
    - ctx (Context): The context object representing the interaction.
    - limit (int): The number of messages to purge (default: 100).

    Returns:
    - None

    Raises:
    - None
    """
    if not ctx.channel.type == discord.ChannelType.private:
        await check_admin_permissions(ctx)
        return
    await ctx.respond(f"Purging {limit} messages...")
    logger.info(f"Purging {limit} messages...")
    await ctx.channel.purge(limit=limit)
    await ctx.edit(f"Purged {limit} messages!")


@bot.slash_command(description="Stops the bot.")
async def shutdown(ctx):
    """
    Stops the bot if the user has administrator permissions in the guild.

    Parameters:
    - ctx (Context): The context object representing the interaction.

    Returns:
    - None

    Raises:
    - None
    """
    if not await check_admin_permissions(ctx):
        return
    await ctx.respond(f"{bot.user.display_name} is shutting down.")
    logger.info(f"{bot.user.display_name} is shutting down.")
    await bot.close()

logger.info("Registered Commands!")

try:
    bot.run(DISCORD_BOT_TOKEN)
finally:
    asyncio.run(on_disconnect())
