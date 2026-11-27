"""SCRUM-28 (UC-03) dan SCRUM-29 (UC-04): menjalankan simulasi dan melihat hasilnya lewat API.

Mesin simulasinya dites tanpa basis data (test_sim_*.py, test_simulation_result.py). Di sini
dites alurnya terhadap PostgreSQL dengan baseline sintetis seed 42 yang diimpor lewat API
SCRUM-15 (fixture dari test_scenario_api.py). Peta izin endpoint ada di test_scenario_api.py.
"""

from collections import defaultdict

from app.modules.evaluasi.kpi_definitions import KPI_KEYS as EVALUASI_KPI_KEYS
from app.modules.simulasi.simulation_models import SimulationRun
from tests.conftest import REQUIRES_DB
from tests.test_scenario_api import (  # noqa: F401  (fixture pytest)
    SCENARIOS,
    baseline,
    create,
    csv_files,
    locs,
    no_snapshots,
    snapshot,
)

# Hari kerja pertama setelah realisasi terakhir data sintetis per 2026-11-06 (Jumat).
DEFAULT_START = "2026-11-09"
ACTIVE_PROJECT_STATUSES = ("planning", "development", "testing")
# KPI hasil simulasi = KPI evaluasi (FR-04.1 s.d. FR-04.10).
KPI_KEYS = list(EVALUASI_KPI_KEYS)


def run(client, scenario_id: int, **body):
    return client.post(f"{SCENARIOS}/{scenario_id}/run", json=body)


def active_overrun(baseline: dict) -> dict:
    """Task Overrun +3 hari pada task belum mulai di proyek aktif.

    `overrun_params` di test_scenario_api.py dapat memilih task proyek on hold, yang memang
    tidak dijadwalkan simulasi sehingga tidak berdampak.
    """
    active = {
        p["project_id"] for p in baseline["projects"] if p["status"] in ACTIVE_PROJECT_STATUSES
    }
    task = next(
        t for t in baseline["tasks"] if t["status"] == "not_started" and t["project_id"] in active
    )
    days = task["estimated_days"]
    return {
        "task_id": task["task_id"],
        "original_days": days,
        "revised_days": days + 3,
        "remaining_days": days + 3,
    }


def project_of(baseline: dict, task_id: str) -> str:
    return next(t["project_id"] for t in baseline["tasks"] if t["task_id"] == task_id)


