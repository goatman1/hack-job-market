from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Query(StrictModel):
    keywords: str = Field(min_length=1, max_length=1000)
    track: Literal["tech", "quant"]


def default_queries() -> list[Query]:
    pairs = [
        ('("hiring" OR "join my team") ("applied scientist" OR "research scientist")', 'tech'),
        ('("hiring" OR "join my team") ("quantitative researcher" OR "quant researcher")', 'quant'),
        ('("hiring" OR "join my team") ("machine learning" OR "AI engineer" OR "agents")', 'tech'),
        ('("hiring" OR "join my team") ("quantitative developer" OR "quant developer")', 'quant'),
        ('("hiring" OR "join my team") ("reinforcement learning" OR "robotics" OR "optimization")', 'tech'),
        ('("hiring" OR "join my team") ("quant" AND ("PhD" OR "graduate" OR "junior"))', 'quant'),
    ]
    return [Query(keywords=k, track=t) for k, t in pairs]


class RunConfig(StrictModel):
    target_count: int = Field(default=100, ge=1, le=10000)
    sort_by: Literal["relevance", "date_posted"] = "relevance"
    date_posted: Literal["past_24_hours", "past_week", "past_month", "all_time"] = "past_week"
    queries: list[Query] = Field(default_factory=default_queries, min_length=1, max_length=100)
    max_inspected: int = Field(default=500, ge=1, le=50000)
    max_loading_actions: int = Field(default=50, ge=1, le=1000)
    max_minutes: float = Field(default=30, gt=0, le=240)
    no_progress_limit: int = Field(default=3, ge=1, le=10)
    shortlist_threshold: int = Field(default=60, ge=0, le=100)
    resume_dir: str = "../resumes"
    available_from: str = "2027-01-05"
    future_sponsorship: bool = True
    # Optional semantic checks: off ignores, flag annotates, enforce gates shortlist.
    semantic_filters: dict[str, Literal["off", "flag", "enforce"]] = Field(
        default_factory=lambda: {"location": "enforce", "job_title": "enforce",
                                 "skills_experience": "flag", "start_date": "flag",
                                 "sponsorship": "flag"}
    )

    @model_validator(mode="after")
    def validate_filters(self):
        allowed = {"location", "job_title", "skills_experience", "start_date", "sponsorship"}
        if set(self.semantic_filters) - allowed:
            raise ValueError("Unknown semantic filter")
        from datetime import date
        date.fromisoformat(self.available_from)
        return self


class Evidence(StrictModel):
    source_url: str = Field(min_length=1)
    quote: str = Field(min_length=5, max_length=4000)


class Check(StrictModel):
    status: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_evidence(self):
        if self.status != "unknown" and not self.evidence:
            raise ValueError("Pass/fail decisions require captured source evidence")
        return self


class Scores(StrictModel):
    role_alignment: int = Field(ge=0, le=30)
    skills: int = Field(ge=0, le=30)
    experience: int = Field(ge=0, le=25)
    education_seniority: int = Field(ge=0, le=15)

    @property
    def total(self):
        return sum(self.model_dump().values())


REQUIRED_CHECKS = {"hiring", "full_time", "entry_level", "us_author", "manager",
                   "company_affiliation", "own_team", "us_job", "role_relevance",
                   "skills_experience", "start_date", "sponsorship"}
CORE_CHECKS = {"hiring", "full_time", "entry_level", "us_author", "manager",
               "company_affiliation", "own_team"}
FILTER_CHECKS = {"location": "us_job", "job_title": "role_relevance",
                 "skills_experience": "skills_experience", "start_date": "start_date",
                 "sponsorship": "sponsorship"}


class Assessment(StrictModel):
    post_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=250)
    company: str = Field(default="", max_length=250)
    requisition_url: str | None = None
    verification: dict[str, Check]
    scores: Scores
    selected_resume: str
    match_explanation: str = Field(min_length=1, max_length=5000)
    eligibility_flags: list[str] = Field(default_factory=list, max_length=20)
    reach_out_message: str = Field(default="", max_length=3000)
    draft_evidence: list[Evidence] = Field(default_factory=list, max_length=10)
    author_location: str = ""

    @model_validator(mode="after")
    def validate_assessment(self):
        if set(self.verification) != REQUIRED_CHECKS:
            raise ValueError(f"verification must contain exactly {sorted(REQUIRED_CHECKS)}")
        if self.reach_out_message and not 70 <= len(self.reach_out_message.split()) <= 120:
            raise ValueError("Outreach must be 70–120 words")
        if self.reach_out_message and not self.draft_evidence:
            raise ValueError("Outreach requires supporting draft_evidence")
        return self


STATUSES = {"new", "saved", "contacted", "applied", "dismissed", "closed"}


def classify(assessment: Assessment, config: RunConfig) -> str:
    checks = set(CORE_CHECKS)
    checks.update(FILTER_CHECKS[k] for k, mode in config.semantic_filters.items() if mode == "enforce")
    states = [assessment.verification[k].status for k in checks]
    if "fail" in states:
        return "Excluded"
    if "unknown" in states:
        return "Needs Review"
    return "Shortlist" if assessment.scores.total >= config.shortlist_threshold else "Candidates"
