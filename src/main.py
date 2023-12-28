"""
This is the main script file for the GlovedBot GPT-based Discord bot.
It contains the code for initializing the bot, handling events, and interacting with the database.

The code includes functions for saving and updating the database, handling message events, and downloading images.
It also defines a custom view class for displaying confirmation prompts and a view for sending messages to the appropriate channel.
"""
import io
import os
import json
import re
import aiohttp
import discord
import elevenlabs
from discord import Interaction, Message as DiscordMessage
from discord.utils import get as discord_get
import logging
import asyncio
from uuid import uuid4
from time import time
from openai import OpenAI
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
    
)

from src.utils import (
    logger,
    discord_message_to_message,
    
)

from src import completion
from src import constants
from src.constants import MY_BOT_EXAMPLE_CONVOS, MY_BOT_NAME
from src.memory import (
    gpt3_embedding,
    save_json, 
    load_convo,
    add_notes,
    notes_history,
    fetch_memories,
    summarize_memories,
    timestamp_to_datetime
    
)

logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
    
)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot()
client = OpenAI(api_key=OPENAI_API_KEY)
images_folder = 'images'
edit_mask = f"{images_folder}/mask.png"
elevenlabs.set_api_key("591720b694f78a02acb4b97aae6c3d83")
disconnect_time = None

try:
    with open('database.json', 'r') as f:
        database = json.load(f)
        
    logger.info(f'Database loaded!')
    # logger.info(f'Database: {database}') # FOR DEBUGGING
    
except FileNotFoundError:
    logger.info('Database not found. Creating new database...')
    database = {}
    with open('database.json', 'w') as f:
        json.dump(database, f, indent=4)
        
    logger.info('Database created!')

async def save_database_loop():
    """
    Continuously saves the database to a JSON file every 5 seconds.
    """
    while True:
        with open('database.json', 'w') as f:
            json.dump(database, f, indent=4)
            
        await asyncio.sleep(120)

def save_database():
    """
    Save the database to a JSON file.

    This function saves the contents of the `database` variable to a JSON file named 'database.json'.
    The file is written with an indentation of 4 spaces.

    """
    with open('database.json', 'w') as f:
        json.dump(database, f, indent=4)
        
    logger.info('Database saved!')

@bot.event
async def on_disconnect():
    """
    Function called when the bot disconnects from the server.
    Saves the database and logs a message indicating that the script has stopped.
    """
    save_database()
    bot.disconnect_time = asyncio.get_event_loop().time()
    logger.info(f'Script Stopped!')

@bot.event
async def on_ready():
    """
    Event handler called when the bot is ready to start processing events.
    It logs in the bot user, sets the bot's name and example conversations,
    and starts the database autosave loop.
    """
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    if hasattr(bot, 'disconnect_time') and asyncio.get_event_loop().time() - bot.disconnect_time > 60 * 2:  # 2 minutes
        print("Bot was disconnected for too long, stopping...")
        await bot.close()
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
    logger.info('Database Autosave Started!')
    for guild in bot.guilds:
        logger.info(f'Guild: {guild.name} (ID: {guild.id})')
        try:
            role = discord_get(guild.roles, name=f'{bot.user.name} Admin')
            if role is None:
                role = await guild.create_role(name=f'{bot.user.name} Admin', permissions=discord.Permissions(administrator=True))
                logger.info('Created GlovedBot Admin role!')
                owner = guild.fetch_member(OWNER_ID)
                if owner is not None:
                    logger.info(f'Found {owner.name} (ID: {OWNER_ID}) in guild!')
                    await owner.add_roles(role)
                    logger.info(f'Added role ({bot.user.name} Admin) to {owner.name}!')
                    
                else:
                    logger.info(f'Owner not found in guild!')
                    
            else:
                logger.info(f'Found {bot.user.name} Admin role!')
                
        except Exception as e:
            logger.info(f'Failed to add or get role for Owner!')
    logger.info(f'{bot.user.name} is ready!')
            
@bot.event
async def on_connect():
    logger.info(f"{bot.user.name} connected to Discord!")
    
@bot.event
async def on_guild_join(guild):
    logger.info(f"Joined guild {guild.name} (ID: {guild.id})")
    category = await guild.create_category("🤖| === GLOVEDBOT === |🤖")
    await category.create_text_channel("gloved-gpt")
    await category.create_text_channel("gloved-images")
    logger.info(f"Created channels in guild {guild.name} (ID: {guild.id})")
    
