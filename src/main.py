from asyncio import tasks
from typing import Optional
import discord
from discord import BotIntegration, Message as DiscordMessage, ButtonStyle, app_commands
from discord import Interaction
from discord.ui import Modal, View, Button, TextInput, Select
import logging
import asyncio
import json
import os
from uuid import uuid4
from time import time
from datetime import datetime

import openai
from pytest import param
from src.base import Message, Conversation, Prompt
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
    DISCORD_CLIENT_ID,
    EXAMPLE_CONVOS,
    MAX_MESSAGE_HISTORY,
    SECONDS_DELAY_RECEIVING_MSG,
    ALLOWED_CHANNEL_NAMES,
    OPENAI_API_KEY,
    MY_GUILD,
)

from src.utils import (
    logger,
    should_block,
    is_last_message_stale,
    discord_message_to_message,
)
from src import completion
from src.completion import MY_BOT_EXAMPLE_CONVOS, MY_BOT_NAME, generate_completion_response, process_response
from src.memory import (
    gpt3_embedding,
    gpt3_response_embedding, 
    save_json, 
    load_convo,
    add_notes,
    notes_history,
    fetch_memories,
    summarize_memories,
    load_memory,
    load_context,
    open_file,
    gpt3_completion,
    timestamp_to_datetime
)


logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
)

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        # A CommandTree is a special type that holds all the application command
        # state required to make it work. This is a separate class because it
        # allows all the extra state to be opt-in.
        # Whenever you want to work with application commands, your tree is used
        # to store and work with them.
        # Note: When using commands.Bot instead of discord.Client, the bot will
        # maintain its own tree instead.
        self.tree = app_commands.CommandTree(self)

    # In this basic example, we just synchronize the app commands to one guild.
    # Instead of specifying a guild to every command, we copy over our global commands instead.
    # By doing so, we don't have to wait up to an hour until they are shown to the end-user.
    async def setup_hook(self):
        # This copies the global commands over to your guild.
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)
        logger.info(f"Successfully synced commands to (Guild: {MY_GUILD.id})")


intents = discord.Intents.default()
intents.message_content = True
client = MyClient(intents=intents)
openai.api_key = OPENAI_API_KEY

# Ready
@client.event
async def on_ready():
    logger.info(f'Logged in as {client.user} (ID: {client.user.id})')
    completion.MY_BOT_NAME = client.user.name
    completion.MY_BOT_EXAMPLE_CONVOS = []
    for c in EXAMPLE_CONVOS:
        messages = []
        for m in c.messages:
            if m.user == "GlovedBot":
                messages.append(Message(user=client.user.name, text=m.text))
            else:
                messages.append(m)
        completion.MY_BOT_EXAMPLE_CONVOS.append(Conversation(messages=messages))
        

def sendMessage(message: DiscordMessage, content: str):
    TextChannel = message.channel.type == discord.ChannelType.text
    if TextChannel:
        return message.reply(content)
    else:
        return message.channel.send(content)

class MyButton(Button):
    def __init__(self):
        super().__init__(label='Start a new thread', style=ButtonStyle.blurple, custom_id='start_thread')
class MyView(View):
    def __init__(self):
        super().__init__()
        self.add_item(MyButton())

## Events
@client.event
async def on_interaction(interaction: discord.Interaction):
    SlashCommand = interaction.type == discord.InteractionType.application_command
    if SlashCommand:
        logger.info('Command deferred successfully!')
        await interaction.followup.send(content=f'You said: {interaction.data["options"][0]["value"]}')

