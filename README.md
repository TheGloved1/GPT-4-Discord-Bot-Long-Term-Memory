# GlovedBot

GlovedBot is a GPT-based Discord bot. It uses the OpenAI API to generate responses to user messages.

## Features

- Send various requests to openai with user messages
- Creates threads for users
- Generate images
- Interacts with a locally created database
- Sends messages to the appropriate channels
- Automatically creates required channels on join
- Works in DMs

## Credits
### Long-term Memory
https://github.com/reality-comes/GPT-4-Discord-Bot-Long-Term-Memory

## Usage

Make sure to have Nodemon installed on your system.
```
git clone https://github.com/TheGloved1/gpt-bot.git

cd gpt-bot && nodemon -e py,txt --ignore logs.txt --exec bot-env/bin/python -m src.main > logs.txt 2>&1
```

