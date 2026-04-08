import os
import re
import json
import asyncio
import discord
import time
from datetime import datetime
from typing import List, Dict, Optional
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
from job_scraper import JobScraper, Job
from leetcode_scraper import LeetCodeScraper, LeetCodeProblem
from database_manager import DatabaseManager
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('discord')

# Load environment variables
load_dotenv()

# Bot configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
POSTINGS_CHANNEL_ID = int(os.getenv('POSTINGS_CHANNEL_ID', 0))
COMMANDS_CHANNEL_ID = int(os.getenv('COMMANDS_CHANNEL_ID', 0))
JOBS_HISTORY_FILE = 'jobs_history.json'

# --- UTILS ---

def is_valid_url(url: Optional[str]) -> bool:
    """Check if a URL is a valid HTTP/HTTPS URL."""
    if not url:
        return False
    url = url.strip()
    return url.startswith(('http://', 'https://'))


def role_accent(title: str) -> tuple[str, discord.Color]:
    """Return emoji + color accent based on role keywords."""
    t = (title or "").lower()
    if any(k in t for k in ['product manager', 'pm intern', 'product management']):
        return "📊", discord.Color.purple()
    if any(k in t for k in ['machine learning', 'ai', 'data scientist', 'ml']):
        return "🤖", discord.Color.teal()
    return "💼", discord.Color.blurple()

# --- FLASK KEEP-ALIVE ---

app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- BOT CLASS ---

class WagmiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        
        self.scheduler = AsyncIOScheduler()
        self.job_scraper = JobScraper()
        self.leetcode_scraper = LeetCodeScraper()
        self.db = DatabaseManager(DATABASE_URL)
        
        self.stats = {
            'total_posted_today': 0,
            'last_reset_date': datetime.now().date().isoformat()
        }
        self.initialized = False

    def migrate_json_to_db(self):
        """One-time migration of jobs_history.json to the database."""
        if os.path.exists(JOBS_HISTORY_FILE):
            logger.info("Found legacy jobs_history.json. Migrating to database...")
            try:
                with open(JOBS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                
                count = 0
                for job_key in history.keys():
                    # Format: company|title|location
                    parts = job_key.split('|')
                    if len(parts) == 3:
                        company, title, location = parts
                        # Use 0.0 for legacy items
                        job = Job(company=company, title=title, location=location, timestamp=0.0)
                        if not self.db.is_duplicate(job):
                            self.db.add_job(job)
                            count += 1
                
                logger.info(f"Successfully migrated {count} records to DB.")
                os.rename(JOBS_HISTORY_FILE, f"{JOBS_HISTORY_FILE}.bak")
                logger.info(f"Renamed {JOBS_HISTORY_FILE} to {JOBS_HISTORY_FILE}.bak")
            except Exception as e:
                logger.error(f"Migration failed: {e}")

    def reset_daily_stats(self):
        today = datetime.now().date().isoformat()
        if self.stats['last_reset_date'] != today:
            self.stats['total_posted_today'] = 0
            self.stats['last_reset_date'] = today

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
            timestamp=datetime.now()
        )
        embed.add_field(name="📍 Location", value=location, inline=True)
        embed.add_field(name="🕒 Posted", value=posted_date, inline=True)
        if valid_url:
            embed.add_field(name="🔗 Application", value=f"[Open application]({valid_url})", inline=False)
        embed.set_footer(text="Wagmi Bot • Auto-updated every 30m")
        return embed

    async def fetch_and_post_jobs(self):
        try:
            self.reset_daily_stats()
            channel = self.get_channel(POSTINGS_CHANNEL_ID)
            if not channel:
                logger.error(f"Channel with ID {POSTINGS_CHANNEL_ID} not found!")
                return
            
            logger.info("Fetching today's jobs from GitHub...")
            jobs = await asyncio.to_thread(self.job_scraper.fetch_jobs, only_today=True)
            
            if not jobs:
                logger.info("No jobs found or error occurred.")
                return
            
            new_jobs_count = 0
            for job in jobs:
                # Deduplication logic: considers company, title, location, AND timestamp
                if self.db.is_duplicate(job):
                    continue
                
                embed = self.create_job_embed(job)
                await channel.send(embed=embed)
                
                self.db.add_job(job)
                new_jobs_count += 1
                self.stats['total_posted_today'] += 1
                await asyncio.sleep(1)
            
            if new_jobs_count > 0:
                logger.info(f"Posted {new_jobs_count} new job(s)!")
            else:
                logger.info("No new jobs to post.")
        except Exception as e:
            logger.error(f"Error in fetch_and_post_jobs: {e}")

    async def on_ready(self):
        if self.initialized:
            return
        logger.info(f'{self.user} has logged in!')
        
        # Run migration once on startup
        self.migrate_json_to_db()
        
        self.scheduler.add_job(
            self.fetch_and_post_jobs,
            'interval',
            minutes=30,
            id='fetch_jobs',
            replace_existing=True
        )
        self.scheduler.start()
        self.initialized = True
        logger.info("Bot is ready and scheduler started (30 minute intervals).")

    async def setup_hook(self):
        logger.info("Bot setup hook running...")

