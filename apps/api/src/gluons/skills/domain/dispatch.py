"""Optional OR-Tools adapter for candidate scheduling using an owner-supplied matrix.

No live traffic, route fetch, dispatch write or real-world improvement is implied. A
second deterministic verifier checks every candidate before it leaves this boundary.
Upstream: google/or-tools 9.15.6755, Apache-2.0, checked 2026-11-02.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Technician(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    skills: set[str]
    certifications: dict[str, date] = Field(default_factory=dict)
    service_areas: set[str]
    shift_start: int = Field(ge=0, le=1440)
    shift_end: int = Field(ge=0, le=1440)
    start_location: int = Field(ge=0)
    end_location: int = Field(ge=0)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    location: int = Field(ge=0)
    duration_minutes: int = Field(gt=0, le=1440)
    window_start: int = Field(ge=0, le=1440)
    window_end: int = Field(ge=0, le=1440)
    required_skills: set[str]
    required_certifications: set[str] = Field(default_factory=set)
    service_area: str
    priority: int = Field(default=1, ge=1, le=10)
    emergency: bool = False
    assigned_technician_id: str | None = None


class DispatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_date: date
    technicians: list[Technician] = Field(min_length=1, max_length=15)
    jobs: list[Job] = Field(min_length=1, max_length=30)
    travel_minutes: list[list[int]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounds(self) -> DispatchInput:
        matrix = self.travel_minutes
        n = len(matrix)
        if any(len(row) != n or any(type(value) is not int or not 0 <= value <= 1440 for value in row) for row in matrix):
            raise ValueError("travel matrix must be square with nonnegative integer minutes")
        if any(matrix[i][i] != 0 for i in range(n)):
            raise ValueError("travel matrix diagonal must be zero")
        if len({t.id for t in self.technicians}) != len(self.technicians) or len({j.id for j in self.jobs}) != len(self.jobs):
            raise ValueError("duplicate technician or job id")
        if any(t.shift_start >= t.shift_end or max(t.start_location, t.end_location) >= n for t in self.technicians):
            raise ValueError("invalid shift or technician location")
        if any(j.window_start + j.duration_minutes > j.window_end or j.location >= n for j in self.jobs):
            raise ValueError("invalid job window or location")
        return self


def eligible(technician: Technician, job: Job, service_date: date) -> bool:
    return (
        job.required_skills <= technician.skills
        and job.service_area in technician.service_areas
        and all(technician.certifications.get(cert, date.min) >= service_date for cert in job.required_certifications)
        and (job.assigned_technician_id is None or job.assigned_technician_id == technician.id)
    )


def verify_candidate(data: DispatchInput, assignments: list[dict[str, Any]]) -> bool:
    jobs, technicians = {j.id: j for j in data.jobs}, {t.id: t for t in data.technicians}
    if len({a["job_id"] for a in assignments}) != len(assignments):
        return False
    for technician in technicians.values():
        route = sorted([a for a in assignments if a["technician_id"] == technician.id], key=lambda a: a["start_minute"])
        previous_end, previous_location = technician.shift_start, technician.start_location
        for assignment in route:
            job = jobs.get(assignment["job_id"])
            if job is None or not eligible(technician, job, data.service_date) or job.emergency:
                return False
            start = assignment["start_minute"]
            end = start + job.duration_minutes
            if start < max(job.window_start, previous_end + data.travel_minutes[previous_location][job.location]):
                return False
            if end > min(job.window_end, technician.shift_end):
                return False
            previous_end, previous_location = end, job.location
        if previous_end + data.travel_minutes[previous_location][technician.end_location] > technician.shift_end:
            return False
    return all(a["technician_id"] in technicians for a in assignments)


def optimize(payload: dict[str, Any]) -> dict[str, Any]:
    data = DispatchInput.model_validate(payload)
    if any(j.emergency for j in data.jobs):
        return {"status": "needs_emergency_review", "assignments": [], "external_actions_performed": 0}
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return {"status": "configuration_required", "dependency": "ortools==9.15.6755", "assignments": [], "external_actions_performed": 0}
    model = cp_model.CpModel()
    starts = {job.id: model.new_int_var(job.window_start, job.window_end - job.duration_minutes, job.id) for job in data.jobs}
    assigned: dict[tuple[str, str], Any] = {}
    objective = []
    for job in data.jobs:
        choices = []
        for technician in data.technicians:
            if not eligible(technician, job, data.service_date):
                continue
            choice = model.new_bool_var(f"{job.id}:{technician.id}")
            assigned[job.id, technician.id] = choice
            choices.append(choice)
            model.add(starts[job.id] >= technician.shift_start + data.travel_minutes[technician.start_location][job.location]).only_enforce_if(choice)
            model.add(starts[job.id] + job.duration_minutes + data.travel_minutes[job.location][technician.end_location] <= technician.shift_end).only_enforce_if(choice)
            objective.append(choice * job.priority * 1_000_000)
        model.add(sum(choices) <= 1)
        objective.append(-starts[job.id] * job.priority)
    for technician in data.technicians:
        possible = [j for j in data.jobs if (j.id, technician.id) in assigned]
        for index, left in enumerate(possible):
            for right in possible[index + 1:]:
                order = model.new_bool_var(f"before:{technician.id}:{left.id}:{right.id}")
                both = [assigned[left.id, technician.id], assigned[right.id, technician.id]]
                model.add(starts[right.id] >= starts[left.id] + left.duration_minutes + data.travel_minutes[left.location][right.location]).only_enforce_if([*both, order])
                model.add(starts[left.id] >= starts[right.id] + right.duration_minutes + data.travel_minutes[right.location][left.location]).only_enforce_if([*both, order.Not()])
    model.maximize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": "no_candidate", "assignments": [], "external_actions_performed": 0}
    results = [
        {"job_id": job.id, "technician_id": technician.id, "start_minute": solver.value(starts[job.id]), "end_minute": solver.value(starts[job.id]) + job.duration_minutes}
        for job in data.jobs for technician in data.technicians
        if (job.id, technician.id) in assigned and solver.boolean_value(assigned[job.id, technician.id])
    ]
    if not verify_candidate(data, results):
        raise ValueError("solver result failed independent scheduling verification")
    selected = {a["job_id"] for a in results}
    return {
        "status": "candidate_for_review", "assignments": results, "unassigned_job_ids": [j.id for j in data.jobs if j.id not in selected],
        "constraint_verified": True, "optimality_proven": status == cp_model.OPTIMAL,
        "provider_verified": False, "travel_basis": "owner_supplied_matrix", "external_actions_performed": 0,
        "limitations": ["Conservative depot travel bounds apply to every job.", "No live mapping, dispatch write or technician notification has occurred."],
    }