def withdrawal_params(baseline: dict) -> dict:
    """Anggota satu-satunya pemegang suatu peran keluar tim: task peran itu tidak terjadwal."""
    holders: dict[str, set[str]] = defaultdict(set)
    for resource in baseline["resources"]:
        roles = [resource["primary_role_id"], *(resource["secondary_role_ids"] or "").split(";")]
        for role in filter(None, roles):
            holders[role].add(resource["resource_id"])
    open_roles = {
        t["required_role_id"]
        for t in baseline["tasks"]
        if t["status"] not in ("done", "dropped", "on_hold")
    }
    role = next(r for r in sorted(open_roles) if len(holders[r]) == 1)
    (member,) = holders[role]
    project_of = {t["task_id"]: t["project_id"] for t in baseline["tasks"]}
    origin = next(
        project_of[a["task_id"]] for a in baseline["assignments"] if a["resource_id"] == member
    )
    return {
        "resource_id": member,
        "origin_project_id": origin,
        "destination_project_id": None,
        "transfer_date": DEFAULT_START,
    }


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestSimulationApi:
    def test_preview_shows_scenario_parameters_before_the_run(self, client, baseline):
        params = active_overrun(baseline)
        scenario = create(client, parameters=params)

        response = client.get(f"{SCENARIOS}/{scenario['scenario_id']}/run-preview")

        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["parameters"] == params
        assert (preview["can_run"], preview["parameter_errors"]) == (True, [])
        assert preview["scenario"]["name"] == scenario["name"]
        assert preview["data_source"] == "synthetic"
        assert preview["default_start_date"] == DEFAULT_START
        assert preview["default_replications"] == 500
        assert preview["latest_run"] is None

    def test_preview_of_incomplete_parameters_cannot_run(self, client, baseline):
        scenario = create(client)
        preview = client.get(f"{SCENARIOS}/{scenario['scenario_id']}/run-preview").json()
        assert preview["can_run"] is False
        assert preview["parameter_errors"]

    def test_run_only_returns_kpis_without_saving_anything(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        url = f"{SCENARIOS}/{scenario['scenario_id']}"

        response = run(client, scenario["scenario_id"], mode="run_only")

        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["run_id"], result["saved"], result["mode"]) == (None, False, "run_only")
        assert result["start_date"] == DEFAULT_START
        assert [k["key"] for k in result["kpis"]] == KPI_KEYS
        for kpi in result["kpis"]:
            assert kpi["status"] in ("better", "same", "worse")
            assert kpi["definition"]
        task_id = active_overrun(baseline)["task_id"]
        overrun = next(t for t in result["tasks"] if t["task_id"] == task_id)
        assert overrun["targeted"] and overrun["affected"]
        assert overrun["scenario"]["effort"] > overrun["baseline"]["effort"]
        assert result["summary"]["affected_tasks"] >= 1
        # Skenario tetap draft dan belum ada hasil tersimpan.
        assert client.get(url).json()["status"] == "draft"
        not_simulated = client.get(f"{url}/result")
        assert not_simulated.status_code == 404
        assert not_simulated.json()["detail"]["type"] == "not_simulated"
        assert "belum disimulasikan" in not_simulated.json()["detail"]["msg"]

    def test_run_and_save_stores_the_scenario_then_the_result(self, client, baseline):
        params = active_overrun(baseline)
        scenario = create(client, parameters=params)
        url = f"{SCENARIOS}/{scenario['scenario_id']}"

        response = run(client, scenario["scenario_id"], mode="run_and_save")

        assert response.status_code == 200, response.text
        result = response.json()
        assert result["saved"] is True
        assert isinstance(result["run_id"], int)
        assert result["created_by"] == "penguji"
        assert result["scenario"]["status"] == "saved"
        saved = client.get(url).json()
        assert (saved["status"], saved["saved_at"] is not None) == ("saved", True)

        stored = client.get(f"{url}/result")
        assert stored.status_code == 200, stored.text
        opened = stored.json()
        assert opened["run_id"] == result["run_id"]
        assert opened["is_stale"] is False
        assert opened["kpis"] == result["kpis"]
        preview = client.get(f"{url}/run-preview").json()
        assert preview["latest_run"]["run_id"] == result["run_id"]

    def test_unsaved_form_parameters_are_saved_before_running(self, client, baseline):
        scenario = create(client)
        params = active_overrun(baseline)

        response = run(client, scenario["scenario_id"], mode="run_and_save", parameters=params)

        assert response.status_code == 200, response.text
        saved = client.get(f"{SCENARIOS}/{scenario['scenario_id']}").json()
        assert saved["status"] == "saved"
        assert saved["parameters"]["task_id"] == params["task_id"]

    def test_invalid_parameters_stop_the_run_and_change_nothing(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        broken = {**active_overrun(baseline), "task_id": "TSK-9999"}

        response = run(client, scenario["scenario_id"], mode="run_and_save", parameters=broken)

        assert response.status_code == 422
        assert locs(response) == [["body", "parameters", "task_id"]]
        unchanged = client.get(url).json()
        assert unchanged["status"] == "draft"
        assert unchanged["parameters"] == active_overrun(baseline)
        assert client.get(f"{url}/result").status_code == 404

    def test_missing_stored_parameters_stop_the_run(self, client, baseline):
        scenario = create(client)
        response = run(client, scenario["scenario_id"], mode="run_only")
        assert response.status_code == 422
        assert ["body", "parameters", "task_id"] in locs(response)

    def test_start_date_outside_the_baseline_is_rejected(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        response = run(client, scenario["scenario_id"], start_date="2099-01-01")
        assert response.status_code == 422
        assert locs(response) == [["body", "start_date"]]

    def test_unscheduled_tasks_are_warned_and_the_run_continues(self, client, baseline):
        scenario = create(
            client,
            name="Satu-satunya pemegang peran keluar",
            situation="member_rotation",
            parameters=withdrawal_params(baseline),
        )

        response = run(client, scenario["scenario_id"], mode="run_only")

        assert response.status_code == 200, response.text
        result = response.json()
        warnings = [w for w in result["warnings"] if w["run"] == "scenario"]
        assert warnings
        assert {w["code"] for w in warnings} <= {
            "no_member_with_role",
            "waits_unscheduled_task",
        }
        unscheduled = [
            t for t in result["tasks"] if t["scenario"] and t["scenario"]["state"] == "unscheduled"
        ]
        assert len(unscheduled) == result["summary"]["unscheduled_tasks"] > 0
        assert all(t["scenario"]["reason"] for t in unscheduled)
        # Proyek dengan task tidak terjadwal tidak dapat diluncurkan: SV kosong (FR-04.1).
        sv = next(k for k in result["kpis"] if k["key"] == "schedule_variance")
        assert sv["scenario"] is None
        assert sv["status"] == ("worse" if sv["baseline"] is not None else "same")
        on_time = next(k for k in result["kpis"] if k["key"] == "on_time_probability")
        assert on_time["scenario"] == 0.0
        # Task lain tetap dijadwalkan.
        assert any(t["scenario"] and t["scenario"]["state"] == "scheduled" for t in result["tasks"])

    def test_replication_reports_median_and_range(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        body = {"mode": "run_only", "replication": {"runs": 20, "seed": 7}}

        first = run(client, scenario["scenario_id"], **body)

        assert first.status_code == 200, first.text
        result = first.json()
        assert result["replication"] == {"runs": 20, "seed": 7}
        rli = next(k for k in result["kpis"] if k["key"] == "resource_load_index")
        assert rli["basis"] == "median"
        stats = rli["replication"]["scenario"]
        assert stats["runs"] == 20
        assert stats["min"] <= stats["median"] <= stats["max"]
        task = next(t for t in result["tasks"] if t["replication"] is not None)
        assert task["replication"]["scenario_finish"]["runs"] >= 0
        # Benih yang sama memberi hasil yang sama.
        again = run(client, scenario["scenario_id"], **body).json()
        assert again["kpis"] == result["kpis"]

    def test_replication_run_count_is_bounded(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        response = run(client, scenario["scenario_id"], replication={"runs": 5})
        assert response.status_code == 422

    def test_default_replication_is_500_runs(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        response = run(client, scenario["scenario_id"], replication={})
        assert response.status_code == 200, response.text
        assert response.json()["replication"]["runs"] == 500

    def test_project_drilldown_shows_the_shift_of_each_task(self, client, baseline):
        params = active_overrun(baseline)
        scenario = create(client, parameters=params)
        run(client, scenario["scenario_id"], mode="run_and_save")
        project_id = project_of(baseline, params["task_id"])
        url = f"{SCENARIOS}/{scenario['scenario_id']}/result/projects/{project_id}"

        response = client.get(url)

        assert response.status_code == 200, response.text
        drilldown = response.json()
        assert drilldown["project"]["project_id"] == project_id
        assert drilldown["project"]["affected"] is True
        assert drilldown["tasks"]
        assert all(t["project_id"] == project_id for t in drilldown["tasks"])
        overrun = next(t for t in drilldown["tasks"] if t["task_id"] == params["task_id"])
        # Estimasi bertambah 3 hari kerja: task selesai lebih lambat.
        assert overrun["finish_shift"] > 0

    def test_member_drilldown_shows_daily_load_with_overload_flags(self, client, baseline):
        params = active_overrun(baseline)
        scenario = create(client, parameters=params)
        result = run(client, scenario["scenario_id"], mode="run_and_save").json()
        overrun = next(t for t in result["tasks"] if t["task_id"] == params["task_id"])
        member = overrun["scenario"]["owner"]

        response = client.get(f"{SCENARIOS}/{scenario['scenario_id']}/result/members/{member}")

        assert response.status_code == 200, response.text
        drilldown = response.json()
        assert drilldown["member"]["resource_id"] == member
        days = drilldown["member"]["days"]
        assert days and days[0]["day"] == DEFAULT_START
        assert all("over_capacity" in day and "scenario_load" in day for day in days)
        periods = drilldown["member"]["periods"]
        assert periods
        # Overload per minggu mengikuti RLI (FR-04.3, FR-04.4).
        assert all("scenario_rli" in p and "overloaded" in p for p in periods)
        assert any(t["task_id"] == params["task_id"] for t in drilldown["tasks"])

    def test_drilldown_of_unknown_items_is_404(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        run(client, scenario["scenario_id"], mode="run_and_save")
        url = f"{SCENARIOS}/{scenario['scenario_id']}/result"
        assert client.get(f"{url}/projects/PRJ-999").status_code == 404
        assert client.get(f"{url}/members/RES-999").status_code == 404

    def test_result_is_marked_stale_after_parameters_change(self, client, baseline):
        params = active_overrun(baseline)
        scenario = create(client, parameters=params)
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        run(client, scenario["scenario_id"], mode="run_and_save")

        changed = {**params, "revised_days": params["revised_days"] + 1}
        changed["remaining_days"] = changed["revised_days"]
        assert client.patch(url, json={"parameters": changed}).status_code == 200

        stored = client.get(f"{url}/result").json()
        assert stored["is_stale"] is True
        assert stored["scenario"]["status"] == "draft"

    def test_result_saved_with_the_old_kpi_list_is_rebuilt_with_evaluasi_kpis(
        self, client, baseline, db
    ):
        scenario = create(client, parameters=active_overrun(baseline))
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        saved = run(client, scenario["scenario_id"], mode="run_and_save").json()
        # Hasil yang tersimpan sebelum KPI mengikuti modul Evaluasi (SCRUM-28).
        row = db.get(SimulationRun, saved["run_id"])
        legacy = {**row.result, "kpis": [{"key": "completion_date", "better": "lower"}]}
        legacy.pop("kpi_config")
        legacy.pop("kpi_summary")
        row.result = legacy
        db.commit()

        opened = client.get(f"{url}/result")

        assert opened.status_code == 200, opened.text
        rebuilt = opened.json()
        assert [k["key"] for k in rebuilt["kpis"]] == KPI_KEYS
        assert rebuilt["kpis"] == saved["kpis"]
        assert (rebuilt["run_id"], rebuilt["saved"]) == (saved["run_id"], True)
        db.expire_all()
        stored = db.get(SimulationRun, saved["run_id"])
        assert [k["key"] for k in stored.result["kpis"]] == KPI_KEYS

    def test_results_are_deleted_with_their_scenario(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        run(client, scenario["scenario_id"], mode="run_and_save")

        assert client.delete(url).status_code == 204
        assert client.get(f"{url}/result").status_code == 404

    def test_unknown_scenario_is_404(self, client, baseline):
        assert run(client, 999999).status_code == 404
        assert client.get(f"{SCENARIOS}/999999/result").status_code == 404
        assert client.get(f"{SCENARIOS}/999999/run-preview").status_code == 404
