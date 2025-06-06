import discord
import os
import json
import logging
from report import Report, ReasonDropdownView, State  # Added State import
from mod_report import ModReport, IN_PROGRESS_EMOJI
from google import genai  
from typing import Optional
from dotenv import load_dotenv
from hashing import HashDB
from io import BytesIO
from pathlib import Path
import asyncio
import tempfile
import mimetypes

load_dotenv()
API_KEY = os.getenv("API_KEY")

MODERATION_PROMPT = """
You are a content moderation assistant for an online social platform. Your task is to review a user’s post and classify it into one of the harm categories listed below **only if it clearly and unambiguously violates platform guidelines**. Do **not** flag posts based on surface-level keyword matches or if the context suggests the content is:

- Discussing the topic in an academic, educational, or awareness-raising way  
- Using sarcasm, self-deprecation, or quoting others in a reflective or critical context  
- Expressing personal experience or mental health challenges **without** promoting harm  
- Speculative, artistic, or literary in nature without explicit harmful intent  

Use common sense and err on the side of **"No violation detected"** for borderline or unclear cases.

### Harm categories:

- **Scam or Spam** – e.g., phishing links, fraudulent schemes, unsolicited commercial promotions.
- **Bullying or Harassment** – e.g., slurs, targeted personal attacks, or content clearly intended to hurt, threaten, or demean someone.
- **Suicide or Self-Injury** – e.g., explicit encouragement or glamorization of self-harm, suicidal ideation, or disordered eating.
- **Selling Restricted Items** – e.g., promoting or facilitating the sale of drugs, weapons, counterfeit goods, etc.
- **False Information** – e.g., demonstrably false claims about public health, elections, or science, presented as fact.
- **Nudity or Sexual Activity (over 18)** – e.g., graphic sexual content or explicit adult material.
- **Nudity or Sexual Activity (under 18)** – e.g., any sexually suggestive or exploitative content involving minors (real, fictional, or implied).

Instructions:  
If the content violates any of these categories, respond with the category name followed by a brief explanation of why it violates that category. If it does not violate any category, respond with "No violation detected".

Content to review: {content}
"""


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

        self.hash_db = HashDB.load()

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
            elif payload.emoji.name == IN_PROGRESS_EMOJI:
                user = self.get_user(payload.user_id)
                if not user:
                    user = await self.fetch_user(payload.user_id)

                message = await channel.fetch_message(payload.message_id)

                for mod_report in self.mod_reports.values():
                    if mod_report.thread_parent_message.id == message.id:
                        if mod_report.in_progress_by:
                            await channel.send(f"🔒 Already in progress by <@{mod_report.in_progress_by}>.")
                            await message.remove_reaction(IN_PROGRESS_EMOJI, user)
                        else:
                            mod_report.in_progress_by = user.id
                            await channel.send(f"✅ Claimed by <@{user.id}>.")
                        break

    async def on_raw_reaction_remove(self, payload):
        if payload.user_id == self.user.id:
            return

        if payload.emoji.name != IN_PROGRESS_EMOJI:
            return

        # Get the channel
        channel = self.get_channel(payload.channel_id)
        if not channel:
            channel = await self.fetch_channel(payload.channel_id)

        if not isinstance(channel, discord.TextChannel) or channel.name != f"group-{GROUP_NUM}-mod":
            # Ignore reactions in channels other than the mod channel
            return

        message = await channel.fetch_message(payload.message_id)

        for mod_report in self.mod_reports.values():
            if mod_report.thread_parent_message.id == message.id:
                if mod_report.in_progress_by == payload.user_id:
                    mod_report.in_progress_by = None
                    await mod_report.thread.edit(locked=False)
                    await channel.send(f"🔓 Unclaimed by <@{payload.user_id}>.")
                    if mod_report.lock_message:
                        try:
                            await mod_report.lock_message.delete()
                        except discord.NotFound:
                            pass
                        mod_report.lock_message = None
                else:
                    user = self.get_user(payload.user_id)
                    if not user:
                        user = await self.fetch_user(payload.user_id)
                    await message.remove_reaction(IN_PROGRESS_EMOJI, user)
                break
    
    async def get_ai_classification(self, message: discord.Message) -> Optional[str]:
        text_content = message.content.strip()
        parts: list[object] = []

        for atch in message.attachments:
            # 1) detect if this is an image
            mime = (
                atch.content_type
                or mimetypes.guess_type(atch.filename)[0]
                or ""
            )

            if mime.startswith("image/"):
                # 2) read into memory
                data = await atch.read()
                # 3) dump to a real temp file with the right extension
                suffix = Path(atch.filename).suffix or ""
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(data)
                    tmp.flush()
                    tmp_path = tmp.name

                try:
                    # 4) upload the temp file path—SDK will infer mime from .suffix
                    uploaded = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.client.files.upload(file=tmp_path)
                    )
                finally:
                    # 5) clean up
                    os.unlink(tmp_path)

                parts.extend([uploaded, "\n\n"])
            else:
                # not an image → just note it
                parts.append(f"Attachment filename: {atch.filename}\n\n")

        # 6) add your moderation prompt
        parts.append(MODERATION_PROMPT.format(content=text_content))

        # 7) call Gemini
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=parts
            )
        )

        classification = response.text.strip()
        return classification if classification != "No violation detected" else None

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
                
                # fallback to no violation detected
                if not report.reason_key:
                    logger.info("No violation detected by AI. No moderation report generated.")
                    return None
                    
                report.subreason = "Auto-detected by AI"
                report.ai_classification = classification

                # mark as suspected CSAM to direct to the CSAM flow
                if report.reason_key == "5" and "under 18" in classification_lower:
                    print("Detected suspected CSAM content.")
                    report.is_suspected_csam = True
                    report.priority = 2
                    report.csam_related = True
                
                return report
                
            return None
            
        except Exception as e:
            logger.error(f"Error in auto moderation: {e}")
            return None

client = ModBot()
client.run(discord_token)
