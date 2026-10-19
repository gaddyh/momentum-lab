"""Common schema for point-in-time attention observations.

Not a data collector: no historical/social API is assumed to be available.
All times are timezone-aware UTC. first_seen_at <= decision_at is mandatory
for replay; published_at by itself is NOT sufficient.
"""
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class AttentionEvent:
    symbol: str
    source: str                # news, reddit, stocktwits, etc.
    external_id: str
    published_at: datetime
    first_seen_at: datetime
    event_type: str            # article, post, comment
    publisher_id: str | None = None
    author_id_hash: str | None = None
    story_id: str | None = None
    title: str | None = None
