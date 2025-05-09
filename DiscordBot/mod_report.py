from ast import If
from enum import Enum, auto
import discord
from discord.ui import View, Select
import random
import asyncio

IN_PROGRESS_EMOJI = "🟨"
COMPLETED_EMOJI = "✅"

class State(Enum):
    INITIAL_CSAM = auto()
    NON_CSAM_DECIDE_ACTION = auto()
    LAW_ENFORCEMENT_OR_NO = auto()

    INITIAL_NON_CSAM = auto()
    HASH_MATCH = auto()
    CSAM_CONFIRMED = auto()
    IS_AI_CSAM = auto()
    IS_CSAM = auto()

    REPORT_COMPLETE = auto()
    
INTERACTIVE_STATES = {
    State.IS_AI_CSAM: {
        "prompt": "Is this AI-generated CSAM?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.IS_CSAM: {
        "prompt": """Does this content qualify as CSAM?

			Note: CSAM content involves individuals under 18 years old, and includes any of: 
			- Sexual intercourse
			- Bestiality
			- Masturbaation
			- Sadistic or masochistic abuse
			- Lascivious exhibition of the anus, genitals, or pubic area of any person (if unsure, apply Dost Test)""",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.NON_CSAM_DECIDE_ACTION: {
        "prompt": "What action should be taken?",
        "options": [
            discord.SelectOption(label="Remove post and delete user account", value="remove_and_delete_user"),
            discord.SelectOption(label="Remove post and warn user", value="remove_and_warn_user"),
            discord.SelectOption(label="Shadow block post", value="shadow_block_post"),
            discord.SelectOption(label="Restore with note", value="restore_with_note"),
            discord.SelectOption(label="Escalate to specialist team", value="escalate_to_specialist_team"),
            discord.SelectOption(label="Restore post", value="restore"),
        ]
    },
    State.LAW_ENFORCEMENT_OR_NO: {
        "prompt": "Should this post be reported to law enforcement?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    }
}

class ModReport:
    def __init__(self, thread, thread_parent_message, report, reported_user_id, bot_user_id):
        self.thread = thread
        self.thread_parent_message = thread_parent_message
        self.reported_user_id = reported_user_id
        self.bot_user_id = bot_user_id
        self.state = None
        self.latest_bot_message = None
        self.report = report

    @classmethod
    async def create(cls, channel, report, bot_user_id):
        parent_message = await channel.send(
            content=
                f"**NEW REPORT**\n"
                f"**Reason**: {report.REPORT_REASONS[report.reason_key]} → {report.subreason}\n"
                f"**Details**: {', '.join(report.followups) if report.followups else '-'}\n"
                f"**Reporting User**: {report.message.author.name}\n"
                f"**Flag**: {report.flag or 'None'}\n"
                f"**Reported User**: {report.message.author.name}\n"
                f"**Link**: {report.message.jump_url}\n"
                f"**Message**: ```{report.message.content}```\n",
            embeds=report.message.embeds or None,
            files=[atch.to_file() for atch in report.message.attachments]
        )
        thread = await parent_message.create_thread(name=f"Review of {report.message.author.name}'s message")
        mod_report = cls(thread, parent_message, report, report.message.author.id, bot_user_id)
        await mod_report.start_report()
        return mod_report

    async def start_report(self):
        await self.thread_parent_message.add_reaction(IN_PROGRESS_EMOJI)
        if self.report.flag == "CSAM-related":
            await self.set_state(State.INITIAL_CSAM)
        else:
            await self.set_state(State.INITIAL_NON_CSAM)

    async def set_state(self, state):
        self.state = state

        if state in INTERACTIVE_STATES:

            prompt = INTERACTIVE_STATES[self.state]["prompt"]
            options = INTERACTIVE_STATES[self.state]["options"]

            select = Select(
                placeholder="Choose an option...",
                min_values=1,
                max_values=1,
                options=options,
            )
            select.callback = lambda interaction: self.handle_selection(interaction, select.values[0])

            view = View()
            view.add_item(select)

            self.latest_bot_message = await self.thread.send(prompt, view=view)

        # Treating non-interactive states like this allows us to give more 
        # info on what is happening under the hood if we want. 
        elif state == State.INITIAL_CSAM:
            await self.thread.send("Content has been temporarily removed.")
            await self.set_state(State.HASH_MATCH)
        elif state == State.INITIAL_NON_CSAM:
            await self.thread.send("Content has been temporarily shadow banned.")
            await self.set_state(State.NON_CSAM_DECIDE_ACTION)
        elif state == State.REPORT_COMPLETE:
            await self.thread_parent_message.clear_reaction(IN_PROGRESS_EMOJI)
            await self.thread_parent_message.add_reaction(COMPLETED_EMOJI)
            await self.thread.send("Report complete. Archiving thread.")
            await self.thread.edit(archived=True, locked=True)
        elif state == State.HASH_MATCH:
            await self.thread.send("Checking for match with known CSAM hashes...")
            await asyncio.sleep(2)
            if random.randint(0, 1) == 0:
                await self.thread.send("Match found.")
                await self.set_state(State.CSAM_CONFIRMED)
            else:
                await self.thread.send("No match found. Manual review required.")
                await self.set_state(State.IS_CSAM)
        elif state == State.CSAM_CONFIRMED:
            await self.thread.send("Removing post permanently and placing account under monitoring.")
            await self.thread.sent("Running AI-generation detectors...")
            await asyncio.sleep(5)
            await self.thread.sent(f"Text is AI-generated   -   Confidence: {round(random.uniform(0.3, 1.0), 2)}.")
	        await self.thread.sent(f"Image/video is AI-generated   -   Confidence: {round(random.uniform(0.3, 1.0), 2)}.")
	        await self.thread.sent("Human CSAM team investigating...")
	        await asyncio.sleep(5)
            options = ["strong", "medium", "weak"]
            await self.thread.sent(f"Human CSAM team finds {random.choice(options)} behavioral signals of AI.")
	        await self.thread.sent(f"Human CSAM team finds {random.choice(options)} forensics/metadata signals of AI.")
	        await self.thread.sent("Human CSAM team will share detailed investigation report.")
            await self.set_state(State.IS_AI_CSAM)
        else:
            raise ValueError(f"Invalid state: {state}")


    async def handle_selection(self, interaction: discord.Interaction, picked: str):
        await interaction.response.defer()  # defer the response to prevent timeout

        if self.state == State.IS_AI_CSAM:
            if picked == "yes":
                await self.thread.send("Creating CyberTipline report and indicating that the content is likely AI-generated.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "no":
                await self.thread.send("Hashing CSAM content and adding it to internal database. Reporting user to NCMEC and including the hash.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.IS_CSAM:
            if picked == "yes":
                await self.thread.send("Reported for CSAM.")
                await self.set_state(State.IS_AI_CSAM)
            elif picked == "no":
                await self.thread.send("Restoring temporarily removed content.")
                await self.thread.send("Reporting user will be automatically warned/banned if systematically sending false reports.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.NON_CSAM_DECIDE_ACTION:
            if picked == "remove_and_delete_user":
                await self.thread.send("Removing post and user account.")
                await self.set_state(State.LAW_ENFORCEMENT_OR_NO)
            elif picked == "remove_and_warn_user":
                await self.thread.send("Removing post and warning user.")
                await self.set_state(State.LAW_ENFORCEMENT_OR_NO)
            elif picked == "shadow_block_post":
                await self.thread.send("Permanently shadow blocking post.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "restore_with_note":
                await self.thread.send("Restoring post with a pinned community note.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "restore_with_warning":
                await self.thread.send("Restoring post with a warning.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "escalate_to_specialist_team":
                await self.thread.send("Escalating to specialist team.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "restore":
                await self.thread.send("Restoring post.")
                await self.thread.send("Reporting user will be automatically warned/banned if systematically sending false reports.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.LAW_ENFORCEMENT_OR_NO:
            if picked == "yes":
                await self.thread.send("Reporting user to law enforcement.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "no":
                await self.thread.send("Not reporting user to law enforcement.")
                await self.set_state(State.REPORT_COMPLETE)
