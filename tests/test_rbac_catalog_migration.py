from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.core.permissions import PERMISSION_CATALOG
from app.services.rbac import ROLE_DEFINITIONS


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "a2d9c6e4f817_sync_complete_rbac_catalog.py"
    )
    spec = spec_from_file_location("rbac_catalog_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rbac_migration_contains_the_complete_permission_catalog():
    migration = _load_migration()
    runtime_codes = {item["code"] for item in PERMISSION_CATALOG}
    migration_codes = {item[0] for item in migration._PERMISSIONS}

    assert migration_codes == runtime_codes


def test_rbac_migration_matches_all_native_tenant_role_grants():
    migration = _load_migration()
    runtime_grants = {
        slug: set(definition["permissions"])
        for slug, definition in ROLE_DEFINITIONS.items()
        if not definition.get("global", False)
    }
    migration_grants = {
        slug: set(codes) for slug, codes in migration._ROLE_GRANTS.items()
    }

    assert migration_grants == runtime_grants
    assert set(migration._NATIVE_ROLES) == set(runtime_grants)
