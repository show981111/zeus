"""Database schema."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import VARCHAR
from zeus.optimizer.batch_size.server.job.models import Stage


class Base(DeclarativeBase):
    """Base class for schemas."""

    pass


class JobTable(Base):
    """Job table schema.

    Refer to `JobState`[zeus.optimizer.batch_size.server.job.models] for attributes.
    """

    __tablename__ = "Job"

    job_id: Mapped[str] = mapped_column(VARCHAR(300), primary_key=True)
    default_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    higher_is_better_metric: Mapped[bool] = mapped_column(Boolean, default=True)
    eta_knob: Mapped[float] = mapped_column(Float, default=0.5)
    beta_knob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_metric: Mapped[float] = mapped_column(Float, default=0.5)
    max_epochs: Mapped[int] = mapped_column(Integer, default=100)
    num_pruning_rounds: Mapped[int] = mapped_column(Integer, default=2)
    window_size: Mapped[int] = mapped_column(Integer, default=10)

    max_power: Mapped[float] = mapped_column(Float, nullable=False)
    number_of_gpus: Mapped[int] = mapped_column(Integer, nullable=False)
    gpu_model: Mapped[str] = mapped_column(VARCHAR(length=30), nullable=False)

    mab_prior_mean: Mapped[float] = mapped_column(Float, default=0.0)
    mab_prior_precision: Mapped[float] = mapped_column(Float, default=0.0)
    mab_num_explorations: Mapped[int] = mapped_column(Integer, default=2)
    mab_seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    mab_random_generator_state: Mapped[Optional[str]] = mapped_column(
        VARCHAR(length=10000), nullable=True
    )
    exp_default_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)

    stage: Mapped[Stage] = mapped_column(Enum(Stage), default=Stage.Pruning)
    min_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)

    batch_sizes: Mapped[list["BatchSizeTable"]] = relationship(
        order_by="BatchSizeTable.batch_size.asc()",
        back_populates="job",
        # always fetch batch size(int) whenever we fetch the job.
        # https://docs.sqlalchemy.org/en/14/orm/loading_relationships.html#relationship-loading-techniques
        lazy="joined",
    )


class BatchSizeTable(Base):
    """Batch size states table schema. Represents one batch size of a job.

    (job_id, batch_size) as a pk, and have three states(exploration, measurement, GaussianTs arm state) as fk.
    For explorations and measurements, one-to-many relationship. For arm_state, one-to-(zero or one) relationship.
    """

    __tablename__ = "BatchSize"

    job_id: Mapped[str] = mapped_column(ForeignKey("Job.job_id"), primary_key=True)
    batch_size: Mapped[int] = mapped_column(Integer, primary_key=True)

    trials: Mapped[list["TrialTable"]] = relationship(back_populates="batch_size_state")

    arm_state: Mapped[Optional["GaussianTsArmStateTable"]] = relationship(
        back_populates="batch_size_state",  # populates GaussianTsArmState->BatchSize
        # https://stackoverflow.com/questions/39869793/when-do-i-need-to-use-sqlalchemy-back-populates
    )

    job: Mapped["JobTable"] = relationship(back_populates="batch_sizes")


class GaussianTsArmStateTable(Base):
    """Gaussian arm state schema. Represents a gaussian thompson arm states of a batch size.

    Refer to `GaussianTsArmStateModel`[zeus.optimizer.batch_size.server.batch_size_state.models.GaussianTsArmStateModel] for attributes.
    """

    __tablename__ = "GaussianTsArmState"

    job_id: Mapped[str] = mapped_column(VARCHAR(300), primary_key=True)
    batch_size: Mapped[int] = mapped_column(Integer, primary_key=True)  # arm

    param_mean: Mapped[float] = mapped_column(Float, default=0.0)
    param_precision: Mapped[float] = mapped_column(Float, default=0.0)
    reward_precision: Mapped[float] = mapped_column(Float, default=0.0)
    num_observations: Mapped[int] = mapped_column(Integer, default=0)

    batch_size_state: Mapped["BatchSizeTable"] = relationship(
        back_populates="arm_state"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            [job_id, batch_size], [BatchSizeTable.job_id, BatchSizeTable.batch_size]
        ),
    )


class TrialType(enum.Enum):
    """Type of trial.

    Exploration is a trial done during Pruning stage.
    Concurrent is a trial done as a concurrent job submission.
    MAB is a trial done during the MAB stage.
    """

    Exploration = "Exploration"
    Concurrent = "Concurrent"
    MAB = "MAB"


class TrialStatus(enum.Enum):
    """Status of trial.

    Dispatched means this trial is issued.
    Succeded means trial ended without error.
    Failed means trial ended with error.
    """

    Dispatched = "Dispatched"
    Succeeded = "Succeeded"
    Failed = "Failed"


class TrialTable(Base):
    """Represents each trial of training."""

    __tablename__ = "Trial"

    job_id: Mapped[str] = mapped_column(VARCHAR(300), primary_key=True)
    batch_size: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    type: Mapped[TrialType] = mapped_column(Enum(TrialType), nullable=False)
    status: Mapped[TrialStatus] = mapped_column(Enum(TrialStatus), nullable=False)

    end_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    energy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    converged: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    batch_size_state: Mapped["BatchSizeTable"] = relationship(back_populates="trials")

    __table_args__ = (
        ForeignKeyConstraint(
            [job_id, batch_size], [BatchSizeTable.job_id, BatchSizeTable.batch_size]
        ),
    )


# class ExplorationStateTable(Base):
#     """Exploration state table schema with (job_id, batch_size, round_number) as a pk. Represents an exploration state of a batch size.

#     Refer to `ExplorationStateModel`[zeus.optimizer.batch_size.server.batch_size_state.models.ExplorationStateModel] for attributes.
#     """

#     __tablename__ = "ExplorationState"

#     job_id: Mapped[str] = mapped_column(VARCHAR(300), primary_key=True)
#     batch_size: Mapped[int] = mapped_column(Integer, primary_key=True)
#     round_number: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)
#     state: Mapped[State] = mapped_column(Enum(State), default=State.Exploring)
#     cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

#     batch_size_state: Mapped["BatchSizeTable"] = relationship(
#         back_populates="explorations"
#     )

#     __table_args__ = (
#         ForeignKeyConstraint(
#             [job_id, batch_size], [BatchSizeTable.job_id, BatchSizeTable.batch_size]
#         ),
#     )


# class MeasurementTable(Base):
#     """Measurement schema. Represents the training result of the batch size.

#     Refer to `MeasurementOfBs`[zeus.optimizer.batch_size.server.batch_size_state.models.MeasurementOfBs] for attributes.
#     """

#     __tablename__ = "Measurement"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
#     job_id: Mapped[str] = mapped_column(VARCHAR(300))
#     batch_size: Mapped[int] = mapped_column(Integer)
#     time: Mapped[float] = mapped_column(Float, nullable=False)
#     energy: Mapped[float] = mapped_column(Float, nullable=False)
#     converged: Mapped[bool] = mapped_column(Boolean, nullable=False)

#     timestamp: Mapped[datetime] = mapped_column(
#         DateTime, default=func.now(), nullable=False
#     )

#     batch_size_state: Mapped["BatchSizeTable"] = relationship(
#         back_populates="measurements"
#     )

#     __table_args__ = (
#         ForeignKeyConstraint(
#             [job_id, batch_size], [BatchSizeTable.job_id, BatchSizeTable.batch_size]
#         ),
#     )
