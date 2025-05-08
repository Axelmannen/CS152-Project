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

class FollowUpView(View):
    def __init__(self, report, question, options):
        super().__init__(timeout=60)
        self.report = report
        for label, flag in options:
            self.add_item(FollowUpButton(label, flag, report))
        self.add_item(CancelButton(report))
        

class FollowUpButton(Button):
    def __init__(self, label, flag, report):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.flag = flag
        self.report = report

    async def callback(self, interaction: discord.Interaction):
        self.report.followup_response = self.label
        self.report.flag = self.flag
        self.report.state = State.REPORT_COMPLETE
        await interaction.response.send_message(
            f"✅ You reported: **{self.report.REPORT_REASONS[self.report.reason_key]} → {self.report.subreason}**\n"
            f"**Details**: {self.report.followup_response}\n"
            "Our team will review the report."
        )
        await self.report.log_to_mods(interaction.user)
        self.report.cleanup(interaction.user.id)

class SubreasonDropdown(Select):
    def __init__(self, report):
        self.report = report
        subreasons = Report.REPORT_SUBREASONS.get(report.reason_key, [])
        options = [discord.SelectOption(label=sr) for sr in subreasons]
        super().__init__(placeholder="Select a subreason for reporting...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.disabled = True  # Lock the dropdown after selection
        self.report.subreason = self.values[0]
        self.report.state = State.AWAITING_FOLLOWUP

        reason = self.report.REPORT_REASONS[self.report.reason_key]
        sub = self.report.subreason

        if reason == "Suicide or self-injury":
            await interaction.response.send_message(
                "If you or someone you know needs help, call the Suicide and Crisis Lifeline at 988."
            )
            self.report.flag = "Mental Health"
            self.report.state = State.REPORT_COMPLETE
            await self.report.log_to_mods(interaction.user)
            self.report.cleanup(interaction.user.id)
            return

        if reason == "Bullying, hate or harassment" and sub == "Bullying":
            await interaction.response.send_message(
                "Who is this bullying targeted toward?",
                view=FollowUpView(
                    self.report,
                    "Target of bullying",
                    [("Myself", "Self-targeted"), ("Someone else", "3rd-party")]
                )
            )
        elif reason == "Nudity or sexual activity" and sub == "Threatening to share nude images":
            await interaction.response.send_message(
                "Does this involve someone under 18?",
                view=FollowUpView(
                    self.report,
                    "CSAM Check",
                    [("Yes", "CSAM-related"), ("No", "Adult content")]
                )
            )
        else:
            self.report.state = State.REPORT_COMPLETE
            await interaction.response.send_message(
                f"✅ You reported: **{reason} → {sub}**"
            )
            await self.report.log_to_mods(interaction.user)
            self.report.cleanup(interaction.user.id)

class SubreasonDropdownView(View):
    def __init__(self, report):
        super().__init__(timeout=60)
        self.add_item(SubreasonDropdown(report))
        self.add_item(GoBackToReasonButton(report))
        self.add_item(CancelButton(report))

class ReasonDropdown(Select):
    def __init__(self, report):
        self.report = report
        options = [discord.SelectOption(label=v, value=k) for k, v in report.REPORT_REASONS.items()]
        super().__init__(placeholder="Select a reason for reporting...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.disabled = True  # Lock the dropdown
        self.report.reason_key = self.values[0]
        self.report.state = State.AWAITING_SUBREASON
        await interaction.response.send_message(
            f"✅ You selected: **{self.report.REPORT_REASONS[self.report.reason_key]}**\n"
            "Please select a subreason (or click 'Go Back'):",
            view=SubreasonDropdownView(self.report)
        )

class ReasonDropdownView(View):
    def __init__(self, report):
        super().__init__(timeout=60)
        self.add_item(ReasonDropdown(report))
        self.add_item(CancelButton(report))

class CancelButton(Button):
    def __init__(self, report):
        super().__init__(label="Cancel Report", style=discord.ButtonStyle.secondary)
        self.report = report

    async def callback(self, interaction: discord.Interaction):
        self.report.state = State.REPORT_COMPLETE
        await interaction.response.send_message("❌ Report cancelled.")
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
            "Okay! Please select a different reason:",
            view=ReasonDropdownView(self.report)
        )

class Report:
    REPORT_REASONS = {
        "1": "Scam, fraud or spam",
        "2": "Bullying, hate or harassment",
        "3": "Suicide or self-injury",
        "4": "Selling or promoting restricted items",
        "5": "Nudity or sexual activity",
        "6": "False information"
    }

    REPORT_SUBREASONS = {
        "1": ["Fraud", "Spam", "Scam"],
        "2": ["Hate speech", "Violence", "Bullying", "Terrorism"],
        "3": ["Suicide", "Eating disorder"],
        "4": ["Drugs", "Weapons", "Animals"],
        "5": ["Nude images", "Prostitution", "Sexual exploitation", "Sexual activity", "Threatening to share nude images"],
        "6": ["Misinformation", "Fake news", "Misleading content"]
    }

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.reason_key = None
        self.subreason = None
        self.followup_response = None
        self.flag = None

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE

    def cleanup(self, user_id):
        if hasattr(self.client, "reports") and user_id in self.client.reports:
            self.client.reports.pop(user_id)

    async def log_to_mods(self, user):
        for mod_channel in self.client.mod_channels.values():
            mod_report = await ModReport.create(mod_channel, self, self.client.user.id)
            self.client.mod_reports[mod_report.thread.id] = mod_report
