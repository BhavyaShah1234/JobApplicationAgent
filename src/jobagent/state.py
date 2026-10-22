"""Shared data schemas."""

from pydantic import BaseModel, Field


class PayRange(BaseModel):
    min: float | None = Field(None, description="Lowest amount as a number, no currency symbol")
    max: float | None = Field(None, description="Highest amount as a number")
    currency: str | None = Field(None, description="ISO code, e.g. USD")
    period: str | None = Field(None, description="year | month | hour")
    raw: str | None = Field(None, description="The pay text exactly as written in the posting")


class JobDescription(BaseModel):
    """A job posting, extracted faithfully: copy facts, never invent; unknown → null / empty list."""

    title: str = Field(description="Job title as written")
    company: str = Field(description="Hiring company name")
    company_description: str | None = Field(None, description="What the company does, 1-3 sentences from the posting")
    team: str | None = Field(None, description="Team / org / product area, if stated")
    locations: list[str] = Field(default_factory=list, description="Each location as written, e.g. 'Seattle, WA'")
    workplace_type: str | None = Field(None, description="remote | hybrid | onsite, only if stated")
    employment_type: str | None = Field(None, description="full-time | part-time | internship | contract, if stated")
    seniority: str | None = Field(None, description="e.g. intern, new grad, entry, mid, senior, staff — as implied by title/text")
    pay_range: PayRange | None = Field(None, description="Only if the posting states pay")
    role_summary: str | None = Field(None, description="2-4 sentence summary of the role, from the posting")
    responsibilities: list[str] = Field(default_factory=list, description="What the person will do, one item per bullet")
    basic_qualifications: list[str] = Field(default_factory=list, description="Required / minimum qualifications")
    preferred_qualifications: list[str] = Field(default_factory=list, description="Preferred / nice-to-have qualifications")
    technical_skills: list[str] = Field(default_factory=list,
                                        description="Concrete technologies, languages, tools named anywhere in the posting")
    values: list[str] = Field(default_factory=list, description="Qualities / culture / what they look for in a candidate")
    benefits: list[str] = Field(default_factory=list)
    visa_sponsorship: str | None = Field(None, description="What the posting says about sponsorship / work authorization")
    education: str | None = Field(None, description="Degree requirements as written")
    years_experience: str | None = Field(None, description="Experience required, as written")
    req_id: str | None = Field(None, description="Requisition / job id if shown")
    posted_date: str | None = Field(None, description="As shown, ISO if possible")
    application_deadline: str | None = None
    # Filled in by the scraper, not the model:
    source_url: str = ""
    apply_url: str | None = None
    portal: str = ""

    def missing_core(self) -> list[str]:
        """Core fields a usable JD must have."""
        missing = [f for f in ("title", "company") if not getattr(self, f)]
        if len(self.responsibilities) + len(self.basic_qualifications) + len(self.preferred_qualifications) < 3:
            missing.append("responsibilities/qualifications")
        if not self.locations:
            missing.append("locations")
        return missing
