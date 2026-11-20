"""SCRUM-15: impor baseline snapshot, endpoint snapshot terkini, dan loader untuk skenario."""

from datetime import date

import pytest
from sqlalchemy import text

from app.main import app
from app.modules.auth.permissions import Perm
from app.modules.data.enums import TaskStatus
from app.modules.simulasi import snapshot_cli
from app.modules.simulasi import snapshot_service as service
from app.modules.simulasi.snapshot_models import SNAPSHOT_TABLES
from synthetic.final_sources import final_source_rows, write_final_sources
from synthetic.world import build_world
from tests.conftest import REQUIRES_DB, make_token
from tests.test_data_permissions import required_permissions, walk

AS_OF = date(2026, 11, 6)
PREFIX = "/api/v1/simulasi"
IMPORT = f"{PREFIX}/baseline-snapshots/import"

# Peta izin endpoint simulasi; endpoint baru wajib ditambahkan di sini.
EXPECTED = {
    ("POST", "/baseline-snapshots/import"): Perm.IMPORT_RUN,
    ("GET", "/baseline-snapshots"): Perm.BASELINE_READ,
    ("GET", "/baseline-snapshots/current"): Perm.BASELINE_READ,
    ("GET", "/baseline-snapshots/{snapshot_id}"): Perm.BASELINE_READ,
}


def test_every_snapshot_endpoint_uses_the_agreed_permission():
    actual = {
        (method, path.removeprefix(PREFIX)): set(inherited) | required_permissions(route.dependant)
        for path, route, inherited in walk(app.routes)
        if path.startswith(PREFIX + "/baseline-snapshots")
        for method in route.methods
    }
    assert actual == {key: {perm} for key, perm in EXPECTED.items()}


# --- terhadap PostgreSQL ------------------------------------------------------------------


@pytest.fixture(scope="module")
def synthetic_folder(tmp_path_factory):
    folder = tmp_path_factory.mktemp("final_sources")
    write_final_sources(build_world(42, AS_OF), folder)
    return folder


@pytest.fixture(scope="module")
def csv_files(synthetic_folder) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in synthetic_folder.glob("*.csv")}


@pytest.fixture
def no_snapshots(session_factory):
    with session_factory() as session:
        session.execute(text("TRUNCATE twinner.baseline_snapshot CASCADE"))
        session.commit()


