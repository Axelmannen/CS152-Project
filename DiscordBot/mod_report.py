from enum import Enum, auto
import discord
from discord.ui import View, Select
import random
import asyncio
import re
from hashing import HashDB, AIGeneratedOption, HashRecord, get_phash_from_discord_attachment
from google import genai
from google.genai.types import HttpOptions, Part
from pathlib import Path
import tempfile
import asyncio
import os
import mimetypes
import sys


NORMAL_PRIORITY_EMOJI = "🟨"
HIGH_PRIORITY_EMOJI = "🟥"
IN_PROGRESS_EMOJI = "🔄"
COMPLETED_EMOJI = "✅"

GEMINI_TEXT_CLASSIFIER_PROMPT = """
You're an expert in detecting AI-generated content, especially social media captions or prompts used to generate images with tools like DALL·E, Midjourney, Imagen, or Stable Diffusion.

Analyze the caption below and determine if it contains any of the following:
- A prompt-like structure (e.g., "portrait of a girl in ultra HD", "3D render of...")
- Mentions of generative AI tools or hashtags (e.g., #dalle2, #midjourney, #stablediffusion)
- Language that appears to be describing an AI-generated image (not a personal experience)


---

**Output Format**

Classification: [0 / 1 / Don't know]
- 0 = Human-written, non-AI-related caption
- 1 = Likely associated with AI-generated content
- Don't know = Caption is ambiguous or lacks clear indicators

Confidence Score: [X]%
- Provide a percentage (0-100%) indicating your confidence in the binary classification.

Brief Justification:
- In 2–3 concise sentences, explain the most significant reasons for your classification. Focus on structural, linguistic, or hashtag clues. Do not just restate the task description.

---

**Important Guidance**:
- If confidence is low or evidence is unclear, prefer “Don't know”.
- Weigh multiple subtle AI indicators more strongly than a single obvious one.
- Be cautious: some real captions may use odd phrasing without being AI-related.
- Prioritize linguistic patterns, keyword usage, and formatting common in AI prompts.
"""

GEMINI_IMAGE_CLASSIFIER_PROMPT = """
You are an expert digital forensics analyst trained in Professor Hany Farid's methodologies for detecting AI-generated faces and explaining your decision. Analyze the provided facial image for signs of artificial generation.
Examine the following categories systematically:
1. Anatomical Integrity
Count facial features (eyes, nostrils, ears, etc.) - are there duplicates or missing elements?
Check for impossible anatomical configurations
Verify natural placement and proportions of features
Look for missing details (eyelashes, tear ducts, nasal hair, skin pores)
2. Phenotypic Plausibility
Assess if phenotypic combinations are statistically probable (e.g., skin tone vs. eye color)
Check for impossible genetic combinations
Verify age-appropriate features match across the face
3. Geometric Consistency
Analyze facial symmetry (natural faces are slightly asymmetric)
Check perspective consistency across features
Verify consistent facial landmark alignment
Look for warping or morphing artifacts
4. Texture and Detail Analysis
Examine skin texture consistency and realism
Check hair patterns for naturalness and consistent growth direction
Verify consistent detail resolution across facial regions
Look for smoothing or sharpening artifacts
5. Ocular Examination
Verify matching reflections in both eyes
Check iris pattern complexity and uniqueness
Examine pupil shape and size consistency
Look for natural eye moisture and blood vessels
6. Lighting and Shadow Coherence
Verify consistent light source direction across all features
Check shadow placement and softness
Examine specular highlights for consistency
Look for impossible lighting conditions
7. Edge and Transition Analysis
Examine face-to-background transitions
Check for halo effects or unnatural boundaries
Verify natural hair-to-skin transitions
Look for copy-paste or blending artifacts

Output Format:


Classification: [0/1/Don't know]
0 = Real/authentic face
1 = AI-generated face
Don't know = Insufficient information or ambiguous indicators
Confidence Score: [X]% Provide a percentage (0-100%) indicating your confidence in the binary classification.

Brief Justification: In 2-3 sentences, cite the most significant indicators that led to your classification. Keep this concise but be formal and professional. 
Important Notes:
If image quality is too low to make reliable assessments, output "Don't know"
Weight multiple subtle anomalies more heavily than single obvious features
Consider that some real faces may have unusual features due to medical conditions, cosmetic procedures, or rare genetics
Focus on patterns consistent with known GAN, diffusion model, or other AI generation artifacts


Always give a 2-3 sentence justification for your decision. This is a very important part of your role.

"""

API_KEY = os.getenv("API_KEY")
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = "us-central1" 
tuning_job_id = os.getenv("tuning_job_id")


client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

class State(Enum):
    INITIAL_CSAM = auto()
    NON_CSAM_DECIDE_ACTION = auto()
    LAW_ENFORCEMENT_OR_NO = auto()

    INITIAL_NON_CSAM = auto()
    HASH_MATCH = auto()
    RUN_AI_IMAGE_CLASSIFIER = auto()
    RUN_AI_TEXT_CLASSIFIER = auto()
    MANUAL_IS_IT_AI_CSAM = auto()
    MANUAL_IS_IT_CSAM = auto()
    AWAITING_CONFIRMATION = auto()

    REPORT_COMPLETE = auto()
    
