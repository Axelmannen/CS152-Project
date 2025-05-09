import discord
import os
import json
import logging
from report import Report, ReasonDropdownView
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


class ModBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        super().__init__(intents=intents)

        self.group_num = None
        self.content_channels = {}  # Map from guild to the content channel id for that guild
        self.mod_channels = {}  # Map from guild to the mod channel id for that guild
        self.reports = {}  # Map from user IDs to the state of their report
        self.mod_reports = {}  # Map from thread IDs to the state of their report

    async def on_ready(self):
        print(f"{self.user.name} has connected to Discord! It's in these guilds:")
        for guild in self.guilds:
            print(f" - {guild.name}")
        print("Press Ctrl-C to quit.")

        self.group_num = GROUP_NUM

        # Find the content and mod channel in each guild
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f"group-{self.group_num}":
                    self.content_channels[guild.id] = channel
                if channel.name == f"group-{self.group_num}-mod":
                    self.mod_channels[guild.id] = channel

    async def on_message(self, message):
        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        if message.author.id == self.user.id:
            return
        await message.channel.send("Use the ❗ reaction in the server to start a report.")

    async def handle_channel_message(self, message):
        if message.channel.name == f"group-{self.group_num}":
            # Ignore messages from the bot (although bot should never post here)
            if message.author.id == self.user.id:
                return
            # TODO: Automatic scanning of messages
            
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.user.id:
            return

        # Get channel from cache if available, otherwise fetch from API
        channel = self.get_channel(payload.channel_id)
        if not channel:
            channel = await self.fetch_channel(payload.channel_id)

        if isinstance(channel, discord.TextChannel) and channel.name == f"group-{GROUP_NUM}":
            message = await channel.fetch_message(payload.message_id)
            if payload.emoji.name == BEGIN_REPORT_EMOJI:

                # Clear reaction
                await message.clear_reaction(payload.emoji.name)

                # Get user from cache if available, otherwise fetch from API
                user = self.get_user(payload.user_id)
                if not user:
                    user = await self.fetch_user(payload.user_id)

                report = Report(self)
                report.message = message
                self.reports[user.id] = report

                try:
                    await user.send(
                        content=
                        f"You reacted with ❗ to report the following [message]({message.jump_url}) by *{message.author.name}*:\n"
                        f"```{message.content}```\n"
                        "Please select the reason for reporting this message:",
                        embeds=message.embeds or None,
                        files=[await atch.to_file() for atch in message.attachments],
                    )
                    await user.send(view=ReasonDropdownView(report))
                except discord.Forbidden:
                    print(f"[Error] Cannot DM user {user.name}. They likely have DMs disabled.")

client = ModBot()
client.run(discord_token)
