from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Tuple
from uuid import UUID

import numpy as np
from numpy.random import Generator as np_Generator
from sqlalchemy.ext.asyncio.session import AsyncSession
from zeus.optimizer.batch_size.server.batch_size_state.commands import (
    CreateExploration,
    UpdateExploration,
)
from zeus.optimizer.batch_size.server.batch_size_state.models import (
    BatchSizeBase,
    ExplorationsPerBs,
    ExplorationsPerJob,
    GaussianTsArmStateModel,
    MeasurementOfBs,
    MeasurementsPerBs,
)
from zeus.optimizer.batch_size.server.batch_size_state.repository import (
    BatchSizeStateRepository,
)
from zeus.optimizer.batch_size.server.exceptions import (
    ZeusBSOServiceBadRequestError,
    ZeusBSOValueError,
)
from zeus.optimizer.batch_size.server.job.commands import (
    CreateJob,
    UpdateExpDefaultBs,
    UpdateGeneratorState,
    UpdateJobMinCost,
    UpdateJobStage,
)
from zeus.optimizer.batch_size.server.job.models import JobState
from zeus.optimizer.batch_size.server.job.repository import JobStateRepository
from zeus.optimizer.batch_size.server.services.commands import (
    GetNormal,
    GetRandomChoices,
)
from zeus.util import zeus_cost


class ZeusService:
    def __init__(self, db_session: AsyncSession):
        self.bs_repo = BatchSizeStateRepository(db_session)
        self.job_repo = JobStateRepository(db_session)

    async def get_arms(self, job_id: UUID) -> list[GaussianTsArmStateModel]:
        return await self.bs_repo.get_arms(job_id)

    async def get_arm(self, bs: BatchSizeBase) -> GaussianTsArmStateModel | None:
        return await self.bs_repo.get_arm(bs)

    async def get_explorations_of_job(self, job_id: UUID) -> ExplorationsPerJob:
        return await self.bs_repo.get_explorations_of_job(job_id)

    async def get_explorations_of_bs(self, bs: BatchSizeBase) -> ExplorationsPerBs:
        return await self.bs_repo.get_explorations_of_bs(bs)

    async def update_exploration(
        self,
        measurement: MeasurementOfBs,
        updated_exp: UpdateExploration,
    ) -> None:
        self._check_job_fetched(measurement.job_id)
        """
        1. add measurement
        2. update that exploration
        3. Update min cost if it is needed
        """
        job = self._get_job(measurement.job_id)
        self.bs_repo.add_measurement(measurement)
        await self.bs_repo.update_exploration(updated_exp)
        self._update_min_if_needed(measurement, job)

    async def update_arm_state(
        self,
        measurement: MeasurementOfBs,
        updated_arm: GaussianTsArmStateModel,
    ):
        self._check_job_fetched(measurement.job_id)
        """
        1. add measurement
        2. update arm_state
        3. Update min cost if it is needed
        """
        job = self._get_job(measurement.job_id)
        self.bs_repo.add_measurement(measurement)
        await self.bs_repo.update_arm_state(updated_arm)
        self._update_min_if_needed(measurement, job)

    def report_concurrent_job(self, measurement: MeasurementOfBs):
        self._check_job_fetched(measurement.job_id)
        """
        1. add measurement
        2. update min cost
        """
        job = self._get_job(measurement.job_id)
        self.bs_repo.add_measurement(measurement)
        self._update_min_if_needed(measurement, job)

    def update_exp_default_bs(self, updated_default_bs: UpdateExpDefaultBs) -> None:
        self._check_job_fetched(updated_default_bs.job_id)
        """
        1. Update exp_default bs
        """
        self.job_repo.update_exp_default_bs(updated_default_bs)

    def add_exploration(self, exp: CreateExploration):
        self._check_job_fetched(exp.job_id)
        """
        add exploration
        """
        self.bs_repo.add_exploration(exp)

    # JOBSTATE
    def get_random_choices(self, choice: GetRandomChoices) -> np.ndarray[Any, Any]:
        self._check_job_fetched(choice.job_id)
        """
        If seed is not none,
        1. get generator state
        2. Get the sequence
        3. Update state
        """
        arr = np.array(choice.choices)
        ret = self._get_generator(choice.job_id)
        should_update = ret[1]
        res = ret[0].choice(arr, len(arr), replace=False)

        if should_update:
            self.job_repo.update_generator_state(
                UpdateGeneratorState(
                    job_id=choice.job_id, state=json.dumps(ret[0].__getstate__())
                )
            )

        return res

    def get_normal(self, arg: GetNormal):
        """
        Sample from normal distribution and update the generator state if seed was set
        """
        self._check_job_fetched(arg.job_id)

        ret = self._get_generator(arg.job_id)
        res = ret[0].normal(arg.loc, arg.scale)
        should_update = ret[1]

        if should_update:
            self.job_repo.update_generator_state(
                UpdateGeneratorState(
                    job_id=arg.job_id, state=json.dumps(ret[0].__getstate__())
                )
            )

        return res

    async def get_job(self, job_id: UUID) -> JobState | None:
        return await self.job_repo.get_job(job_id)

    def create_job(self, new_job: CreateJob) -> None:
        return self.job_repo.create_job(new_job)

    async def get_measurements_of_bs(self, bs: BatchSizeBase) -> MeasurementsPerBs:
        job = self._get_job(bs.job_id)
        return await self.bs_repo.get_measurements_of_bs(
            BatchSizeBase(job_id=bs.job_id, batch_size=bs.batch_size),
            job.window_size,
        )

    def create_arms(self, new_arms: list[GaussianTsArmStateModel]):
        if len(new_arms) != 0:
            self._check_job_fetched(new_arms[0].job_id)
            self.bs_repo.create_arms(new_arms)

    def update_job_stage(self, updated_stage: UpdateJobStage):
        self._check_job_fetched(updated_stage.job_id)

        self.job_repo.update_stage(updated_stage)

    def _update_min_if_needed(
        self,
        measurement: MeasurementOfBs,
        job: JobState,
    ):
        cur_cost = zeus_cost(
            measurement.energy, measurement.time, job.eta_knob, job.max_power
        )
        if job.min_cost == None or job.min_cost > cur_cost:
            self.job_repo.update_min(
                UpdateJobMinCost(
                    job_id=job.job_id,
                    min_cost=cur_cost,
                    min_batch_size=measurement.batch_size,
                )
            )

    def _get_generator(self, job_id: UUID) -> Tuple[np_Generator, bool]:
        """
        Get generator based on job_id. If mab_seed is not none, we should update the state after using generator
        Returns [Generator, if we should update state]
        """
        jobState = self._get_job(job_id)

        rng = np.random.default_rng(int(datetime.now().timestamp()))

        should_update = jobState.mab_seed != None
        if jobState.mab_seed != None:
            if jobState.mab_random_generator_state == None:
                raise ZeusBSOValueError(
                    f"Seed is set but generator state is none. Should be impossible"
                )

            state = json.loads(jobState.mab_random_generator_state)
            rng.__setstate__(state)

        return (rng, should_update)

    def _get_job(self, job_id: UUID) -> JobState:
        res = self.job_repo.get_job_from_session(job_id)
        if res == None:
            raise ZeusBSOServiceBadRequestError(
                f"Should have fetched the job first or job does not exist(job_id = {job_id})"
            )
        return res

    def _check_job_fetched(self, job_id: UUID) -> None:
        """
        Check if we fetched the job in the current session
        """
        return self.job_repo.check_job_fetched(job_id)