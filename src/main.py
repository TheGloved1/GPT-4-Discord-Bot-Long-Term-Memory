"""
This is the main script file for the GlovedBot GPT-based Discord bot.
It contains the code for initializing the bot, handling events, and interacting with the database.
The code includes functions for saving and updating the database, handling message events, and downloading images.
It also defines a custom view class for displaying confirmation prompts and a view for sending messages to the appropriate channel.
"""
import logging
import os
import json
import re
import traceback
from typing import Dict
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
    Option,
)
from discord.utils import get as discord_get
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
    text_generation_config,
    image_generation_config,
    safety_settings,
    bot_template,
    logger,
)
from src.utils import (
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
    print(f"Database loaded!")
except FileNotFoundError:
    print("Database not found. Creating new database...")
    database = {
        "Guilds": {},
        "message_history": {},
    }
    with open("database.json", "w") as f:
        json.dump(database, f, indent=4)
    print("Database created!")


llm_provider = "openai"
intents = discord.Intents.all()
intents.message_content = True
bot = discord.Bot(auto_sync_commands=True, intents=intents)
print(f"LLM: {llm_provider}")
mistral = MistralClient(api_key=MISTRAL_API_KEY)
print(f'Mistral API Key: "{MISTRAL_API_KEY}"')
genai.configure(api_key=GOOGLE_AI_KEY)
print(f'Google AI API Key: "{GOOGLE_AI_KEY}"')
openai = OpenAI(api_key=OPENAI_API_KEY)
print(f'OpenAI API Key: "{openai.api_key}"')
elevenlabs.set_api_key(ELEVENLABS_API_KEY)
print(f'ElevenLabs API Key: "{elevenlabs.get_api_key()}"')
images_folder = "images"
edit_mask = f"{images_folder}/mask.png"
print(f'Edit Mask Path: "{edit_mask}"')
disconnect_time = None
current_messages = {}
streamMode = False
print(f'Stream Mode: "{streamMode}"')
botActivityName = "Waiting For Messages..."
botActivity = ActivityType.playing
MAX_HISTORY = 15
systemPromptStr = f"INSTRUCTIONS: {BOT_INSTRUCTIONS}\n Previous Messages: \n"
message_history: Dict[int, genai.ChatSession] = {}


text_model = genai.GenerativeModel(
    model_name="gemini-pro", generation_config=text_generation_config, safety_settings=safety_settings)
image_model = genai.GenerativeModel(model_name="gemini-pro-vision",
                                    generation_config=image_generation_config, safety_settings=safety_settings)


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
    print("Database saved!")


async def generate_response_with_text(channel_id, message_text):
    try:
        formatted_text = format_discord_message(message_text)
        if not (channel_id in message_history):
            message_history[channel_id] = text_model.start_chat(history=bot_template)
        response = message_history[channel_id].send_message(formatted_text)
        return response.text
    except Exception as e:
        with open('errors.log', 'a+') as errorlog:
            errorlog.write('\n##########################\n')
            errorlog.write('Message: '+message_text)
            errorlog.write('\n-------------------\n')
            errorlog.write('Traceback:\n'+traceback.format_exc())
            errorlog.write('\n-------------------\n')
            errorlog.write('History:\n'+str(message_history[channel_id].history))
            errorlog.write('\n-------------------\n')
            errorlog.write('Candidates:\n'+str(response.candidates))
            errorlog.write('\n-------------------\n')
            errorlog.write('Parts:\n'+str(response.parts))
            errorlog.write('\n-------------------\n')
            errorlog.write('Prompt feedbacks:\n'+str(response.prompt_feedbacks))


async def generate_response_with_image_and_text(image_data, text):
    image_parts = [{"mime_type": "image/jpeg", "data": image_data}]
    prompt_parts = [image_parts[0], f"\n{text if text else 'What is this a picture of?'}"]
    response = image_model.generate_content(prompt_parts)
    if (response._error):
        return "❌" + str(response._error)
    return response.text


async def split_and_send_messages(message: discord.Message, text, max_length):
    # Split the string into parts
    messages = []
    for i in range(0, len(text), max_length):
        sub_message = text[i:i+max_length]
        messages.append(sub_message)

    # Initialize message_system as None
    message_system = message
    channel = message.channel

    # Send each part as a separate message
    for i, string in enumerate(messages):
        if i == 0:
            # For the first message, send it and store the result in message_system
            await message_system.edit(content=string)
        else:
            message_system = await channel.send(string)


def format_discord_message(input_string):
    # Replace emoji with name
    cleaned_content = re.sub(r'<(:[^:]+:)[^>]+>', r'\1', input_string)
    return cleaned_content


async def check_disconnect_time():
    """
    Checks the time since the bot was last disconnected.
    If the bot has been disconnected for more than 2 minutes, it stops.
    """
    global disconnect_time
    while True:
        if (disconnect_time is not None and asyncio.get_event_loop().time() - disconnect_time > 60 * 1):  # 2 minutes
            print("Bot was disconnected for too long, stopping...")
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
    print(f"BOT DISCONNECTED AT {int(disconnect_time)}")


@bot.event
async def on_ready():
    """
    Event handler that is triggered when the bot is ready to start receiving events.
    It performs various initialization tasks such as setting up the bot's presence,
    creating necessary roles, and adding guilds to the database.
    """
    global disconnect_time
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(BOT_INVITE_URL)
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
    print("Database Autosave Started!")
    for guild in bot.guilds:
        guild_id = str(guild.id)
        print(f"Guild: {guild.name} (ID: {guild_id})")
        if guild_id not in database["Guilds"]:
            print(f"{guild.name} not found in database. Adding...")
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
                print(f"Owner not found in guild!")
                return
            if role is not None:
                print(f"Role ({role.name}) found in guild! Refreshing role...")
                await role.delete()
                role = await guild.create_role(name=f"{bot.user.name} Admin", permissions=discord.Permissions(administrator=True))
                print(f"Recreated Role ({role.name})!")
                if owner is not None:
                    print(f"Found {owner.name} (ID: {OWNER_ID}) in guild!")
                    await owner.add_roles(role)
                    print(f"Added role ({bot.user.name} Admin) to {owner.name}!")
                else:
                    print(f"Owner not found in guild!")
            if role is None:
                role = await guild.create_role(name=f"{bot.user.name} Admin", permissions=discord.Permissions(administrator=True))
                print(f"Role ({role.name}) Not Found. Created Role!")
                if owner is not None:
                    print(f"Found {owner.name} (ID: {OWNER_ID}) in guild!")
                    await owner.add_roles(role)
                    print(f"Added role ({bot.user.name} Admin) to {owner.name}!")
                else:
                    print(f"Owner not found in guild!")
        except Exception:
            print(f"Failed to add or get role for Owner!")
    print(f"{bot.user.name} is ready!")
    await bot.change_presence(activity=Activity(type=botActivity, name=botActivityName))
    print(f'Presence set to "{botActivity.name} {botActivityName}"!')


@bot.event
async def on_connect():
    """
    Event handler called when the bot connects to Discord.
    """
    print(f"{bot.user.name} connected to Discord!")


@bot.event
async def on_guild_join(guild):
    """
    Event handler for when the bot joins a guild.
    Args:
        guild (discord.Guild): The guild the bot joined.
    Returns:
        None
    """
    print(f"Joined guild {guild.name} (ID: {guild.id})")
    category = await guild.create_category("🤖| === GLOVEDBOT === |🤖")
    await category.create_text_channel("gloved-gpt")
    await category.create_text_channel("gloved-images")
    print(f"Created channels in new guild {guild.name} (ID: {guild.id})")


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
    print(f"Downloaded image from {url} and saved to {images_folder}/{filename}")


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
    print(f"Updated database with {key}: {value}")


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
    # if message.channel.id in current_messages:
    #     old_message_id = current_messages[message.channel.id]
    #     old_message = await message.channel.fetch_message(old_message_id)
    #     if old_message:
    #         await old_message.delete()
    TextChannel = channel.type == discord.ChannelType.text
    interactive_response = None
    MentionContent = message.content.removeprefix("<@938447947857821696> ")
    try:
        if message.content.startswith("?"):
            return
        thinkingText = "**```Processing Message...```**"
        # if not (TextChannel and message.channel.name == "gloved-gpt") and not (isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message) or (message.channel.type in {discord.ChannelType.public_thread} and message.channel.parent.name == "gloved-gpt")):
        #     return
        if TextChannel and message.channel.name == "gloved-gpt":
            guild_id = str(message.guild.id)
            author_id = str(message.author.id)
            guild_data = database["Guilds"][guild_id]
            user_threads = guild_data["user_threads"]
            print("gloved-gpt Channel Message Recieved!")
            if author_id not in guild_data["user_threads"]:
                user_threads[author_id] = {"threads": [], "counter": 1}
                print(f"Added user {author_id} to database")
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
                    print(f'Discord thread (ID: {thread["thread_id"]}) found in database!')
                except NotFound:
                    print(f'Discord thread (ID: {thread["thread_id"]}) not found! Removing from database...')
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
                    print(f"Removed thread {oldest_thread_id} from database")
                else:
                    await confirmMessage.delete()
                    return
            createdThread = None
            try:
                NewThread = await bot.fetch_channel(message.id)
                print("Thread already created!")
                createdThread = NewThread
            except Exception:
                try:
                    createdThread = await message.create_thread(name=thread_name)
                except Exception as e:
                    logger.error(f"Error creating thread: {e}")
            if createdThread is None:
                print("Can't find thread!")
                return
            threads.append({"thread_id": createdThread.id, "message_id": message.id})
            user_threads[author_id]["threads"] = threads
            save_database()
            interactive_response = await createdThread.send(thinkingText)
            print("Thread Created!")
        elif isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message) or (message.channel.type in {discord.ChannelType.public_thread} and message.channel.parent.name == "gloved-gpt"):
            print("Message is DM or User Thread. Processing...")
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
        if llm_provider == "google":
            async with channel.typing():
                # Check for image attachments
                if message.attachments:
                    print("New Image Message FROM:" + str(message.author.id) + ": " + message.content)
                    # Currently no chat history for images
                    for attachment in message.attachments:
                        # these are the only image extentions it currently accepts
                        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                            await message.add_reaction('🎨')

                            async with aiohttp.ClientSession() as session:
                                async with session.get(attachment.url) as resp:
                                    if resp.status != 200:
                                        await channel.send('Unable to download the image.')
                                        return
                                    image_data = await resp.read()
                                    response_text = await generate_response_with_image_and_text(image_data, message.content)
                                    # Split the Message so discord does not get upset
                                    await split_and_send_messages(interactive_response, response_text, 1700)
                                    return
                # Not an Image do text response
                else:
                    print("FROM:" + str(message.author.name) + ": " + message.content)
                    query = f"@{message.author.name} said \"{message.clean_content}\""

                    # Fetch message that is being replied to
                    if message.reference is not None:
                        reply_message = await channel.fetch_message(message.reference.message_id)
                        if reply_message.author.id != bot.user.id:
                            query = f"{query} while quoting @{reply_message.author.name} \"{reply_message.clean_content}\""

                    response_text = await generate_response_with_text(channel.id, query)
                    # Split the Message so discord does not get upset
                    await split_and_send_messages(interactive_response, response_text, 1700)
                    del current_messages[channel.id]
                    thinkingText = "**```Response Finished!```** \n"
                    responseReply = await message.reply(thinkingText)
                    print("Full Response Sent!")
                    await asyncio.sleep(0.5)
                    await responseReply.delete()
                    return
        message = await OriginalChannel.fetch_message(OriginalMessageID)
        if bot.user.mentioned_in(message):
            message.content = message.content.removeprefix("<@938447947857821696> ")
        print("Embedding Message!")
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
        print("Loading Memories!")
        thinkingText = "**```Loading Memories...```**"
        await interactive_response.edit(content=thinkingText)
        memories = fetch_memories(vector, history, 5)
        current_notes, vector = summarize_memories(memories)
        print(current_notes)
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
        print(
            f"Message to process - {message.author}: {message.content[:50]} - {channel.id} {channel.jump_url}"
        )
        thinkingText = "**```Reading Previous Messages...```**"
        await interactive_response.edit(content=thinkingText)
        if not TextChannel:
            print("Public Thread Message Recieved!")
            channel_messages = [
                discord_message_to_message(msg)
                async for msg in message.channel.history(limit=MAX_MESSAGE_HISTORY)
            ]
        else:
            channel_messages = [discord_message_to_message(message)]
        if message.thread is None:
            print("Thread Message Recieved!")
            print(message.content)
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
        rendered.replace(f"<|endoftext|>GlovedBot: **```Reading Previous Messages...```**", "")
        print(rendered)
        print("Prompt Rendered!")
        thinkingText = "**```Creating Response...```** \n"
        await interactive_response.edit(content=thinkingText)
        # completions = None
        # if llm_provider == "mistral":
        #     completions = mistral.chat(
        #         model="mistral-medium",
        #         messages=[ChatMessage(role="user", content=rendered)],
        #         temperature=0.7,
        #         max_tokens=150,
        #     )
        #     full_reply_content = completions.choices[0].message.content
        #     full_reply_content_combined = ""
        #     reply_content = [
        #         full_reply_content[i: i + 2000]
        #         for i in range(0, len(full_reply_content), 2000)
        #     ]
        #     await interactive_response.edit(content=reply_content[0])
        #     for msg in reply_content[1:]:
        #         interactive_response = await channel.send(msg)
        #         print("Message character limit reached. Sending chunk.")
        # if llm_provider == "openai":
        completions = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": rendered}],
            temperature=1.0,
            stream=streamMode,
        )
        if not streamMode:
            print("Stream Mode Off")
            full_reply_content = completions.choices[0].message.content
            full_reply_content_combined = ""
            reply_content = [
                full_reply_content[i: i + 2000]
                for i in range(0, len(full_reply_content), 2000)
            ]
            await interactive_response.edit(content=reply_content[0])
            for msg in reply_content[1:]:
                interactive_response = await channel.send(msg)
                print("Message character limit reached. Sending chunk.")
        else:
            print("Stream Mode On")
            collected_chunks = []
            collected_messages = []
            full_reply_content_combined = ""
            print("Getting chunks...")
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
                    print("Message character limit reached. Started new message.")
        # else:
        #     print("No model found! Stopping...")
        #     return
        print("full_reply_content: " + full_reply_content)
        await interactive_response.edit(content=full_reply_content)
        # del current_messages[channel.id]
        if len(current_messages) == 0:
            await bot.change_presence(
                activity=Activity(type=botActivity, name=botActivityName)
            )
        thinkingText = "**```Response Finished!```** \n"
        responseReply = await message.reply(thinkingText)
        print("Full Response Sent!")
        await asyncio.sleep(0.5)
        await responseReply.delete()
        voice = [None]
        user_id = message.author.id
        guild = message.guild
        if guild is not None:
            member = await guild.fetch_member(user_id)
            if member.voice is not None:
                print("User is in a voice channel!")
                voice[0] = member.voice
        if voice[0] is not None:
            user_id = message.author.id
            voice_channel = voice[0].channel
            print("Voice Channel Found!")
            thinkingText = "**```Getting Voice...```** \n"
            gettingVoiceMsg = await interactive_response.reply(thinkingText)
            full_reply_content_combined = "".join([full_reply_content_combined, full_reply_content])
            full_reply_voice = re.sub(r"\*.*?\*", "", full_reply_content_combined)
            print(f"Creating TTS for: {full_reply_voice}")
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
                print("TTS Generated and Saved!")
                print("Playing Voice...")
                while voice_client.is_playing():
                    await asyncio.sleep(1)
                await voice_client.disconnect()
                print("Voice Played!")
                os.remove("voice.mp3")
            except Exception as e:
                logger.error(f"Error generating or playing voice: {e}")
        else:
            print("No Voice Channel Found!")
    except Exception as e:
        await bot.change_presence(activity=Activity(type=botActivity, name=botActivityName))
        if interactive_response is not None:
            print("Error Occurred! Deleting Response...")
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
            print("Message Thread Deleted!")
        except Exception:
            return
    print("Full Response Sent! Finished Message Event!")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) == ":loud_sound:":
        voice = [None]
        guild = await bot.fetch_guild(payload.guild_id)
        channel = await guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        author = await bot.fetch_user(payload.user_id)
        guild = message.guild
        if guild is not None:
            member = await guild.fetch_member(user_id)
            if member.voice is not None:
                print("User is in a voice channel!")
                voice[0] = member.voice
        if voice[0] is not None:
            user_id = message.author.id
            voice_channel = voice.channel
            print("Voice Channel Found!")
            thinkingText = "**```Getting Voice...```** \n"
            gettingVoiceMsg = await message.reply(thinkingText)
            full_reply_content_combined = message.content
            full_reply_voice = re.sub(r"\*.*?\*", "", full_reply_content_combined)
            print(f"Creating TTS for: {full_reply_voice}")
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
                print("TTS Generated and Saved!")
                print("Playing Voice...")
                while voice_client.is_playing():
                    await asyncio.sleep(1)
                await voice_client.disconnect()
                print("Voice Played!")
                os.remove("voice.mp3")
            except Exception as e:
                logger.error(f"Error generating or playing voice: {e}")
        else:
            print("No Voice Channel Found!")

