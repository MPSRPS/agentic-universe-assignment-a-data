"""Shared pytest fixtures for the interaction ledger tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from solution.config import DEFAULT_SCHEMAS_DIR
from solution.db.connection import open_connection
from solution.db.schema import init_db
from solution.models.schema_loader import SchemaRegistry, load_schemas


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """Provide an initialized temporary SQLite database connection."""
    conn = open_connection(tmp_path / "test_ledger.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def schema_registry() -> SchemaRegistry:
    """Load the assignment workflow schemas and disposition catalogue."""
    return load_schemas(DEFAULT_SCHEMAS_DIR)
