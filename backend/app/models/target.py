"""
Target Goal Model (F2)
Optional per-target, per-filter integration goals (e.g. "8h of Ha on M31").
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint

from app.database import Base


class TargetGoal(Base):
    """
    An optional integration-time goal for a target + normalized filter group.
    filter_group is either a normalized filter bucket (see app.utils.filter_names)
    or the literal "ANY" for a target-wide goal regardless of filter.
    """
    __tablename__ = "target_goals"

    id = Column(Integer, primary_key=True, index=True)

    target_key = Column(String(64), nullable=False, index=True)
    filter_group = Column(String(20), nullable=False)  # normalized filter, or "ANY"
    goal_seconds = Column(Float, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('target_key', 'filter_group', name='uq_target_goal'),
    )

    def __repr__(self):
        return f"<TargetGoal(target_key='{self.target_key}', filter_group='{self.filter_group}', goal_seconds={self.goal_seconds})>"
