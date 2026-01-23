import os
import re
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
import discord
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

# Initialize bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize scheduler and scraper
scheduler = AsyncIOScheduler()
job_scraper = JobScraper()

# Statistics tracking
stats = {
    'total_posted_today': 0,
    'last_reset_date': datetime.now().date().isoformat()
}
initialized = False


# Flask Keep-Alive Server
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    # Use PORT environment variable for Render, default to 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True # Ensure thread exits when main program does
    t.start()


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


def reset_daily_stats():
    """Reset daily statistics if it's a new day."""
    today = datetime.now().date().isoformat()
    if stats['last_reset_date'] != today:
        stats['total_posted_today'] = 0
        stats['last_reset_date'] = today


def create_job_embed(job: Job) -> discord.Embed:
    """Create a Discord embed for a job posting."""
    # Determine emoji based on role
    emoji = "💼"  # Default for SWE
    if any(keyword in job.title.lower() for keyword in ['product manager', 'pm intern', 'product management']):
        emoji = "📊"
    
    # Validate URL - only use valid HTTP/HTTPS URLs
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
    
    # Only add link field if we have a valid URL
    if valid_url:
        embed.add_field(name="🔗 Application Link", value=f"[Apply Here]({valid_url})", inline=False)
    
    embed.set_footer(text="Job Bot | Hourly Updates")
    
    return embed


async def fetch_and_post_jobs():
    """Fetch new jobs and post them to Discord."""
    try:
        reset_daily_stats()
        
        # Get channel
        channel = bot.get_channel(POSTINGS_CHANNEL_ID)
        if not channel:
            print(f"Channel with ID {POSTINGS_CHANNEL_ID} not found!")
            return
        
        # Load jobs history
        jobs_history = load_jobs_history()
        
        # Fetch jobs from GitHub
        print("Fetching today's jobs from GitHub...")
        jobs = await asyncio.to_thread(job_scraper.fetch_jobs, only_today=True)
        
        if not jobs:
            print("No jobs found or error occurred.")
            return
        
        # Filter and post new jobs
        new_jobs_count = 0
        for job in jobs:
            job_key = get_job_key(job)
            
            # Skip if already posted
            if job_key in jobs_history:
                continue
            
            # Post job to Discord
            embed = create_job_embed(job)
            await channel.send(embed=embed)
            
            # Mark as posted
            jobs_history[job_key] = True
            new_jobs_count += 1
            stats['total_posted_today'] += 1
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(1)
        
        # Save updated history
        save_jobs_history(jobs_history)
        
        if new_jobs_count > 0:
            print(f"Posted {new_jobs_count} new job(s) to Discord!")
        else:
            print("No new jobs to post.")
            
    except Exception as e:
        print(f"Error in fetch_and_post_jobs: {e}")
        import traceback
        traceback.print_exc()


@bot.event
async def on_ready():
    """Called when the bot is ready."""
    global initialized
    if initialized:
        return
        
    print(f'{bot.user} has logged in!')
    print(f'Bot is in {len(bot.guilds)} guild(s)')
    
    # Schedule hourly job fetching
    scheduler.add_job(
        fetch_and_post_jobs,
        'interval',
        hours=1,
        id='fetch_jobs',
        replace_existing=True
    )
    scheduler.start()
    print("Scheduler started - will fetch jobs every hour")
    
    initialized = True
    print("Bot is ready and waiting for commands.")
    # Removed immediate fetch_and_post_jobs() to reduce startup noise

@bot.command(name='sync')
@commands.is_owner()
async def sync_commands(ctx):
    """Manually sync slash commands. Only accessible by bot owner."""
    try:
        print("Syncing commands...")
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} command(s)!")
        print(f"Synced {len(synced)} command(s): {[c.name for c in synced]}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        await ctx.send(f"❌ Failed to sync: {e}")

async def check_commands_channel(interaction: discord.Interaction) -> bool:
    """Check if the command is being used in the correct channel."""
    if COMMANDS_CHANNEL_ID != 0 and interaction.channel_id != COMMANDS_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ This command can only be used in the <#{COMMANDS_CHANNEL_ID}> channel.",
            ephemeral=True
        )
        return False
    return True


@bot.tree.command(name='latest', description='Show the 5 most recent tech internship postings')
async def latest_command(interaction: discord.Interaction):
    """Show the 5 most recent tech internship postings."""
    if not await check_commands_channel(interaction):
        return
    await interaction.response.defer()
    
    try:
        # Fetch latest jobs (all, not just today) to ensure we have content
        jobs = await asyncio.to_thread(job_scraper.fetch_jobs, only_today=False)
        
        if not jobs:
            await interaction.followup.send("❌ No jobs found matching the criteria.")
            return
        
        # Take the most recent 5
        latest_5 = jobs[:5]
        
        embed = discord.Embed(
            title="🆕 Latest Tech Internships",
            description="Here are the 5 most recently posted roles:",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for i, job in enumerate(latest_5, 1):
            location = job.location if job.location else "Not specified"
            date_str = f" ({job.date_posted})" if job.date_posted else ""
            value = f"📍 {location}{date_str}\n🔗 [Apply Here]({job.link})"
            embed.add_field(
                name=f"{i}. {job.company} - {job.title}",
                value=value,
                inline=False
            )
        
        embed.set_footer(text="Use /stats for more info")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"Error in latest_command: {e}")
        await interaction.followup.send("❌ An error occurred while fetching the latest jobs.")

