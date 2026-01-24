import os
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from job_scraper import Job

Base = declarative_base()
logger = logging.getLogger('discord')

class JobRecord(Base):
    __tablename__ = 'job_history'
    
    id = Column(Integer, primary_key=True)
    company = Column(String, index=True)
    title = Column(String, index=True)
    location = Column(String)
    link = Column(String)
    posted_at_ts = Column(Float, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DatabaseManager:
    def __init__(self, database_url: Optional[str] = None):
        # Fallback to local SQLite if no DATABASE_URL is provided
        if not database_url:
            database_url = "sqlite:///jobs_history.db"
            logger.info(f"No DATABASE_URL found. Using local SQLite: {database_url}")
        
        # PostgreSQL specific fix: Render/Heroku often provide 'postgres://' but SQLAlchemy needs 'postgresql://'
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
            
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def is_duplicate(self, job: Job) -> bool:
        """
        Check if a job is a duplicate.
        Returns True if a job with the same company, title, location, AND timestamp exists.
        If the timestamp is different, it's considered a "repost" and not a duplicate.
        """
        session = self.Session()
        try:
            record = session.query(JobRecord).filter(
                JobRecord.company == job.company,
                JobRecord.title == job.title,
                JobRecord.location == job.location,
                JobRecord.posted_at_ts == job.timestamp
            ).first()
            return record is not None
        except Exception as e:
            logger.error(f"Error checking duplicate in DB: {e}")
            return False
        finally:
            session.close()

    def add_job(self, job: Job):
        """Add a new job posting to the database."""
        session = self.Session()
        try:
            record = JobRecord(
                company=job.company,
                title=job.title,
                location=job.location,
                link=job.link,
                posted_at_ts=job.timestamp
            )
            session.add(record)
            session.commit()
            logger.debug(f"Saved job to DB: {job.company} - {job.title}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving job to DB: {e}")
        finally:
            session.close()

    def get_total_count(self) -> int:
        """Return the total number of tracked jobs."""
        session = self.Session()
        try:
            return session.query(JobRecord).count()
        except Exception as e:
            logger.error(f"Error getting total count from DB: {e}")
            return 0
        finally:
            session.close()