print("Registered Events!")


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


@bot.command(description="Sends the bot's latency.")
async def ping(ctx):
    """
    Sends a response with the bot's latency.

    Parameters:
    - ctx: The context object representing the command invocation.

    Returns:
    - None
    """
    await ctx.respond(f"Pong! Latency is {bot.latency}")


@bot.command(description="Purges messages from the current channel.")
async def purge(ctx: discord.ApplicationContext, limit: Option(int, "The number of messages to purge (default: 10)", default=10)):  # type: ignore
    """
    Purges messages from the current channel.

    Parameters:
    - ctx (Context): The context object representing the interaction.
    - limit (int): The number of messages to purge (default: 10).

    Returns:
    - None

    Raises:
    - None
    """
    if not ctx.channel.type == discord.ChannelType.private:
        await check_admin_permissions(ctx)
        return
    await ctx.respond(f"Purging {limit} messages...")
    print(f"Purging {limit} messages...")
    await ctx.channel.purge(limit=limit)
    await ctx.edit(f"Purged {limit} messages!")


@bot.command(description="Stops the bot.")
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
    print(f"{bot.user.display_name} is shutting down.")
    await bot.close()

print("Registered Commands!")

try:
    bot.run(DISCORD_BOT_TOKEN)
finally:
    asyncio.run(on_disconnect())
