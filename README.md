# Discord Job Bot

A Discord bot that automatically fetches and posts new Software Engineer Intern, Product Management Intern, and Machine Learning Intern job listings from the [SimplifyJobs Summer 2026 Internships](https://github.com/SimplifyJobs/Summer2026-Internships) repository.

## Features

- 🔄 **Automatic Updates**: Checks for new jobs every hour
- 🎯 **Smart Filtering**: Only posts relevant roles (SWE, PM, ML, etc.)
- 🏢 **Company Search**: Search for postings from specific companies via `/company`
- 📝 **Duplicate Prevention**: Tracks posted jobs to avoid reposting
- 💼 **Rich Embeds**: Beautiful Discord embeds with application links
- 📊 **Statistics**: Track jobs posted today and total jobs tracked
- ⚡ **Manual Commands**: Full library of slash commands
- 🛡️ **Channel Isolation**: Separate job feeds from bot command spam

## Setup

### Prerequisites

- Python 3.8 or higher
- A Discord bot token
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

5. **Get your Channel ID**:
   - Enable Developer Mode in Discord (User Settings > Advanced > Developer Mode)
   - Right-click on the channel where you want jobs posted
   - Click "Copy ID"

6. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Fill in your Discord bot token and channel ID:
     ```
     DISCORD_TOKEN=your_bot_token_here
     CHANNEL_ID=your_channel_id_here
     ```

### Running the Bot

```bash
python bot.py
```

The bot will:
- Log in to Discord
- Sync slash commands (may take a few minutes to appear in Discord)
- Immediately fetch and post any new jobs
- Continue checking for new jobs every hour

**Note**: Slash commands may take up to an hour to appear globally. If you want commands to appear immediately, you can sync them to a specific guild (server) by modifying the code.

## Commands

- `/company {name}` - List recent job postings from a specific company
- `/latest` - Show the 5 most recent tech internship postings
- `/fetch` - Manually check for new jobs (today only)
- `/stats` - Show statistics about jobs posted today
- `/test` - Test command to verify the bot is working

## How It Works

1. **Job Fetching**: The bot fetches the README.md file from the GitHub repository
2. **Parsing**: Parses the markdown table/list to extract job information
3. **Filtering**: Filters jobs based on role keywords:
   - Software Engineer Intern / SWE Intern
   - Product Manager Intern / PM Intern
   - Machine Learning Intern / ML Intern
4. **Deduplication**: Checks against `jobs_history.json` to avoid reposting
5. **Posting**: Creates Discord embeds and posts new jobs to the configured channel

## File Structure

```
.
├── bot.py              # Main bot file with Discord commands and scheduling
├── job_scraper.py      # Job fetching and parsing logic
├── requirements.txt    # Python dependencies
├── .env.example       # Environment variable template
├── .gitignore         # Git ignore file
├── README.md          # This file
└── jobs_history.json  # Generated file storing posted jobs (auto-created)
```

## Configuration

All configuration is done through environment variables in the `.env` file:

- `DISCORD_TOKEN`: Your Discord bot token
- `POSTINGS_CHANNEL_ID`: The channel ID where automated jobs are posted
- `COMMANDS_CHANNEL_ID`: The channel ID where bot commands are allowed (set to 0 for all)

## Hosting

To keep the bot running 24/7, you should host it on a server. I've prepared a [Hosting Guide](hosting_guide.md) with instructions for:
- Railway (Easiest)
- VPS / DigitalOcean (Recommended)
- Docker

## Troubleshooting

- **Bot not responding**: Check that the bot token is correct and the bot is invited to your server
- **No jobs posted**: Verify the channel ID is correct and the bot has permission to send messages
- **Rate limiting**: The bot includes delays between posts to avoid Discord rate limits
- **Parsing errors**: The GitHub README format may have changed; check the repository structure

## License

This project is open source and available for personal use.
