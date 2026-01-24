# Discord Job Bot

A Discord bot that automatically fetches and posts new Software Engineer Intern, Product Management Intern, and Machine Learning Intern job listings from the [SimplifyJobs Summer 2026 Internships](https://github.com/SimplifyJobs/Summer2026-Internships) repository.

## Features

- 🔄 **Automatic Updates**: Checks for new jobs every **30 minutes**
- 🎯 **Smart Filtering**: Only posts relevant roles (SWE, PM, ML, etc.)
- 🏢 **Company Search**: Search for postings from specific companies via `/company`
- 🧠 **LeetCode Questions**: Get interview questions for specific companies via `/leetcode`
- 📝 **Duplicate Prevention**: Persistent database storage with timestamp-aware deduplication
- 💼 **Rich Embeds**: Beautiful Discord embeds with application links
- 📊 **Statistics**: Track jobs posted today and total jobs tracked
- ⚡ **Manual Commands**: Full library of slash commands
- 🛡️ **Channel Isolation**: Separate job feeds from bot command spam

## Setup

### Prerequisites

- Python 3.8 or higher
- A Discord bot token
- A database (PostgreSQL, MySQL, or SQLite)
- A Discord channel ID where jobs should be posted

### Installation

1. **Clone or download this repository**

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Create a Discord Bot**:
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application
   - Go to "Bot" section and create a bot
   - Copy the bot token
   - Enable "Message Content Intent" under Privileged Gateway Intents

4. **Invite the bot to your server**:
   - Go to "OAuth2" > "URL Generator"
   - Select "bot" and "applications.commands" scopes (required for slash commands)
   - Select permissions: "Send Messages", "Embed Links", "Read Message History"
   - Copy the generated URL and open it in your browser to invite the bot

5. **Get your Channel IDs**:
   - Enable Developer Mode in Discord (User Settings > Advanced > Developer Mode)
   - Right-click on the channel where you want jobs posted (Postings Channel)
   - Right-click on the channel where you want bot commands (Commands Channel)
   - Click "Copy ID" for both

6. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Fill in your Discord bot token, channel IDs, and database URL:
     ```
     DISCORD_TOKEN=your_bot_token_here
     POSTINGS_CHANNEL_ID=your_channel_id_here
     COMMANDS_CHANNEL_ID=your_channel_id_here
     DATABASE_URL=postgresql://user:password@localhost:5432/dbname
     ```
     *Note: If `DATABASE_URL` is omitted, the bot will default to a local SQLite database (`jobs_history.db`).*

### Running the Bot

```bash
python bot.py
```

The bot will:
- Log in to Discord
- Sync slash commands (may take a few minutes to appear in Discord)
- Automatically migrate any legacy `jobs_history.json` data to the database on first run
- Immediately fetch and post any new jobs
- Continue checking for new jobs every 30 minutes

## Commands

- `/leetcode {company}` - Get LeetCode interview questions for a specific company
- `/company {name}` - List recent job postings from a specific company
- `/latest` - Show the 5 most recent tech internship postings
- `/fetch` - Manually check for new jobs (today only)
- `/stats` - Show statistics about jobs posted today
- `/test` - Test command to verify the bot is working

## How It Works

1. **Job Fetching**: The bot fetches the README.md file from the GitHub repository.
2. **Parsing**: Parses the markdown table/list to extract job information.
3. **Filtering**: Filters jobs based on role keywords (SWE, PM, ML, etc.).
4. **Deduplication**: Checks against a persistent database. It uses a combination of company, title, location, and timestamp to identify unique postings, allowing reposted jobs to be posted again if their timestamp has changed.
5. **Posting**: Creates Discord embeds and posts new jobs to the configured channel.

## File Structure

```
.
├── bot.py              # Main bot file with Discord commands and scheduling
├── database_manager.py # Database interaction logic and models
├── job_scraper.py      # Job fetching and parsing logic
├── leetcode_scraper.py # LeetCode question fetching logic
├── requirements.txt    # Python dependencies
├── .env.example       # Environment variable template
├── .gitignore         # Git ignore file
└── README.md          # This file
```

## Configuration

All configuration is done through environment variables in the `.env` file:

- `DISCORD_TOKEN`: Your Discord bot token
- `POSTINGS_CHANNEL_ID`: The channel ID where automated jobs are posted
- `COMMANDS_CHANNEL_ID`: The channel ID where bot commands are allowed (set to 0 for all)
- `DATABASE_URL`: Connection string for your database (SQLAlchemy format)

## Hosting

To keep the bot running 24/7, you should host it on a server like Railway, Render, or a VPS.
The bot includes a Flask keep-alive server for hosting on platforms like Render.

## License

This project is open source and available for personal use.
