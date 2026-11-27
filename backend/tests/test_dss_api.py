# ruff: noqa: F811  (fixture pytest diimpor lalu dipakai sebagai parameter tes)
"""SCRUM-69, SCRUM-70, SCRUM-71: sweep DSS lewat API, dari Run and save sampai rekomendasi.

Sweep, penilaian, dan alasannya dites tanpa basis data (test_dss_*.py). Di sini dites alurnya
terhadap PostgreSQL dengan baseline sintetis seed 42 (fixture dari test_scenario_api.py). Job
sweep dijalankan langsung di dalam permintaan (`InlineScheduler`), kecuali tes yang memerlukan
sweep tetap `pending` (`NoScheduler`, bawaan fixture `client`).
"""

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.main import app
from app.modules.auth.permissions import Perm
from app.modules.dss import sweep_job
from app.modules.dss.models import DssSweep
from app.modules.dss.sweep_job import InlineScheduler, get_sweep_scheduler, run_sweep_job
from tests.conftest import REQUIRES_DB
from tests.test_data_permissions import required_permissions, walk
from tests.test_scenario_api import (  # noqa: F401  (fixture pytest)
    SCENARIOS,
    baseline,
    create,
    csv_files,
    no_snapshots,
    snapshot,
)
from tests.test_simulation_api import ACTIVE_PROJECT_STATUSES, run

PREFIX = "/api/v1/dss"

EXPECTED = {
    ("GET", "/scenarios"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}/sweep"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}/sweep/candidates/{candidate_id}"): Perm.BASELINE_READ,
    ("POST", "/scenarios/{scenario_id}/sweep"): Perm.BASELINE_READ,
}
# Replikasi kecil supaya tiga kandidat teratas cepat direplikasi.
REPLICATION = {"runs": 20, "seed": 7}


def test_every_dss_endpoint_uses_the_agreed_permission():
    actual = {
        (method, path.removeprefix(PREFIX)): set(inherited) | required_permissions(route.dependant)
        for path, route, inherited in walk(app.routes)
        if path.startswith(PREFIX)
        for method in route.methods
    }
    assert actual == {key: {perm} for key, perm in EXPECTED.items()}


# --- parameter skenario untuk baseline sintetis ------------------------------------------------


def absence_params(baseline: dict) -> dict:
    """Anggota dengan task terbuka terbanyak di proyek aktif, tanpa ketidakhadiran di periode."""
    active = {
        p["project_id"] for p in baseline["projects"] if p["status"] in ACTIVE_PROJECT_STATUSES
    }
    open_tasks = {
        t["task_id"]
        for t in baseline["tasks"]
        if t["project_id"] in active and t["status"] in ("not_started", "in_progress")
    }
    busy = {
        a["resource_id"]
        for a in baseline["availability"]
        if a["date_start"] <= "2026-11-20" and "2026-11-09" <= a["date_end"]
    }
    owners = Counter(
        a["resource_id"]
        for a in baseline["assignments"]
        if a["task_id"] in open_tasks
        and a["released_date"] is None
        and a["resource_id"] not in busy
    )
    member = owners.most_common(1)[0][0]
    return {
        "resource_id": member,
        "absence_type": "sudden_leave",
        "absence_period": {"start": "2026-11-09", "end": "2026-11-20"},
    }


def new_member_params(baseline: dict) -> dict:
    role = Counter(r["primary_role_id"] for r in baseline["resources"]).most_common(1)[0][0]
    return {
        "role_id": role,
        "seniority": "junior",
        "join_date": "2026-11-16",
        "onboarding_weeks": 2,
        "initial_capacity": 0.5,
    }


@pytest.fixture
def sweeps(client):
    """Client yang menjalankan job sweep langsung di dalam permintaan."""
    app.dependency_overrides[get_sweep_scheduler] = InlineScheduler
    return client


def run_and_save(client, situation: str, params: dict, **body) -> tuple[dict, dict]:
    scenario = create(client, name=f"DSS {situation}", situation=situation, parameters=params)
    response = run(client, scenario["scenario_id"], mode="run_and_save", **body)
    assert response.status_code == 200, response.text
    return scenario, response.json()


