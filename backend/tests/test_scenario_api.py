"""SCRUM-22: endpoint Scenario Manager (daftar, buat draft, baca, ubah, simpan, duplikasi, hapus).

Aturan parameter per situasi dites tanpa basis data di test_scenario_params.py. Di sini dites
alurnya terhadap PostgreSQL dengan baseline sintetis seed 42 yang diimpor lewat API SCRUM-15.
"""

from datetime import date

import pytest
from sqlalchemy import text

from app.main import app
from app.modules.auth.permissions import Perm
from app.modules.simulasi.scenario_models import Situation
from synthetic.final_sources import write_final_sources
from synthetic.world import build_world
from tests.conftest import REQUIRES_DB, make_token
from tests.test_data_permissions import required_permissions, walk

AS_OF = date(2026, 11, 6)
PREFIX = "/api/v1/simulasi"
SCENARIOS = f"{PREFIX}/scenarios"

# Peta izin endpoint skenario; endpoint baru wajib ditambahkan di sini.
EXPECTED = {
    ("GET", "/scenarios/situations"): Perm.BASELINE_READ,
    ("GET", "/scenarios"): Perm.BASELINE_READ,
    ("POST", "/scenarios"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}"): Perm.BASELINE_READ,
    ("PATCH", "/scenarios/{scenario_id}"): Perm.BASELINE_READ,
    ("POST", "/scenarios/{scenario_id}/save"): Perm.BASELINE_READ,
    ("POST", "/scenarios/{scenario_id}/duplicate"): Perm.BASELINE_READ,
    ("DELETE", "/scenarios/{scenario_id}"): Perm.BASELINE_READ,
    # Menjalankan simulasi dan hasilnya (SCRUM-28, SCRUM-29).
    ("GET", "/scenarios/{scenario_id}/run-preview"): Perm.BASELINE_READ,
    ("POST", "/scenarios/{scenario_id}/run"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}/result"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}/result/projects/{project_id}"): Perm.BASELINE_READ,
    ("GET", "/scenarios/{scenario_id}/result/members/{resource_id}"): Perm.BASELINE_READ,
}


def test_every_scenario_endpoint_uses_the_agreed_permission():
    actual = {
        (method, path.removeprefix(PREFIX)): set(inherited) | required_permissions(route.dependant)
        for path, route, inherited in walk(app.routes)
        if path.startswith(SCENARIOS)
        for method in route.methods
    }
    assert actual == {key: {perm} for key, perm in EXPECTED.items()}


def test_situations_route_is_not_shadowed_by_the_id_route():
    paths = [path for path, _, _ in walk(app.routes) if path.startswith(SCENARIOS + "/")]
    assert paths.index(SCENARIOS + "/situations") < paths.index(SCENARIOS + "/{scenario_id}")


# --- terhadap PostgreSQL ------------------------------------------------------------------


