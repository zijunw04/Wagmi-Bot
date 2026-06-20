import os
import json
import asyncio
import discord
import time
from datetime import datetime
from typing import List, Optional
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
from job_scraper import JobScraper, Job
from leetcode_scraper import LeetCodeScraper, LeetCodeProblem
from supabase_manager import SupabaseManager
from country_utils import (
    COUNTRY_PATTERNS,
    extract_countries_from_jobs,
)
from filter_utils import (
    GuildFilters,
    job_matches_guild_filters,
    normalize_company,
    normalize_region,
)
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("discord")

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_OWNER_ID = os.getenv("DISCORD_OWNER_ID")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")
JOBS_HISTORY_FILE = "jobs_history.json"


def is_valid_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return url.strip().startswith(("http://", "https://"))


def role_accent(title: str) -> tuple[str, discord.Color]:
    t = (title or "").lower()
    if any(k in t for k in ["product manager", "pm intern", "product management"]):
        return "📊", discord.Color.purple()
    if any(k in t for k in ["machine learning", "ai", "data scientist", "ml"]):
        return "🤖", discord.Color.teal()
    return "💼", discord.Color.blurple()


def autocomplete_matches(options: List[str], current: str, limit: int = 25) -> List[str]:
    """Prefer prefix matches, then substring matches."""
    current_lower = current.lower().strip()
    if not current_lower:
        return options[:limit]
    prefix = [o for o in options if o.lower().startswith(current_lower)]
    substring = [o for o in options if current_lower in o.lower() and o not in prefix]
    return (prefix + substring)[:limit]


app = Flask("")


@app.route("/")
def home():
    return "I'm alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


async def user_is_bot_owner(bot: commands.Bot, user: discord.User | discord.Member) -> bool:
    if DISCORD_OWNER_ID and str(user.id) == DISCORD_OWNER_ID:
        return True
    return await bot.is_owner(user)


class WagmiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.scheduler = AsyncIOScheduler()
        self.job_scraper = JobScraper()
        self.leetcode_scraper = LeetCodeScraper()
        self.storage = SupabaseManager(SUPABASE_URL, SUPABASE_KEY)

        self.stats = {
            "total_posted_today": 0,
            "last_reset_date": datetime.now().date().isoformat(),
        }
        self.initialized = False

    def migrate_json_to_db(self):
        if not os.path.exists(JOBS_HISTORY_FILE):
            return
        logger.info("Found legacy jobs_history.json. Migrating to storage...")
        try:
            with open(JOBS_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            count = 0
            for job_key in history.keys():
                parts = job_key.split("|")
                if len(parts) == 3:
                    company, title, location = parts
                    job = Job(company=company, title=title, location=location, timestamp=0.0)
                    if not self.storage.is_duplicate(job):
                        self.storage.add_job(job)
                        count += 1
            logger.info(f"Successfully migrated {count} records.")
            os.rename(JOBS_HISTORY_FILE, f"{JOBS_HISTORY_FILE}.bak")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def reset_daily_stats(self):
        today = datetime.now().date().isoformat()
        if self.stats["last_reset_date"] != today:
            self.stats["total_posted_today"] = 0
            self.stats["last_reset_date"] = today

    def get_posting_targets(self) -> List[dict]:
        return self.storage.get_all_posting_targets()

    async def resolve_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        channel = self.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            fetched = await self.fetch_channel(channel_id)
            return fetched
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Could not resolve channel {channel_id}: {e}")
            return None

    def create_job_embed(self, job: Job) -> discord.Embed:
        emoji, color = role_accent(job.title)
        valid_url = job.link if is_valid_url(job.link) else None
        location = job.location or "Not specified"
        posted_date = job.date_posted or "Unknown date"
        embed = discord.Embed(
            title=f"{emoji} {job.title}",
            description=f"**{job.company}**",
            color=color,
            url=valid_url,
            timestamp=datetime.now(),
        )
        embed.add_field(name="📍 Location", value=location, inline=True)
        embed.add_field(name="🕒 Posted", value=posted_date, inline=True)
        if valid_url:
            embed.add_field(
                name="🔗 Application",
                value=f"[Open application]({valid_url})",
                inline=False,
            )
        embed.set_footer(text="Wagmi Bot • Auto-updated every 30m")
        return embed

    async def fetch_and_post_jobs(self):
        try:
            self.reset_daily_stats()
            targets = self.get_posting_targets()
            if not targets:
                logger.warning("No posting channels configured. Use /setup in a server.")
                return

            channel_ids = [t["channel_id"] for t in targets]
            logger.info(f"Posting to {len(channel_ids)} channel(s): {channel_ids}")

            logger.info("Fetching today's jobs from GitHub...")
            jobs = await asyncio.to_thread(self.job_scraper.fetch_jobs, only_today=True)
            if not jobs:
                logger.info("No jobs found or error occurred.")
                return

            new_jobs_count = 0
            messages_sent = 0
            for job in jobs:
                if self.storage.is_duplicate(job):
                    continue

                embed = self.create_job_embed(job)
                matching_targets = [
                    t for t in targets
                    if job_matches_guild_filters(job, t["filters"])
                ]
                if not matching_targets:
                    continue

                posted_this_job = False
                for target in matching_targets:
                    channel_id = target["channel_id"]
                    channel = await self.resolve_channel(channel_id)
                    if not channel:
                        continue
                    try:
                        await channel.send(embed=embed)
                        posted_this_job = True
                        messages_sent += 1
                        self.stats["total_posted_today"] += 1
                        logger.info(
                            f"Posted {job.company} - {job.title} to channel {channel_id}"
                        )
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Failed to send to channel {channel_id}: {e}")

                if posted_this_job:
                    self.storage.add_job(job)
                    new_jobs_count += 1

            if messages_sent > 0:
                logger.info(
                    f"Posted {new_jobs_count} new job(s) across {messages_sent} message(s)!"
                )
            else:
                logger.info("No new jobs to post.")
        except Exception as e:
            logger.error(f"Error in fetch_and_post_jobs: {e}")

    async def on_ready(self):
        if self.initialized:
            return
        logger.info(f"{self.user} has logged in!")
        self.migrate_json_to_db()
        self.scheduler.add_job(
            self.fetch_and_post_jobs,
            "interval",
            minutes=30,
            id="fetch_jobs",
            replace_existing=True,
        )
        self.scheduler.start()
        self.initialized = True
        logger.info("Bot is ready and scheduler started (30 minute intervals).")

    async def clear_guild_commands(self, guild: discord.abc.Snowflake) -> None:
        """Remove guild-scoped commands so they don't duplicate global ones."""
        self.tree.clear_commands(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info(f"Cleared guild-scoped commands for guild {guild.id}")

    async def clear_global_commands(self) -> None:
        """Remove global commands from Discord (use when switching to guild-only sync)."""
        await self.http.bulk_upsert_global_commands(self.application_id, [])
        logger.info("Cleared global commands from Discord")

    async def sync_slash_commands(
        self,
        guild: Optional[discord.abc.Snowflake] = None,
        *,
        guild_only: bool = False,
    ) -> int:
        if guild_only and guild is not None:
            await self.clear_global_commands()
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"Guild-only sync: {len(synced)} command(s) to guild {guild.id}")
        elif guild is not None:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced)} command(s) to guild {guild.id}")
        else:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} global command(s)")
        return len(synced)

    async def setup_hook(self):
        logger.info("Bot setup hook running...")
        try:
            if DEV_GUILD_ID:
                dev_guild = discord.Object(id=int(DEV_GUILD_ID))
                await self.sync_slash_commands(dev_guild, guild_only=True)
            else:
                await self.sync_slash_commands()
        except Exception as e:
            logger.error(f"Command sync failed on startup: {e}")


