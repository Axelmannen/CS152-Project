import discord
import os
import json
import logging
from report import Report, ReasonDropdownView, State  # Added State import
from mod_report import ModReport
from google import genai
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEY")

MODERATION_PROMPT = """You are a moderation assistant for an online social platform. Your task is to review the content of a user post and classify it into one of the following harm categories, if applicable. Each category represents a type of violation of the platform's safety guidelines.

Here are the possible harm categories:

Scam or Spam – e.g., phishing links, deceptive schemes, unsolicited promotions.

Bullying or Harassment – e.g., personal attacks, slurs, targeted harassment, hate speech.

Suicide or Self-Injury – e.g., mentions or encouragement of self-harm, suicidal ideation, or promotion of disordered eating.

Selling Restricted Items – e.g., drugs, weapons, counterfeit goods, or other banned products.

False information – e.g., health misinformation, election conspiracy theories, misleading claims.

Nudity or sexual activity (over 18) – e.g., adult sexual content or nudity involving adults.

Nudity or sexual activity (under 18) – e.g., any sexually suggestive content involving minors (even if implied or fictionalized).

If the content violates any of these categories, respond with the category name followed by a brief explanation of why it violates that category. If it does not violate any category, respond with "No violation detected".

Content to review: {content}"""

GROUP_NUM = 23
BEGIN_REPORT_EMOJI = "❗"

# UI Reason Map
REASON_MAP = {
    "1": "Scam or Spam",
    "2": "Bullying or Harassment",
    "3": "Suicide or Self-Injury",
    "4": "Selling Restricted Items",
    "5": "Nudity or Sexual Activity",
    "6": "False Information",
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

        self.client = genai.Client(api_key=API_KEY)

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
            
            # AUTO CLASSIFIER
            report = await self.auto_moderate(message)
        
            # if violation detected, send to mods
            if report:
                await report.send_to_mods()
                await message.channel.send(
                    f"⚠️ This message from {message.author.mention} has been flagged for review.",
                    reference=message
                )

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
                reporting_user = self.get_user(payload.user_id)
                if not reporting_user:
                    reporting_user = await self.fetch_user(payload.user_id)

                report = Report(self)
                report.message = message
                report.reporter = reporting_user
                self.reports[reporting_user.id] = report

                try:
                    await reporting_user.send(
                        content=f"You reacted with ❗ to report the following [message]({message.jump_url}) by *{message.author.name}*:\n"
                        f"```{message.content}```\n"
                        "Please select the reason for reporting this message:",
                        embeds=message.embeds or None,
                        files=[await atch.to_file() for atch in message.attachments],
                    )
                    await reporting_user.send(view=ReasonDropdownView(report))
                except discord.Forbidden:
                    print(f"[Error] Cannot DM user {reporting_user.name}. They likely have DMs disabled.")

    async def get_ai_classification(self, message: discord.Message) -> Optional[str]:
        try:
            content = f"{message.content}\n"
            if message.attachments:
                content += "Attachments: " + ", ".join([a.filename for a in message.attachments])
                
            # get Gemini response
            response = self.client.models.generate_content(
                model='gemini-2.0-flash', 
                contents=[MODERATION_PROMPT.format(content=content)]
            )

            print(response.text)
            
            classification = response.text.strip()
            return classification if classification != "No violation detected" else None
            
        except Exception as e:
            logger.error(f"Error getting AI classification: {e}")
            return None

    async def auto_moderate(self, message: discord.Message) -> Optional[Report]:
        try:
            classification = await self.get_ai_classification(message)
            
            if classification:
                report = Report(self)
                report.message = message
                report.reporter = self.user
                report.state = State.REPORT_COMPLETE
                
                # fleshed out mapping with word stems to match to reason keys
                classification_lower = classification.lower()
                reason_map = {
                    "scam": "1",
                    "spam": "1",
                    "bull": "2",  # bullying, bully
                    "harass": "2",
                    "hate": "2",
                    "suicid": "3",  # suicide, suicidal
                    "self-injury": "3",
                    "self injury": "3",
                    "eating disorder": "3",
                    "selling": "4",
                    "drugs": "4",
                    "weapons": "4",
                    "nud": "5",  # nude, nudity
                    "sexual": "5",
                    "false": "6",
                    "misinform": "6"
                }
                
                for keyword, key in reason_map.items():
                    if keyword in classification_lower:
                        report.reason_key = key
                        break
                
                # fallback to false information
                if not report.reason_key:
                    report.reason_key = "6"
                    
                report.subreason = "Auto-detected by AI"
                report.ai_classification = classification
                return report
                
            return None
            
        except Exception as e:
            logger.error(f"Error in auto moderation: {e}")
            return None

client = ModBot()
client.run(discord_token)
