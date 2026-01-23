import asyncio
from job_scraper import JobScraper

async def test_scraper():
    print("Testing JobScraper...")
    scraper = JobScraper()
    
    # Test 1: All jobs
    all_jobs = scraper.fetch_jobs(only_today=False)
    print(f"Found {len(all_jobs)} total tech jobs.")
    
    # Test 2: Today only
    today_jobs = scraper.fetch_jobs(only_today=True)
    print(f"Found {len(today_jobs)} jobs posted today (based on local time).")
    
    if today_jobs:
        print("\nJobs found today:")
        for i, job in enumerate(today_jobs, 1):
            print(f"{i}. {job.company} - {job.title}")
            print(f"   Location: {job.location}")
            print(f"   Date Posted: {job.date_posted}")
            print("-" * 30)
    elif all_jobs:
        print("\nNo jobs posted today, but here's a sample of all tech jobs found:")
        for i, job in enumerate(all_jobs[:3], 1):
            print(f"{i}. {job.company} - {job.title}")
            print(f"   Location: {job.location}")
            print("-" * 30)

if __name__ == "__main__":
    asyncio.run(test_scraper())
