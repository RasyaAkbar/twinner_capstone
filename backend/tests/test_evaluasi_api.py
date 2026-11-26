# ruff: noqa: F811  (fixture pytest diimpor lalu dipakai sebagai parameter tes)
"""SCRUM-32: endpoint evaluasi KPI (hitung, simpan, baca, bandingkan skenario).

Rumus KPI dan pembandingnya dites tanpa basis data (test_eval_*.py). Di sini dites alurnya
terhadap PostgreSQL dengan baseline sintetis seed 42 (fixture dari test_scenario_api.py) dan
simulasi tersimpan dari endpoint Run and save modul Simulasi.
"""

from app.main import app
from app.modules.auth.permissions import Perm
from app.modules.evaluasi.kpi_definitions import KPI_KEYS
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
from tests.test_simulation_api import active_overrun, run, withdrawal_params

PREFIX = "/api/v1/evaluasi"

EXPECTED = {
    ("GET", "/kpi-definitions"): Perm.BASELINE_READ,
    ("POST", "/runs/{run_id}/kpis"): Perm.BASELINE_READ,
    ("GET", "/runs/{run_id}/kpis"): Perm.BASELINE_READ,
    ("POST", "/compare"): Perm.BASELINE_READ,
}


def test_every_evaluasi_endpoint_uses_the_agreed_permission():
    actual = {
        (method, path.removeprefix(PREFIX)): set(inherited) | required_permissions(route.dependant)
        for path, route, inherited in walk(app.routes)
        if path.startswith(PREFIX)
        for method in route.methods
    }
    assert actual == {key: {perm} for key, perm in EXPECTED.items()}


def saved_run(client, baseline, **body) -> dict:
    scenario = create(client, parameters=active_overrun(baseline), **body)
    response = run(client, scenario["scenario_id"], mode="run_and_save")
    assert response.status_code == 200, response.text
    return response.json()


