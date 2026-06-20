"""Guild posting filter matching."""

from dataclasses import dataclass
from typing import List, Optional

from job_scraper import Job
from country_utils import job_matches_countries, normalize_country


@dataclass
class GuildFilters:
    regions: List[str]
    companies: List[str]
    remote_only: Optional[bool]  # None = no remote filter, True = remote only, False = on-site only

    @property
    def is_empty(self) -> bool:
        return not self.regions and not self.companies and self.remote_only is None

    def summary_lines(self) -> List[str]:
        lines = []
        if self.regions:
            lines.append(f"🌍 **Regions:** {', '.join(self.regions)}")
        if self.companies:
            lines.append(f"🏢 **Companies:** {', '.join(self.companies)}")
        if self.remote_only is True:
            lines.append("🏠 **Remote:** remote jobs only")
        elif self.remote_only is False:
            lines.append("🏢 **Remote:** on-site jobs only")
        return lines


def job_is_remote(job: Job) -> bool:
    loc = (job.location or "").lower()
    return "remote" in loc


def job_matches_guild_filters(job: Job, filters: GuildFilters) -> bool:
    """Job must satisfy every active filter category (AND across types, OR within lists)."""
    if filters.regions and not job_matches_countries(job, filters.regions):
        return False
    if filters.companies:
        company_lower = (job.company or "").lower()
        if not any(c.lower() in company_lower for c in filters.companies):
            return False
    if filters.remote_only is not None:
        is_remote = job_is_remote(job)
        if filters.remote_only and not is_remote:
            return False
        if not filters.remote_only and is_remote:
            return False
    return True


def normalize_region(value: str) -> str:
    return normalize_country(value) or value.strip()


def normalize_company(value: str) -> str:
    return value.strip()