INTERACTIVE_STATES = {
    State.MANUAL_IS_IT_AI_CSAM: {
        "prompt": "Given the above information, is this AI-generated CSAM?",
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

        elif state == State.RUN_AI_IMAGE_CLASSIFIER:
            # Real classifier to be implemented for Milestone 3
            if self.file is None:
                await self.thread.send("⚠️ No image attached—cannot run the AI image classifier.")
                return

            try:
                image_bytes = await self.file.read()
            except Exception as e:
                await self.thread.send(f"Failed to read attachment: {e}")
                return

            mime_type = (
                self.file.content_type
                or mimetypes.guess_type(self.file.filename)[0]
                or "application/octet-stream"
            )

            # Instead of uploading to Gemini Dev, build a Part from bytes and send directly.

            # 1) Derive suffix exactly as before:
            filename = getattr(self.file, "filename", None)
            if filename:
                suffix = Path(filename).suffix
            else:
                suffix = mimetypes.guess_extension(mime_type) or ""

            # 2) Build a Part() directly from the raw bytes:
            #    (The Part type is imported from google.genai.types.Part)
            part = Part.from_bytes(data=image_bytes, mime_type=mime_type)

            # 3) Call generate_content using that Part + your text PROMPT, no need to upload to a file.
            tuning_job_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/tuningJobs/{tuning_job_id}"
            tuning_job = client.tunings.get(name=tuning_job_name)

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model= tuning_job.tuned_model.endpoint,
                    contents=[part, GEMINI_IMAGE_CLASSIFIER_PROMPT]
                )
            )
            ai_output = response.text.strip()
            #print(ai_output)
            lines = ai_output.split("\n")
            non_empty_lines = [line for line in lines if line.strip() != ""]
            classification = non_empty_lines[0]
            confidence = non_empty_lines[1]
            if len(non_empty_lines) >= 3:
                justification = non_empty_lines[2]

            await self.thread.send("\n **Output of image classifier: ** \n \n")

            await self.thread.send(f"**Classification:** {"NOT AI-generated" if classification.strip() == '0' else "AI-generated"}")
            if "confidence score" in confidence.lower():
                await self.thread.send(f"`{confidence.rstrip("%")}%`")
            else:
                await self.thread.send(f"**Confidence:** `{confidence.rstrip("%")}%`")
            if len(non_empty_lines) >= 3:
                await self.thread.send(f"**Justification:** {justification}")
        
        
        elif state == State.RUN_AI_TEXT_CLASSIFIER:
            if self.original_message and self.original_message.content.strip():
                caption = self.original_message.content.strip()
                prompt = GEMINI_TEXT_CLASSIFIER_PROMPT
                parts = [{"text": f"{prompt}\n\nCaption:\n\"\"\"{caption}\"\"\""}]

                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.report.client.client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[{"role": "user", "parts": parts}]
                    )
                )

                gemini_output = response.text.strip()
                classification_match = re.search(r"Classification:\s*(\[?(\d|Don't know)\]?)", gemini_output)
                confidence_match = re.search(r"Confidence Score:\s*\[?(\d+)%?", gemini_output)
                justification_match = re.search(r"Brief Justification:\s*(.*)", gemini_output, re.DOTALL)

                label = classification_match.group(1) if classification_match else "N/A"
                label = label.strip("[]")
                confidence = confidence_match.group(1) if confidence_match else "N/A"
                justification = justification_match.group(1).strip() if justification_match else gemini_output

                label_display_map = {
                    "0": "NOT AI-generated",
                    "[0]": "NOT AI-generated",
                    "[1]": "AI-generated", 
                    "1": "AI-generated",
                    "Don't know": "Unclear"
                }
                label_display = label_display_map.get(label, label)

                await self.thread.send(
                    f"**Output of text classifier:**\n \n"
                    f"**Classification**: {label_display} \n"
                    f"**Confidence**: `{confidence}%`\n"
                    f"**Justification**: {justification}"
                )
                await self.set_state(State.RUN_AI_IMAGE_CLASSIFIER)

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
                if self.file:
                    phash = await get_phash_from_discord_attachment(self.file)
                    self.hash_db.add_record(HashRecord(phash, ai_generated=AIGeneratedOption.YES))  
                await self.thread.send("⚠️ Please report to NCMEC and indicate that the content is likely AI-generated.")
                await self.set_state(State.AWAITING_CONFIRMATION)
            elif picked == "no":
                await self.thread.send("Hashing CSAM content and adding it to internal database.")
                if self.file:
                    phash = await get_phash_from_discord_attachment(self.file)
                    self.hash_db.add_record(HashRecord(phash, ai_generated=AIGeneratedOption.NO))  
                await self.thread.send("⚠️ Please report to NCMEC and include the hash.")
                await self.set_state(State.AWAITING_CONFIRMATION)

        elif self.state == State.MANUAL_IS_IT_CSAM:
            if picked == "yes":
                await self.delete_reported_message()
                await self.thread.send("Reported for CSAM.")
                # --- ML Classifier will now evaluate the content ---
                await self.thread.send("Next, we will determine if the CSAM content is AI-generated.")
                await self.thread.send("Running AI-generation detectors to help your decision...")
                await self.set_state(State.RUN_AI_TEXT_CLASSIFIER)
                await self.set_state(State.MANUAL_IS_IT_AI_CSAM)

                    # ----------------------------------------------------
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