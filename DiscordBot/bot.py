import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import Report, ReasonDropdownView
import pdb
from mod_report import ModReport

GROUP_NUM = 23
BEGIN_REPORT_EMOJI = "❗"

# UI Reason Map
REASON_MAP = {
    "1": "Scam or Spam",
    "2": "Bullying or Harassment",
    "3": "Suicide or Self-Injury",
    "4": "Selling Restricted Items",
    "5": "Nudity or Sexual Activity",
    "6": "False Information"
}

# Set up logging to the console
logger = logging.getLogger("discord")
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
handler.setFormatter(logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s"))
logger.addHandler(handler)

# There should be a file called 'tokens.json' inside the same folder as this file
token_path = "tokens.json"
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    tokens = json.load(f)
    discord_token = tokens["discord"]

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

bot.reports = {}
mod_channels = {}
mod_reports = {}

@bot.event
async def on_ready():
    print(f"{bot.user.name} has connected to Discord! It's in these guilds:")
    for guild in bot.guilds:
        print(f" - {guild.name}")
        for channel in guild.text_channels:
            if channel.name == f"group-{GROUP_NUM}-mod":
                mod_channels[guild.id] = channel

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        await message.channel.send("Use the ❗ reaction in the server to start a report.")
    elif message.channel.name == f"group-{GROUP_NUM}":
        mod_channel = mod_channels.get(message.guild.id)
        if mod_channel:
            content = f'User: {message.author.name}\nMessage: "{message.content}"\nMessage Link: {message.jump_url}'
            await mod_channel.send(content=content, embeds=message.embeds or None, files=[await a.to_file() for a in message.attachments])

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return

    if isinstance(channel, discord.TextChannel) and channel.name == f"group-{GROUP_NUM}":
        message = await channel.fetch_message(payload.message_id)
        if payload.emoji.name == BEGIN_REPORT_EMOJI:
            user = bot.get_user(payload.user_id)
            if not user:
                return

            report = Report(bot)
            report.message = message
            bot.reports[user.id] = report

            try:
                await user.send(
                    f"You reacted with ❗ to report the following message:\n"
                    f"```{message.author.name}: {message.content}```\n"
                    f"[Click to view message]({message.jump_url})\n\n"
                    "Let's begin the reporting process."
                )
                await user.send("Please select the reason for reporting this message:", view=ReasonDropdownView(report))
            except discord.Forbidden:
                print(f"[Error] Cannot DM user {user.name}. They likely have DMs disabled.")

@bot.command()
async def report(ctx):
    await ctx.send("Please react to the message you'd like to report with ❗")

bot.run(discord_token)
