"""Country matching helpers for job location filtering."""

from typing import List, Set, Optional
from job_scraper import Job

# Canonical country names mapped to location substrings / patterns
COUNTRY_PATTERNS: dict[str, list[str]] = {
    "United States": [
        "united states", "u.s.a.", "u.s.", ", usa", ", us", " usa", " us",
        ", ca,", ", ny", ", tx", ", wa", ", ma", ", il", ", ga", ", co",
        ", nc", ", va", ", pa", ", az", ", ut", ", mn", ", or", ", nj",
        ", oh", ", mi", ", fl", ", dc", "san francisco", "new york",
        "seattle", "austin", "boston", "chicago", "los angeles",
    ],
    "Canada": ["canada", ", on", ", bc", ", qc", "toronto", "vancouver", "montreal", "ottawa"],
    "United Kingdom": ["united kingdom", " u.k.", " uk", "london", "england", "scotland"],
    "India": ["india", "bangalore", "bengaluru", "hyderabad", "mumbai", "delhi", "pune"],
    "Germany": ["germany", "berlin", "munich", "frankfurt"],
    "France": ["france", "paris"],
    "Netherlands": ["netherlands", "amsterdam"],
    "Ireland": ["ireland", "dublin"],
    "Singapore": ["singapore"],
    "Australia": ["australia", "sydney", "melbourne"],
    "Japan": ["japan", "tokyo"],
    "South Korea": ["south korea", "korea", "seoul"],
    "China": ["china", "beijing", "shanghai", "shenzhen"],
    "Taiwan": ["taiwan", "taipei"],
    "Israel": ["israel", "tel aviv"],
    "Switzerland": ["switzerland", "zurich"],
}


def normalize_country(name: str) -> Optional[str]:
    """Resolve user input to a canonical country name."""
    needle = name.strip().lower()
    if not needle:
        return None
    for canonical, patterns in COUNTRY_PATTERNS.items():
        if needle == canonical.lower():
            return canonical
        if needle in [p.strip() for p in patterns]:
            return canonical
        if any(needle in p for p in patterns):
            return canonical
    return None


def job_matches_countries(job: Job, countries: List[str]) -> bool:
    """Return True if job location matches any of the configured country filters."""
    if not countries:
        return True
    loc = (job.location or "").lower()
    if not loc:
        return False
    for country in countries:
        canonical = normalize_country(country) or country
        patterns = COUNTRY_PATTERNS.get(canonical, [country.lower()])
        if any(p in loc for p in patterns):
            return True
        if canonical.lower() in loc:
            return True
    return False


def extract_countries_from_jobs(jobs: List[Job]) -> List[str]:
    """Build a sorted list of countries detected across job locations."""
    found: Set[str] = set()
    for job in jobs:
        loc = (job.location or "").lower()
        if not loc:
            continue
        for canonical, patterns in COUNTRY_PATTERNS.items():
            if any(p in loc for p in patterns) or canonical.lower() in loc:
                found.add(canonical)
    return sorted(found)