class LeetCodeView(discord.ui.View):
    def __init__(self, problems: List[LeetCodeProblem], company: str):
        super().__init__(timeout=60)
        self.all_problems = problems
        self.company = company
        self.page = 0
        self.per_page = 10
        self.sorted_by_freq = False
        self.problems = problems

    def get_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        current_list = self.problems[start:end]
        total_pages = (len(self.problems) - 1) // self.per_page + 1
        sort_mode = "Frequency" if self.sorted_by_freq else "Default"
        embed = discord.Embed(
            title=f"💻 LeetCode Questions: {self.company.title()}",
            description=f"Practice set for **{self.company.title()}** • Sort: **{sort_mode}**",
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        for i, p in enumerate(current_list, start + 1):
            diff_emoji = (
                "🟢" if "Easy" in p.difficulty else "🟡" if "Medium" in p.difficulty else "🔴"
            )
            embed.add_field(
                name=f"{i}. {p.title}",
                value=(
                    f"{diff_emoji} {p.difficulty}  •  Acc {p.acceptance}  •  "
                    f"Freq {p.frequency}\n[Open problem]({p.url})"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} • {len(self.problems)} total questions")
        return embed

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.gray)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = (len(self.problems) - 1) // self.per_page + 1
        if self.page < total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="📊 Sort by Frequency", style=discord.ButtonStyle.blurple)
    async def sort(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sorted_by_freq = not self.sorted_by_freq
        if self.sorted_by_freq:
            self.problems = sorted(self.all_problems, key=lambda x: x.freq_value, reverse=True)
            button.label = "📋 Default Sort"
        else:
            self.problems = self.all_problems
            button.label = "📊 Sort by Frequency"
        self.page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


class JobResultsView(discord.ui.View):
    def __init__(self, jobs: List[Job], title: str, per_page: int = 5):
        super().__init__(timeout=120)
        self.jobs = jobs
        self.page = 0
        self.per_page = per_page
        self.title = title

    def get_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        current_jobs = self.jobs[start:end]
        total_pages = max(1, (len(self.jobs) - 1) // self.per_page + 1)
        embed = discord.Embed(
            title=self.title,
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        for i, job in enumerate(current_jobs, start + 1):
            loc = job.location or "Not specified"
            date_value = job.date_posted or "Unknown date"
            link_value = f"[Apply]({job.link})" if is_valid_url(job.link) else "No valid link"
            role_emoji, _ = role_accent(job.title)
            embed.add_field(
                name=f"{i}. {role_emoji} {job.title}",
                value=f"**{job.company}**\n📍 {loc}\n🕒 {date_value}\n🔗 {link_value}",
                inline=False,
            )
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} • {len(self.jobs)} total jobs")
        return embed

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.gray)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, (len(self.jobs) - 1) // self.per_page + 1)
        if self.page < total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()


class SetupView(discord.ui.View):
    def __init__(self, bot: WagmiBot, guild_id: int, channel_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id

    def _can_manage(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        perms = interaction.user.guild_permissions
        return perms.manage_guild or perms.manage_channels or interaction.user.guild_permissions.administrator

    async def _status_embed(self) -> discord.Embed:
        channels: List[int] = self.bot.storage.get_posting_channels(self.guild_id)
        filters = self.bot.storage.get_guild_filters(self.guild_id)

        channel_lines = [f"<#{cid}>" for cid in channels] if channels else ["None configured"]
        if filters.is_empty:
            filter_lines = ["None — all jobs are posted"]
        else:
            filter_lines = filters.summary_lines()

        embed = discord.Embed(
            title="⚙️ Wagmi Bot Server Setup",
            description="Configure where new internship jobs are posted for this server.",
            color=discord.Color.green(),
        )
        embed.add_field(name="📢 Posting Channels", value="\n".join(channel_lines), inline=False)
        embed.add_field(name="🔍 Posting Filters", value="\n".join(filter_lines), inline=False)
        embed.add_field(
            name="Tips",
            value=(
                "• **Add This Channel** registers the channel you're in.\n"
                "• Use `/filter add` to filter by region, company, or remote.\n"
                "• Commands work in any channel."
            ),
            inline=False,
        )
        embed.set_footer(text=f"Server ID: {self.guild_id}")
        return embed

    @discord.ui.button(label="➕ Add This Channel", style=discord.ButtonStyle.green)
    async def add_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** or **Manage Channels** to configure the bot.",
                ephemeral=True,
            )
            return
        ok = self.bot.storage.add_posting_channel(self.guild_id, self.channel_id)
        if ok:
            await interaction.response.edit_message(embed=await self._status_embed(), view=self)
        else:
            await interaction.response.send_message("❌ Failed to save channel.", ephemeral=True)

    @discord.ui.button(label="➖ Remove This Channel", style=discord.ButtonStyle.red)
    async def remove_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** or **Manage Channels** to configure the bot.",
                ephemeral=True,
            )
            return
        ok = self.bot.storage.remove_posting_channel(self.guild_id, self.channel_id)
        if ok:
            await interaction.response.edit_message(embed=await self._status_embed(), view=self)
        else:
            await interaction.response.send_message("❌ Failed to remove channel.", ephemeral=True)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.blurple)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=await self._status_embed(), view=self)