@bot.command(name='latest')
async def latest_prefix(ctx):
    """Fallback prefix command for latest."""
    if COMMANDS_CHANNEL_ID != 0 and ctx.channel.id != COMMANDS_CHANNEL_ID:
        return
    
    async with ctx.typing():
        try:
            jobs = await asyncio.to_thread(job_scraper.fetch_jobs, only_today=False)
            if not jobs:
                await ctx.send("❌ No jobs found matching the criteria.")
                return
            
            latest_5 = jobs[:5]
            embed = discord.Embed(
                title="🆕 Latest Tech Internships",
                description="Here are the 5 most recently posted roles:",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            for i, job in enumerate(latest_5, 1):
                location = job.location if job.location else "Not specified"
                date_str = f" ({job.date_posted})" if job.date_posted else ""
                value = f"📍 {location}{date_str}\n🔗 [Apply Here]({job.link})"
                embed.add_field(name=f"{i}. {job.company} - {job.title}", value=value, inline=False)
            
            embed.set_footer(text="Use !stats for more info")
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in latest_prefix: {e}")
            await ctx.send("❌ An error occurred while fetching the latest jobs.")


@bot.tree.command(name='company', description='List recent job postings from a specific company')
@discord.app_commands.describe(company="The name of the company to search for")
async def company_command(interaction: discord.Interaction, company: str):
    """List recent job postings from a specific company."""
    if not await check_commands_channel(interaction):
        return
    await interaction.response.defer()
    
    try:
        # Fetch all jobs to allow filtering by company
        jobs = await asyncio.to_thread(job_scraper.fetch_jobs, only_today=False)
        
        if not jobs:
            await interaction.followup.send("❌ No jobs found.")
            return

        # Improved matching logic: prioritize exact word matches
        company_query = company.strip()
        
        # Try word boundary match first (e.g., "Intuit" matches "Intuit Inc" but not "Intuitive")
        pattern = re.compile(rf'\b{re.escape(company_query)}\b', re.IGNORECASE)
        filtered_jobs = [job for job in jobs if pattern.search(job.company)]
        
        # Fallback to substring match if no word-boundary matches are found
        if not filtered_jobs:
            company_lower = company_query.lower()
            filtered_jobs = [
                job for job in jobs 
                if company_lower in job.company.lower()
            ]
        
        if not filtered_jobs:
            await interaction.followup.send(f"❌ No jobs found for **{company}**.")
            return
        
        # Limit to the top 10 most recent results
        display_jobs = filtered_jobs[:10]
        
        embed = discord.Embed(
            title=f"🏢 Jobs at {display_jobs[0].company}",
            description=f"Here are the most recent postings matching '**{company}**':",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for i, job in enumerate(display_jobs, 1):
            location = job.location if job.location else "Not specified"
            date_str = f" ({job.date_posted})" if job.date_posted else ""
            value = f"📍 {location}{date_str}\n🔗 [Apply Here]({job.link})"
            embed.add_field(
                name=f"{i}. {job.title}",
                value=value,
                inline=False
            )
        
        if len(filtered_jobs) > 10:
            embed.set_footer(text=f"Showing 10 of {len(filtered_jobs)} total results found.")
        else:
            embed.set_footer(text=f"Total: {len(filtered_jobs)} job(s) found.")
            
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"Error in company_command: {e}")
        await interaction.followup.send("❌ An error occurred while searching for company jobs.")

@bot.command(name='company')
async def company_prefix(ctx, *, company: str):
    """Fallback prefix command for company."""
    if COMMANDS_CHANNEL_ID != 0 and ctx.channel.id != COMMANDS_CHANNEL_ID:
        return
    
    async with ctx.typing():
        try:
            jobs = await asyncio.to_thread(job_scraper.fetch_jobs, only_today=False)
            if not jobs:
                await ctx.send("❌ No jobs found.")
                return

            company_query = company.strip()
            pattern = re.compile(rf'\b{re.escape(company_query)}\b', re.IGNORECASE)
            filtered_jobs = [job for job in jobs if pattern.search(job.company)]
            
            if not filtered_jobs:
                company_lower = company_query.lower()
                filtered_jobs = [job for job in jobs if company_lower in job.company.lower()]
            
            if not filtered_jobs:
                await ctx.send(f"❌ No jobs found for **{company}**.")
                return
            
            display_jobs = filtered_jobs[:10]
            embed = discord.Embed(
                title=f"🏢 Jobs at {display_jobs[0].company}",
                description=f"Here are the most recent postings matching '**{company}**':",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            for i, job in enumerate(display_jobs, 1):
                location = job.location if job.location else "Not specified"
                date_str = f" ({job.date_posted})" if job.date_posted else ""
                value = f"📍 {location}{date_str}\n🔗 [Apply Here]({job.link})"
                embed.add_field(name=f"{i}. {job.title}", value=value, inline=False)
            
            footer_text = f"Showing 10 of {len(filtered_jobs)} total." if len(filtered_jobs) > 10 else f"Total: {len(filtered_jobs)} job(s)."
            embed.set_footer(text=footer_text)
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in company_prefix: {e}")
            await ctx.send("❌ An error occurred while searching.")


@bot.tree.command(name='fetch', description='Manually fetch and post new jobs (today only)')
async def fetch_command(interaction: discord.Interaction):
    """Manually fetch and post new jobs (today only)."""
    if not await check_commands_channel(interaction):
        return
    await interaction.response.send_message("🔄 Checking for new jobs posted today...")
    await fetch_and_post_jobs()
    await interaction.followup.send("✅ Check completed!")


@bot.tree.command(name='stats', description='Show statistics about jobs posted today')
async def stats_command(interaction: discord.Interaction):
    """Show statistics about jobs posted today."""
    if not await check_commands_channel(interaction):
        return
    reset_daily_stats()
    
    jobs_history = load_jobs_history()
    total_jobs = len(jobs_history)
    
    embed = discord.Embed(
        title="📊 Job Bot Statistics",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Jobs Posted Today",
        value=str(stats['total_posted_today']),
        inline=True
    )
    embed.add_field(
        name="Total Jobs Tracked",
        value=str(total_jobs),
        inline=True
    )
    embed.set_footer(text=f"Last reset: {stats['last_reset_date']}")
    
    await interaction.response.send_message(embed=embed)

@bot.command(name='stats')
async def stats_prefix(ctx):
    """Fallback prefix command for stats."""
    if COMMANDS_CHANNEL_ID != 0 and ctx.channel.id != COMMANDS_CHANNEL_ID:
        return
    reset_daily_stats()
    jobs_history = load_jobs_history()
    total_jobs = len(jobs_history)
    
    embed = discord.Embed(title="📊 Job Bot Statistics", color=discord.Color.green())
    embed.add_field(name="Jobs Posted Today", value=str(stats['total_posted_today']), inline=True)
    embed.add_field(name="Total Jobs Tracked", value=str(total_jobs), inline=True)
    embed.set_footer(text=f"Last reset: {stats['last_reset_date']}")
    await ctx.send(embed=embed)

@bot.command(name='test')
async def test_prefix(ctx):
    """Fallback prefix command for test."""
    if COMMANDS_CHANNEL_ID != 0 and ctx.channel.id != COMMANDS_CHANNEL_ID:
        return
    await ctx.send("✅ Bot is working (prefix command)!")

@bot.command(name='fetch')
async def fetch_prefix(ctx):
    """Fallback prefix command for fetch."""
    if COMMANDS_CHANNEL_ID != 0 and ctx.channel.id != COMMANDS_CHANNEL_ID:
        return
    await ctx.send("🔄 Checking for new jobs posted today...")
    await fetch_and_post_jobs()
    await ctx.send("✅ Check completed!")


@bot.tree.command(name='test', description='Test command to verify bot is working')
async def test_command(interaction: discord.Interaction):
    """Test command to verify bot is working."""
    if not await check_commands_channel(interaction):
        return
    await interaction.response.send_message("✅ Bot is working!")


if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found in environment variables!")
        exit(1)
    
    if not POSTINGS_CHANNEL_ID:
        print("ERROR: POSTINGS_CHANNEL_ID not found in environment variables!")
        exit(1)
    
    import time
    retry_delay = 30  # Start with 30 seconds
    max_delay = 600   # Max 10 minutes
    
    # Small delay to avoid hammering Discord if Render restarts it quickly
    print("Pre-start cooldown (5 seconds)...")
    time.sleep(5)
    
    async def runner():
        async with bot:
            await bot.start(DISCORD_TOKEN)

    keep_alive()
    
    while True:
        try:
            print(f"Attempting to log in to Discord...")
            import asyncio
            asyncio.run(runner())
            # If runner() exits normally, break the loop
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"CRITICAL: 429 Too Many Requests (Rate Limited).")
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                # Exponential backoff
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                print(f"Discord HTTP Error: {e}")
                time.sleep(30)
        except Exception as e:
            if "Session is closed" in str(e):
                print("Session was closed, retrying with fresh session...")
            else:
                print(f"Unexpected error running bot: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
        
        print("Restarting bot loop...")
