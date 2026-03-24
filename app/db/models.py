from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.db.session import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255))
    company = Column(String(255))
    location = Column(String(100))
    salary_min = Column(Integer)
    salary_max = Column(Integer)
    description = Column(Text)
    url = Column(Text, unique=True)
    date_posted = Column(Date)
    date_scraped = Column(DateTime, server_default=func.now())
    status = Column(String(50), default="active")
    pinecone_id = Column(String(100))

    skills = relationship("Skill", back_populates="job")


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    skill_name = Column(String(100))
    category = Column(String(50))

    job = relationship("Job", back_populates="skills")


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    notion_page_id = Column(String(100))
    match_score = Column(Float)
    notes = Column(Text)
    saved_at = Column(DateTime, server_default=func.now())
    status = Column(String(50), default="considering")


class AgentMemory(Base):
    __tablename__ = "agent_memory"

    id = Column(Integer, primary_key=True, index=True)
    memory_type = Column(String(50))
    content = Column(Text)
    pinecone_id = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())
