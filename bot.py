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

# Load environment variables
load_dotenv()

# Bot configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
POSTINGS_CHANNEL_ID = int(os.getenv('POSTINGS_CHANNEL_ID', 0))
COMMANDS_CHANNEL_ID = int(os.getenv('COMMANDS_CHANNEL_ID', 0))
JOBS_HISTORY_FILE = 'jobs_history.json'

# --- GLOBAL UTILS ---

def load_jobs_history() -> Dict[str, bool]:
    """Load previously posted jobs from JSON file."""
    try:
        if os.path.exists(JOBS_HISTORY_FILE):
            with open(JOBS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading jobs history: {e}")
    return {}

def save_jobs_history(jobs_history: Dict[str, bool]):
    """Save posted jobs to JSON file."""
    try:
        with open(JOBS_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(jobs_history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving jobs history: {e}")

def get_job_key(job: Job) -> str:
    """Generate a unique key for a job to track duplicates."""
    return f"{job.company}|{job.title}|{job.location}"

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
        self.stats = {
            'total_posted_today': 0,
            'last_reset_date': datetime.now().date().isoformat()
        }
        self.initialized = False

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
                print(f"Channel with ID {POSTINGS_CHANNEL_ID} not found!")
                return
            
            jobs_history = load_jobs_history()
            print("Fetching today's jobs from GitHub...")
            jobs = await asyncio.to_thread(self.job_scraper.fetch_jobs, only_today=True)
            
            if not jobs:
                print("No jobs found or error occurred.")
                return
            
            new_jobs_count = 0
            for job in jobs:
                job_key = get_job_key(job)
                if job_key in jobs_history:
                    continue
                
                embed = self.create_job_embed(job)
                await channel.send(embed=embed)
                
                jobs_history[job_key] = True
                new_jobs_count += 1
                self.stats['total_posted_today'] += 1
                await asyncio.sleep(1)
            
            save_jobs_history(jobs_history)
            if new_jobs_count > 0:
                print(f"Posted {new_jobs_count} new job(s)!")
            else:
                print("No new jobs to post.")
        except Exception as e:
            print(f"Error in fetch_and_post_jobs: {e}")

    async def on_ready(self):
        if self.initialized:
            return
        print(f'{self.user} has logged in!')
        
        self.scheduler.add_job(
            self.fetch_and_post_jobs,
            'interval',
            hours=1,
            id='fetch_jobs',
            replace_existing=True
        )
        self.scheduler.start()
        self.initialized = True
        print("Bot is ready and scheduler started.")

    async def setup_hook(self):
        print("Bot setup hook running...")
        # We don't auto-sync here to avoid rate limits on Render

# --- INSTANTIATE & COMMANDS ---

# These will be applied to the bot instance created in the main loop
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

    # SLASH COMMANDS
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
        except Exception:
            await interaction.followup.send("❌ Error fetching jobs.")

    @bot.tree.command(name='stats', description='Show statistics about jobs posted today')
    async def stats_slash(interaction: discord.Interaction):
        if not await check_channel(interaction): return
        bot.reset_daily_stats()
        total = len(load_jobs_history())
        embed = discord.Embed(title="📊 Job Bot Statistics", color=discord.Color.green())
        embed.add_field(name="Posted Today", value=str(bot.stats['total_posted_today']))
        embed.add_field(name="Total Tracked", value=str(total))
        await interaction.response.send_message(embed=embed)

    # PREFIX COMMANDS (Fallbacks)
    @bot.command(name='sync')
    @commands.is_owner()
    async def sync_prefix(ctx):
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"✅ Synced {len(synced)} command(s)!")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}")

    @bot.command(name='latest')
    async def latest_prefix(ctx):
        if not await check_channel(ctx): return
        async with ctx.typing():
            try:
                jobs = await asyncio.to_thread(bot.job_scraper.fetch_jobs, only_today=False)
                if not jobs:
                    await ctx.send("❌ No jobs found.")
                    return
                latest_5 = jobs[:5]
                embed = discord.Embed(title="🆕 Latest Tech Internships", color=discord.Color.blue(), timestamp=datetime.now())
                for i, job in enumerate(latest_5, 1):
                    loc = job.location or "Not specified"
                    val = f"📍 {loc} ({job.date_posted})\n🔗 [Apply]({job.link})"
                    embed.add_field(name=f"{i}. {job.company} - {job.title}", value=val, inline=False)
                await ctx.send(embed=embed)
            except Exception:
                await ctx.send("❌ Error fetching jobs.")

    @bot.command(name='fetch')
    async def fetch_prefix(ctx):
        if not await check_channel(ctx): return
        await ctx.send("🔄 Fetching jobs...")
        await bot.fetch_and_post_jobs()
        await ctx.send("✅ Check completed!")

    @bot.command(name='test')
    async def test_prefix(ctx):
        await ctx.send("✅ Bot is online!")

# --- MAIN LOOP ---

async def start_bot():
    retry_delay = 60  # Initial wait 1 minute
    max_delay = 600   # Max 10 minutes
    
    print("Initializing environment...")
    keep_alive()
    
    while True:
        print(f"Attempting to start Wagmi Bot...")
        bot_instance = WagmiBot()
        register_commands(bot_instance)
        
        try:
            async with bot_instance:
                await bot_instance.start(DISCORD_TOKEN)
            break # If it exits cleanly
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"CRITICAL: 429 Too Many Requests (Rate Limited).")
                print(f"Wait before retry: {retry_delay}s")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                print(f"Discord HTTP Error ({e.status}): {e}")
                await asyncio.sleep(30)
        except Exception as e:
            print(f"Bot execution error: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
        
        print("Bot instance closed. Cleaning up and restarting...")
        # Clear the instance to ensure fresh session on next loop
        del bot_instance

if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN is missing!")
        exit(1)
    
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
