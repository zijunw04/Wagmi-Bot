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
        self.stats = {
            'total_posted_today': 0,
            'last_reset_date': datetime.now().date().isoformat()
        }
        self.initialized = False

    def load_jobs_history(self) -> Dict[str, bool]:
        """Load previously posted jobs from JSON file."""
        try:
            if os.path.exists(JOBS_HISTORY_FILE):
                with open(JOBS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading jobs history: {e}")
        return {}

    def save_jobs_history(self, jobs_history: Dict[str, bool]):
        """Save posted jobs to JSON file."""
        try:
            with open(JOBS_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving jobs history: {e}")

    def get_job_key(self, job: Job) -> str:
        """Generate a unique key for a job to track duplicates."""
        return f"{job.company}|{job.title}|{job.location}"

    def reset_daily_stats(self):
        today = datetime.now().date().isoformat()
        if self.stats['last_reset_date'] != today:
            self.stats['total_posted_today'] = 0
            self.stats['last_reset_date'] = today

    def create_job_embed(self, job: Job) -> discord.Embed:
        emoji = "💼"
        if any(keyword in job.title.lower() for keyword in ['product manager', 'pm intern', 'product management']):
            emoji = "📊"
        
        valid_url = job.link if is_valid_url(job.link) else None
        embed = discord.Embed(
            title=f"{emoji} {job.title}",
            description=f"**Company:** {job.company}",
            color=discord.Color.blue(),
            url=valid_url,
            timestamp=datetime.now()
        )
        if job.location:
            embed.add_field(name="📍 Location", value=job.location, inline=True)
        if job.date_posted:
            embed.add_field(name="📅 Date Posted", value=job.date_posted, inline=True)
        if valid_url:
            embed.add_field(name="🔗 Application Link", value=f"[Apply Here]({valid_url})", inline=False)
        embed.set_footer(text="Job Bot | Hourly Updates")
        return embed

    async def fetch_and_post_jobs(self):
        try:
            self.reset_daily_stats()
            channel = self.get_channel(POSTINGS_CHANNEL_ID)
            if not channel:
                logger.error(f"Channel with ID {POSTINGS_CHANNEL_ID} not found!")
                return
            
            jobs_history = self.load_jobs_history()
            logger.info("Fetching today's jobs from GitHub...")
            jobs = await asyncio.to_thread(self.job_scraper.fetch_jobs, only_today=True)
            
            if not jobs:
                logger.info("No jobs found or error occurred.")
                return
            
            new_jobs_count = 0
            for job in jobs:
                job_key = self.get_job_key(job)
                if job_key in jobs_history:
                    continue
                
                embed = self.create_job_embed(job)
                await channel.send(embed=embed)
                
                jobs_history[job_key] = True
                new_jobs_count += 1
                self.stats['total_posted_today'] += 1
                await asyncio.sleep(1)
            
            self.save_jobs_history(jobs_history)
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
        
        self.scheduler.add_job(
            self.fetch_and_post_jobs,
            'interval',
            hours=1,
            id='fetch_jobs',
            replace_existing=True
        )
        self.scheduler.start()
        self.initialized = True
        logger.info("Bot is ready and scheduler started.")

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
            description=f"Showing top questions for **{self.company.title()}**.\nSorted by: **{sort_mode}**",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        
        for i, p in enumerate(current_list, start + 1):
            diff_emoji = "🟢" if "Easy" in p.difficulty else "🟡" if "Medium" in p.difficulty else "🔴"
            embed.add_field(
                name=f"{i}. {p.title}",
                value=f"{diff_emoji} {p.difficulty} | Acc: {p.acceptance} | Freq: {p.frequency}\n[Link to Problem]({p.url})",
                inline=False
            )
            
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} | {len(self.problems)} total")
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

# --- COMMANDS ---

def register_commands(bot: WagmiBot):
    
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
            latest_5 = jobs[:5]
            embed = discord.Embed(title="🆕 Latest Tech Internships", color=discord.Color.blue(), timestamp=datetime.now())
            for i, job in enumerate(latest_5, 1):
                loc = job.location or "Not specified"
                val = f"📍 {loc} ({job.date_posted})\n🔗 [Apply]({job.link})"
                embed.add_field(name=f"{i}. {job.company} - {job.title}", value=val, inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in latest command: {e}")
            await interaction.followup.send("❌ Error fetching jobs.")

    @bot.tree.command(name='stats', description='Show statistics about jobs posted today')
    async def stats_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
        bot.reset_daily_stats()
        total = len(bot.load_jobs_history())
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
            latest_5 = company_jobs[:5]
            embed = discord.Embed(title=f"🏢 Recent Jobs at {name}", color=discord.Color.blue(), timestamp=datetime.now())
            for i, job in enumerate(latest_5, 1):
                loc = job.location or "Not specified"
                val = f"📍 {loc} ({job.date_posted})\n🔗 [Apply]({job.link})"
                embed.add_field(name=f"{i}. {job.title}", value=val, inline=False)
            await interaction.followup.send(embed=embed)
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
