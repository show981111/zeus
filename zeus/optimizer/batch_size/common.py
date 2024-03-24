"""Shared model definitions for the server and client."""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, root_validator, validator
from pydantic.fields import Field
from zeus.optimizer.batch_size.exceptions import ZeusBSOConfigError


REGISTER_JOB_URL = "/jobs"
GET_NEXT_BATCH_SIZE_URL = "/jobs/batch_size"
REPORT_RESULT_URL = "/jobs/report"


class JobParams(BaseModel):
    """Job parameters.

    Attributes:
        job_id: unique ID for the job
        batch_sizes: list of batch sizes to try
        default_batch_size: first batch size to try
        eta_knob: eta for computing `zeus_cost`
        beta_knob: beta for early stopping. If min_cost*beta_knob < current_cost, job will be stopped by bso server.
                    To disable, set it to None.
        target_metric: target metric to achieve for training.
        higher_is_better_metric: if the goal of training is achieving higher metric than `target_metric`
        max_epochs: Maximum number of epochs for a training run.
        num_pruning_rounds: Number of rounds we are trying for pruning stage
        window_size: For MAB, how many recent measurements to fetch for computing the arm states. If set to 0, fetch all measurements.

        mab_prior_mean: Mean of the belief prior distribution.
        mab_prior_precision: Precision of the belief prior distribution.
        mab_num_explorations: How many static explorations to run when no observations are available.
        mab_seed: The random seed to use.
    """

    job_id: str
    batch_sizes: list[int]
    default_batch_size: int = Field(gt=0)
    eta_knob: float = 0.5
    beta_knob: Optional[float] = 2.0
    target_metric: float = 0.50
    higher_is_better_metric: bool = True
    max_epochs: int = Field(100, gt=0)
    num_pruning_rounds: int = Field(2, ge=0)
    window_size: int = 10

    mab_prior_mean: float = 0.0
    mab_prior_precision: float = 0.0
    mab_num_explorations: int = Field(2, ge=0)
    mab_seed: Optional[int] = None

    @validator("batch_sizes")
    def _validate_batch_sizes(cls, bs: list[int]) -> int:
        if bs is not None and len(bs) > 0:
            bs.sort()
            return bs
        else:
            raise ValueError(f"Batch Sizes = {bs} is empty")

    @validator("eta_knob")
    def _validate_eta_knob(cls, v: float) -> int:
        if v < 0 or v > 1:
            raise ValueError("eta_knob should be in range [0,1]")
        return v

    @validator("beta_knob")
    def _validate_beta_knob(cls, v: float) -> int:
        if v is None or v > 0:
            return v
        else:
            raise ValueError(
                f"Invalid beta_knob({v}). To disable early stop, set beta_knob = None to disable or positive value."
            )

    @root_validator(skip_on_failure=True)
    def _check_default_batch_size(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        bs = values["default_batch_size"]
        bss = values["batch_sizes"]
        if bs not in bss:
            raise ValueError(f"Default BS({bs}) not in batch_sizes({bss}).")
        return values


class JobSpec(JobParams):
    """Job specification that user inputs.

    Attributes:
        job_id: ID of job
        job_id_prefix: Prefix of job.

    Refer [`JobParams`][`zeus.optimizer.batch_size.common.JobParams`] for other attributes.
    """

    job_id: Optional[str]
    job_id_prefix: str

    @root_validator(skip_on_failure=True)
    def _check_job_id(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        job_id: str | None = values.get("job_id")
        prefix: str = values["job_id_prefix"]

        if job_id is not None and not job_id.startswith(prefix):
            raise ValueError(f"Job_id({job_id}) does not start with prefix({prefix}).")
        elif job_id is None:
            values["job_id"] = f"{prefix}-{uuid4()}"
        return values


class JobConfig(JobParams):
    """Internal job configuration including gpu settigns.

    Attributes:
        max_power: sum of maximum power limit of all gpus we are using
        number_of_gpus: number of gpus that are being used for training
    """

    max_power: float = Field(gt=0)
    number_of_gpus: int = Field(gt=0)
    gpu_model: str

    @validator("gpu_model")
    def _validate_gpu_model(cls, v: str) -> str:
        if v is None or v == "":
            raise ValueError(f"Invalid gpu_model({v}). Shouldn't be empty.")
        else:
            return v


class PredictResponse(BaseModel):
    """Response format from the server for getting a batch size to use.

    Attributes:
        job_id: ID of job
        batch_size: batch size to use.
        trial_number: trial number of current training.
    """

    job_id: str
    batch_size: int
    trial_number: int


class TrainingResult(PredictResponse):
    """Result of training for that job & batch size.

    Attributes:
        error: True if there was an error while training, otherwise False.
        time: total time consumption so far
        energy: total energy consumption so far
        metric: current metric value after `current_epoch`
        current_epoch: current epoch of training. Server can check if the train reached the `max_epochs`
    """

    error: bool
    time: Optional[float]
    energy: Optional[float]
    metric: Optional[float]
    current_epoch: int

    @root_validator(skip_on_failure=True)
    def _check_sanity(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        error: bool = values["error"]
        if not error and (
            values.get("time") is None
            or values.get("energy") is None
            or values.get("metric") is None
        ):
            raise ValueError(
                f'All fields should be populated: time({values.get("time")}), energy({values.get("energy")}), metric({values.get("metric")})'
            )
        return values


class ReportResponse(BaseModel):
    """Response format from the server for client's training result report.

    Attributes:
        stop_train: Whether we should stop training or not.
        converged: Whether the target metric has been reached.
        message: message from the server regarding training. ex) why train should be stopped.
    """

    stop_train: bool
    converged: bool
    message: str
