"""
Recommendation feedback, events and impressions (R2a, docs/design/R2a-feedback-dashboard.md §3).

- recommendation_target_state: the current per-user state of one canonical
  target key (pinned / snoozed_until / dismissed). The engine reads this.
- recommendation_events: an append-only log of every action; each state change
  writes exactly one event in the same transaction (IMAGED writes only an event).
- recommendation_impressions: what Tonight showed for tonight's default night,
  one row per (user, night, key); the first showing wins.
"""

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base

# JSONB on PostgreSQL, plain JSON elsewhere (keeps the models usable in unit tests).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class RecommendationTargetState(Base):
    __tablename__ = "recommendation_target_state"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_key = Column(String(64), primary_key=True)
    pinned = Column(Boolean, nullable=False, default=False)
    snoozed_until = Column(Date, nullable=True)          # hidden for nights < snoozed_until
    dismissed = Column(Boolean, nullable=False, default=False)
    dismiss_reason = Column(String(20), nullable=True)   # DONE | NOT_MY_TYPE | TOO_HARD | OTHER
    note = Column(String(200), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<RecommendationTargetState(user_id={self.user_id}, target_key='{self.target_key}')>"


class RecommendationEvent(Base):
    __tablename__ = "recommendation_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_key = Column(String(64), nullable=False, index=True)
    action = Column(String(12), nullable=False)          # PIN | UNPIN | SNOOZE | UNSNOOZE | DISMISS | UNDISMISS | IMAGED
    night = Column(Date, nullable=True)                  # the night being viewed
    lane = Column(String(20), nullable=True)
    rank = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)
    rig_id = Column(Integer, nullable=True)
    payload = Column(_JSON, nullable=True)               # e.g. {"nights": 7} or {"reason": "NOT_MY_TYPE"}
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_recommendation_events_user_action", "user_id", "action"),
    )


class RecommendationImpression(Base):
    __tablename__ = "recommendation_impressions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    night = Column(Date, nullable=False)
    target_key = Column(String(64), nullable=False)
    site_id = Column(Integer, nullable=True)
    rig_mode = Column(String(16), nullable=True)
    rig_id = Column(Integer, nullable=True)
    lane = Column(String(20), nullable=True)
    rank = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)
    is_hero = Column(Boolean, nullable=False, default=False)
    first_shown_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "night", "target_key", name="uq_recommendation_impression"),
    )
