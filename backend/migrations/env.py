from alembic import context

import app.modules.auth.models  # noqa: F401  (mendaftarkan app_user ke metadata)
import app.modules.auth.rbac_models  # noqa: F401  (mendaftarkan tabel RBAC ke metadata)
import app.modules.data.event_log_models  # noqa: F401  (mendaftarkan event_log ke metadata)
import app.modules.data.import_job_models  # noqa: F401  (mendaftarkan import_job ke metadata)
import app.modules.data.models  # noqa: F401  (mendaftarkan D-01 s.d. D-11 ke metadata)
import app.modules.data.resource_alias_models  # noqa: F401  (mendaftarkan resource_alias)
import app.modules.data.staging_models  # noqa: F401  (mendaftarkan staging Trello ke metadata)
import app.modules.data.support_models  # noqa: F401  (mendaftarkan tabel pendukung 7.3)
import app.modules.simulasi.scenario_models  # noqa: F401  (mendaftarkan twinner.scenario)
import app.modules.simulasi.snapshot_models  # noqa: F401  (mendaftarkan tabel schema twinner)
from app.db.base import Base
from app.db.session import engine

# Import model milik modul di sini sebelum autogenerate saat model sudah tersedia.
target_metadata = Base.metadata

# Schema yang isinya dikelola migrasi ini: public (None) milik J&C dan twinner milik tWinner
# (DATA_CONTRACT bagian 4). Tanpa include_schemas, autogenerate hanya membaca schema public dan
# akan selalu menganggap tabel twinner belum ada.
MANAGED_SCHEMAS = {None, "twinner"}


def include_name(name, type_, parent_names) -> bool:
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tes migrasi mengirim koneksi sendiri lewat config.attributes agar tidak
    # menyentuh database development dari DATABASE_URL.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return

    with engine.connect() as connection:
        _run_with_connection(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