def body(files, **extra):
    return {"data_source": "synthetic", "files": files, "dry_run": False, **extra}


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestApi:
    @pytest.fixture(autouse=True)
    def _clean(self, no_snapshots):
        pass

    def test_no_snapshot_yet(self, client):
        response = client.get(f"{PREFIX}/baseline-snapshots/current")
        assert response.status_code == 404
        assert response.json()["detail"] == (
            "Belum ada baseline snapshot. Impor data baseline terlebih dahulu"
        )
        assert client.get(f"{PREFIX}/baseline-snapshots").json() == []

    def test_dry_run_checks_without_saving(self, client, csv_files):
        response = client.post(IMPORT, json=body(csv_files, dry_run=True))
        assert response.status_code == 200
        result = response.json()
        assert (result["applied"], result["error_count"], result["snapshot"]) == (False, 0, None)
        assert result["row_counts"]["tasks"] == 136
        assert client.get(f"{PREFIX}/baseline-snapshots").json() == []

    def test_dry_run_is_the_default(self, client, csv_files):
        result = client.post(IMPORT, json={"data_source": "puti", "files": csv_files}).json()
        assert (result["dry_run"], result["applied"]) == (True, False)

    def test_import_creates_snapshot_with_timestamp_source_and_counts(self, client, csv_files):
        response = client.post(IMPORT, json=body(csv_files, label="seed 42 per 2026-11-06"))
        assert response.status_code == 200
        result = response.json()
        assert result["applied"] is True
        snapshot = result["snapshot"]
        assert snapshot["data_source"] == "synthetic"
        assert snapshot["label"] == "seed 42 per 2026-11-06"
        assert snapshot["imported_by"] == "penguji"
        assert snapshot["created_at"]
        assert snapshot["is_current"] is True
        expected = {t: len(r) for t, r in final_source_rows(build_world(42, AS_OF)).items()}
        assert snapshot["row_counts"] == expected

    def test_current_snapshot_returns_every_table(self, client, csv_files):
        snapshot_id = client.post(IMPORT, json=body(csv_files)).json()["snapshot"]["snapshot_id"]

        response = client.get(f"{PREFIX}/baseline-snapshots/current")

        assert response.status_code == 200
        current = response.json()
        assert current["snapshot_id"] == snapshot_id
        for table in SNAPSHOT_TABLES:
            assert len(current[table]) == current["row_counts"][table], table
        # Isi snapshot sama persis dengan data sumber (urut primary key).
        source = final_source_rows(build_world(42, AS_OF))
        expected_tasks = sorted(source["tasks"], key=lambda t: t["task_id"])
        assert [t["task_id"] for t in current["tasks"]] == [t["task_id"] for t in expected_tasks]
        first = expected_tasks[0]
        assert current["tasks"][0] == {
            **first,
            "planned_start": first["planned_start"].isoformat(),
            "planned_finish": first["planned_finish"].isoformat(),
            "actual_start": first["actual_start"] and first["actual_start"].isoformat(),
            "actual_finish": first["actual_finish"] and first["actual_finish"].isoformat(),
        }

    def test_new_import_becomes_current_and_old_stays_unchanged(self, client, csv_files):
        first = client.post(IMPORT, json=body(csv_files)).json()["snapshot"]
        changed = dict(csv_files)
        changed["holidays.csv"] += "2026-12-31,Libur tambahan\n"
        second = client.post(IMPORT, json=body(changed, data_source="puti")).json()["snapshot"]

        assert second["snapshot_id"] > first["snapshot_id"]
        assert second["checksum"] != first["checksum"]
        listed = client.get(f"{PREFIX}/baseline-snapshots").json()
        assert [(s["snapshot_id"], s["is_current"]) for s in listed] == [
            (second["snapshot_id"], True),
            (first["snapshot_id"], False),
        ]
        current = client.get(f"{PREFIX}/baseline-snapshots/current").json()
        assert (current["snapshot_id"], current["data_source"]) == (second["snapshot_id"], "puti")
        old = client.get(f"{PREFIX}/baseline-snapshots/{first['snapshot_id']}").json()
        assert old["is_current"] is False
        assert len(old["holidays"]) == len(current["holidays"]) - 1

    def test_same_content_gives_same_checksum(self, client, csv_files):
        first = client.post(IMPORT, json=body(csv_files)).json()["snapshot"]
        second = client.post(IMPORT, json=body(csv_files)).json()["snapshot"]
        assert first["checksum"] == second["checksum"]
        assert first["snapshot_id"] != second["snapshot_id"]

    def test_malformed_files_fail_cleanly_and_store_nothing(self, client, csv_files):
        broken = dict(csv_files)
        broken["tasks.csv"] = broken["tasks.csv"].replace(",2026-", ",05/10/", 1)
        del broken["holidays.csv"]

        response = client.post(IMPORT, json=body(broken))

        assert response.status_code == 200
        result = response.json()
        assert (result["applied"], result["snapshot"]) == (False, None)
        assert result["error_count"] == 2
        files = {(e["file"], e["field"]) for e in result["errors"]}
        assert files == {("holidays.csv", None), ("tasks.csv", "planned_start")}
        assert all(e["message"] for e in result["errors"])
        assert client.get(f"{PREFIX}/baseline-snapshots").json() == []

    def test_error_list_is_capped_but_counted(self, client, csv_files):
        broken = dict(csv_files)
        lines = broken["tasks.csv"].splitlines()
        broken["tasks.csv"] = "\n".join([lines[0]] + [line + ",x" for line in lines[1:]] * 5)
        result = client.post(IMPORT, json=body(broken)).json()
        assert result["error_count"] > 500
        assert len(result["errors"]) == 500

    def test_unknown_snapshot(self, client):
        response = client.get(f"{PREFIX}/baseline-snapshots/999999")
        assert response.status_code == 404
        assert response.json()["detail"] == "Baseline snapshot 999999 tidak ditemukan"

    def test_request_shape_errors_use_fastapi_422(self, client, csv_files):
        response = client.post(IMPORT, json={"data_source": "excel", "files": csv_files})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("role", "method", "path", "allowed"),
        [
            (None, "get", "/baseline-snapshots/current", False),
            ("anggota_tim", "get", "/baseline-snapshots/current", False),
            ("admin_it", "get", "/baseline-snapshots", False),
            ("kaur_asman", "get", "/baseline-snapshots/current", True),
            ("system_analyst", "get", "/baseline-snapshots", True),
            ("kaur_asman", "post", "/baseline-snapshots/import", False),
            ("system_analyst", "post", "/baseline-snapshots/import", False),
            ("admin_data", "post", "/baseline-snapshots/import", True),
        ],
    )
    def test_permissions(self, client, db, csv_files, role, method, path, allowed):
        headers = (
            {"Authorization": f"Bearer {make_token(db, f'uji_{role}', [role])}"}
            if role
            else {"Authorization": ""}
        )
        kwargs = {"json": body(csv_files, dry_run=True)} if method == "post" else {}
        response = getattr(client, method)(PREFIX + path, headers=headers, **kwargs)
        if allowed:
            assert response.status_code in (200, 404)
        else:
            assert response.status_code == (403 if role else 401)


