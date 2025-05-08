# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import Report
import pdb
from mod_report import ModReport

GROUP_NUM = 23
BEGIN_REPORT_EMOJI = "▶️"

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
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens["discord"]


class ModBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        super().__init__(command_prefix=".", intents=intents)
        self.group_num = None
        self.mod_channels = {}  # Map from guild to the mod channel id for that guild
        self.reports = {}  # Map from user IDs to the state of their report
        self.mod_reports = {}  # Map from thread IDs to the state of their report

    async def on_ready(self):
        print(f"{self.user.name} has connected to Discord! It's in these guilds:")
        for guild in self.guilds:
            print(f" - {guild.name}")
        print("Press Ctrl-C to quit.")

        self.group_num = GROUP_NUM

        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f"group-{self.group_num}-mod":
                    self.mod_channels[guild.id] = channel

    async def on_message(self, message):
        """
        This function is called whenever a message is sent in a channel that the bot can see (including DMs).
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel.
        """
        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        # Handle a help message
        if message.content == Report.HELP_KEYWORD:
            reply = "Use the `report` command to begin the reporting process.\n"
            reply += "Use the `cancel` command to cancel the report process.\n"
            await message.channel.send(reply)
            return

        author_id = message.author.id
        responses = []

        # Only respond to messages if they're part of a reporting flow
        if author_id not in self.reports and not message.content.startswith(Report.START_KEYWORD):
            return

        # If we don't currently have an active report for this user, add one
        if author_id not in self.reports:
            self.reports[author_id] = Report(self)

        # Let the report class handle this message; forward all the messages it returns to us
        responses = await self.reports[author_id].handle_message(message)
        for r in responses:
            await message.channel.send(r)

        # If the report is complete or cancelled, remove it from our map
        if self.reports[author_id].report_complete():
            self.reports.pop(author_id)

    async def handle_channel_message(self, message):

        if message.channel.name == f"group-{self.group_num}-mod":
            await message.add_reaction(BEGIN_REPORT_EMOJI)

        elif message.channel.name == f"group-{self.group_num}":
            # Ignore messages from the bot (although bot should never post here)
            if message.author.id == self.user.id:
                return

            # Forward the message to the mod channel
            # Sadly does not work with stickers or gifs without clumsy workarounds, hence we also provide the link.
            mod_channel = self.mod_channels[message.guild.id]
            content = f'\nUser: {message.author.name}\nMessage: "{message.content}"\nMessage Link: {message.jump_url}'
            await mod_channel.send(
                content=content,
                embeds=message.embeds or None,
                files=[await atch.to_file() for atch in message.attachments]
            )

            # scores = self.eval_text(message.content)
            # await mod_channel.send(self.code_format(scores))

    async def on_raw_reaction_add(self, payload):

        # Never react to own reactions
        if payload.user_id == self.user.id:
            return

        # Only respond in mod channel
        channel = self.get_channel(payload.channel_id)
        if channel.name == f"group-{self.group_num}-mod":
            message = await channel.fetch_message(payload.message_id)
            
            if payload.emoji.name == BEGIN_REPORT_EMOJI:
                thread = await message.create_thread(name="Report of " + message.author.name)
                self.mod_reports[thread.id] = ModReport(thread, message, message.author.id, self.user.id)
                await self.mod_reports[thread.id].start_report()

        elif isinstance(channel, discord.Thread):
            await self.mod_reports[channel.id].handle_reaction(payload.message_id, payload.emoji.name, payload.user_id)


    def eval_text(self, message):
        """'
        TODO: Once you know how you want to evaluate messages in your channel,
        insert your code here! This will primarily be used in Milestone 3.
        """
        return message

    def code_format(self, text):
        """'
        TODO: Once you know how you want to show that a message has been
        evaluated, insert your code here for formatting the string to be
        shown in the mod channel.
        """
        return "Evaluated: '" + text + "'"


client = ModBot()
client.run(discord_token)