async def download_image(url, images_folder, filename):
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
            
    with open(os.path.join(images_folder, f"{filename}"), 'wb') as f:
        f.write(image_data)
        
def sendMessage(message: DiscordMessage, content: str):
    """
    Sends a message to the appropriate channel based on the type of the message's channel.

    Parameters:
    - message (DiscordMessage): The message object.
    - content (str): The content of the message.

    Returns:
    - The sent message object.
    """
    TextChannel = message.channel.type == discord.ChannelType.text
    if TextChannel:
        return message.reply(content)
    
    else:
        return message.channel.send(content)
    
async def check_admin_permissions(ctx):
    author = ctx.author
    if not author.guild_permissions.administrator:
        await ctx.respond("You don't have permission to perform this action.")
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
    logger.info(f'Updated database with {key}: {value}')

class ConfirmView(discord.ui.View):
    """
    A custom view class for displaying a confirmation prompt with "Confirm" and "Cancel" buttons.
    """

    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
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

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
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
        message (DiscordMessage): The message object.
    """
    if (message.author == bot.user) or message.author.bot or message.author.system: 
        return
    
    OriginalMessage = message
    channel = message.channel
    TextChannel = message.channel.type == discord.ChannelType.text
    PublicThread = message.channel.type == discord.ChannelType.public_thread
    PrivateThread = message.channel.type == discord.ChannelType.private_thread
    MentionsBot = bot.user.mentioned_in(message)
    MentionContent = message.content.removeprefix('<@938447947857821696> ')
    if message.content.startswith('@everyone'):
        return
    
    if message.content == "?resetchannel":
        if not TextChannel:
            return
        
        channel_position = channel.position
        new_channel = await channel.clone(reason="Channel reset")
        await new_channel.edit(position=channel_position)
        await channel.delete(reason="Channel reset by command")
        await new_channel.send("Channel has been reset. Not a trace left, like my last user's dignity.")
        return
    
    try:
        if message.content.startswith('?'):
            return
                
        thinkingText = "**```Processing Message...```**"
        
        # <TODO> Add functionality for adding guild IDs to database.json below that each have their own 'images' and 'user_threads' in them
        
        if (TextChannel and message.channel.name == 'gloved-gpt'):
            logger.info('Message is not in the gloved-gpt channel or is not in a guild. Skipping...')
            if TextChannel and message.channel.name == 'gloved-gpt':
                if message.guild.id not in database:
                    database[message.guild.id] = {'images': {}, 'user_threads': {}, 'overflow_counts': {}}
                    
                guild_data = database[message.guild.id]
                images = guild_data['images']
                user_threads = guild_data['user_threads']
                overflow_counts = guild_data['overflow_counts']
                if 'overflow_counts' not in guild_data:
                    guild_data['overflow_counts'] = {}
                    
                if message.author.id not in overflow_counts:
                    overflow_counts[message.author.id] = 0
                    
                logger.info('gloved-gpt Channel Message Recieved!')
                if message.author.id not in user_threads:
                    user_threads[message.author.id] = []
                    logger.info(f'Added user {message.author.name} to database')
                    thread_name = f"{message.author.name} - 1"
                    
                else:
                    threads = database['user_threads'][message.author.id]
                    thread_name = f"{message.author.name} - {len(threads) + 1}"
                    
                threads = user_threads[message.author.id]
                for thread in threads[:]:
                    try:
                        await message.guild.fetch_channel(thread["thread_id"])
                        
                    except discord.NotFound:
                        logger.info(f'Thread (ID: {thread["thread_id"]}) not found!')
                        threads.remove(thread)
                        
                user_threads[message.author.id] = threads
                await asyncio.sleep(1)
                if (user_thread_count := len(threads)) >= 3:
                    view = ConfirmView()
                    confirmMessage = await message.reply("You have reached the limit of 3 threads. Are you sure you want to archive your oldest thread and create a new one?", view=view)
                    await view.wait()
                    if view.value is True:
                        oldest_thread = threads.pop(0)
                        oldest_thread_id = oldest_thread['thread_id']
                        oldest_message_id = oldest_thread['message_id']
                        oldest_thread_channel = await message.guild.fetch_channel(oldest_thread_id)
                        await oldest_thread_channel.delete()
                        oldest_message = await message.channel.fetch_message(oldest_message_id)
                        await oldest_message.delete()
                        await confirmMessage.delete()
                        overflow_counts[message.author.id] += 1
                        logger.info(f'Removed thread {oldest_thread_id} from database')
                        
                    else:
                        await confirmMessage.delete()
                        return
                    
                else:
                    overflow_counts[message.author.id] = 0
                    
                user_thread_count = len(threads)
                overflow_count = overflow_counts[message.author.id]
                thread_name = f"{message.author.name} - {user_thread_count + 1 + overflow_count}"
                createdThread = await message.create_thread(name=thread_name)
                threads.append({'thread_id': createdThread.id, 'message_id': message.id})
                user_threads[message.author.id] = threads
                save_database()
                interactive_response = await createdThread.send(thinkingText)
                logger.info('Thread Created!')
                
        elif isinstance(message.channel, discord.DMChannel) or (message.channel.type == discord.ChannelType.public_thread and message.channel.parent.name == 'gloved-gpt'):
            logger.info('Message is DM or User Thread. Processing...')
            interactive_response = await channel.send(thinkingText)
            
        else:
            return
        
        message = await channel.fetch_message(message.id)
        logger.info('Embedding Message!')
        vector = gpt3_embedding(message)
        timestamp = time()
        timestring = timestring = timestamp_to_datetime(timestamp)
        user = message.author.name
        extracted_message = '%s: %s - %s' % (user, timestring, MentionContent)
        info = {'speaker': user, 'timestamp': timestamp,'uuid': str(uuid4()), 'vector': vector, 'message': extracted_message, 'timestring': timestring}
        filename = 'log_%s_user' % timestamp
        save_json(f'./src/chat_logs/{filename}.json', info)
        history = load_convo()
        logger.info('Loading Memories!')
        thinkingText = "**```Loading Memories...```**"
        await interactive_response.edit(content = thinkingText)
        memories = fetch_memories(vector, history, 5)
        current_notes, vector = summarize_memories(memories)
        logger.info(current_notes)
        print('-------------------------------------------------------------------------------')
        add_notes(current_notes)
        if len(notes_history) >= 2:
            print(notes_history[-2])
            
        else:
            print("The list does not have enough elements to access the second-to-last element.")
            
        message_notes = Message(user='memories', text=current_notes)
        context_notes = None
        if len(notes_history) >= 2:
            context_notes = Message(user='context', text=notes_history[-2])
            
        else:
            print("The list does not have enough elements create context")
            
        logger.info(
            f"Message to process - {message.author}: {message.content[:50]} - {channel.id} {channel.jump_url}"
            
        )
        thinkingText = "**```Reading Previous Messages...```**"
        await interactive_response.edit(content = thinkingText)
        if not TextChannel:
            logger.info('Public Thread Message Recieved!')
            channel_messages = [
                discord_message_to_message(msg)
                async for msg in message.channel.history(limit=MAX_MESSAGE_HISTORY)
                
            ]
        else:
            channel_messages = [discord_message_to_message(message)]
            
        if message.thread is None:
            logger.info('Thread Message Recieved!')
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
            convo=Conversation(channel_messages + [Message(f"{timestring} {MY_BOT_NAME}")]),
            
        )
        rendered = prompt.render()
        print(rendered)
        logger.info('Prompt Rendered!')
        mentions = re.findall(r'<@(\d+)>', rendered)
        for mention in mentions:
            user = await bot.fetch_user(mention)
            rendered = rendered.replace(f'<@{mention}>', user.name)
            
        thinkingText = "**```Creating Response...```** \n"
        await interactive_response.edit(content = thinkingText)
        completions = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": rendered}],
            stream=True,
            
        )
        collected_chunks = []
        collected_messages = []
        logger.info('Getting chunks...')
        for chunk in completions:
            await asyncio.sleep(.4) # Throttle the loop to avoid rate limits
            collected_chunks.append(chunk)
            chunk_message = chunk.choices[0].delta
            if chunk_message.content is not None:  # Add this check
                collected_messages.append(chunk_message)
                
            full_reply_content = ''.join([m.content for m in collected_messages])
            if full_reply_content and not full_reply_content.isspace():
                await interactive_response.edit(content = thinkingText + full_reply_content)
                
            if len(full_reply_content) > 1950:
                await interactive_response.edit(content = full_reply_content)
                logger.info(full_reply_content)
                interactive_response = await channel.send(thinkingText)
                collected_messages = [] 
                
        thinkingText = "**```Getting Voice...```** \n"
        await interactive_response.edit(content = thinkingText + full_reply_content)
        thinkingText = "**```Response Finished!```** \n"
        logger.info(f'GlovedBot: {full_reply_content}')
        full_reply_voice = re.sub(r'\*.*?\*', '', full_reply_content)
        try:
            audio = elevenlabs.generate(
                text=full_reply_voice,
                voice="Roetpv5aIoWbL37AfGp3",
                model="eleven_multilingual_v2"
                
            )
        except Exception as e:
            logger.exception(e)
        audio_file = io.BytesIO(audio)
        logger.info('TTS Generated and Saved!')
        await interactive_response.edit(content=full_reply_content, file=discord.File(audio_file, filename='reply.mp3'))
        responseReply = await message.reply(thinkingText)
        logger.info('Full Response Sent!')
        await asyncio.sleep(1.5)
        await responseReply.delete()
        
    except Exception as e:
        logger.exception(e)
        
logger.info('Registered Events!')
@bot.slash_command(description="Purges messages from the current channel.")
async def purge(ctx, limit: discord.Option(int, description="The number of messages to purge (default: 100)", default=100)):
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
    if not await check_admin_permissions(ctx):
        return
    await ctx.respond(f'Purging {limit} messages...')
    logger.info(f'Purging {limit} messages...')
    await ctx.channel.purge(limit=limit)
    
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
    
    await ctx.respond(f'{bot.user.display_name} is shutting down.')
    logger.info(f'{bot.user.display_name} is shutting down.')
    await bot.close()
    
@bot.slash_command(description="Generate an image from a prompt.")
async def image(ctx, prompt: discord.Option(str, description="The prompt to generate an image from"), edit: discord.Option(str, description="Enter the ID of message with image (Copy/Paste Message ID)") = None, showfilteredprompt: discord.Option(bool, description="Shows the hidden filtered prompt generated in response") = False):
    """
    Generate an image from a prompt.

    Parameters:
    - ctx: The context object representing the invocation of the command.
    - prompt: The prompt to generate an image from.
    - edit: (Optional) The ID of the message with the image to edit.
    - showfilteredprompt: (Optional) Whether to show the hidden filtered prompt generated in response.

    Returns:
    - None

    Raises:
    - discord.NotFound: If the message with the given ID is not found.
    - ValueError: If the given ID is not a valid integer.

    """
    await ctx.defer()
    author = ctx.author
    channel = ctx.channel
    message = None
    if edit is not None:
        try:
            message_id = int(edit)
            logger.info('Looking for message with ID: ' + str(message_id))
            message = await channel.fetch_message(message_id)
            
        except (discord.NotFound, ValueError) as e:
            await ctx.edit(content = f'Error: {str(e)}')
            logger.exception(e)
            return
        
    if message:
        message_id_str = str(message_id)
        database = database[message.guild.id]
        if message_id_str in database and 'filteredprompt' in database['images'][message_id_str] and 'prompt' in database['images'][message_id_str]:
            original_filtered_prompt = database['images'][message_id_str]['filteredprompt']
            original_prompt = database['images'][message_id_str]['prompt']
            logger.info('Original Filtered Prompt Found!')
            logger.info(f'Original Filtered Prompt: \n{original_filtered_prompt}')
            
        else:
            await ctx.edit(content = 'Error: Original filtered prompt not found')
            return
            
    if message:
        logger.info('Message Found!')
        logger.info('Message ID: ' + str(message_id))
        FilteredMessage = [
                {"role": "system", "content": "You will be given an image prompt to edit, and your job is to edit the prompt to make it loosely comply with OpenAI's safety system filters. Expand upon the original prompt by adding more detail and being more descriptive. Don't go over 3 sentences. If you are provided with two prompts, you must add the second one to the first."}, 
                {"role": "system", "content": f"This is the original prompt to add to: {original_filtered_prompt}"},
                {"role": "user", "content": prompt}
                
            ]
        
    else:
        logger.info('No Reference Message Found. Using Prompt...')
        FilteredMessage = [
                {"role": "system", "content": "You will be given an image generation prompt, and your job is to edit the prompt to make it loosely comply with OpenAI's safety system filters. Expand upon the original prompt by adding more detail and being more descriptive. Don't go over 3 sentences."}, 
                {"role": "user", "content": prompt}
                
            ]
        
    logger.info('Received Image Command. Processing...')
    thinkingText = '**```Filtering Prompt...```**'
    ImageResponse = await ctx.edit(content = thinkingText)
    FilterArgs = client.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=FilteredMessage, 
        stream=False
        
    )
    thinkingText = '**```Generating Image...```**'
    await ctx.edit(content = thinkingText)
    FilteredResponse = FilterArgs.choices[0].message.content
    print(f'Creating Image with filtered Prompt: {FilteredResponse}')
    if message:
        filename = f'{message.id}.png'
        imagePath = os.path.join(images_folder, filename)
        checkImage = os.path.exists(imagePath)
        if message.embeds:
            logger.info('Message Found. Getting local image...')
            image_url = message.embeds[0].image.url
            logger.info('Image URL: ' + image_url)
            if not (checkImage):
                logger.info('Image Not Found. Try generating a new one.')
                await ctx.edit('Error: Image too old! Try generating a new one.', delete_after=10)
                return
                
            else:
                logger.info('Image Found at ' + imagePath)
                
            image = Image.open(imagePath)
            image_rgba = image.convert('RGBA')
            image = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
            image.save(f'{images_folder}/mask.png')
            logger.info('Image Converted to RGBA')
            image_rgba.save(imagePath)
            response = client.images.edit(
                model="dall-e-2",
                image=open(imagePath, "rb"),
                mask=open(edit_mask, "rb"),
                prompt=FilteredResponse,
                size="1024x1024",
                n=1,
                
            )
            logger.info('Image Created! Getting URL...')
            image_url = response.data[0].url
            logger.info('Creating Embed...')
            embed = discord.Embed(
                title=f'Edited an Image',
                description='**Original Prompt:** ' + original_prompt,
                color=discord.Colour.blurple(),
                
            )
            embed.add_field(name='Edit:', value=prompt, inline=True)
            if showfilteredprompt:
                embed.add_field(name='Filtered Prompt', value=FilteredResponse, inline=False)
                
            embed.set_author(name="GlovedBot", icon_url=bot.user.display_avatar.url)
            embed.set_thumbnail(url=bot.user.display_avatar.url)
            embed.set_image(url=image_url)
            embed.set_footer(text=f'Requested by {author.display_name}.', icon_url=ctx.author.display_avatar.url)  # Fix: Added closing parenthesis
            logger.info('Image Embed: ' + embed.to_dict()['image']['url'])
            ImageResponse = await ctx.edit(content=None, embed=embed)
            logger.info('Image Sent! (ID: ' + str(ImageResponse.id) + ')')
            await download_image(image_url, images_folder, f'{ImageResponse.id}.png')
            database['images'][ImageResponse.id] = {
                'prompt': prompt,
                'filteredprompt': FilteredResponse, 
                'image': image_url,
                
            }
            logger.info('Image Saved!')
            save_database()
            
    else: 
        response = client.images.generate(
            model="dall-e-2",
            prompt=FilteredResponse,
            size="1024x1024",
            #quality="standard", # DALL-E 3 
            #style="vivid", # DALL-E 3
            n=1,
            
        )
        print('Image Created! Getting URL...')
        image_url = response.data[0].url
        print('Creating Embed...')
        embed = discord.Embed(
            title=f'Generated an Image',
            description='**Prompt:** ' + prompt,
            color=discord.Colour.blurple(),
            
        )
        if showfilteredprompt:
            embed.add_field(name='Filtered Prompt', value=FilteredResponse, inline=False)
            
        embed.set_author(name="GlovedBot", icon_url=bot.user.display_avatar.url)
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.set_image(url=image_url)
        embed.set_footer(text=f'Requested by {author.display_name}.', icon_url=ctx.author.display_avatar.url)  # Fix: Added closing parenthesis
        logger.info('Image Embed: ' + embed.to_dict()['image']['url'])
        ImageResponse = await ctx.edit(content=None, embed=embed)
        logger.info('Image Sent! (ID: ' + str(ImageResponse.id) + ')')
        await download_image(image_url, images_folder, f'{ImageResponse.id}.png')
        database['images'][ImageResponse.id] = {
            'prompt': prompt,
            'filteredprompt': FilteredResponse, 
            'image': image_url
            
        }
        logger.info('Image Saved!')
        save_database()
        
logger.info('Registered Commands!')
try:
    bot.run(DISCORD_BOT_TOKEN)
    
finally:
    asyncio.run(on_disconnect())
    