def evaluate(client, run_id: int, **body):
    return client.post(f"{PREFIX}/runs/{run_id}/kpis", json=body or None)


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestEvaluasiApi:
    def test_definitions_list_the_agreed_kpis_and_defaults(self, client):
        response = client.get(f"{PREFIX}/kpi-definitions")
        assert response.status_code == 200, response.text
        catalog = response.json()
        assert [k["key"] for k in catalog["kpis"]] == list(KPI_KEYS)
        config = catalog["default_config"]
        assert (config["wp"], config["wo"], config["wt"]) == (1.0, 0.5, 0.3)
        assert set(config["tolerances"]) == set(KPI_KEYS)

    def test_evaluate_stores_kpis_per_simulation_run(self, client, baseline):
        result = saved_run(client, baseline)
        run_id = result["run_id"]
        url = f"{PREFIX}/runs/{run_id}/kpis"
        assert client.get(url).json()["detail"]["type"] == "not_evaluated"

        response = evaluate(client, run_id)

        assert response.status_code == 200, response.text
        evaluation = response.json()
        assert isinstance(evaluation["evaluation_id"], int)
        assert (evaluation["run_id"], evaluation["scenario_id"]) == (
            run_id,
            result["scenario"]["scenario_id"],
        )
        assert evaluation["created_by"] == "penguji"
        assert evaluation["start_date"] == result["start_date"]
        assert [k["key"] for k in evaluation["kpis"]] == list(KPI_KEYS)
        for kpi in evaluation["kpis"]:
            assert kpi["status"] in ("better", "same", "worse")
            assert kpi["basis"] == "deterministic"
            assert kpi["replication"] is None
        summary = evaluation["summary"]
        assert summary["better"] + summary["same"] + summary["worse"] == len(KPI_KEYS)
        # Overrun tidak pernah membuat estimasi lebih akurat.
        ear = next(k for k in evaluation["kpis"] if k["key"] == "estimate_accuracy_ratio")
        assert ear["scenario"] >= ear["baseline"]

        stored = client.get(url)
        assert stored.status_code == 200, stored.text
        assert stored.json() == evaluation
        # Konfigurasi sama: hasil tersimpan dipakai ulang.
        assert evaluate(client, run_id).json()["evaluation_id"] == evaluation["evaluation_id"]
        refreshed = evaluate(client, run_id, refresh=True).json()
        assert refreshed["evaluation_id"] != evaluation["evaluation_id"]
        assert refreshed["kpis"] == evaluation["kpis"]

    def test_other_config_is_stored_separately(self, client, baseline):
        run_id = saved_run(client, baseline)["run_id"]
        default = evaluate(client, run_id).json()
        custom = evaluate(client, run_id, config={"tolerances": {"schedule_variance": 100}}).json()
        assert custom["evaluation_id"] != default["evaluation_id"]
        sv = next(k for k in custom["kpis"] if k["key"] == "schedule_variance")
        assert (sv["tolerance"], sv["status"]) == (100, "same")
        # GET tetap mengembalikan konfigurasi bawaan.
        assert client.get(f"{PREFIX}/runs/{run_id}/kpis").json() == default

    def test_replicated_run_reports_median_and_range(self, client, baseline):
        scenario = create(client, parameters=active_overrun(baseline))
        response = run(
            client,
            scenario["scenario_id"],
            mode="run_and_save",
            replication={"runs": 10, "seed": 5},
        )
        assert response.status_code == 200, response.text

        evaluation = evaluate(client, response.json()["run_id"]).json()

        assert evaluation["replication"] == {"runs": 10, "seed": 5}
        for kpi in evaluation["kpis"]:
            assert kpi["basis"] == "median"
            assert kpi["replication"]["runs"] == 10
            if kpi["key"] != "on_time_probability":
                assert kpi["baseline"] == kpi["replication"]["baseline"]["median"]

    def test_unknown_run_and_invalid_config(self, client, baseline):
        assert client.post(f"{PREFIX}/runs/999999/kpis").status_code == 404
        assert client.get(f"{PREFIX}/runs/999999/kpis").status_code == 404
        run_id = saved_run(client, baseline)["run_id"]
        bad = evaluate(client, run_id, config={"tolerances": {"sprint_velocity": 1}})
        assert bad.status_code == 422

    def test_compare_picks_the_best_scenario(self, client, baseline):
        overrun = saved_run(client, baseline)
        withdrawal = create(
            client,
            name="Anggota keluar tim",
            situation="member_rotation",
            parameters=withdrawal_params(baseline),
        )
        assert run(client, withdrawal["scenario_id"], mode="run_and_save").status_code == 200

        response = client.post(
            f"{PREFIX}/compare",
            json={"scenario_ids": [overrun["scenario"]["scenario_id"], withdrawal["scenario_id"]]},
        )

        assert response.status_code == 200, response.text
        comparison = response.json()
        ranking = comparison["ranking"]
        assert [r["rank"] for r in ranking] == [1, 2]
        assert ranking[0]["is_best"] and not ranking[1]["is_best"]
        assert comparison["best_run_id"] == ranking[0]["run_id"]
        assert (ranking[0]["status_score"], ranking[0]["magnitude_score"]) >= (
            ranking[1]["status_score"],
            ranking[1]["magnitude_score"],
        )
        assert comparison["comparable"] is True
        assert len(comparison["evaluations"]) == 2
        # Evaluasi setiap simulasi ikut tersimpan.
        for evaluation in comparison["evaluations"]:
            stored = client.get(f"{PREFIX}/runs/{evaluation['run_id']}/kpis")
            assert stored.status_code == 200

    def test_compare_requires_saved_simulations(self, client, baseline):
        draft = create(client, parameters=active_overrun(baseline))
        response = client.post(f"{PREFIX}/compare", json={"scenario_ids": [draft["scenario_id"]]})
        assert response.status_code == 404
        assert response.json()["detail"]["type"] == "not_simulated"
        assert client.post(f"{PREFIX}/compare", json={"run_ids": [999999]}).status_code == 404
        both = client.post(f"{PREFIX}/compare", json={"run_ids": [1], "scenario_ids": [1]})
        assert both.status_code == 422
        bad_weight = client.post(
            f"{PREFIX}/compare", json={"run_ids": [1], "weights": {"sprint_velocity": 1}}
        )
        assert bad_weight.status_code == 422

    def test_evaluation_is_deleted_with_its_scenario(self, client, baseline):
        result = saved_run(client, baseline)
        run_id = result["run_id"]
        assert evaluate(client, run_id).status_code == 200
        url = f"{SCENARIOS}/{result['scenario']['scenario_id']}"
        assert client.delete(url).status_code == 204
        assert client.get(f"{PREFIX}/runs/{run_id}/kpis").status_code == 404
