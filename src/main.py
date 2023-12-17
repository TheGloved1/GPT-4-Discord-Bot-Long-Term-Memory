import discord
from discord import Button, ButtonStyle, Embed, Interaction, Message as DiscordMessage
from discord.ui import Button, View
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


def sendMessage(message: DiscordMessage, content: str):
    TextChannel = message.channel.type == discord.ChannelType.text
    if TextChannel:
        return message.reply(content)
    else:
        return message.channel.send(content)

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
    if (message.author == bot.user) or message.author.bot: 
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
        
        # <\>TODO: Before archiving the old thread, send a message to the user saying with a button to Confirm or Cancel the archiving</>
        
        if message.channel.name == 'gloved-gpt':
            logger.info('gloved-gpt Channel Message Recieved!')

            thread_name = f"{message.author.display_name}'s Chat"
            # Check if there's already a thread with the same name
            for thread in message.channel.threads:
                if thread.name == thread_name:
                    view = ConfirmView()
                    confirmMessage = await message.reply("Are you sure you want to archive your old thread and create a new one?", view=view)
                    await view.wait()  # Wait for the user to click a button

                    if view.value is True:
                        # Archive the old thread
                        await thread.archive()
                        await confirmMessage.delete()
                    else:
                        await confirmMessage.delete()
                        return

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
        if PublicThread and message.channel.parent.name == 'gloved-gpt':
            logger.info('Public Thread Message Recieved!')
            channel_messages = [
                discord_message_to_message(msg)
                async for msg in channel.history(limit=MAX_MESSAGE_HISTORY)
            ]
        else:
            channel_messages = []
        # Check if the event message is not in a thread
        if message.thread is None:
            logger.info('Thread Message Recieved!')
            # Convert the event message and add it to channel_messages
            channel_messages.append(discord_message_to_message(message))
            logger.info(message)

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
        logger.info(thinkingText)
        responseReply = await message.reply(thinkingText)
        await asyncio.sleep(1.5)
        await responseReply.delete()

    except Exception as e:
        logger.exception(e)
        await channel.send(e)
logger.info('Registered Events!')

## Commands
# Restart Command
@bot.slash_command(guild=MY_GUILD, name="restart", description="Restarts the bot.")
async def restart(ctx):
    author = ctx.author
    # Check if the author has the necessary permissions to restart the bot
    if author.guild_permissions.administrator:
        await ctx.respond("Restarting...")
        # Here you can add any code you want to run before restarting
        # For example, you might want to log that the bot is restarting
        logger.info('Bot is restarting...')
        # Finally, stop the bot
        await bot.close()
    else:
        await ctx.channel.send("You don't have permission to restart the bot.")
        
# Image Command
@bot.slash_command(description="Generate an image from a prompt.")
async def image(ctx, prompt: discord.Option(str, description="The prompt to generate an image from."), showfilteredprompt: discord.Option(bool, description="Shows the hidden filtered prompt generated in response.") = False):
    author = ctx.author
    channel = ctx.channel
    logger.info('Received Image Command. Making Image...')
    # await asyncio.sleep(1)
    thinkingText = '**```Filtering Prompt...```**'
    await ctx.respond(thinkingText)
    FilterArgs = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "system", "content": "You will be given an image generation prompt, and your job is to remake the prompt to make it follow OpenAI's safety system filters and not get blocked. Expand upon the original prompt by adding more detail and being more descriptive. Don't go over 3 sentences."}, {"role": "user", "content": prompt}], stream=False)

    thinkingText = '**```Generating Image...```**'
    await ctx.edit(content = thinkingText)
    FilteredResponse = FilterArgs.choices[0].message.content
    print(f'Creating Image with filtered Prompt: {FilteredResponse}')
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

    await ctx.edit(content=None, embed=embed)
    logger.info('Image Sent!')

logger.info('Registered Commands!')
## Run Client
bot.run(DISCORD_BOT_TOKEN)