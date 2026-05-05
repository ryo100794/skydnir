#!/usr/bin/env python3
"""Host-only verifier for the disposable SQLite project metadata index."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "design" / "PROJECT_METADATA_INDEX.md"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA user_version = 1;

CREATE TABLE schema_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    build_id TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0),
    rebuilt_at_ms INTEGER,
    checkpointed_at_ms INTEGER
);

CREATE TABLE source_snapshots (
    snapshot_id TEXT PRIMARY KEY CHECK (length(snapshot_id) >= 32),
    kind TEXT NOT NULL CHECK (kind IN ('primary', 'replica', 'rebuild')),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0),
    manifest_json TEXT NOT NULL CHECK (length(manifest_json) > 2)
);

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY CHECK (length(project_id) >= 32),
    display_name TEXT NOT NULL CHECK (length(display_name) > 0),
    project_root TEXT NOT NULL UNIQUE CHECK (project_root LIKE 'projects/%'),
    git_repository_id TEXT,
    git_remote_url TEXT,
    git_head_ref TEXT,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    archived_at_ms INTEGER
);

CREATE TABLE compose_files (
    compose_file_id TEXT PRIMARY KEY CHECK (length(compose_file_id) >= 32),
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL CHECK (relative_path NOT LIKE '/%'),
    content_hash TEXT NOT NULL CHECK (length(content_hash) >= 16),
    discovered_at_ms INTEGER NOT NULL CHECK (discovered_at_ms > 0),
    UNIQUE (project_id, relative_path)
);

CREATE TABLE compose_services (
    compose_service_id TEXT PRIMARY KEY CHECK (length(compose_service_id) >= 32),
    compose_file_id TEXT NOT NULL REFERENCES compose_files(compose_file_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    service_name TEXT NOT NULL CHECK (length(service_name) > 0),
    service_hash TEXT NOT NULL CHECK (length(service_hash) >= 16),
    UNIQUE (compose_file_id, service_name)
);

CREATE TABLE images (
    image_id TEXT PRIMARY KEY CHECK (length(image_id) >= 32),
    config_digest TEXT NOT NULL UNIQUE CHECK (config_digest LIKE 'sha256:%'),
    repo_tags_json TEXT NOT NULL,
    config_path TEXT NOT NULL,
    rootfs_path TEXT,
    source_hash TEXT NOT NULL CHECK (length(source_hash) >= 16),
    indexed_at_ms INTEGER NOT NULL CHECK (indexed_at_ms > 0)
);

CREATE TABLE image_layers (
    layer_digest TEXT PRIMARY KEY CHECK (layer_digest LIKE 'sha256:%'),
    image_id TEXT NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
    diff_id TEXT CHECK (diff_id IS NULL OR diff_id LIKE 'sha256:%'),
    layer_path TEXT NOT NULL,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    UNIQUE (image_id, ordinal)
);

CREATE TABLE containers (
    container_id TEXT PRIMARY KEY CHECK (length(container_id) >= 12),
    project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
    compose_service_id TEXT REFERENCES compose_services(compose_service_id) ON DELETE SET NULL,
    image_id TEXT REFERENCES images(image_id) ON DELETE SET NULL,
    engine_state_path TEXT NOT NULL,
    upper_dir TEXT,
    lower_rootfs TEXT,
    storage_mode TEXT NOT NULL CHECK (storage_mode IN ('materialized', 'cow_bind')),
    state_hash TEXT NOT NULL CHECK (length(state_hash) >= 16),
    indexed_at_ms INTEGER NOT NULL CHECK (indexed_at_ms > 0)
);

CREATE TABLE volumes (
    volume_id TEXT PRIMARY KEY CHECK (length(volume_id) >= 32),
    project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL CHECK (length(display_name) > 0),
    data_path TEXT NOT NULL UNIQUE,
    metadata_hash TEXT NOT NULL CHECK (length(metadata_hash) >= 16),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0)
);

CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY CHECK (length(job_id) >= 32),
    project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
    object_kind TEXT NOT NULL,
    object_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms)
);

CREATE TABLE overlay_paths (
    overlay_path_id TEXT PRIMARY KEY CHECK (length(overlay_path_id) >= 32),
    container_id TEXT NOT NULL REFERENCES containers(container_id) ON DELETE CASCADE,
    guest_path TEXT NOT NULL CHECK (guest_path LIKE '/%' AND guest_path NOT LIKE '%/../%'),
    lower_image_id TEXT REFERENCES images(image_id) ON DELETE SET NULL,
    lower_layer_digest TEXT REFERENCES image_layers(layer_digest) ON DELETE SET NULL,
    lower_source_path TEXT,
    upper_path TEXT,
    is_whiteout INTEGER NOT NULL DEFAULT 0 CHECK (is_whiteout IN (0, 1)),
    is_opaque_dir INTEGER NOT NULL DEFAULT 0 CHECK (is_opaque_dir IN (0, 1)),
    mode INTEGER,
    uid INTEGER,
    gid INTEGER,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    mtime_ms INTEGER,
    xattr_digest TEXT,
    symlink_target TEXT,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) >= 16),
    scanned_at_ms INTEGER NOT NULL CHECK (scanned_at_ms > 0),
    CHECK (upper_path IS NOT NULL OR lower_source_path IS NOT NULL OR is_whiteout = 1),
    UNIQUE (container_id, guest_path)
);

CREATE INDEX idx_projects_git_repository_id ON projects(git_repository_id);
CREATE INDEX idx_compose_files_project_id ON compose_files(project_id);
CREATE INDEX idx_compose_services_project_id ON compose_services(project_id);
CREATE INDEX idx_containers_project_id ON containers(project_id);
CREATE INDEX idx_containers_compose_service_id ON containers(compose_service_id);
CREATE INDEX idx_volumes_project_id ON volumes(project_id);
CREATE INDEX idx_jobs_project_state ON jobs(project_id, state);
CREATE INDEX idx_overlay_paths_container_path ON overlay_paths(container_id, guest_path);
CREATE INDEX idx_overlay_paths_upper_path ON overlay_paths(upper_path);
CREATE INDEX idx_overlay_paths_lower_source_path ON overlay_paths(lower_source_path);
"""

