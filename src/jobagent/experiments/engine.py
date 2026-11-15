"""Compatibility interface; production implementation lives in jobagent.filling."""
from jobagent.filling.planned import *  # noqa: F403


class Candidate(PlannedFiller):
    engine_name = "candidate"
