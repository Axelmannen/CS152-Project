from ast import If
from enum import Enum, auto
import discord
from discord.ui import View, Select

IN_PROGRESS_EMOJI = "🟨"
COMPLETED_EMOJI = "✅"

class State(Enum):
    INITIAL_CSAM = auto()
    INITIAL_NON_CSAM = auto()
    QUESTION_NON_CSAM = auto()
    REPORT_COMPLETE = auto()
    HASH_MATCH = auto()
    IS_AI_CSAM = auto()
    IS_CSAM = auto()
    REPEATING_USER = auto()
    
INTERACTIVE_STATES = {
    State.HASH_MATCH: {
        "prompt": "Does this reported CSAM match an existing CSAM hash?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.IS_AI_CSAM: {
        "prompt": "Is this AI-generated CSAM?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.IS_CSAM: {
        "prompt": "Does this content qualify as CSAM?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.REPEATING_USER: {
        "prompt": "Has the user repeatedly falsely reported non-CSAM content as CSAM?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.QUESTION_NON_CSAM: {
        "prompt": "Does this message violate our policies?",
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
                f"**Details**: {report.followup_response or '-'}\n"
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
            await self.set_state(State.HASH_MATCH)
        elif state == State.INITIAL_NON_CSAM:
            await self.set_state(State.QUESTION_NON_CSAM)
        elif state == State.REPORT_COMPLETE:
            await self.thread_parent_message.clear_reaction(IN_PROGRESS_EMOJI)
            await self.thread_parent_message.add_reaction(COMPLETED_EMOJI)
            await self.thread.send("✅ Report complete.")
            await self.thread.edit(archived=True, locked=True)
        else:
            raise ValueError(f"Invalid state: {state}")


    async def handle_selection(self, interaction: discord.Interaction, picked: str):
        await interaction.response.defer()  # defer the response to prevent timeout

        if self.state == State.HASH_MATCH:
            if picked == "yes":
                await self.set_state(State.IS_AI_CSAM)
            elif picked == "no":
                await self.set_state(State.IS_CSAM)

        elif self.state == State.IS_AI_CSAM:
            if picked == "yes":
                await self.thread.send("Removing content and all that.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "no":
                await self.thread.send("Removing content and all that.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.IS_CSAM:
            if picked == "yes":
                await self.thread.send("Reported for CSAM.")
                await self.set_state(State.IS_AI_CSAM)
            elif picked == "no":
                await self.thread.send("Reported for non-CSAM.")
                await self.set_state(State.REPEATING_USER)

        elif self.state == State.REPEATING_USER:
            if picked == "yes":
                await self.thread.send("Sending warning to user.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "no":
                await self.thread.send("User will not be warned.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.QUESTION_NON_CSAM:
            if picked == "yes":
                await self.thread.send("Removing content.")
                await self.set_state(State.REPORT_COMPLETE)
            elif picked == "no":
                await self.thread.send("Okay, no action taken.")
                await self.set_state(State.REPORT_COMPLETE)
        
        