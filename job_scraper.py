import re
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime
import requests


@dataclass
class Job:
    """Represents a job posting."""
    company: str
    title: str
    location: str
    link: Optional[str] = None
    date_posted: Optional[str] = None
    timestamp: float = 0.0


class JobScraper:
    """Scrapes job listings from GitHub JSON source."""
    
    GITHUB_JSON_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"
    
    # All tech-related keywords to be as inclusive as possible
    TECH_KEYWORDS = [
        'software', 'swe', 'engineer', 'developer', 'coding', 'programming',
        'technology', 'tech', 'information', 'it intern', 'i.t.',
        'product', 'pm intern', 'product management',
        'machine learning', 'ml ', 'ai ', 'artificial intelligence',
        'data', 'analytics', 'statistic', 'quant', 'math',
        'cyber', 'security', 'infosec', 'network',
        'cloud', 'devops', 'sre', 'reliability', 'infrastructure', 'ops',
        'systems', 'hardware', 'firmware', 'embedded', 'robotics',
        'mobile', 'ios', 'android', 'web', 'frontend', 'backend', 'fullstack',
        'app ', 'application', 'platform', 'interface', 'ux', 'ui ',
        'qa ', 'testing', 'automation', 'technical', 'compute'
    ]
    
    # Categories that are definitely tech-related
    TECH_CATEGORIES = {
        'software', 'software engineering', 'ai/ml/data', 'ai', 'data',
        'product', 'quant', 'quantitative finance', 'hardware', 'security',
        'infrastructure', 'devops', 'mobile', 'web', 'ux/ui'
    }
    
    def __init__(self):
        self.all_keywords = self.TECH_KEYWORDS
    
    def fetch_jobs(self, only_today: bool = False) -> List[Job]:
        """Fetch and parse jobs from GitHub JSON."""
        try:
            response = requests.get(self.GITHUB_JSON_URL, timeout=10)
            response.raise_for_status()
            listings = response.json()
            
            # Start of today (local time)
            now = datetime.now()
            today_start = datetime(now.year, now.month, now.day).timestamp()
            
            jobs = []
            for item in listings:
                # Check if the job is active and has a URL
                if not item.get('active', False) or not item.get('url'):
                    continue
                
                # Filter by date if requested
                date_posted_ts = item.get('date_posted', 0)
                if only_today and date_posted_ts < today_start:
                    continue
                
                title = item.get('title', '')
                company = item.get('company_name', '')
                category = item.get('category', '')
                category_lower = category.lower()
                
                # Broad tech filtering logic
                title_lower = title.lower()
                is_tech_cat = any(cat in category_lower for cat in self.TECH_CATEGORIES)
                has_tech_keyword = any(kw in title_lower for kw in self.all_keywords)
                
                # If it's a known tech category OR has a tech keyword, fetch it
                if company and title and (is_tech_cat or has_tech_keyword):
                    # Format locations as string
                    locations = item.get('locations', [])
                    location_str = ", ".join(locations) if isinstance(locations, list) else str(locations)
                    
                    # Human-readable date
                    date_posted = None
                    if date_posted_ts:
                        try:
                            date_posted = datetime.fromtimestamp(date_posted_ts).strftime('%Y-%m-%d')
                        except:
                            date_posted = None
                    
                    job = Job(
                        company=company,
                        title=title,
                        location=location_str,
                        link=item.get('url'),
                        date_posted=date_posted,
                        timestamp=float(date_posted_ts)
                    )
                    jobs.append(job)
            
            # Sort by absolute timestamp (newest first)
            jobs.sort(key=lambda x: x.timestamp, reverse=True)
            return jobs
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error fetching/parsing jobs: {e}")
            return []