@client.event            
async def on_message(message: DiscordMessage):

    if (message.author == client.user) or message.author.bot: 
        return
    
    channel = message.channel
    TextChannel = message.channel.type == discord.ChannelType.text
    PublicThread = message.channel.type == discord.ChannelType.public_thread
    PrivateThread = message.channel.type == discord.ChannelType.private_thread
    MentionsBot = client.user.mentioned_in(message)
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
        if message.channel.name == 'gloved-gpt':
            logger.info('Gloved GPT Channel Message Recieved!')

            thread_name = f"{message.author.display_name}'s Chat"
            # Check if there's already a thread with the same name
            for thread in message.channel.threads:
                if thread.name == thread_name:
                    # Archive the old thread
                    await thread.archive(reason="User created a new thread.")

            # Create the new thread
            thread = await message.create_thread(name=thread_name)
            interactive_response = await thread.send(thinkingText)
        elif PublicThread and channel.parent.name == 'gloved-gpt': 
            interactive_response = await sendMessage(message, thinkingText)
        
        
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
        channel_messages = [
            discord_message_to_message(message)
            async for message in channel.history(limit=MAX_MESSAGE_HISTORY)
        ]
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

        thinkingText = "**```Creating Response...```** \n"
        await interactive_response.edit(content = thinkingText)

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": rendered}],
            stream=True
        )

        collected_chunks = []
        collected_messages = []

        # Fetch chunks from the stream
        logger.info('Getting chunks...')
        for chunk in response:
            await asyncio.sleep(.5) # Throttle the loop to avoid rate limits
            collected_chunks.append(chunk)
            chunk_message = chunk['choices'][0]['delta']
            collected_messages.append(chunk_message)
            full_reply_content = ''.join([m.get('content', '') for m in collected_messages])
            if full_reply_content and not full_reply_content.isspace():
                await interactive_response.edit(content = thinkingText + full_reply_content)
            if len(full_reply_content) > 1950:
                await interactive_response.edit(content = full_reply_content)
                logger.info(full_reply_content)
                interactive_response = await channel.send(thinkingText)
                collected_messages = [] 
        
        await interactive_response.edit(content = full_reply_content)
        logger.info(full_reply_content)
        thinkingText = "**```Response Finished!```** \n"
        responseReply = await message.reply(thinkingText)
        await asyncio.sleep(1.5)
        await responseReply.delete()

        # # send response
        # await process_response(
        #     channel=message.channel, user=message.author, response_data=response_data, message=message
        # )
    except Exception as e:
        logger.exception(e)
        await interactive_response.edit(content = e)
logger.info('Registered Events!')

## Commands
@client.tree.command(description='Starts a new chat with GlovedBot.')
# @app_commands.describe(
#     prompt='The message you want to send to GlovedBot.',
# )
async def chat(interaction: discord.Interaction, prompt: str):
    """Starts a new chat with GlovedBot."""
    await interaction.response.defer(thinking=True)
    logger.info('Recieved Chat Command!')
    await interaction.response.edit_message(content=f'You said: {prompt}')

logger.info('Registered Commands!')


# @bot.command()
# async def start_thread(ctx):
#     if ctx.channel.name == 'gloved-gpt':
#         await ctx.send("Click the button below to start a new chat with GlovedBot.", view=MyView())
        
# @bot.command()
# async def image(ctx, *, arg):
#     # Start typing; this will continue until the bot sends another message
#     logger.info('Recieved Image Command. Making Image...')
#     thinkingText = '**```Processing Response...```**'
#     replyMessage = await ctx.reply(content=thinkingText)
#     try:

#         thinkingText = '**```Filtering Prompt...```**'
#         await replyMessage.edit(content = thinkingText)
#         FilterArgs = openai.ChatCompletion.create(
#             model="gpt-4",
#             messages=[{"role": "system", "content": "You will be given an image generation prompt, and your job is to remake the prompt to make it follow OpenAI's safety system filters and not get blocked, and expand upon the original prompt by adding more detail, but keeping the original intention of the prompt while also making the images look realistic unless otherwise specified."},
#                       {"role": "user", "content": arg}],
#             stream=False
#         )

#         thinkingText = '**```Generating Image```**'
#         await replyMessage.edit(content = thinkingText)
#         FilteredResponse = FilterArgs['choices'][0]['message']['content']
#         logger.info(FilteredResponse)

#         response = openai.Image.create(
#             model="dall-e-3",
#             prompt=FilteredResponse,
#             size="1024x1024",
#             quality="standard",
#             n=1,
#         )

#         image_url = response.data[0].url
#         logger.info(image_url)
#         # Create an embed object for the Discord message
#         embed = discord.Embed(
#             title='Generated Image',
#             description='**Prompt:** ' + arg,
#             color=discord.Color.blue()
#         )

#         embed.set_image(url=image_url)
#         embed.set_footer(text=f'Requested by {ctx.author.display_name}.')

#         # Send the embed and stop typing
#         thinkingText=''
#         await replyMessage.delete()
#         await ctx.reply(embed=embed)
#     except Exception as e:
#         # If something goes wrong, notify and stop typing
#         await replyMessage.delete()
#         await ctx.reply(f'Uh oh, shit hit the proverbial fan: {str(e)}')


client.run(DISCORD_BOT_TOKEN)