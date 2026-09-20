from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import ContentItem, Course, ModuleItem, RemoteFile, StoredFile


@dataclass(frozen=True, slots=True)
class DiscordJob:
    job_id: str
    command: str
    arguments: dict[str, object]
    requester_id: int
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: str | None = None
    channel_id: int | None = None
    message_id: int | None = None
    error: str | None = None
    gateway: str = "discord"


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
            CREATE TABLE IF NOT EXISTS catalog_module_items (
                course_id TEXT NOT NULL,
                module_id TEXT NOT NULL,
                module_name TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                title TEXT NOT NULL,
                position INTEGER,
                content_id TEXT,
                html_url TEXT,
                external_url TEXT,
                published INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                available INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (course_id, module_id, item_id),
                FOREIGN KEY (course_id) REFERENCES courses(remote_id)
            );
            CREATE TABLE IF NOT EXISTS pull_artifacts (
                artifact_type TEXT NOT NULL,
                course_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                local_path TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                sha256 TEXT,
                size_bytes INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (artifact_type, course_id, source_id),
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
            CREATE TABLE IF NOT EXISTS discord_jobs (
                job_id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                requester_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                progress TEXT,
                channel_id INTEGER,
                message_id INTEGER,
                error TEXT,
                gateway TEXT NOT NULL DEFAULT 'discord'
            );
            CREATE INDEX IF NOT EXISTS discord_jobs_status_idx
                ON discord_jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS telegram_updates (
                update_id INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_poll_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                last_update_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS telegram_digest (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                last_sent_date TEXT,
                last_error_date TEXT
            );
            """
        )
        # Columns are added independently so databases created by pre-catalog
        # versions migrate without rebuilding download history tables.
        self._ensure_column("courses", "course_code", "TEXT")
        self._ensure_column("courses", "semester_id", "TEXT")
        self._ensure_column("courses", "starts_at", "TEXT")
        self._ensure_column("courses", "ends_at", "TEXT")
        self._ensure_column("courses", "syllabus_html", "TEXT")
        self._ensure_column("catalog_files", "content_type", "TEXT")
        self._ensure_column("catalog_content_items", "updated_at", "TEXT")
        self._ensure_column("catalog_content_items", "body_html", "TEXT")
        self._ensure_column("discord_jobs", "gateway", "TEXT NOT NULL DEFAULT 'discord'")
        self.db.commit()

    @staticmethod
    def _discord_job(row: sqlite3.Row) -> DiscordJob:
        arguments = json.loads(str(row["arguments_json"]))
        return DiscordJob(
            job_id=str(row["job_id"]),
            command=str(row["command"]),
            arguments=arguments if isinstance(arguments, dict) else {},
            requester_id=int(row["requester_id"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            started_at=str(row["started_at"]) if row["started_at"] else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            progress=str(row["progress"]) if row["progress"] else None,
            channel_id=int(row["channel_id"]) if row["channel_id"] else None,
            message_id=int(row["message_id"]) if row["message_id"] else None,
            error=str(row["error"]) if row["error"] else None,
            gateway=str(row["gateway"]),
        )

    def create_discord_job(
        self,
        job_id: str,
        command: str,
        arguments: dict[str, object],
        requester_id: int,
        *,
        channel_id: int | None = None,
    ) -> DiscordJob:
        return self.create_remote_job(
            "discord", job_id, command, arguments, requester_id, channel_id=channel_id
        )

    def create_remote_job(
        self,
        gateway: str,
        job_id: str,
        command: str,
        arguments: dict[str, object],
        requester_id: int,
        *,
        channel_id: int | None = None,
    ) -> DiscordJob:
        commands = {"discord": {"refresh", "sync", "pull", "login"}, "telegram": {"sync"}}
        if gateway not in commands or command not in commands[gateway]:
            raise ValueError(f"unsupported {gateway} job command: {command}")
        forbidden = {"token", "password", "passwd", "cookie", "verification_code", "code"}
        if any(str(key).casefold() in forbidden for key in arguments):
            raise ValueError("remote job arguments must not contain secrets")
        queued = self.db.execute(
            "SELECT COUNT(*) FROM discord_jobs WHERE status='queued' AND gateway=?", (gateway,)
        ).fetchone()[0]
        if int(queued) >= 10:
            raise ValueError(f"{gateway} job queue is full")
        now = utc_now()
        self.db.execute(
            """INSERT INTO discord_jobs(
                 job_id, command, arguments_json, requester_id, status, created_at, channel_id, gateway
               ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)""",
            (
                job_id,
                command,
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                requester_id,
                now,
                channel_id,
                gateway,
            ),
        )
        self.db.commit()
        row = self.db.execute("SELECT * FROM discord_jobs WHERE job_id=?", (job_id,)).fetchone()
        assert row is not None
        return self._discord_job(row)

    def list_discord_jobs(self, *, limit: int = 20) -> list[DiscordJob]:
        return self.list_remote_jobs("discord", limit=limit)

    def list_remote_jobs(self, gateway: str, *, limit: int = 20) -> list[DiscordJob]:
        rows = self.db.execute(
            "SELECT * FROM discord_jobs WHERE gateway=? ORDER BY created_at DESC LIMIT ?",
            (gateway, limit),
        ).fetchall()
        return [self._discord_job(row) for row in rows]

    def get_discord_job(self, job_id: str) -> DiscordJob | None:
        row = self.db.execute("SELECT * FROM discord_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._discord_job(row) if row is not None else None

    def next_discord_job(self) -> DiscordJob | None:
        return self.next_remote_job("discord")

    def next_remote_job(self, gateway: str) -> DiscordJob | None:
        # A write transaction makes the cross-process check and claim atomic.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            active = self.db.execute(
                "SELECT 1 FROM discord_jobs WHERE status IN ('running', 'cancel_requested') LIMIT 1"
            ).fetchone()
            if active is not None:
                self.db.commit()
                return None
            row = self.db.execute(
                """SELECT * FROM discord_jobs WHERE status='queued' AND gateway=?
                   ORDER BY created_at LIMIT 1""",
                (gateway,),
            ).fetchone()
            if row is None:
                self.db.commit()
                return None
            self.db.execute(
                "UPDATE discord_jobs SET status='running', started_at=? WHERE job_id=?",
                (utc_now(), row["job_id"]),
            )
            self.db.commit()
            return self.get_discord_job(str(row["job_id"]))
        except Exception:
            self.db.rollback()
            raise

    def update_discord_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: str | None = None,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        allowed = {
            "queued",
            "running",
            "completed",
            "failed",
            "cancel_requested",
            "cancelled",
            "interrupted",
        }
        if status is not None and status not in allowed:
            raise ValueError(f"invalid Discord job status: {status}")
        updates: list[str] = []
        values: list[object] = []
        if status is not None:
            updates.append("status=?")
            values.append(status)
            if status in {"completed", "failed", "cancelled", "interrupted"}:
                updates.append("finished_at=?")
                values.append(utc_now())
        if progress is not None:
            updates.append("progress=?")
            values.append(progress[:1000])
        if message_id is not None:
            updates.append("message_id=?")
            values.append(message_id)
        if error is not None:
            updates.append("error=?")
            values.append(error[:2000])
        if not updates:
            return
        values.append(job_id)
        self.db.execute(f"UPDATE discord_jobs SET {', '.join(updates)} WHERE job_id=?", values)
        self.db.commit()

    def request_discord_job_cancel(self, job_id: str) -> bool:
        return self.request_remote_job_cancel(job_id, "discord")

    def request_remote_job_cancel(self, job_id: str, gateway: str) -> bool:
        cursor = self.db.execute(
            """UPDATE discord_jobs SET status=CASE
                 WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                 finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END
               WHERE job_id=? AND gateway=? AND status IN ('queued', 'running')""",
            (utc_now(), job_id, gateway),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def interrupt_discord_jobs(self) -> int:
        return self.interrupt_remote_jobs("discord")

    def interrupt_remote_jobs(self, gateway: str) -> int:
        cursor = self.db.execute(
            """UPDATE discord_jobs SET status='interrupted', finished_at=?,
                 error=?
               WHERE gateway=? AND status IN ('queued', 'running', 'cancel_requested')""",
            (
                utc_now(),
                f"{gateway.capitalize()} daemon restarted before this job completed",
                gateway,
            ),
        )
        self.db.commit()
        return cursor.rowcount

    def telegram_update_seen(self, update_id: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM telegram_updates WHERE update_id=?", (update_id,)
            ).fetchone()
            is not None
        )

    def reset_telegram_state(self) -> None:
        self.db.execute("DELETE FROM telegram_updates")
        self.db.execute("DELETE FROM telegram_poll_state")
        self.db.execute("DELETE FROM telegram_digest")
        self.db.commit()

    def record_telegram_update(self, update_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO telegram_updates(update_id, processed_at) VALUES (?, ?)",
            (update_id, utc_now()),
        )
        self.db.execute(
            "DELETE FROM telegram_updates WHERE processed_at < ?",
            ((datetime.now(UTC) - timedelta(days=30)).isoformat(),),
        )
        self.db.execute("INSERT OR IGNORE INTO telegram_poll_state(singleton) VALUES (1)")
        self.db.execute(
            "UPDATE telegram_poll_state SET last_update_id=? WHERE singleton=1", (update_id,)
        )
        self.db.commit()

    def telegram_poll_offset(self) -> int | None:
        row = self.db.execute(
            "SELECT last_update_id FROM telegram_poll_state WHERE singleton=1"
        ).fetchone()
        value = row[0] if row is not None else None
        if value is None:
            return None
        processed = self.db.execute(
            "SELECT processed_at FROM telegram_updates WHERE update_id=?", (value,)
        ).fetchone()
        if processed is None:
            return None
        if datetime.fromisoformat(str(processed[0])) < datetime.now(UTC) - timedelta(days=3):
            # Telegram may choose a fresh, lower update ID after a quiet week.
            return None
        return int(value) + 1

    def telegram_digest_dates(self) -> tuple[str | None, str | None]:
        row = self.db.execute(
            "SELECT last_sent_date, last_error_date FROM telegram_digest WHERE singleton=1"
        ).fetchone()
        return (
            (str(row[0]) if row[0] else None, str(row[1]) if row[1] else None)
            if row
            else (None, None)
        )

    def mark_telegram_digest(self, day: str, *, error: bool = False) -> None:
        column = "last_error_date" if error else "last_sent_date"
        self.db.execute("INSERT OR IGNORE INTO telegram_digest(singleton) VALUES (1)")
        self.db.execute(f"UPDATE telegram_digest SET {column}=? WHERE singleton=1", (day,))
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
        assert cursor.lastrowid is not None
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
                 syllabus_html, active, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(remote_id) DO UPDATE SET
                 name=excluded.name, url=excluded.url, course_code=excluded.course_code,
                 semester_id=excluded.semester_id, starts_at=excluded.starts_at,
                 ends_at=excluded.ends_at, syllabus_html=excluded.syllabus_html,
                 active=1, last_seen_at=excluded.last_seen_at""",
            (
                course.remote_id,
                course.name,
                course.url,
                course.course_code,
                course.semester.semester_id if course.semester else None,
                course.starts_at,
                course.ends_at,
                course.syllabus_html,
                now,
            ),
        )

    def record_catalog_refresh(self, scope: str, row_count: int, *, complete: bool = True) -> None:
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
        self.db.execute(
            "UPDATE catalog_files SET available=0 WHERE course_id=?", (course.remote_id,)
        )
        for remote in files:
            self.db.execute(
                """INSERT INTO catalog_files(
                     file_id, course_id, file_name, folder_path, remote_path,
                     download_url, size_bytes, updated_at, etag, content_type,
                     available, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(course_id, file_id) DO UPDATE SET
                     file_name=excluded.file_name, folder_path=excluded.folder_path,
                     remote_path=excluded.remote_path, download_url=excluded.download_url,
                     size_bytes=excluded.size_bytes, updated_at=excluded.updated_at,
                     etag=excluded.etag, content_type=excluded.content_type,
                     available=1, last_seen_at=excluded.last_seen_at""",
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
                    remote.content_type,
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
                     published_at, due_at, updated_at, body_html, available, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(course_id, content_type, content_id) DO UPDATE SET
                     title=excluded.title, url=excluded.url,
                     published_at=excluded.published_at, due_at=excluded.due_at,
                     updated_at=excluded.updated_at, body_html=excluded.body_html,
                     available=1, last_seen_at=excluded.last_seen_at""",
                (
                    item.remote_id,
                    item.course_id,
                    item.kind,
                    item.title,
                    item.url,
                    item.published_at,
                    item.due_at,
                    item.updated_at,
                    item.body_html,
                    now,
                ),
            )
        self.record_catalog_refresh(f"{scope}:{course.remote_id}", len(items))

    def replace_catalog_modules(self, course: Course, items: list[ModuleItem]) -> None:
        now = utc_now()
        self.db.execute(
            "UPDATE catalog_module_items SET available=0 WHERE course_id=?",
            (course.remote_id,),
        )
        for item in items:
            self.db.execute(
                """INSERT INTO catalog_module_items(
                     course_id, module_id, module_name, item_id, item_type, title,
                     position, content_id, html_url, external_url, published, locked,
                     available, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(course_id, module_id, item_id) DO UPDATE SET
                     module_name=excluded.module_name, item_type=excluded.item_type,
                     title=excluded.title, position=excluded.position,
                     content_id=excluded.content_id, html_url=excluded.html_url,
                     external_url=excluded.external_url, published=excluded.published,
                     locked=excluded.locked, available=1,
                     last_seen_at=excluded.last_seen_at""",
                (
                    item.course_id,
                    item.module_id,
                    item.module_name,
                    item.remote_id,
                    item.item_type,
                    item.title,
                    item.position,
                    item.content_id,
                    item.html_url,
                    item.external_url,
                    int(item.published),
                    int(item.locked),
                    now,
                ),
            )
        self.record_catalog_refresh(f"modules:{course.remote_id}", len(items))

    def get_artifact(
        self, artifact_type: str, course_id: str, source_id: str
    ) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT * FROM pull_artifacts
               WHERE artifact_type=? AND course_id=? AND source_id=?""",
            (artifact_type, course_id, source_id),
        ).fetchone()

    def record_artifact(
        self,
        *,
        artifact_type: str,
        course_id: str,
        source_id: str,
        local_path: Path,
        source_revision: str,
        sha256: str | None,
        size_bytes: int | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO pull_artifacts(
                 artifact_type, course_id, source_id, local_path, source_revision,
                 sha256, size_bytes, status, error, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(artifact_type, course_id, source_id) DO UPDATE SET
                 local_path=excluded.local_path,
                 source_revision=excluded.source_revision,
                 sha256=excluded.sha256,
                 size_bytes=excluded.size_bytes,
                 status=excluded.status,
                 error=excluded.error,
                 last_seen_at=excluded.last_seen_at""",
            (
                artifact_type,
                course_id,
                source_id,
                str(local_path),
                source_revision,
                sha256,
                size_bytes,
                status,
                error,
                utc_now(),
            ),
        )

    def list_artifact_paths(self) -> list[Path]:
        rows = self.db.execute(
            "SELECT local_path FROM pull_artifacts UNION SELECT local_path FROM files"
        ).fetchall()
        return [Path(str(row[0])) for row in rows if row[0]]

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

    def catalog_counts(self) -> tuple[int, int, int, int]:
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
        quiz_count = self.db.execute(
            """SELECT count(*) FROM catalog_content_items
               WHERE available=1 AND content_type='quiz'"""
        ).fetchone()[0]
        return int(file_count), int(article_count), int(assignment_count), int(quiz_count)
