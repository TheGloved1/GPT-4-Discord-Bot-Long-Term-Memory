from cProfile import label
from calendar import c
import datetime
import io
import json
from os import name
from select import epoll
import signal
import sys
from turtle import title
import aiohttp
import discord
from discord import Button, ButtonStyle, Embed, Interaction, Message as DiscordMessage, Thread
from discord.ui import Button, View, Modal, InputText, Select
import logging
import asyncio
from uuid import uuid4
from time import time


from openai import OpenAI


from src.base import Message, Conversation, Prompt
from src.constants import (
    BOT_INSTRUCTIONS,
    DISCORD_BOT_TOKEN,
    EXAMPLE_CONVOS,
    MAX_MESSAGE_HISTORY,
    OPENAI_API_KEY,
    MY_GUILD,
)
client = OpenAI(api_key=OPENAI_API_KEY)
from src.utils import (
    logger,
    discord_message_to_message,
)
from src import completion
from src.completion import MY_BOT_EXAMPLE_CONVOS, MY_BOT_NAME
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
user_thread_counters = {}
user_threads = {}



try:
    with open('database.json', 'r') as f:
        database = json.load(f)
    logger.info(f'Database loaded!')
    logger.info(f'Database: {database}') ## ONLY FOR DEBUGGING ##
except FileNotFoundError:
    # If the file does not exist, start with an empty database
    database = {}
    with open('database.json', 'w') as f:
        json.dump(database, f)
    logger.info('Database created!')


async def save_database_loop():
    while True:
        with open('database.json', 'w') as f:
            json.dump(database, f)
        await asyncio.sleep(60)  # Wait for 60 seconds
        logger.info('Database saved!')

def save_database():
    with open('database.json', 'w') as f:
        json.dump(database, f)
    logger.info('Database saved!')


# Ready
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    completion.MY_BOT_NAME = bot.user.name
    completion.MY_BOT_EXAMPLE_CONVOS = []
    
    for c in EXAMPLE_CONVOS:
        messages = []
        for m in c.messages:
            if m.user == "GlovedBot":
                messages.append(Message(user=bot.user.name, text=m.text))
            else:
                messages.append(m)
        completion.MY_BOT_EXAMPLE_CONVOS.append(Conversation(messages=messages))
        
    bot.loop.create_task(save_database_loop())
    logger.info('Database autosave loop started!')

        
@bot.event
async def on_disconnect():
    # Code to run before the bot logs out
    logger.info("Bot is disconnecting...")
    # Additional code here

        
def sendMessage(message: DiscordMessage, content: str):
    TextChannel = message.channel.type == discord.ChannelType.text
    if TextChannel:
        return message.reply(content)
    else:
        return message.channel.send(content)

async def messageInNamedChannel(message: DiscordMessage, name: str):
    if message.channel.name:
        if message.channel.name == name:
            return True
    else:
        return False

async def updateDatabase(key, value):
    database[key] = value
    await save_database()
    logger.info(f'Updated database with {key}: {value}')

class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: Interaction):
        self.value = True
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def cancel(self, button: discord.ui.Button, interaction: Interaction):
        self.value = False
        self.stop()
        
@bot.event            
async def on_message(message: DiscordMessage):
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
        
        # Clone the channel
        new_channel = await channel.clone(reason="Channel reset")
        await new_channel.edit(position=channel_position)

        # Delete the original channel
        await channel.delete(reason="Channel reset by command")
        # Send confirmation to the new channel
        await new_channel.send("Channel has been reset. Not a trace left, like my last user's dignity.")
        return

    try:
        
        if message.content.startswith('?'):
            return
        ## Check if message is in a valid channel
        if (TextChannel):
            if not (MentionsBot):
                if not (channel.name == 'gloved-gpt'):
                    return
        thinkingText = "**```Processing Message...```**"

        if TextChannel and message.channel.name == 'gloved-gpt':
            logger.info('gloved-gpt Channel Message Recieved!')

            user_thread_count = len(user_threads.get(message.author.id, []))
            logger.info(f'User: {message.author.name} Threads: {user_thread_count}')
            
            if user_thread_count >= 3:
                view = ConfirmView()
                confirmMessage = await message.reply("You have reached the limit of 3 threads. Are you sure you want to archive your oldest thread and create a new one?", view=view)
                await view.wait()

                if view.value is True:
                    oldest_thread = user_threads[message.author.id].pop(0)
                    await oldest_thread.archive()
                    await confirmMessage.delete()
                    user_thread_counters[message.author.id] = 0
                    
                else:
                    await confirmMessage.delete()
                    return
            thread_name = f"{message.author.name} - {user_thread_count + 1}"
            
            createdThread = await message.create_thread(name=thread_name)
            
            if message.author.id not in user_threads:
                user_threads[message.author.id] = []
                
            user_threads[message.author.id].append(createdThread)
            interactive_response = await createdThread.send(thinkingText)
        message = await channel.fetch_message(message.id)
        # Start off the response message, this'll be the one we keep updating
        print('Embedding Message!')
        # save message as embedding, vectorize
        vector = gpt3_embedding(message)
        timestamp = time()
        timestring = timestring = timestamp_to_datetime(timestamp)
        user = message.author.name
        extracted_message = '%s: %s - %s' % (user, timestring, MentionContent)
        info = {'speaker': user, 'timestamp': timestamp,'uuid': str(uuid4()), 'vector': vector, 'message': extracted_message, 'timestring': timestring}
        filename = 'log_%s_user' % timestamp
        save_json(f'./src/chat_logs/{filename}.json', info)

        # load past conversations
        history = load_convo()

        # fetch memories (histroy + current input)
        print('Loading Memories!')

        thinkingText = "**```Loading Memories...```**"
        await interactive_response.edit(content = thinkingText)

        memories = fetch_memories(vector, history, 5)

        # create notes from memories

        current_notes, vector = summarize_memories(memories)

        print(current_notes)
        print('-------------------------------------------------------------------------------')

        add_notes(current_notes)

        if len(notes_history) >= 2:
            print(notes_history[-2])
        else:
            print("The list does not have enough elements to access the second-to-last element.")


        # create a Message object from the notes
        message_notes = Message(user='memories', text=current_notes)
        
        # create a Message object from the notes_history for context

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
        if PublicThread and message.channel.parent.name == 'gloved-gpt':
            logger.info('Public Thread Message Recieved!')
            channel_messages = [
                discord_message_to_message(msg)
                async for msg in message.thread.history(limit=MAX_MESSAGE_HISTORY)
            ]
        else:
            channel_messages = [discord_message_to_message(message)]
        # Check if the event message is not in a thread
        logger.info(f'Checking if following message is in a thread: {message.content}')
        if message.thread is None:
            logger.info('Thread Message Recieved!')
            # channel_messages.append(discord_message_to_message(message))
            logger.info(message.content)

        channel_messages = [x for x in channel_messages if x is not None]
        channel_messages.reverse()
        channel_messages.insert(0, message_notes)
        if context_notes:
            channel_messages.insert(0, context_notes)

        # generate the response
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

        thinkingText = "**```Creating Response...```** \n"
        await interactive_response.edit(content = thinkingText)

        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": rendered}],
            stream=True,
        )

        collected_chunks = []
        collected_messages = []

        # Fetch chunks from the stream
        logger.info('Getting chunks...')
        for chunk in completion:
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
        
        await interactive_response.edit(content = full_reply_content)
        thinkingText = "**```Response Finished!```** \n"
        logger.info(full_reply_content)
        responseReply = await message.reply(thinkingText)
        await asyncio.sleep(1.5)
        await responseReply.delete()

    except Exception as e:
        logger.exception(e)
        await channel.send(e)
