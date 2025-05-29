from enum import Enum, auto
import discord
from discord.ui import View, Select
import random
from hashing import HashDB, AIGeneratedOption, HashRecord, get_phash_from_discord_attachment

NORMAL_PRIORITY_EMOJI = "🟨"
HIGH_PRIORITY_EMOJI = "🟥"
IN_PROGRESS_EMOJI = "🔄"
COMPLETED_EMOJI = "✅"

class State(Enum):
    INITIAL_CSAM = auto()
    NON_CSAM_DECIDE_ACTION = auto()
    LAW_ENFORCEMENT_OR_NO = auto()

    INITIAL_NON_CSAM = auto()
    HASH_MATCH = auto()
    RUN_AI_CLASSIFIER = auto()
    MANUAL_IS_IT_AI_CSAM = auto()
    MANUAL_IS_IT_CSAM = auto()
    AWAITING_CONFIRMATION = auto()

    REPORT_COMPLETE = auto()
    
INTERACTIVE_STATES = {
    State.MANUAL_IS_IT_AI_CSAM: {
        "prompt": "Is this AI-generated CSAM?",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.MANUAL_IS_IT_CSAM: {
        "prompt": """Does this content qualify as CSAM?

Note: CSAM content involves individuals under 18 years old, and includes any of: 
- Sexual intercourse
- Bestiality
- Masturbation
- Sadistic or masochistic abuse
- Lascivious exhibition of the anus, genitals, or pubic area of any person (if unsure, apply Dost Test)""",
        "options": [
            discord.SelectOption(label="Yes", value="yes"),
            discord.SelectOption(label="No", value="no"),
        ]
    },
    State.AWAITING_CONFIRMATION: {
        "prompt": "Final confirmation: Has the NCMEC report been sent and this case fully reviewed?",
        "options": [
            discord.SelectOption(label="Yes, mark complete", value="confirm_done"),
        ]
    },
    State.NON_CSAM_DECIDE_ACTION: {
        "prompt": "What action should be taken?",
        "options": [
            discord.SelectOption(label="Remove post and delete user account", value="remove_and_delete_user"),
            discord.SelectOption(label="Remove post and warn user", value="remove_and_warn_user"),
            discord.SelectOption(label="Shadow block post and warn user", value="shadow_block_post_and_warn_user"),
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
    def __init__(self, thread, thread_parent_message, report, reported_user_id, bot_user_id, hash_db):
        self.thread = thread
        self.thread_parent_message = thread_parent_message
        self.reported_user_id = reported_user_id
        self.bot_user_id = bot_user_id
        self.in_progress_by = None  # discord.User.id of the claiming moderator
        self.lock_message = None
        self.hash_db = hash_db
        self.state = None
        self.latest_bot_message = None
        self.report = report
        self.original_message = report.message

        # We only support one attachment for now
        self.file = None if len(report.message.attachments) == 0 else report.message.attachments[0]

    @classmethod
    async def create(cls, channel, report, bot_user_id):
        parent_message = await channel.send(
            content=
                f"**NEW REPORT**\n"
                f"**Reporter**: {report.reporter}\n"
                f"**Reason**: {report.REPORT_REASONS[report.reason_key]} → {report.subreason}\n"
                f"**Details**: {', '.join(report.followups) if report.followups else '-'}\n"
                f"**Reported User**: {report.message.author.name}\n"
                f"**Link**: {report.message.jump_url}\n"
                f"**Message**: ```{report.message.content}```\n",
            embeds=report.message.embeds or None,
            files=[await atch.to_file() for atch in report.message.attachments]
        )
        thread = await parent_message.create_thread(name=f"Review of {report.message.author.name}'s message")
        mod_report = cls(thread, parent_message, report, report.message.author.id, bot_user_id, report.client.hash_db)
        await mod_report.start_report()
        return mod_report

    async def start_report(self):
        if self.report.priority == 2:
            await self.thread_parent_message.add_reaction(HIGH_PRIORITY_EMOJI)
        else:
            await self.thread_parent_message.add_reaction(NORMAL_PRIORITY_EMOJI)
        if self.report.csam_related:
            await self.set_state(State.INITIAL_CSAM)
        else:
            await self.set_state(State.INITIAL_NON_CSAM)

    async def set_state(self, state):
        self.state = state

        if state in INTERACTIVE_STATES:

            prompt = f"**{INTERACTIVE_STATES[self.state]['prompt']}**"
            options = INTERACTIVE_STATES[self.state]["options"]

            select = Select(
                placeholder="Choose an option...",
                min_values=1,
                max_values=1,
                options=options,
            )
            select.callback = lambda interaction: self.handle_selection(interaction, select.values[0])

            view = View(timeout=None)
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
            await self.thread_parent_message.clear_reaction(HIGH_PRIORITY_EMOJI)
            await self.thread_parent_message.clear_reaction(NORMAL_PRIORITY_EMOJI)
            await self.thread_parent_message.clear_reaction(IN_PROGRESS_EMOJI)  
            await self.thread_parent_message.add_reaction(COMPLETED_EMOJI)
            await self.thread.send("Review complete. Archiving thread.")
            await self.thread.edit(archived=True, locked=True)

        elif state == State.HASH_MATCH:
            await self.thread.send("Checking for match with known CSAM hashes...")

            if self.file:
                phash = await get_phash_from_discord_attachment(self.file)
                record = self.hash_db.check_matching_hash(phash)
                if record:                        
                    if record.ai_generated == AIGeneratedOption.YES:
                        await self.thread.send("The following image has been matched with an AI-generated CSAM hash.")
                        await self.thread.send(self.file.url)
                    elif record.ai_generated == AIGeneratedOption.NO:
                        await self.thread.send("The following image has been matched with a non-AI-generated CSAM hash.")
                        await self.thread.send(self.file.url)
                    else:
                        await self.thread.send("Unknown AI-generated status. Manual review required.")
                        await self.set_state(State.MANUAL_IS_IT_AI_CSAM)
                        return

                    await self.thread.send("Removing post permanently and placing account under monitoring.")
                    await self.delete_reported_message()
                    await self.thread.send("⚠️ Please report to NCMEC and include the hash.")
                    await self.set_state(State.AWAITING_CONFIRMATION)
                else:
                    await self.thread.send("No match found. Manual review required.")
                    await self.set_state(State.MANUAL_IS_IT_CSAM)
            else:
                await self.thread.send("No attachment found. Manual review required.")
                await self.set_state(State.MANUAL_IS_IT_CSAM)

        elif state == State.RUN_AI_CLASSIFIER:
            # Real classifier to be implemented for Milestone 3
            await self.thread.send("Running AI-generation detectors to help your decision...")
            await self.thread.send(f"Results: {random.random() * 100:.2f}% likely to be AI-generated.")
            await self.set_state(State.MANUAL_IS_IT_AI_CSAM)
        else:
            raise ValueError(f"Invalid state: {state}")

    async def show_review_reminder(self):
        if self.lock_message:
            try:
                await self.lock_message.delete()
            except discord.NotFound:
                pass
        self.lock_message = await self.thread.send(f"🔒 This thread is currently under review by <@{self.in_progress_by}>.")


    async def delete_reported_message(self):
        try:
            await self.original_message.delete()
            await self.thread.send("Original message has been deleted.")
        except discord.errors.NotFound:
            await self.thread.send("⚠️ Message already deleted or not found.")
        except discord.errors.Forbidden:
            await self.thread.send("⚠️ Bot lacks permission to delete the message.")
        except Exception as e:
            await self.thread.send(f"⚠️ Error deleting message: {str(e)}")

    async def handle_selection(self, interaction: discord.Interaction, picked: str):
        # Block any interaction if the user is not the assigned reviewer
        if self.in_progress_by is not None and interaction.user.id != self.in_progress_by:
            await interaction.response.send_message(
                f"⛔️ This report is currently being handled by <@{self.in_progress_by}>.",
                ephemeral=True
            )
            await self.show_review_reminder()
            return
        # If the user is the assigned reviewer, allow them to proceed
        # Automatically claim the report if not already claimed
        if not self.in_progress_by:
            self.in_progress_by = interaction.user.id
            await self.thread_parent_message.add_reaction(IN_PROGRESS_EMOJI)
            await self.thread.send(f"🔄 Marked in progress by <@{interaction.user.id}>.")
            await self.show_review_reminder()  # Show the reminder visibly in the thread

        # Remind others if they try to interact while not the reviewer
        if interaction.user.id != self.in_progress_by:
            await self.show_review_reminder()

        await interaction.response.defer()


        if self.state == State.MANUAL_IS_IT_AI_CSAM:
            if picked == "yes":
                await self.thread.send("Hashing CSAM content and adding it to internal database.")
                phash = await get_phash_from_discord_attachment(self.file)
                self.hash_db.add_record(HashRecord(phash, ai_generated=AIGeneratedOption.YES))  
                await self.thread.send("⚠️ Please report to NCMEC and indicate that the content is likely AI-generated.")
                await self.set_state(State.AWAITING_CONFIRMATION)
            elif picked == "no":
                await self.thread.send("Hashing CSAM content and adding it to internal database.")
                phash = await get_phash_from_discord_attachment(self.file)
                self.hash_db.add_record(HashRecord(phash, ai_generated=AIGeneratedOption.NO))  
                await self.thread.send("⚠️ Please report to NCMEC and include the hash.")
                await self.set_state(State.AWAITING_CONFIRMATION)

        elif self.state == State.MANUAL_IS_IT_CSAM:
            if picked == "yes":
                await self.delete_reported_message()
                await self.thread.send("Reported for CSAM.")
                await self.set_state(State.RUN_AI_CLASSIFIER)
            elif picked == "no":
                await self.thread.send("Restoring temporarily removed content.")
                await self.thread.send("Reporting user will be automatically warned/banned if systematically sending false reports.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.AWAITING_CONFIRMATION:
            if picked == "confirm_done":
                await self.thread.send("Confirmation received. Marking report as complete.")
                await self.set_state(State.REPORT_COMPLETE)

        elif self.state == State.NON_CSAM_DECIDE_ACTION:

            if picked == "remove_and_delete_user":
                await self.delete_reported_message()
                await self.thread.send("Removing post and user account.")
                await self.set_state(State.LAW_ENFORCEMENT_OR_NO)
            
            elif picked == "remove_and_warn_user":
                await self.delete_reported_message()
                await self.thread.send("Removing post and warning user.")
                await self.set_state(State.LAW_ENFORCEMENT_OR_NO)
            
            elif picked == "shadow_block_post_and_warn_user":
                await self.delete_reported_message()
                await self.thread.send("Permanently shadow blocking post and warning user.")
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