# --- VIEWS ---

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
            timestamp=datetime.now()
        )
        
        for i, p in enumerate(current_list, start + 1):
            diff_emoji = "🟢" if "Easy" in p.difficulty else "🟡" if "Medium" in p.difficulty else "🔴"
            embed.add_field(
                name=f"{i}. {p.title}",
                value=f"{diff_emoji} {p.difficulty}  •  Acc {p.acceptance}  •  Freq {p.frequency}\n[Open problem]({p.url})",
                inline=False
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
            timestamp=datetime.now()
        )

        for i, job in enumerate(current_jobs, start + 1):
            loc = job.location or "Not specified"
            date_value = job.date_posted or "Unknown date"
            link_value = f"[Apply]({job.link})" if is_valid_url(job.link) else "No valid link"
            role_emoji, _ = role_accent(job.title)
            embed.add_field(
                name=f"{i}. {role_emoji} {job.title}",
                value=f"**{job.company}**\n📍 {loc}\n🕒 {date_value}\n🔗 {link_value}",
                inline=False
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

# --- COMMANDS ---

def register_commands(bot: WagmiBot):
    cache = {
        "companies": [],
        "companies_ts": 0.0,
        "leetcode_companies": [],
        "leetcode_companies_ts": 0.0
    }

    COMPANY_CACHE_TTL_SECONDS = 300
    LEETCODE_COMPANY_CACHE_TTL_SECONDS = 3600

    async def get_job_companies() -> List[str]:
        now_ts = time.time()
        if cache["companies"] and (now_ts - cache["companies_ts"] < COMPANY_CACHE_TTL_SECONDS):
            return cache["companies"]
        jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, False)
        company_set = {job.company for job in jobs if job.company}
        companies = sorted(company_set)
        cache["companies"] = companies
        cache["companies_ts"] = now_ts
        return companies

    async def get_leetcode_companies() -> List[str]:
        now_ts = time.time()
        if cache["leetcode_companies"] and (now_ts - cache["leetcode_companies_ts"] < LEETCODE_COMPANY_CACHE_TTL_SECONDS):
            return cache["leetcode_companies"]
        companies = await asyncio.to_thread(bot.leetcode_scraper.fetch_company_list)
        cache["leetcode_companies"] = companies
        cache["leetcode_companies_ts"] = now_ts
        return companies
    
    async def check_channel(ctx_or_int) -> bool:
        channel_id = getattr(ctx_or_int, 'channel_id', getattr(ctx_or_int, 'channel', None).id if hasattr(ctx_or_int, 'channel') else 0)
        if COMMANDS_CHANNEL_ID != 0 and channel_id != COMMANDS_CHANNEL_ID:
            msg = f"❌ This command can only be used in <#{COMMANDS_CHANNEL_ID}>."
            if isinstance(ctx_or_int, discord.Interaction):
                await ctx_or_int.response.send_message(msg, ephemeral=True)
            else:
                await ctx_or_int.send(msg)
            return False
        return True

    @bot.tree.command(name='latest', description='Show the 5 most recent tech internship postings')
    async def latest_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
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

    @bot.tree.command(name='stats', description='Show statistics about jobs posted today')
    async def stats_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
        bot.reset_daily_stats()
        total = bot.db.get_total_count()
        embed = discord.Embed(title="📊 Job Bot Statistics", color=discord.Color.green())
        embed.add_field(name="Posted Today", value=str(bot.stats['total_posted_today']))
        embed.add_field(name="Total Tracked", value=str(total))
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name='company', description='Search for the 5 most recent jobs at a specific company')
    @discord.app_commands.describe(name='Company name to search for')
    async def company_slash(interaction: discord.Interaction, name: str):
        if not await check_channel(interaction): return
        await interaction.response.defer()
        try:
            jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, only_today=False)
            company_jobs = [j for j in jobs if name.lower() in j.company.lower()]
            if not company_jobs:
                await interaction.followup.send(f"❌ No jobs found for **{name}**.")
                return
            view = JobResultsView(company_jobs, f"🏢 Recent Jobs at {name}", per_page=5)
            await interaction.followup.send(embed=view.get_embed(), view=view)
        except Exception as e:
            logger.error(f"Error in company command: {e}")
            await interaction.followup.send("❌ Error searching for jobs.")

    @bot.tree.command(name='leetcode', description='Fetch company leetcode list')
    @discord.app_commands.describe(company='Company name (e.g., google, amazon)')
    async def leetcode_slash(interaction: discord.Interaction, company: str):
        if not await check_channel(interaction): return
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

    @company_slash.autocomplete('name')
    async def company_autocomplete(interaction: discord.Interaction, current: str):
        companies = await get_job_companies()
        current_lower = current.lower().strip()
        if current_lower:
            matches = [c for c in companies if current_lower in c.lower()]
        else:
            matches = companies
        return [
            discord.app_commands.Choice(name=company[:100], value=company)
            for company in matches[:25]
        ]

    @leetcode_slash.autocomplete('company')
    async def leetcode_company_autocomplete(interaction: discord.Interaction, current: str):
        companies = await get_leetcode_companies()
        current_lower = current.lower().strip()
        if current_lower:
            matches = [c for c in companies if current_lower in c.lower()]
        else:
            matches = companies
        return [
            discord.app_commands.Choice(name=company[:100], value=company)
            for company in matches[:25]
        ]

    @bot.tree.command(name='fetch', description='Manually trigger a job search and post new ones')
    async def fetch_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
        await interaction.response.send_message("🔄 Manually triggering job fetch...")
        await bot.fetch_and_post_jobs()

    @bot.tree.command(name='test', description='Check if the bot is alive and responsive')
    async def test_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
        await interaction.response.send_message("✅ Bot is working!")

    @bot.command(name='sync')
    @commands.is_owner()
    async def sync_prefix(ctx):
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"✅ Synced {len(synced)} command(s)!")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}")

# --- MAIN LOOP ---

async def start_bot():
    retry_delay = 60
    max_delay = 600
    
    logger.info("Initializing environment...")
    keep_alive()
    
    while True:
        logger.info(f"Attempting to start Wagmi Bot instance...")
        bot_instance = WagmiBot()
        register_commands(bot_instance)
        
        try:
            async with bot_instance:
                await bot_instance.start(DISCORD_TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logger.error(f"CRITICAL: 429 Too Many Requests (Rate Limited).")
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

if __name__ == '__main__':
    if not DISCORD_TOKEN:
        logger.error("ERROR: DISCORD_TOKEN is missing!")
        exit(1)
    
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