@REQUIRES_DB[0]
@REQUIRES_DB[1]
class TestLoaderAndCli:
    @pytest.fixture(autouse=True)
    def _clean(self, no_snapshots):
        pass

    @pytest.fixture
    def cli_session(self, session_factory, monkeypatch):
        """CLI memakai SessionLocal aplikasi; arahkan ke basis data tes."""
        monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    def test_loader_returns_typed_independent_copies(self, db, cli_session, synthetic_folder):
        assert service.current_snapshot_id(db) is None
        with pytest.raises(service.SnapshotNotFoundError):
            service.load_snapshot(db)

        assert snapshot_cli.main([str(synthetic_folder), "--source", "synthetic"]) == 0

        snapshot_id = service.current_snapshot_id(db)
        baseline = service.load_snapshot(db)
        assert baseline.snapshot_id == snapshot_id
        assert baseline.imported_by == "cli"
        task = baseline.tasks[0]
        assert isinstance(task.planned_start, date)
        assert isinstance(task.status, TaskStatus)
        assert baseline.roles[0].capacity_reference > 0

        # Aturan skenario mengubah salinannya; baseline di basis data tetap.
        task.estimated_days = 999
        baseline.availability.clear()
        again = service.load_snapshot(db, snapshot_id)
        assert again.tasks[0].estimated_days != 999
        assert len(again.availability) == again.row_counts["availability"] > 0

    def test_cli_dry_run_and_errors(self, db, cli_session, synthetic_folder, tmp_path, capsys):
        assert snapshot_cli.main([str(synthetic_folder), "--source", "puti", "--dry-run"]) == 0
        assert "dry run" in capsys.readouterr().out
        assert service.current_snapshot_id(db) is None

        for path in synthetic_folder.glob("*.csv"):
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / "roles.csv").unlink()
        assert snapshot_cli.main([str(tmp_path), "--source", "synthetic"]) == 1
        assert "roles.csv: Berkas tidak ada" in capsys.readouterr().err
        assert service.current_snapshot_id(db) is None

        assert snapshot_cli.main([str(tmp_path / "tidak-ada"), "--source", "synthetic"]) == 2