def sweep_of(client, scenario_id: int) -> dict:
    response = client.get(f"{PREFIX}/scenarios/{scenario_id}/sweep")
    assert response.status_code == 200, response.text
    return response.json()


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestDssApi:
    def test_run_and_save_ranks_replacements_for_an_absence(self, sweeps, baseline):
        params = absence_params(baseline)
        scenario, result = run_and_save(sweeps, "member_absence", params, replication=REPLICATION)

        assert result["dss_sweep_id"] is not None
        sweep = sweep_of(sweeps, scenario["scenario_id"])
        assert sweep["sweep_id"] == result["dss_sweep_id"]
        assert (sweep["status"], sweep["error"]) == ("done", None)
        assert sweep["lever"] == "replacement_resource_id"
        assert sweep["completed_candidates"] == sweep["total_candidates"] > 1
        assert sweep["scenario"]["scenario_id"] == scenario["scenario_id"]
        assert sweep["data_source"] == "synthetic"
        assert set(sweep["weights"]) == {
            "role_match",
            "available_capacity",
            "current_work",
            "kpi_effect",
        }

        ranked = [c for c in sweep["candidates"] if c["rank"] is not None]
        assert [c["rank"] for c in ranked] == list(range(1, len(ranked) + 1))
        assert sweep["ranked_candidates"] == len(ranked)
        current = next(c for c in sweep["candidates"] if c["is_current"])
        assert sweep["current_rank"] == current["rank"]
        assert current["reason"].startswith("Ini skenario pilihan Anda, tanpa pengalihan task.")
        assert all(c["reason"] for c in sweep["candidates"])
        for candidate in ranked:
            assert 0 <= candidate["score"] <= 1
            assert set(candidate["criteria"]) == set(sweep["weights"])
        # Tiga kandidat teratas selain pilihan Anda mendapat replikasi Monte Carlo.
        others = [c for c in ranked if not c["is_current"]]
        assert [c["replicated"] for c in others[:3]] == [True] * min(3, len(others))
        assert not any(c["replicated"] for c in others[3:])

    def test_new_member_sweep_tries_every_mentor(self, sweeps, baseline):
        scenario, _ = run_and_save(sweeps, "new_member", new_member_params(baseline))

        sweep = sweep_of(sweeps, scenario["scenario_id"])

        assert (sweep["status"], sweep["lever"]) == ("done", "mentor_resource_id")
        current = next(c for c in sweep["candidates"] if c["is_current"])
        assert (current["value"], current["label"]) == (None, "Tanpa mentor")
        mentors = [c for c in sweep["candidates"] if c["value"] is not None]
        assert len(mentors) >= 2
        assert all(c["value"].startswith("RES-") for c in mentors)

    def test_candidate_detail_has_criteria_facts_and_kpis(self, sweeps, baseline):
        scenario, _ = run_and_save(sweeps, "member_absence", absence_params(baseline))
        sweep = sweep_of(sweeps, scenario["scenario_id"])
        top = sweep["candidates"][0]
        url = f"{PREFIX}/scenarios/{scenario['scenario_id']}/sweep/candidates"

        response = sweeps.get(f"{url}/{top['candidate_id']}")

        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["reason"] == top["reason"]
        assert {item["key"] for item in detail["vs_baseline"]} == {
            item["key"] for item in detail["vs_current"]
        }
        assert set(detail["criteria_facts"]) == set(top["criteria"])
        other = create(sweeps, name="Skenario lain")
        missing = sweeps.get(
            f"{PREFIX}/scenarios/{other['scenario_id']}/sweep/candidates/{top['candidate_id']}"
        )
        assert missing.status_code == 404

    def test_list_shows_scenarios_with_their_sweep_and_staleness(self, sweeps, baseline):
        params = absence_params(baseline)
        scenario, _ = run_and_save(sweeps, "member_absence", params)

        listed = sweeps.get(f"{PREFIX}/scenarios").json()
        item = next(i for i in listed if i["scenario"]["scenario_id"] == scenario["scenario_id"])
        assert (item["sweep"]["status"], item["is_stale"]) == ("done", False)

        changed = {**params, "absence_period": {"start": "2026-11-09", "end": "2026-11-13"}}
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        assert sweeps.patch(url, json={"parameters": changed}).status_code == 200
        listed = sweeps.get(f"{PREFIX}/scenarios").json()
        item = next(i for i in listed if i["scenario"]["scenario_id"] == scenario["scenario_id"])
        assert item["is_stale"] is True
        assert sweep_of(sweeps, scenario["scenario_id"])["is_stale"] is True

    def test_run_only_starts_no_sweep_and_errors_are_typed(self, client, baseline):
        scenario = create(client, situation="member_absence", parameters=absence_params(baseline))
        response = run(client, scenario["scenario_id"], mode="run_only")
        assert response.json()["dss_sweep_id"] is None

        url = f"{PREFIX}/scenarios/{scenario['scenario_id']}/sweep"
        not_simulated = client.get(url)
        assert not_simulated.status_code == 404
        assert not_simulated.json()["detail"]["type"] == "not_simulated"
        assert client.post(url).status_code == 404
        assert client.get(f"{PREFIX}/scenarios/999999/sweep").status_code == 404

    def test_retry_is_refused_while_running_and_allowed_after_a_stale_sweep(
        self, client, baseline, db
    ):
        # `client` memakai NoScheduler: sweep Run and save tetap pending.
        scenario, result = run_and_save(client, "member_absence", absence_params(baseline))
        url = f"{PREFIX}/scenarios/{scenario['scenario_id']}/sweep"
        assert client.get(url).json()["status"] == "pending"

        busy = client.post(url)
        assert busy.status_code == 409
        assert busy.json()["detail"]["type"] == "sweep_running"

        old = datetime.now(UTC) - timedelta(hours=2)
        sweep_id = result["dss_sweep_id"]
        db.execute(update(DssSweep).where(DssSweep.sweep_id == sweep_id).values(updated_at=old))
        db.commit()
        stale = client.get(url).json()
        assert stale["status"] == "failed"
        assert "Terhenti tanpa kemajuan" in stale["error"]

        app.dependency_overrides[get_sweep_scheduler] = InlineScheduler
        retried = client.post(url)
        assert retried.status_code == 202, retried.text
        assert retried.json()["sweep_id"] > result["dss_sweep_id"]
        assert client.get(url).json()["status"] == "done"

    def test_newer_run_and_save_cancels_the_older_sweep(self, client, baseline, session_factory):
        params = absence_params(baseline)
        scenario, first = run_and_save(client, "member_absence", params)
        second = run(client, scenario["scenario_id"], mode="run_and_save").json()

        run_sweep_job(first["dss_sweep_id"], session_factory)
        run_sweep_job(second["dss_sweep_id"], session_factory)

        with session_factory() as session:
            older = session.get(DssSweep, first["dss_sweep_id"])
            newer = session.get(DssSweep, second["dss_sweep_id"])
            assert (older.status, older.error) == (
                "cancelled",
                "Digantikan sweep yang lebih baru untuk skenario ini",
            )
            assert newer.status == "done"

    def test_failed_sweep_keeps_the_simulation_result(self, sweeps, baseline, monkeypatch):
        def broken(*args, **kwargs):
            raise ValueError("Kandidat tidak dapat disusun")

        monkeypatch.setattr(sweep_job, "build_candidates", broken)
        scenario, result = run_and_save(sweeps, "member_absence", absence_params(baseline))

        sweep = sweep_of(sweeps, scenario["scenario_id"])
        assert (sweep["status"], sweep["error"]) == ("failed", "Kandidat tidak dapat disusun")
        assert sweep["candidates"] == []
        saved = sweeps.get(f"{SCENARIOS}/{scenario['scenario_id']}/result")
        assert saved.status_code == 200
        assert saved.json()["run_id"] == result["run_id"]