@pytest.fixture(scope="module")
def csv_files(tmp_path_factory) -> dict[str, str]:
    folder = tmp_path_factory.mktemp("final_sources")
    write_final_sources(build_world(42, AS_OF), folder)
    return {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.csv")}


@pytest.fixture
def no_snapshots(session_factory):
    """Skenario ikut terhapus lewat foreign key ke baseline_snapshot (TRUNCATE ... CASCADE)."""
    with session_factory() as session:
        session.execute(text("TRUNCATE twinner.baseline_snapshot CASCADE"))
        session.commit()


def import_snapshot(client, csv_files, data_source="synthetic") -> dict:
    response = client.post(
        f"{PREFIX}/baseline-snapshots/import",
        json={"data_source": data_source, "files": csv_files, "dry_run": False},
    )
    assert response.status_code == 200, response.text
    return response.json()["snapshot"]


@pytest.fixture
def snapshot(client, no_snapshots, csv_files) -> dict:
    return import_snapshot(client, csv_files)


@pytest.fixture
def baseline(client, snapshot) -> dict:
    response = client.get(f"{PREFIX}/baseline-snapshots/{snapshot['snapshot_id']}")
    assert response.status_code == 200
    return response.json()


def open_task_with_estimate(baseline: dict) -> dict:
    return next(t for t in baseline["tasks"] if t["status"] not in ("done", "dropped"))


def overrun_params(baseline: dict) -> dict:
    """Parameter Task Overrun yang valid untuk baseline sintetis."""
    task = open_task_with_estimate(baseline)
    days = task["estimated_days"]
    return {
        "task_id": task["task_id"],
        "original_days": days,
        "revised_days": days + 2,
        "remaining_days": days,
    }


def create(client, **body) -> dict:
    body = {"name": "Overrun task utama", "situation": "task_overrun", **body}
    response = client.post(SCENARIOS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def locs(response) -> list[list]:
    return [error["loc"] for error in response.json()["detail"]]


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestScenarioApi:
    def test_situations_with_parameter_schema(self, client):
        response = client.get(f"{SCENARIOS}/situations")
        assert response.status_code == 200
        situations = response.json()
        assert [s["situation"] for s in situations] == [s.value for s in Situation]
        absence = situations[1]
        assert absence["label"] == "Member Absence"
        schema = absence["parameters_schema"]
        assert schema["required"] == ["resource_id", "absence_type", "absence_period"]
        assert schema["properties"]["absence_type"]["enum"] == [
            "planned_leave",
            "sudden_leave",
            "maternity_leave",
        ]

    def test_cannot_create_without_baseline(self, client, no_snapshots):
        response = client.post(SCENARIOS, json={"name": "A", "situation": "new_member"})
        assert response.status_code == 409
        assert "Belum ada baseline snapshot" in response.json()["detail"]["msg"]
        assert client.get(SCENARIOS).json() == []

    def test_create_draft_linked_to_current_snapshot(self, client, csv_files, snapshot):
        newest = import_snapshot(client, csv_files, data_source="puti")

        scenario = create(client, name="  Overrun task utama  ")

        assert scenario["name"] == "Overrun task utama"
        assert scenario["situation"] == "task_overrun"
        assert scenario["status"] == "draft"
        assert scenario["snapshot_id"] == newest["snapshot_id"] != snapshot["snapshot_id"]
        assert scenario["data_source"] == "puti"
        assert scenario["created_by"] == scenario["updated_by"] == "penguji"
        assert scenario["saved_at"] is None
        assert scenario["duplicated_from"] is None
        assert scenario["parameters"] == {}
        # Draft kosong: setiap field wajib dilaporkan, tidak ditolak.
        assert {tuple(e["loc"]) for e in scenario["parameter_errors"]} == {
            ("body", "parameters", name)
            for name in ("task_id", "original_days", "revised_days", "remaining_days")
        }

    def test_create_on_a_chosen_snapshot(self, client, csv_files, snapshot):
        import_snapshot(client, csv_files, data_source="puti")
        scenario = create(client, snapshot_id=snapshot["snapshot_id"])
        assert (scenario["snapshot_id"], scenario["data_source"]) == (
            snapshot["snapshot_id"],
            "synthetic",
        )

    def test_create_on_unknown_snapshot(self, client, snapshot):
        response = client.post(
            SCENARIOS, json={"name": "A", "situation": "new_member", "snapshot_id": 999999}
        )
        assert response.status_code == 422
        assert locs(response) == [["body", "snapshot_id"]]

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "", "situation": "new_member"},
            {"name": "   ", "situation": "new_member"},
            {"name": "x" * 201, "situation": "new_member"},
            {"name": "A", "situation": "pandemi"},
            {"name": "A", "situation": "new_member", "status": "saved"},
            {"name": "A", "situation": "new_member", "parameters": []},
        ],
    )
    def test_create_request_shape_errors(self, client, snapshot, body):
        assert client.post(SCENARIOS, json=body).status_code == 422

    def test_get_returns_parameters_and_validation(self, client, baseline):
        params = overrun_params(baseline)
        scenario = create(client, parameters=params)
        assert scenario["parameter_errors"] == []

        response = client.get(f"{SCENARIOS}/{scenario['scenario_id']}")

        assert response.status_code == 200
        assert response.json() == scenario

    def test_unknown_scenario(self, client, snapshot):
        for method, suffix in [
            ("get", ""),
            ("patch", ""),
            ("post", "/save"),
            ("post", "/duplicate"),
            ("delete", ""),
        ]:
            kwargs = {"json": {}} if method in ("patch", "post") else {}
            response = getattr(client, method)(f"{SCENARIOS}/999999{suffix}", **kwargs)
            assert response.status_code == 404, (method, suffix)
            assert response.json()["detail"] == "Skenario 999999 tidak ditemukan"

    def test_update_keeps_incomplete_draft_and_reports_errors(self, client, baseline):
        scenario = create(client)
        params = {**overrun_params(baseline), "revised_days": 1}
        del params["remaining_days"]

        url = f"{SCENARIOS}/{scenario['scenario_id']}"

        response = client.patch(url, json={"parameters": params})

        assert response.status_code == 200
        updated = response.json()
        assert updated["parameters"] == params
        assert updated["status"] == "draft"
        assert [e["loc"] for e in updated["parameter_errors"]] == [
            ["body", "parameters", "remaining_days"]
        ]
        assert updated["parameter_errors"][0]["msg"] == "Wajib diisi"

    def test_update_name_only_keeps_parameters(self, client, baseline):
        params = overrun_params(baseline)
        scenario = create(client, parameters=params)
        response = client.patch(f"{SCENARIOS}/{scenario['scenario_id']}", json={"name": "Baru"})
        assert response.status_code == 200
        assert (response.json()["name"], response.json()["parameters"]) == ("Baru", params)

    def test_update_cannot_change_situation_or_snapshot(self, client, snapshot):
        scenario = create(client)
        for body in ({"situation": "new_member"}, {"snapshot_id": 1}, {"status": "saved"}):
            response = client.patch(f"{SCENARIOS}/{scenario['scenario_id']}", json=body)
            assert response.status_code == 422, body

    def test_save_rejects_invalid_parameters_with_field_errors(self, client, baseline):
        params = {**overrun_params(baseline), "task_id": "TSK-9999"}
        scenario = create(client, parameters=params)

        response = client.post(f"{SCENARIOS}/{scenario['scenario_id']}/save")

        assert response.status_code == 422
        assert response.json()["detail"] == [
            {
                "loc": ["body", "parameters", "task_id"],
                "msg": "Task TSK-9999 tidak ada di baseline",
                "type": "task_not_found",
            }
        ]
        unchanged = client.get(f"{SCENARIOS}/{scenario['scenario_id']}").json()
        assert (unchanged["status"], unchanged["saved_at"]) == ("draft", None)

    def test_save_stores_canonical_parameters_and_lists_as_saved(self, client, baseline):
        task = next(t for t in baseline["tasks"] if t["status"] not in ("done", "dropped"))
        member = next(
            a["resource_id"] for a in baseline["assignments"] if a["task_id"] == task["task_id"]
        )
        scenario = create(
            client,
            name="Bug kritis",
            situation="critical_bug",
            parameters={"severity": "critical", "fixing_days": 2, "resource_id": member},
        )

        response = client.post(f"{SCENARIOS}/{scenario['scenario_id']}/save")

        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["status"] == "saved"
        assert saved["saved_at"] is not None
        assert saved["parameter_errors"] == []
        # Nilai bawaan ikut disimpan supaya mesin simulasi tidak perlu menebak.
        assert saved["parameters"] == {
            "severity": "critical",
            "fixing_days": 2.0,
            "resource_id": member,
            "affected_task_ids": [],
        }
        listed = client.get(SCENARIOS, params={"status": "saved"}).json()
        assert [s["scenario_id"] for s in listed] == [scenario["scenario_id"]]
        assert "parameters" not in listed[0]
        assert client.get(SCENARIOS, params={"status": "draft"}).json() == []

    def test_saved_scenario_can_be_reopened_edited_and_resaved(self, client, baseline):
        params = overrun_params(baseline)
        scenario = create(client, parameters=params)
        url = f"{SCENARIOS}/{scenario['scenario_id']}"
        first = client.post(f"{url}/save").json()

        # Nama saja: tetap saved.
        renamed = client.patch(url, json={"name": "Overrun versi 2"}).json()
        assert renamed["status"] == "saved"
        # Parameter sama persis: tetap saved.
        same = client.patch(url, json={"parameters": first["parameters"]}).json()
        assert same["status"] == "saved"
        # Parameter berubah: kembali draft sampai disimpan lagi.
        changed = {**first["parameters"], "revised_days": params["revised_days"] + 1}
        edited = client.patch(url, json={"parameters": changed}).json()
        assert edited["status"] == "draft"
        assert edited["saved_at"] == first["saved_at"]

        resaved = client.post(f"{url}/save").json()
        assert resaved["status"] == "saved"
        assert resaved["parameters"]["revised_days"] == changed["revised_days"]
        assert resaved["saved_at"] >= first["saved_at"]

    def test_duplicate_copies_parameters_and_snapshot_with_new_name(self, client, baseline):
        params = overrun_params(baseline)
        source = create(client, parameters=params)
        client.post(f"{SCENARIOS}/{source['scenario_id']}/save")

        response = client.post(
            f"{SCENARIOS}/{source['scenario_id']}/duplicate", json={"name": "Overrun B"}
        )

        assert response.status_code == 201
        copy = response.json()
        assert copy["scenario_id"] != source["scenario_id"]
        assert copy["name"] == "Overrun B"
        assert copy["duplicated_from"] == source["scenario_id"]
        assert copy["situation"] == source["situation"]
        assert (copy["snapshot_id"], copy["data_source"]) == (
            source["snapshot_id"],
            source["data_source"],
        )
        assert copy["status"] == "saved"
        assert copy["saved_at"] is not None
        original = client.get(f"{SCENARIOS}/{source['scenario_id']}").json()
        assert copy["parameters"] == original["parameters"]

        # Mengubah salinan tidak mengubah asalnya.
        edited = {**copy["parameters"], "remaining_days": 1}
        client.patch(f"{SCENARIOS}/{copy['scenario_id']}", json={"parameters": edited})
        assert client.get(f"{SCENARIOS}/{source['scenario_id']}").json() == original

    def test_duplicate_default_name_and_draft_status(self, client, snapshot):
        source = create(client, name="Anggota baru FE", situation="new_member")
        response = client.post(f"{SCENARIOS}/{source['scenario_id']}/duplicate")
        assert response.status_code == 201
        copy = response.json()
        assert copy["name"] == "Anggota baru FE (salinan)"
        assert (copy["status"], copy["saved_at"]) == ("draft", None)

    def test_duplicate_needs_a_different_name(self, client, snapshot):
        source = create(client, name="Sama")
        response = client.post(
            f"{SCENARIOS}/{source['scenario_id']}/duplicate", json={"name": " Sama "}
        )
        assert response.status_code == 422
        assert locs(response) == [["body", "name"]]

    def test_list_filters_and_order(self, client, snapshot):
        first = create(client, name="Pertama", situation="new_member")
        second = create(client, name="Kedua", situation="critical_bug")
        client.patch(f"{SCENARIOS}/{first['scenario_id']}", json={"name": "Pertama diubah"})

        listed = client.get(SCENARIOS).json()
        assert [s["scenario_id"] for s in listed] == [first["scenario_id"], second["scenario_id"]]
        assert listed[0]["data_source"] == "synthetic"
        by_situation = client.get(SCENARIOS, params={"situation": "critical_bug"}).json()
        assert [s["scenario_id"] for s in by_situation] == [second["scenario_id"]]
        assert len(client.get(SCENARIOS, params={"limit": 1}).json()) == 1
        assert client.get(SCENARIOS, params={"situation": "pandemi"}).status_code == 422

    def test_delete(self, client, snapshot):
        source = create(client)
        copy = client.post(f"{SCENARIOS}/{source['scenario_id']}/duplicate").json()

        response = client.delete(f"{SCENARIOS}/{source['scenario_id']}")

        assert response.status_code == 204
        assert client.get(f"{SCENARIOS}/{source['scenario_id']}").status_code == 404
        # Salinannya tetap ada; rujukan ke skenario asal dikosongkan.
        assert client.get(f"{SCENARIOS}/{copy['scenario_id']}").json()["duplicated_from"] is None

    def test_oversized_parameters_are_rejected(self, client, snapshot):
        response = client.post(
            SCENARIOS,
            json={
                "name": "Besar",
                "situation": "critical_bug",
                "parameters": {"catatan": "x" * 200_001},
            },
        )
        assert response.status_code == 422
        assert locs(response) == [["body", "parameters"]]

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            (None, False),
            ("anggota_tim", False),
            ("admin_data", False),
            ("admin_it", False),
            ("kaur_asman", True),
            ("system_analyst", True),
        ],
    )
    def test_permissions(self, client, db, snapshot, role, allowed):
        headers = (
            {"Authorization": f"Bearer {make_token(db, f'uji_{role}', [role])}"}
            if role
            else {"Authorization": ""}
        )
        body = {"name": "Uji izin", "situation": "new_member"}
        created = client.post(SCENARIOS, json=body, headers=headers)
        listed = client.get(SCENARIOS, headers=headers)
        if allowed:
            assert (created.status_code, listed.status_code) == (201, 200)
            assert created.json()["created_by"] == f"uji_{role}"
        else:
            expected = 403 if role else 401
            assert (created.status_code, listed.status_code) == (expected, expected)
