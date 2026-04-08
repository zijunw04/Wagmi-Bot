import csv
import io
import requests
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class LeetCodeProblem:
    """Represents a LeetCode problem from the company-wise CSV."""
    id: str
    url: str
    title: str
    difficulty: str
    acceptance: str
    frequency: str

    @property
    def freq_value(self) -> float:
        """Convert frequency percentage string to float for sorting."""
        try:
            return float(self.frequency.strip('%'))
        except (ValueError, AttributeError):
            return 0.0

class LeetCodeScraper:
    """Scrapes LeetCode questions from snehasishroy's repository."""
    
    BASE_RAW_URL = "https://raw.githubusercontent.com/snehasishroy/leetcode-companywise-interview-questions/master"
    GITHUB_API_ROOT = "https://api.github.com/repos/snehasishroy/leetcode-companywise-interview-questions/contents"
    
    def fetch_problems(self, company: str) -> List[LeetCodeProblem]:
        """
        Fetches 'all.csv' for a specific company and parses it.
        Company name should be lowercase and hyphenated (e.g., 'google', 'capital-one').
        """
        url = f"{self.BASE_RAW_URL}/{company.lower()}/all.csv"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            
            # Use io.StringIO to treat the response text as a file for csv.DictReader
            f = io.StringIO(response.text)
            reader = csv.DictReader(f)
            
            problems = []
            for row in reader:
                # Column names from my research: ID,URL,Title,Difficulty,Acceptance %,Frequency %
                problem = LeetCodeProblem(
                    id=row.get('ID', ''),
                    url=row.get('URL', ''),
                    title=row.get('Title', ''),
                    difficulty=row.get('Difficulty', ''),
                    acceptance=row.get('Acceptance %', ''),
                    frequency=row.get('Frequency %', '')
                )
                problems.append(problem)
            
            return problems
        except Exception as e:
            print(f"Error fetching LeetCode problems for {company}: {e}")
            return []

    def get_formatted_company_name(self, name: str) -> str:
        """Helper to format user input into repository's company folder format."""
        # Simple transformation, might need more robust handling for special cases
        return name.lower().replace(" ", "-")

    def fetch_company_list(self) -> List[str]:
        """
        Fetch company folders from the repository root.
        These folder names are valid values for fetch_problems().
        """
        try:
            response = requests.get(self.GITHUB_API_ROOT, timeout=10)
            response.raise_for_status()
            items = response.json()
            companies = []
            for item in items:
                if item.get("type") == "dir" and item.get("name"):
                    companies.append(item["name"])
            companies.sort()
            return companies
        except Exception as e:
            print(f"Error fetching LeetCode company list: {e}")
            return []