def register_commands(bot: WagmiBot):
    cache = {
        "companies": [],
        "companies_ts": 0.0,
        "leetcode_companies": [],
        "leetcode_companies_ts": 0.0,
        "countries": [],
        "countries_ts": 0.0,
    }
    COMPANY_CACHE_TTL = 300
    LEETCODE_CACHE_TTL = 3600
    COUNTRY_CACHE_TTL = 600

    async def get_job_companies() -> List[str]:
        now_ts = time.time()
        if cache["companies"] and (now_ts - cache["companies_ts"] < COMPANY_CACHE_TTL):
            return cache["companies"]
        jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, False)
        companies = sorted({job.company for job in jobs if job.company})
        cache["companies"] = companies
        cache["companies_ts"] = now_ts
        return companies

    async def get_leetcode_companies() -> List[str]:
        now_ts = time.time()
        if cache["leetcode_companies"] and (now_ts - cache["leetcode_companies_ts"] < LEETCODE_CACHE_TTL):
            return cache["leetcode_companies"]
        companies = await asyncio.to_thread(bot.leetcode_scraper.fetch_company_list)
        cache["leetcode_companies"] = companies
        cache["leetcode_companies_ts"] = now_ts
        return companies

    async def get_country_options() -> List[str]:
        now_ts = time.time()
        if cache["countries"] and (now_ts - cache["countries_ts"] < COUNTRY_CACHE_TTL):
            return cache["countries"]
        jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, False)
        detected = extract_countries_from_jobs(jobs)
        all_options = sorted(set(list(COUNTRY_PATTERNS.keys()) + detected))
        cache["countries"] = all_options
        cache["countries_ts"] = now_ts
        return all_options

    @bot.tree.command(name="setup", description="Configure posting channels for this server")
    async def setup_slash(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Run `/setup` inside a server, not in DMs.", ephemeral=True
            )
            return
        view = SetupView(bot, interaction.guild.id, interaction.channel_id)
        embed = await view._status_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    filter_group = discord.app_commands.Group(
        name="filter", description="Manage job posting filters (region, company, remote)"
    )

    FILTER_TYPE_CHOICES = [
        discord.app_commands.Choice(name="Region", value="region"),
        discord.app_commands.Choice(name="Company", value="company"),
        discord.app_commands.Choice(name="Remote", value="remote"),
    ]
    REMOTE_CHOICES = [
        discord.app_commands.Choice(name="Remote only (true)", value="true"),
        discord.app_commands.Choice(name="On-site only (false)", value="false"),
    ]

    def filters_embed(filters: GuildFilters) -> discord.Embed:
        embed = discord.Embed(
            title="🔍 Posting Filters",
            color=discord.Color.blue(),
        )
        if filters.is_empty:
            embed.description = "No filters — all matching jobs are posted."
        else:
            embed.description = "\n".join(filters.summary_lines())
        return embed

    @filter_group.command(name="show", description="Show active posting filters")
    async def filter_show(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
            return
        filters = bot.storage.get_guild_filters(interaction.guild.id)
        await interaction.response.send_message(embed=filters_embed(filters), ephemeral=True)

    @filter_group.command(name="clear", description="Clear all posting filters")
    async def filter_clear(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
            return
        bot.storage.clear_guild_filters(interaction.guild.id)
        await interaction.response.send_message(
            "✅ Cleared all filters. All jobs will be posted.", ephemeral=True
        )

    @filter_group.command(name="add", description="Add a region, company, or remote filter")
    @discord.app_commands.describe(
        filter_type="Filter category",
        value="Region or company name (not needed for remote)",
        remote="Remote filter: true = remote only, false = on-site only",
    )
    @discord.app_commands.choices(filter_type=FILTER_TYPE_CHOICES)
    @discord.app_commands.choices(remote=REMOTE_CHOICES)
    async def filter_add(
        interaction: discord.Interaction,
        filter_type: str,
        value: Optional[str] = None,
        remote: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
            return

        remote_only: Optional[bool] = None
        if filter_type == "region":
            if not value:
                await interaction.response.send_message(
                    "❌ Provide a **value** for region (e.g. United States).", ephemeral=True
                )
                return
            value = normalize_region(value)
        elif filter_type == "company":
            if not value:
                await interaction.response.send_message(
                    "❌ Provide a **value** for company (e.g. Microsoft).", ephemeral=True
                )
                return
            value = normalize_company(value)
        elif filter_type == "remote":
            if remote is None:
                await interaction.response.send_message(
                    "❌ Pick **remote**: true (remote only) or false (on-site only).", ephemeral=True
                )
                return
            remote_only = remote == "true"
            value = None

        ok, msg = bot.storage.add_guild_filter(
            interaction.guild.id, filter_type, value, remote_only
        )
        if not ok:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        filters = bot.storage.get_guild_filters(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Filter added.\n\n{filters_embed(filters).description}",
            ephemeral=True,
        )

    @filter_group.command(name="remove", description="Remove a region, company, or remote filter")
    @discord.app_commands.describe(
        filter_type="Filter category to remove",
        value="Region or company to remove (not needed for remote)",
    )
    @discord.app_commands.choices(filter_type=FILTER_TYPE_CHOICES)
    async def filter_remove(
        interaction: discord.Interaction,
        filter_type: str,
        value: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ Use this in a server.", ephemeral=True)
            return

        if filter_type == "region" and value:
            value = normalize_region(value)
        elif filter_type == "company" and value:
            value = normalize_company(value)
        elif filter_type in ("region", "company") and not value:
            await interaction.response.send_message(
                f"❌ Provide a **value** to remove for {filter_type}.", ephemeral=True
            )
            return

        ok, msg = bot.storage.remove_guild_filter(interaction.guild.id, filter_type, value)
        if not ok:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        filters = bot.storage.get_guild_filters(interaction.guild.id)
        if filters.is_empty:
            text = "✅ Filter removed. No filters remain — all jobs will be posted."
        else:
            text = f"✅ Filter removed.\n\n{filters_embed(filters).description}"
        await interaction.response.send_message(text, ephemeral=True)

    @filter_add.autocomplete("value")
    @filter_remove.autocomplete("value")
    async def filter_value_autocomplete(interaction: discord.Interaction, current: str):
        filter_type = getattr(interaction.namespace, "filter_type", None)
        if filter_type == "company":
            companies = await get_job_companies()
            matches = autocomplete_matches(companies, current)
            return [
                discord.app_commands.Choice(name=c[:100], value=c) for c in matches
            ]
        if filter_type == "region":
            options = await get_country_options()
            matches = autocomplete_matches(options, current)
            return [
                discord.app_commands.Choice(name=c[:100], value=c) for c in matches
            ]
        return []

    @bot.tree.command(name="latest", description="Show the 5 most recent tech internship postings")
    async def latest_slash(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, only_today=False)
            if not jobs:
                await interaction.followup.send("❌ No jobs found.")
                return
            view = JobResultsView(jobs, "🆕 Latest Tech Internships", per_page=5)
            await interaction.followup.send(embed=view.get_embed(), view=view)
        except Exception as e:
            logger.error(f"Error in latest command: {e}")
            await interaction.followup.send("❌ Error fetching jobs.")

    @bot.tree.command(name="stats", description="Show statistics about jobs posted today")
    async def stats_slash(interaction: discord.Interaction):
        bot.reset_daily_stats()
        total = bot.storage.get_total_count()
        targets = bot.get_posting_targets()
        channel_count = len(targets)
        embed = discord.Embed(title="📊 Job Bot Statistics", color=discord.Color.green())
        embed.add_field(name="Posted Today", value=str(bot.stats["total_posted_today"]))
        embed.add_field(name="Total Tracked", value=str(total))
        embed.add_field(name="Posting Channels", value=str(channel_count))
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="company", description="Search recent jobs at a specific company")
    @discord.app_commands.describe(name="Company name to search for")
    async def company_slash(interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        try:
            jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, only_today=False)
            company_jobs = [j for j in jobs if name.lower() in j.company.lower()]
            if not company_jobs:
                await interaction.followup.send(f"❌ No jobs found for **{name}**.")
                return
            display = company_jobs[0].company if company_jobs else name
            view = JobResultsView(company_jobs, f"🏢 Recent Jobs at {display}", per_page=5)
            await interaction.followup.send(embed=view.get_embed(), view=view)
        except Exception as e:
            logger.error(f"Error in company command: {e}")
            await interaction.followup.send("❌ Error searching for jobs.")

    @bot.tree.command(name="leetcode", description="Fetch company LeetCode interview questions")
    @discord.app_commands.describe(company="Company name (e.g., google, amazon)")
    async def leetcode_slash(interaction: discord.Interaction, company: str):
        await interaction.response.defer()
        try:
            formatted_name = bot.leetcode_scraper.get_formatted_company_name(company)
            problems = await asyncio.to_thread(bot.leetcode_scraper.fetch_problems, formatted_name)
            if not problems:
                await interaction.followup.send(f"❌ No LeetCode data found for **{company}**.")
                return
            view = LeetCodeView(problems, company)
            await interaction.followup.send(embed=view.get_embed(), view=view)
        except Exception as e:
            logger.error(f"Error in leetcode command: {e}")
            await interaction.followup.send("❌ Error fetching LeetCode questions.")

    @company_slash.autocomplete("name")
    async def company_autocomplete(interaction: discord.Interaction, current: str):
        companies = await get_job_companies()
        matches = autocomplete_matches(companies, current)
        return [
            discord.app_commands.Choice(name=company[:100], value=company)
            for company in matches
        ]

    @leetcode_slash.autocomplete("company")
    async def leetcode_company_autocomplete(interaction: discord.Interaction, current: str):
        companies = await get_leetcode_companies()
        matches = autocomplete_matches(companies, current)
        return [
            discord.app_commands.Choice(name=company[:100], value=company)
            for company in matches
        ]

    @bot.tree.command(name="fetch", description="Manually trigger a job search and post new ones")
    async def fetch_slash(interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Manually triggering job fetch...")
        await bot.fetch_and_post_jobs()

    @bot.tree.command(name="test", description="Check if the bot is alive and responsive")
    async def test_slash(interaction: discord.Interaction):
        await interaction.response.send_message("✅ Bot is working! Supabase: ✅ connected")

    @bot.tree.command(name="sync", description="Re-register slash commands (bot owner only)")
    @discord.app_commands.describe(scope="How to sync commands")
    @discord.app_commands.choices(scope=[
        discord.app_commands.Choice(name="Global (recommended)", value="global"),
        discord.app_commands.Choice(name="This server only (dev)", value="guild"),
    ])
    async def sync_slash(interaction: discord.Interaction, scope: str = "global"):
        if not await user_is_bot_owner(bot, interaction.user):
            await interaction.response.send_message(
                "❌ Only the bot owner can run this.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if scope == "guild":
                if not interaction.guild:
                    await interaction.followup.send("❌ Use this in a server for guild sync.")
                    return
                count = await bot.sync_slash_commands(interaction.guild, guild_only=True)
                await interaction.followup.send(
                    f"✅ Guild-only sync: **{count}** command(s). "
                    "Global commands were cleared to avoid duplicates."
                )
            else:
                if interaction.guild:
                    await bot.clear_guild_commands(interaction.guild)
                count = await bot.sync_slash_commands()
                await interaction.followup.send(
                    f"✅ Global sync: **{count}** command(s). "
                    "Guild duplicates in this server were removed."
                )
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            await interaction.followup.send(f"❌ Sync failed: {e}")

    @bot.command(name="sync")
    async def sync_prefix(ctx, scope: str = "global"):
        if not await user_is_bot_owner(bot, ctx.author):
            await ctx.send("❌ Only the bot owner can run this.")
            return
        try:
            if scope.lower() == "guild" and ctx.guild:
                count = await bot.sync_slash_commands(ctx.guild, guild_only=True)
                await ctx.send(
                    f"✅ Guild-only sync: **{count}** command(s).\n"
                    "Global commands were cleared to avoid duplicates."
                )
            else:
                if ctx.guild:
                    await bot.clear_guild_commands(ctx.guild)
                count = await bot.sync_slash_commands()
                await ctx.send(
                    f"✅ Global sync: **{count}** command(s).\n"
                    "Guild duplicates in this server were removed. "
                    "Commands may take a few minutes to update globally."
                )
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            await ctx.send(f"❌ Sync failed: {e}")

    bot.tree.add_command(filter_group)


async def start_bot():
    retry_delay = 60
    max_delay = 600
    logger.info("Initializing environment...")
    keep_alive()

    while True:
        logger.info("Attempting to start Wagmi Bot instance...")
        bot_instance = WagmiBot()
        register_commands(bot_instance)

        try:
            async with bot_instance:
                await bot_instance.start(DISCORD_TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logger.error("CRITICAL: 429 Too Many Requests (Rate Limited).")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                logger.error(f"Discord HTTP Error ({e.status}): {e}")
                await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Bot connection error: {type(e).__name__}: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)

        logger.info("Bot instance closed. Preparing for restart...")
        del bot_instance


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("ERROR: DISCORD_TOKEN is missing!")
        exit(1)
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("ERROR: SUPABASE_URL and SUPABASE_KEY are required!")
        exit(1)
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