logger.info('Registered Events!')

## Commands
# Restart Command
@bot.slash_command(description="Stops the bot.")
async def shutdown(ctx):
    author = ctx.author
    # Check if the author has the necessary permissions to restart the bot
    if author.guild_permissions.administrator:
        await ctx.respond(f'{bot.user.display_name} is shutting down.')
        # Here you can add any code you want to run before restarting
        # For example, you might want to log that the bot is restarting
        logger.info(f'{bot.user.display_name} is shutting down.')
        # Finally, stop the bot
        await bot.close()
    else:
        await ctx.respond("You don't have permission to stop me. Hehe.")
        
# Image Command
@bot.slash_command(description="Generate an image from a prompt.")
async def image(ctx, prompt: discord.Option(str, description="The prompt to generate an image from."), message_id: discord.Option(str, description="Message with image to edit. (Copy/Paste Message ID)") = None, showfilteredprompt: discord.Option(bool, description="Shows the hidden filtered prompt generated in response.") = False):
    author = ctx.author
    channel = ctx.channel
    if not message_id == DiscordMessage.id:
        await ctx.respond('Please provide a valid message ID.').delete_after(10)
        return
    message = await channel.fetch_message(message_id)
    logger.info('Received Image Command. Making Image...')
    # await asyncio.sleep(1)
    thinkingText = '**```Filtering Prompt...```**'
    ImageResponse = await ctx.respond(thinkingText)
    FilterArgs = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "system", "content": "You will be given an image generation prompt, and your job is to remake the prompt to make it follow OpenAI's safety system filters and not get blocked. Expand upon the original prompt by adding more detail and being more descriptive. Don't go over 3 sentences."}, {"role": "user", "content": prompt}], stream=False)

    thinkingText = '**```Generating Image...```**'
    await ImageResponse.edit(content = thinkingText)
    FilteredResponse = FilterArgs.choices[0].message.content
    print(f'Creating Image with filtered Prompt: {FilteredResponse}')
    
    if message:
        if message.embeds:
            logger.info('Reference Message Found. Using Reference...')
            image_url = message.embeds[0].image.url

            # Download the image
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    image_data = await resp.read()

            # Create a BytesIO object from the image data
            image = io.BytesIO(image_data)

            response = client.images.edit(
                model="dall-e-2",
                image=image,
                mask=None,
                prompt=FilteredResponse,
                size="1024x1024",
                n=1,
            )
            print('Image Created! Getting URL...')
            image_url = response.data[0].url
            # Create an embed object for the Discord message
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

            await message.reply(content=None, embed=embed)
            logger.info('Image Sent!')
    else: 
        logger.info('No Reference Message Found. Using Prompt...')
        response = client.images.generate(
            model="dall-e-3",
            prompt=FilteredResponse,
            size="1024x1024",
            #quality="standard", # DALL-E 3 
            #style="vivid", # DALL-E 3
            n=1,
        )
        
        print('Image Created! Getting URL...')
        image_url = response.data[0].url
        # Create an embed object for the Discord message
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

        await ImageResponse.edit(content=None, embed=embed)
        logger.info('Image Sent!')
    
logger.info('Registered Commands!')
try:
    bot.run(DISCORD_BOT_TOKEN)
finally:
    save_database()
    logger.info(f'Script Stopped!')