import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Literal

from job_scraper import Job
from filter_utils import GuildFilters

logger = logging.getLogger("discord")

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None  # type: ignore
    Client = None  # type: ignore


class SupabaseManager:
    """Supabase-backed storage for job history and per-guild bot configuration."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        url = url or os.getenv("SUPABASE_URL")
        key = key or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY are required")
        if create_client is None:
            raise ImportError("supabase package is not installed")
        self.client: Client = create_client(url, key)

    # --- Job history (global deduplication) ---

    def is_duplicate(self, job: Job) -> bool:
        try:
            result = (
                self.client.table("job_history")
                .select("id")
                .eq("company", job.company)
                .eq("title", job.title)
                .eq("location", job.location or "")
                .eq("posted_at_ts", job.timestamp)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.error(f"Supabase duplicate check failed: {e}")
            return False

    def add_job(self, job: Job) -> None:
        try:
            self.client.table("job_history").insert(
                {
                    "company": job.company,
                    "title": job.title,
                    "location": job.location or "",
                    "link": job.link or "",
                    "posted_at_ts": job.timestamp,
                }
            ).execute()
        except Exception as e:
            logger.error(f"Supabase add job failed: {e}")

    def get_total_count(self) -> int:
        try:
            result = self.client.table("job_history").select("id", count="exact").execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"Supabase count failed: {e}")
            return 0

    # --- Guild configuration ---

    def _ensure_guild(self, guild_id: int) -> None:
        try:
            existing = (
                self.client.table("guild_config")
                .select("guild_id")
                .eq("guild_id", guild_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                self.client.table("guild_config").insert(
                    {
                        "guild_id": guild_id,
                        "country_filters": [],
                        "company_filters": [],
                        "remote_only": None,
                    }
                ).execute()
        except Exception as e:
            logger.error(f"Supabase ensure guild failed: {e}")

    def get_posting_channels(self, guild_id: int) -> List[int]:
        try:
            result = (
                self.client.table("guild_posting_channels")
                .select("channel_id")
                .eq("guild_id", guild_id)
                .execute()
            )
            return [int(row["channel_id"]) for row in (result.data or [])]
        except Exception as e:
            logger.error(f"Supabase get posting channels failed: {e}")
            return []

    def add_posting_channel(self, guild_id: int, channel_id: int) -> bool:
        self._ensure_guild(guild_id)
        try:
            self.client.table("guild_posting_channels").upsert(
                {"guild_id": guild_id, "channel_id": channel_id},
                on_conflict="guild_id,channel_id",
            ).execute()
            self._touch_guild(guild_id)
            return True
        except Exception as e:
            logger.error(f"Supabase add posting channel failed: {e}")
            return False

    def remove_posting_channel(self, guild_id: int, channel_id: int) -> bool:
        try:
            self.client.table("guild_posting_channels").delete().eq(
                "guild_id", guild_id
            ).eq("channel_id", channel_id).execute()
            self._touch_guild(guild_id)
            return True
        except Exception as e:
            logger.error(f"Supabase remove posting channel failed: {e}")
            return False

    def get_guild_filters(self, guild_id: int) -> GuildFilters:
        try:
            result = (
                self.client.table("guild_config")
                .select("country_filters, company_filters, remote_only")
                .eq("guild_id", guild_id)
                .limit(1)
                .execute()
            )
            if result.data:
                row = result.data[0]
                remote = row.get("remote_only")
                return GuildFilters(
                    regions=row.get("country_filters") or [],
                    companies=row.get("company_filters") or [],
                    remote_only=remote if remote is not None else None,
                )
            return GuildFilters(regions=[], companies=[], remote_only=None)
        except Exception as e:
            logger.error(f"Supabase get guild filters failed: {e}")
            return GuildFilters(regions=[], companies=[], remote_only=None)

    def get_country_filters(self, guild_id: int) -> List[str]:
        return self.get_guild_filters(guild_id).regions

    def set_guild_filters(self, guild_id: int, filters: GuildFilters) -> bool:
        self._ensure_guild(guild_id)
        try:
            self.client.table("guild_config").update(
                {
                    "country_filters": filters.regions,
                    "company_filters": filters.companies,
                    "remote_only": filters.remote_only,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("guild_id", guild_id).execute()
            return True
        except Exception as e:
            logger.error(f"Supabase set guild filters failed: {e}")
            return False

    def clear_guild_filters(self, guild_id: int) -> bool:
        return self.set_guild_filters(
            guild_id, GuildFilters(regions=[], companies=[], remote_only=None)
        )

    def add_guild_filter(
        self,
        guild_id: int,
        filter_type: Literal["region", "company", "remote"],
        value: Optional[str] = None,
        remote_only: Optional[bool] = None,
    ) -> tuple[bool, str]:
        filters = self.get_guild_filters(guild_id)
        if filter_type == "region":
            if not value:
                return False, "Region value is required."
            region = value.strip()
            if region in filters.regions:
                return False, f"Region **{region}** is already filtered."
            filters.regions.append(region)
        elif filter_type == "company":
            if not value:
                return False, "Company value is required."
            company = value.strip()
            if any(c.lower() == company.lower() for c in filters.companies):
                return False, f"Company **{company}** is already filtered."
            filters.companies.append(company)
        elif filter_type == "remote":
            if remote_only is None:
                return False, "Choose remote true/false for remote filter."
            filters.remote_only = remote_only
        else:
            return False, "Unknown filter type."

        if not self.set_guild_filters(guild_id, filters):
            return False, "Failed to save filter."
        return True, "ok"

    def remove_guild_filter(
        self,
        guild_id: int,
        filter_type: Literal["region", "company", "remote"],
        value: Optional[str] = None,
    ) -> tuple[bool, str]:
        filters = self.get_guild_filters(guild_id)
        if filter_type == "region":
            if not value:
                return False, "Region value is required."
            region = value.strip()
            if region not in filters.regions:
                return False, f"Region **{region}** is not in your filters."
            filters.regions.remove(region)
        elif filter_type == "company":
            if not value:
                return False, "Company value is required."
            company = value.strip()
            match = next((c for c in filters.companies if c.lower() == company.lower()), None)
            if not match:
                return False, f"Company **{company}** is not in your filters."
            filters.companies.remove(match)
        elif filter_type == "remote":
            if filters.remote_only is None:
                return False, "No remote filter is set."
            filters.remote_only = None
        else:
            return False, "Unknown filter type."

        if not self.set_guild_filters(guild_id, filters):
            return False, "Failed to save filter."
        return True, "ok"

    def set_country_filters(self, guild_id: int, filters: List[str]) -> bool:
        current = self.get_guild_filters(guild_id)
        current.regions = filters
        return self.set_guild_filters(guild_id, current)

    def get_all_posting_targets(self) -> List[dict]:
        """Return every configured posting channel with its guild's country filters."""
        try:
            channels_result = self.client.table("guild_posting_channels").select("*").execute()
            if not channels_result.data:
                return []

            targets = []
            seen_channels: set[int] = set()

            for row in channels_result.data:
                channel_id = int(row["channel_id"])
                guild_id = int(row["guild_id"])
                if channel_id in seen_channels:
                    continue
                seen_channels.add(channel_id)
                filters = self.get_guild_filters(guild_id)
                targets.append(
                    {
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "filters": filters,
                    }
                )
            return targets
        except Exception as e:
            logger.error(f"Supabase get posting targets failed: {e}")
            return []

    def _touch_guild(self, guild_id: int) -> None:
        try:
            self.client.table("guild_config").update(
                {"updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("guild_id", guild_id).execute()
        except Exception as e:
            logger.debug(f"Supabase touch guild skipped: {e}")
