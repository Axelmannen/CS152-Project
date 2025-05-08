from enum import Enum, auto
import discord
import re

IN_PROGRESS_EMOJI = "⏳"
COMPLETED_EMOJI = "✅"

class State(Enum):
    REPORT_START = auto()
    TOP_LEVEL_REASON = auto()
    REPORT_COMPLETE = auto()

STATE_PROMPTS_AND_REACTIONS = {
    State.TOP_LEVEL_REASON: {
        "prompt": "Why was this message reported?\n\n 1. Hate Speech\n 2. Bullying\n 3. Other ",
        "reactions": ["1️⃣", "2️⃣", "3️⃣"]
    }
}


class ModReport:
    def __init__(self, thread, parent_message, reported_user_id, bot_user_id):
        self.thread = thread
        self.reported_user_id = reported_user_id
        self.parent_message = parent_message
        self.bot_user_id = bot_user_id

        self.state = State.REPORT_START
        self.awaiting_response = True
        self.latest_bot_message = None

    async def start_report(self):
        await self.set_state(State.TOP_LEVEL_REASON)

    async def set_state(self, state):
        self.state = state

        if state == State.REPORT_COMPLETE:
            await self.parent_message.clear_reaction(IN_PROGRESS_EMOJI)
            await self.parent_message.add_reaction(COMPLETED_EMOJI)
            await self.thread.edit(archived=True, locked=True)
            return

        self.latest_bot_message = await self.thread.send(STATE_PROMPTS_AND_REACTIONS[self.state]["prompt"])
        for reaction in STATE_PROMPTS_AND_REACTIONS[self.state]["reactions"]:
            await self.latest_bot_message.add_reaction(reaction)

    async def handle_reaction(self, message_id, reaction_emoji, user_id):
        if user_id == self.bot_user_id:
            return
        if message_id != self.latest_bot_message.id:
            return

        if self.state == State.TOP_LEVEL_REASON:
            if reaction_emoji == "1️⃣":
                await self.thread.send("Reported for hate speech.")
                await self.set_state(State.REPORT_COMPLETE)
            elif reaction_emoji == "2️⃣":
                await self.thread.send("Reported for bullying.")
                await self.set_state(State.REPORT_COMPLETE)
            elif reaction_emoji == "3️⃣":
                await self.thread.send("Reported for other.")
                await self.set_state(State.REPORT_COMPLETE)