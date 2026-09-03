from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import ContentItem, Course, RemoteFile, StoredFile


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> StateStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("state store is not open")
        return self.connection

    def _migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS semesters (
                semester_id TEXT PRIMARY KEY,
                semester_code TEXT NOT NULL,
                semester_name TEXT NOT NULL,
                academic_year INTEGER,
                starts_at TEXT,
                ends_at TEXT,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS courses (
                remote_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                course_code TEXT,
                semester_id TEXT,
                starts_at TEXT,
                ends_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                remote_id TEXT NOT NULL,
                course_id TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                local_path TEXT NOT NULL,
                size INTEGER,
                updated_at TEXT,
                etag TEXT,
                sha256 TEXT,
                revision TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (course_id, remote_id),
                FOREIGN KEY (course_id) REFERENCES courses(remote_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS files_local_path_idx ON files(local_path);
            CREATE TABLE IF NOT EXISTS catalog_files (
                file_id TEXT NOT NULL,
                course_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                download_url TEXT NOT NULL,
                size_bytes INTEGER,
                updated_at TEXT,
                etag TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (course_id, file_id),
                FOREIGN KEY (course_id) REFERENCES courses(remote_id)
            );
            CREATE TABLE IF NOT EXISTS catalog_content_items (
                content_id TEXT NOT NULL,
                course_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                due_at TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (course_id, content_type, content_id),
                FOREIGN KEY (course_id) REFERENCES courses(remote_id)
            );
            CREATE TABLE IF NOT EXISTS catalog_refreshes (
                scope TEXT PRIMARY KEY,
                refreshed_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                complete INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                downloaded INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                unchanged INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Columns are added independently so databases created by pre-catalog
        # versions migrate without rebuilding download history tables.
        self._ensure_column("courses", "course_code", "TEXT")
        self._ensure_column("courses", "semester_id", "TEXT")
        self._ensure_column("courses", "starts_at", "TEXT")
        self._ensure_column("courses", "ends_at", "TEXT")
        self.db.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        existing = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def begin_run(self) -> int:
        cursor = self.db.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (utc_now(),)
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        downloaded: int,
        updated: int,
        unchanged: int,
        failed: int,
    ) -> None:
        self.db.execute(
            """UPDATE runs SET finished_at=?, status=?, downloaded=?, updated=?, unchanged=?, failed=?
               WHERE id=?""",
            (utc_now(), status, downloaded, updated, unchanged, failed, run_id),
        )
        self.db.commit()

    def deactivate_courses(self) -> None:
        self.db.execute("UPDATE courses SET active=0")

    def upsert_course(self, course: Course) -> None:
        now = utc_now()
        if course.semester is not None:
            semester = course.semester
            self.db.execute(
                """INSERT INTO semesters(
                     semester_id, semester_code, semester_name, academic_year,
                     starts_at, ends_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(semester_id) DO UPDATE SET
                     semester_code=excluded.semester_code,
                     semester_name=excluded.semester_name,
                     academic_year=excluded.academic_year,
                     starts_at=excluded.starts_at,
                     ends_at=excluded.ends_at,
                     last_seen_at=excluded.last_seen_at""",
                (
                    semester.semester_id,
                    semester.semester_code,
                    semester.semester_name,
                    semester.academic_year,
                    semester.starts_at,
                    semester.ends_at,
                    now,
                ),
            )
        self.db.execute(
            """INSERT INTO courses(
                 remote_id, name, url, course_code, semester_id, starts_at, ends_at,
                 active, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(remote_id) DO UPDATE SET
                 name=excluded.name, url=excluded.url, course_code=excluded.course_code,
                 semester_id=excluded.semester_id, starts_at=excluded.starts_at,
                 ends_at=excluded.ends_at, active=1, last_seen_at=excluded.last_seen_at""",
            (
                course.remote_id,
                course.name,
                course.url,
                course.course_code,
                course.semester.semester_id if course.semester else None,
                course.starts_at,
                course.ends_at,
                now,
            ),
        )

    def record_catalog_refresh(
        self, scope: str, row_count: int, *, complete: bool = True
    ) -> None:
        self.db.execute(
            """INSERT INTO catalog_refreshes(scope, refreshed_at, row_count, complete)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(scope) DO UPDATE SET
                 refreshed_at=excluded.refreshed_at,
                 row_count=excluded.row_count,
                 complete=excluded.complete""",
            (scope, utc_now(), row_count, int(complete)),
        )

    def replace_catalog_files(self, course: Course, files: list[RemoteFile]) -> None:
        now = utc_now()
        self.db.execute("UPDATE catalog_files SET available=0 WHERE course_id=?", (course.remote_id,))
        for remote in files:
            self.db.execute(
                """INSERT INTO catalog_files(
                     file_id, course_id, file_name, folder_path, remote_path,
                     download_url, size_bytes, updated_at, etag, available, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(course_id, file_id) DO UPDATE SET
                     file_name=excluded.file_name, folder_path=excluded.folder_path,
                     remote_path=excluded.remote_path, download_url=excluded.download_url,
                     size_bytes=excluded.size_bytes, updated_at=excluded.updated_at,
                     etag=excluded.etag, available=1, last_seen_at=excluded.last_seen_at""",
                (
                    remote.remote_id,
                    remote.course_id,
                    remote.name,
                    "/".join(remote.folder_path),
                    remote.display_path,
                    remote.download_url,
                    remote.size,
                    remote.updated_at,
                    remote.etag,
                    now,
                ),
            )
        self.record_catalog_refresh(f"files:{course.remote_id}", len(files))

    def replace_catalog_content(
        self,
        course: Course,
        content_types: tuple[str, ...],
        items: list[ContentItem],
        *,
        scope: str,
    ) -> None:
        placeholders = ", ".join("?" for _ in content_types)
        self.db.execute(
            f"""UPDATE catalog_content_items SET available=0
                WHERE course_id=? AND content_type IN ({placeholders})""",
            (course.remote_id, *content_types),
        )
        now = utc_now()
        for item in items:
            self.db.execute(
                """INSERT INTO catalog_content_items(
                     content_id, course_id, content_type, title, url,
                     published_at, due_at, available, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(course_id, content_type, content_id) DO UPDATE SET
                     title=excluded.title, url=excluded.url,
                     published_at=excluded.published_at, due_at=excluded.due_at,
                     available=1, last_seen_at=excluded.last_seen_at""",
                (
                    item.remote_id,
                    item.course_id,
                    item.kind,
                    item.title,
                    item.url,
                    item.published_at,
                    item.due_at,
                    now,
                ),
            )
        self.record_catalog_refresh(f"{scope}:{course.remote_id}", len(items))

    def get_file(self, course_id: str, remote_id: str) -> StoredFile | None:
        row = self.db.execute(
            "SELECT * FROM files WHERE course_id=? AND remote_id=?", (course_id, remote_id)
        ).fetchone()
        if row is None:
            return None
        return StoredFile(
            remote_id=row["remote_id"],
            course_id=row["course_id"],
            remote_path=row["remote_path"],
            local_path=row["local_path"],
            size=row["size"],
            updated_at=row["updated_at"],
            etag=row["etag"],
            sha256=row["sha256"],
            revision=row["revision"],
            status=row["status"],
        )

    def path_claimed(self, path: Path, *, course_id: str, remote_id: str) -> bool:
        row = self.db.execute(
            "SELECT course_id, remote_id FROM files WHERE local_path=?", (str(path),)
        ).fetchone()
        return row is not None and (row["course_id"], row["remote_id"]) != (course_id, remote_id)

    def record_file(
        self,
        remote: RemoteFile,
        local_path: Path,
        *,
        sha256: str | None,
        etag: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO files(
                 remote_id, course_id, remote_path, local_path, size, updated_at,
                 etag, sha256, revision, status, error, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(course_id, remote_id) DO UPDATE SET
                 remote_path=excluded.remote_path, local_path=excluded.local_path,
                 size=excluded.size, updated_at=excluded.updated_at, etag=excluded.etag,
                 sha256=COALESCE(excluded.sha256, files.sha256), revision=excluded.revision,
                 status=excluded.status, error=excluded.error, last_seen_at=excluded.last_seen_at""",
            (
                remote.remote_id,
                remote.course_id,
                remote.display_path,
                str(local_path),
                remote.size,
                remote.updated_at,
                etag or remote.etag,
                sha256,
                remote.revision,
                status,
                error,
                utc_now(),
            ),
        )

    def record_failure(self, remote: RemoteFile, attempted_path: Path, error: str) -> None:
        current = self.get_file(remote.course_id, remote.remote_id)
        if current is not None:
            self.db.execute(
                """UPDATE files SET remote_path=?, status='failed', error=?, last_seen_at=?
                   WHERE course_id=? AND remote_id=?""",
                (remote.display_path, error, utc_now(), remote.course_id, remote.remote_id),
            )
            return
        self.record_file(
            remote,
            attempted_path,
            sha256=None,
            etag=None,
            status="failed",
            error=error,
        )

    def last_run(self) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def counts(self) -> tuple[int, int]:
        course_count = self.db.execute("SELECT count(*) FROM courses WHERE active=1").fetchone()[0]
        file_count = self.db.execute("SELECT count(*) FROM files WHERE status='ok'").fetchone()[0]
        return int(course_count), int(file_count)

    def catalog_counts(self) -> tuple[int, int, int]:
        file_count = self.db.execute(
            "SELECT count(*) FROM catalog_files WHERE available=1"
        ).fetchone()[0]
        article_count = self.db.execute(
            """SELECT count(*) FROM catalog_content_items
               WHERE available=1 AND content_type IN ('announcement', 'page')"""
        ).fetchone()[0]
        assignment_count = self.db.execute(
            """SELECT count(*) FROM catalog_content_items
               WHERE available=1 AND content_type='assignment'"""
        ).fetchone()[0]
        return int(file_count), int(article_count), int(assignment_count)