REQUIRED_TABLES = {
    "schema_metadata",
    "source_snapshots",
    "projects",
    "compose_files",
    "compose_services",
    "images",
    "image_layers",
    "containers",
    "volumes",
    "jobs",
    "overlay_paths",
}

REQUIRED_DOC_PHRASES = (
    "SQLite must never become payload storage",
    "Project names",
    "not durable identity",
    "rebuild from filesystem truth",
    "metadata.snapshot.sqlite",
    "overlay_paths",
    "FAT32/exFAT",
    "DocumentProvider",
    "conflict state",
)


class VerificationError(Exception):
    pass


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def apply_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)


def table_names(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def insert_fixture(con: sqlite3.Connection) -> None:
    now = 1_777_888_000_000
    project_id = "018f2f7e-1111-7222-9333-aaaaaaaaaaaa"
    compose_file_id = "018f2f7e-2222-7333-9444-bbbbbbbbbbbb"
    compose_service_id = "018f2f7e-3333-7444-9555-cccccccccccc"
    image_id = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    layer_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    container_id = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"

    con.execute(
        "INSERT INTO schema_metadata VALUES (1, 1, ?, ?, NULL, NULL)",
        ("host-verifier", now),
    )
    con.execute(
        "INSERT INTO source_snapshots VALUES (?, 'rebuild', 1, ?, ?)",
        ("018f2f7e-4444-7555-9666-dddddddddddd", now, '{"projects":1,"containers":1}'),
    )
    con.execute(
        """
        INSERT INTO projects
        (project_id, display_name, project_root, created_at_ms, updated_at_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, "Renamable project", f"projects/{project_id}", now, now),
    )
    con.execute(
        "INSERT INTO compose_files VALUES (?, ?, 'compose.yaml', ?, ?)",
        (compose_file_id, project_id, "f" * 64, now),
    )
    con.execute(
        "INSERT INTO compose_services VALUES (?, ?, ?, 'app', ?)",
        (compose_service_id, compose_file_id, project_id, "e" * 64),
    )
    con.execute(
        """
        INSERT INTO images
        (image_id, config_digest, repo_tags_json, config_path, rootfs_path, source_hash, indexed_at_ms)
        VALUES (?, ?, '["local/app:latest"]', ?, ?, ?, ?)
        """,
        (image_id, image_id, "images/content/config.json", "images/roots/app", "d" * 64, now),
    )
    con.execute(
        "INSERT INTO image_layers VALUES (?, ?, ?, ?, ?, 0)",
        (layer_digest, image_id, layer_digest, "images/content/layer.tar", 12),
    )
    con.execute(
        """
        INSERT INTO containers
        (container_id, project_id, compose_service_id, image_id, engine_state_path,
         upper_dir, lower_rootfs, storage_mode, state_hash, indexed_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'cow_bind', ?, ?)
        """,
        (
            container_id,
            project_id,
            compose_service_id,
            image_id,
            f"containers/{container_id}/state.json",
            f"containers/{container_id}/upper",
            "images/roots/app",
            "c" * 64,
            now,
        ),
    )
    con.execute(
        """
        INSERT INTO volumes
        (volume_id, project_id, display_name, data_path, metadata_hash, created_at_ms)
        VALUES (?, ?, 'workspace-data', ?, ?, ?)
        """,
        (
            "018f2f7e-5555-7666-9777-eeeeeeeeeeee",
            project_id,
            "volumes/018f2f7e-5555-7666-9777-eeeeeeeeeeee/_data",
            "b" * 64,
            now,
        ),
    )
    con.execute(
        "INSERT INTO jobs VALUES (?, ?, 'project', ?, 'succeeded', ?, ?)",
        ("018f2f7e-6666-7777-9888-ffffffffffff", project_id, project_id, now, now),
    )
    con.execute(
        """
        INSERT INTO overlay_paths
        (overlay_path_id, container_id, guest_path, lower_image_id, lower_layer_digest,
         lower_source_path, upper_path, is_whiteout, is_opaque_dir, mode, uid, gid,
         size_bytes, mtime_ms, evidence_hash, scanned_at_ms)
        VALUES (?, ?, '/workspace/file.txt', ?, ?, ?, ?, 0, 0, 33188, 0, 0, 5, ?, ?, ?)
        """,
        (
            "018f2f7e-7777-7888-9999-000000000000",
            container_id,
            image_id,
            layer_digest,
            "images/roots/app/workspace/file.txt",
            f"containers/{container_id}/upper/workspace/file.txt",
            now,
            "a" * 64,
            now,
        ),
    )


def verify_schema(con: sqlite3.Connection) -> list[str]:
    found = table_names(con)
    missing = sorted(REQUIRED_TABLES - found)
    expect(not missing, f"missing tables: {', '.join(missing)}")

    expect("project_id" in columns(con, "projects"), "projects must use project_id")
    expect("display_name" in columns(con, "projects"), "projects must keep names as labels")
    expect("project_name" not in columns(con, "projects"), "projects must not use project_name")
    expect("upper_path" in columns(con, "overlay_paths"), "overlay_paths must index upper paths")
    expect("is_whiteout" in columns(con, "overlay_paths"), "overlay_paths must index whiteouts")
    expect("is_opaque_dir" in columns(con, "overlay_paths"), "overlay_paths must index opaque dirs")
    expect("manifest_json" in columns(con, "source_snapshots"), "source_snapshots must store manifests")

    insert_fixture(con)
    con.commit()

    expect(con.execute("PRAGMA foreign_key_check").fetchall() == [], "foreign key check failed")
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    expect(integrity == "ok", f"integrity_check returned {integrity!r}")

    con.execute(
        "UPDATE projects SET display_name = 'Renamed project' WHERE display_name = 'Renamable project'"
    )
    project_ids = con.execute("SELECT project_id FROM projects").fetchall()
    expect(len(project_ids) == 1, "renaming project changed identity row count")

    errors: list[str] = []
    try:
        con.execute(
            """
            INSERT INTO overlay_paths
            (overlay_path_id, container_id, guest_path, lower_source_path, evidence_hash, scanned_at_ms)
            VALUES ('018f2f7e-8888-7999-aaaa-111111111111',
                    'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'relative/path', 'x', ?, 1)
            """,
            ("a" * 64,),
        )
    except sqlite3.IntegrityError:
        errors.append("relative guest path rejected")
    expect("relative guest path rejected" in errors, "overlay_paths accepted relative guest path")

    return [
        "schema applies",
        "fixture inserts with real IDs",
        "project rename preserves project_id",
        "foreign keys and integrity pass",
        "relative guest paths are rejected",
    ]


def verify_doc(path: Path) -> list[str]:
    text = path.read_text()
    missing = [phrase for phrase in REQUIRED_DOC_PHRASES if phrase not in text]
    expect(not missing, f"{path} missing required phrases: {', '.join(missing)}")
    return ["design doc carries index, identity, rebuild, replica, and overlay contracts"]


def run_verification(db_path: Path | None = None, *, check_doc: bool = True) -> list[str]:
    messages: list[str] = []
    if db_path is None:
        with tempfile.TemporaryDirectory() as td:
            with connect(Path(td) / "metadata.sqlite") as con:
                apply_schema(con)
                messages.extend(verify_schema(con))
    else:
        with connect(db_path) as con:
            apply_schema(con)
            messages.extend(verify_schema(con))

    if check_doc:
        messages.extend(verify_doc(DOC))
    return messages


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="optional SQLite path to create and verify")
    parser.add_argument("--print-schema", action="store_true", help="print the scaffold DDL")
    parser.add_argument("--no-doc", action="store_true", help="skip design document phrase checks")
    args = parser.parse_args(argv)

    if args.print_schema:
        print(SCHEMA_SQL.strip())
        return 0

    try:
        messages = run_verification(args.db, check_doc=not args.no_doc)
    except (OSError, sqlite3.Error, VerificationError) as exc:
        print(f"verify-metadata-index: FAIL: {exc}")
        return 1

    print("verify-metadata-index: PASS")
    print_lines(f"ok: {message}" for message in messages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
