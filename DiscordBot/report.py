from enum import Enum, auto
import discord
from discord.ui import View, Select, Button
from mod_report import ModReport


class State(Enum):
    REPORT_START = auto()
    AWAITING_MESSAGE = auto()
    AWAITING_REASON = auto()
    AWAITING_SUBREASON = auto()
    AWAITING_FOLLOWUP = auto()
    REPORT_COMPLETE = auto()
    REPORT_CANCELLED = auto()


#######################################################
# MAIN REASON / FIRST PROMPT
#######################################################


class ReasonDropdownView(View):
    def __init__(self, report):
        super().__init__(timeout=None)
        self.add_item(ReasonDropdown(report))
        self.add_item(CancelButton(report))


class ReasonDropdown(Select):
    def __init__(self, report):
        self.report = report
        options = [discord.SelectOption(label=v, value=k) for k, v in report.REPORT_REASONS.items()]
        super().__init__(placeholder="Why are you reporting this post?", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.report.REPORT_REASONS[self.values[0]]
        self.placeholder = selected
        self.disabled = True  # Lock the dropdown

        await interaction.message.edit(view=self.view)  # update to reflect lock
        self.report.reason_key = self.values[0]

        if selected == "False information":
            self.report.subreason = "-"
            self.report.state = State.REPORT_COMPLETE
            await interaction.response.send_message(
                f"✅ Thank you for reporting: **{selected}**. Our internal team will decide on the appropriate "
                + "action, including notifying law enforcement if necessary."
            )
            await self.report.send_to_mods()
            self.report.cleanup(interaction.user.id)

        else:
            self.report.state = State.AWAITING_SUBREASON
            await interaction.response.send_message(
                f"You selected: **{self.report.REPORT_REASONS[self.report.reason_key]}**\n"
                "Please select a subreason (or click 'Go Back'):",
                view=SubreasonDropdownView(self.report),
            )


class CancelButton(Button):
    def __init__(self, report):
        super().__init__(label="Cancel Report", style=discord.ButtonStyle.secondary)
        self.report = report

    async def callback(self, interaction: discord.Interaction):
        self.report.state = State.REPORT_CANCELLED
        await interaction.response.send_message("❌ Report cancelled.")
        self.report.cleanup(interaction.user.id)


#######################################################
# SUBREASON / SECOND PROMPT
#######################################################


class SubreasonDropdownView(View):
    def __init__(self, report):
        super().__init__(timeout=None)
        self.add_item(SubreasonDropdown(report))
        self.add_item(GoBackToReasonButton(report))
        self.add_item(CancelButton(report))


class SubreasonDropdown(Select):
    def __init__(self, report):
        self.report = report
        subreasons = Report.REPORT_SUBREASONS.get(report.reason_key, [])
        options = [discord.SelectOption(label=sr) for sr in subreasons]
        super().__init__(placeholder="What best describes the problem?", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        self.placeholder = selected
        self.disabled = True  # Lock the dropdown after selection

        await interaction.message.edit(view=self.view)  # update to reflect lock
        self.report.subreason = selected
        self.report.state = State.AWAITING_FOLLOWUP

        reason = self.report.REPORT_REASONS[self.report.reason_key]
        sub = self.report.subreason

        # suicide, self-injury, or eating disorders flow
        if reason == "Suicide, self-injury, or eating disorders":

            if sub == "Suicide or self-injury":
                await interaction.response.send_message(
                    "If you or someone you know needs help, call the Suicide and Crisis Lifeline at 988.\n\n"
                    "✅ Thank you for reporting. Our internal team will decide on the appropriate action. "
                    "If you or people you know have been personally affected by suicide, self-injury, or eating disorders, "
                    "we recommend exploring the following resources:\n\n"
                    "**[Suicide & Crisis Lifeline](https://988lifeline.org)**\n"
                    "**[Mental Health Resources - NAMI](https://www.nami.org/support-education/nami-helpline/)**"
                )
                self.report.state = State.REPORT_COMPLETE
                self.report.priority = 2
                await self.report.send_to_mods()
                self.report.cleanup(interaction.user.id)

            elif sub == "Eating disorder":
                await interaction.response.send_message(
                    "✅ Thank you for reporting. Our internal team will decide on the appropriate action. "
                    "If you or people you know have been personally affected by suicide, self-injury, or eating disorders, "
                    "we recommend exploring the following resources:\n\n"
                    "**[Suicide & Crisis Lifeline](https://988lifeline.org)**\n"
                    "**[Mental Health Resources - NAMI](https://www.nami.org/Support-Education/NAMI-HelpLine/Top-HelpLine-Resources)**"
                )
                self.report.state = State.REPORT_COMPLETE
                await self.report.send_to_mods()
                self.report.cleanup(interaction.user.id)

        # bullying, hate or harassment flow
        elif reason == "Bullying, hate or harassment" and sub == "Bullying":
            await interaction.response.send_message(
                "Who is this bullying targeted toward?",
                view=FollowUpView(self.report, "Target of bullying", ["Myself", "Someone else"]),
            )

        # nudity or sexual activity flow
        elif reason == "Nudity or sexual activity":
            await interaction.response.send_message(
                "Does the content appear involve someone under 18?",
                view=FollowUpView(self.report, "Suspected CSAM", ["Yes", "No"]),
            )

        else:
            # default flow
            self.report.state = State.REPORT_COMPLETE
            await interaction.response.send_message(
                f"✅ Thank you for reporting: **{reason} → {sub}**. Our internal team will decide on the appropriate action,"
                + " including notifying law enforcement if necessary. "
            )
            await self.report.send_to_mods()
            self.report.cleanup(interaction.user.id)


class GoBackToReasonButton(Button):
    def __init__(self, report):
        super().__init__(label="Go Back to Reason", style=discord.ButtonStyle.secondary)
        self.report = report

    async def callback(self, interaction: discord.Interaction):
        self.report.reason_key = None
        self.report.subreason = None
        self.report.state = State.AWAITING_REASON
        await interaction.response.send_message(
            "Okay, please select a different reason:", view=ReasonDropdownView(self.report)
        )


#######################################################
# FOLLOWUPS / THIRD PROMPTS AND BEYOND
# FOR ASKING QUESTIONS THAT ARE ANSWERED WITH BUTTONS
#######################################################


class FollowUpView(View):
    def __init__(self, report, question_summary, options):
        super().__init__(timeout=None)
        for label in options:
            self.add_item(FollowUpButton(report, question_summary, label))
        self.add_item(CancelButton(report))


class FollowUpButton(Button):
    def __init__(self, report, question_summary, button_label):
        super().__init__(label=button_label, style=discord.ButtonStyle.primary)
        self.question_summary = question_summary
        self.report = report

    async def callback(self, interaction: discord.Interaction):
        self.report.followups.append(f"{self.question_summary}: {self.label}")

        if self.question_summary == "Target of bullying":
            await interaction.response.send_message(
                "Would you like to block this user? Blocking this user will prevent them from interacting with you on this platform in the future.",
                view=FollowUpView(self.report, "Block User", ["Yes", "No"]),
            )

        elif self.question_summary == "Block User":
            reported_user = self.report.message.author.name if self.report.message else "The user"
            # block confirmation
            await interaction.response.send_message(f"{reported_user} has been blocked.")

            # report confirmation (must use followup)
            await interaction.followup.send(
                f"✅ Thank you for reporting: **{self.report.REPORT_REASONS[self.report.reason_key]} → {self.report.subreason}**.\n"
                "Our internal team will decide on the appropriate action, including notifying law enforcement if necessary."
            )

            self.report.state = State.REPORT_COMPLETE
            await self.report.send_to_mods()
            self.report.cleanup(interaction.user.id)

        elif self.question_summary == "Suspected CSAM":
            # if not a block path, this is the first and only message
            await interaction.response.send_message(
                f"✅ Thank you for reporting: **{self.report.REPORT_REASONS[self.report.reason_key]} → {self.report.subreason}**.\n"
                "Our internal team will decide on the appropriate action, including notifying law enforcement if necessary."
            )

            if self.label == "Yes":
                self.report.csam_related = True
                self.report.priority = 2

            self.report.state = State.REPORT_COMPLETE
            await self.report.send_to_mods()
            self.report.cleanup(interaction.user.id)

        else:
            raise ValueError("Invalid followup question")


#######################################################
# REPORT CLASS
#######################################################


class Report:

    REPORT_REASONS = {
        "1": "Scam, fraud or spam",
        "2": "Bullying, hate or harassment",
        "3": "Suicide, self-injury, or eating disorders",
        "4": "Selling or promoting restricted items",
        "5": "Nudity or sexual activity",
        "6": "False information",
    }

    REPORT_SUBREASONS = {
        "1": ["Fraud or Spam", "Scam"],
        "2": [
            "Hate speech",
            "Terrorism or organised crime",
            "Calling for violence",
            "Showing violence, death or severe injury",
            "Bullying",
        ],
        "3": ["Suicide or self-injury", "Eating disorder"],
        "4": ["Drugs", "Weapons", "Animals"],
        "5": [
            "Threatening to share or sharing nude images",
            "Prostitution",
            "Sexual exploitation",
            "Other nudity or sexual activity",
        ],
    }

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.reporter = None
        self.reason_key = None
        self.subreason = None
        self.followups = []
        self.csam_related = False
        self.priority = 1  # 1 = normal, 2 = high

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE

    def cleanup(self, user_id):
        if hasattr(self.client, "reports") and user_id in self.client.reports:
            self.client.reports.pop(user_id)

    async def send_to_mods(self):
        for mod_channel in self.client.mod_channels.values():
            mod_report = await ModReport.create(mod_channel, self, self.client.user.id)
            self.client.mod_reports[mod_report.thread.id] = mod_report
