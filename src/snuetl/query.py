from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .errors import SnuetlError
from .state import StateStore


class QueryError(SnuetlError):
    pass


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class SchemaTable:
    name: str
    columns: str
    description: str


SCHEMA = (
    SchemaTable(
        "semesters",
        "semester_id, semester_code, academic_year, semester_name, starts_at, ends_at, last_seen_at",
        "SNU academic terms",
    ),
    SchemaTable(
        "courses",
        "course_id, semester_id, semester_code, academic_year, course_code, course_name, source_course_name, url, starts_at, ends_at, last_seen_at",
        "currently active courses",
    ),
    SchemaTable(
        "files",
        "file_id, course_id, semester_id, semester_code, academic_year, course_name, file_name, folder_path, remote_path, content_type, size_bytes, updated_at, download_url, local_path, download_status, sha256, last_seen_at",
        "currently available remote files plus local download state",
    ),
    SchemaTable(
        "articles",
        "article_id, content_type, course_id, semester_id, semester_code, academic_year, course_name, title, url, published_at, updated_at, body_html, last_seen_at",
        "announcements and course pages",
    ),
    SchemaTable(
        "announcements",
        "announcement_id, course_id, semester_id, semester_code, academic_year, course_name, title, url, published_at, updated_at, body_html, last_seen_at",
        "announcement-only view",
    ),
    SchemaTable(
        "pages",
        "page_id, course_id, semester_id, semester_code, academic_year, course_name, title, url, published_at, updated_at, body_html, last_seen_at",
        "course-page-only view",
    ),
    SchemaTable(
        "assignments",
        "assignment_id, course_id, semester_id, semester_code, academic_year, course_name, title, url, published_at, due_at, last_seen_at",
        "currently available assignments",
    ),
    SchemaTable(
        "quizzes",
        "quiz_id, course_id, semester_id, semester_code, academic_year, course_name, title, url, published_at, due_at, last_seen_at",
        "currently available quizzes",
    ),
    SchemaTable(
        "modules",
        "module_id, course_id, semester_code, course_name, module_name, item_count, last_seen_at",
        "course modules and their item counts",
    ),
    SchemaTable(
        "module_items",
        "item_id, module_id, module_name, course_id, semester_code, course_name, item_type, title, position, content_id, html_url, external_url, published, locked, last_seen_at",
        "ordered Canvas/LearningX module content",
    ),
    SchemaTable(
        "videos",
        "video_id, source_type, course_id, semester_code, course_name, module_name, title, launch_url, content_type, size_bytes, locked, last_seen_at",
        "lecture-video candidates from modules and uploaded media",
    ),
    SchemaTable(
        "syllabi",
        "course_id, semester_code, course_name, syllabus_html, source_url, last_seen_at",
        "official course syllabus sources",
    ),
    SchemaTable(
        "artifacts",
        "artifact_type, course_id, semester_code, course_name, source_id, local_path, source_revision, sha256, size_bytes, status, error, last_seen_at",
        "locally pulled files, articles, syllabi, and videos",
    ),
    SchemaTable(
        "catalog_status",
        "scope, refreshed_at, row_count, complete",
        "freshness and completeness of cached remote data",
    ),
    SchemaTable(
        "sync_runs",
        "run_id, started_at, finished_at, status, downloaded, updated, unchanged, failed",
        "file synchronization history",
    ),
)


VIEW_SQL = """
CREATE TEMP VIEW semesters AS
SELECT semester_id, semester_code, academic_year, semester_name,
       starts_at, ends_at, last_seen_at
FROM main.semesters;

CREATE TEMP VIEW courses AS
SELECT c.remote_id AS course_id,
       c.semester_id,
       s.semester_code,
       s.academic_year,
       COALESCE(c.course_code, c.name) AS course_code,
       CASE
         WHEN s.semester_code IS NOT NULL
          AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       c.name AS source_course_name,
       c.url,
       c.starts_at,
       c.ends_at,
       c.last_seen_at
FROM main.courses AS c
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE c.active = 1;

CREATE TEMP VIEW files AS
SELECT f.file_id,
       f.course_id,
       c.semester_id,
       s.semester_code,
       s.academic_year,
       CASE
         WHEN s.semester_code IS NOT NULL
          AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       f.file_name,
       f.folder_path,
       f.remote_path,
       f.content_type,
       f.size_bytes,
       f.updated_at,
       f.download_url,
       NULLIF(d.local_path, '') AS local_path,
       COALESCE(d.status, 'remote') AS download_status,
       d.sha256,
       f.last_seen_at
FROM main.catalog_files AS f
JOIN main.courses AS c ON c.remote_id = f.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
LEFT JOIN main.files AS d
  ON d.course_id = f.course_id AND d.remote_id = f.file_id
WHERE f.available = 1 AND c.active = 1;

CREATE TEMP VIEW articles AS
SELECT i.content_id AS article_id,
       i.content_type,
       i.course_id,
       c.semester_id,
       s.semester_code,
       s.academic_year,
       CASE
         WHEN s.semester_code IS NOT NULL
          AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       i.title,
       i.url,
       i.published_at,
       i.updated_at,
       i.body_html,
       i.last_seen_at
FROM main.catalog_content_items AS i
JOIN main.courses AS c ON c.remote_id = i.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE i.available = 1
  AND i.content_type IN ('announcement', 'page')
  AND c.active = 1;

CREATE TEMP VIEW announcements AS
SELECT article_id AS announcement_id, course_id, semester_id, semester_code,
       academic_year, course_name, title, url, published_at, updated_at,
       body_html, last_seen_at
FROM articles WHERE content_type = 'announcement';

CREATE TEMP VIEW pages AS
SELECT article_id AS page_id, course_id, semester_id, semester_code,
       academic_year, course_name, title, url, published_at, updated_at,
       body_html, last_seen_at
FROM articles WHERE content_type = 'page';

CREATE TEMP VIEW assignments AS
SELECT i.content_id AS assignment_id,
       i.course_id,
       c.semester_id,
       s.semester_code,
       s.academic_year,
       CASE
         WHEN s.semester_code IS NOT NULL
          AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       i.title,
       i.url,
       i.published_at,
       i.due_at,
       i.last_seen_at
FROM main.catalog_content_items AS i
JOIN main.courses AS c ON c.remote_id = i.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE i.available = 1 AND i.content_type = 'assignment' AND c.active = 1;

CREATE TEMP VIEW quizzes AS
SELECT i.content_id AS quiz_id,
       i.course_id,
       c.semester_id,
       s.semester_code,
       s.academic_year,
       CASE
         WHEN s.semester_code IS NOT NULL
          AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       i.title,
       i.url,
       i.published_at,
       i.due_at,
       i.last_seen_at
FROM main.catalog_content_items AS i
JOIN main.courses AS c ON c.remote_id = i.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE i.available = 1 AND i.content_type = 'quiz' AND c.active = 1;

CREATE TEMP VIEW module_items AS
SELECT m.item_id,
       m.module_id,
       m.module_name,
       m.course_id,
       s.semester_code,
       CASE
         WHEN s.semester_code IS NOT NULL AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       m.item_type,
       m.title,
       m.position,
       m.content_id,
       m.html_url,
       m.external_url,
       m.published,
       m.locked,
       m.last_seen_at
FROM main.catalog_module_items AS m
JOIN main.courses AS c ON c.remote_id = m.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE m.available = 1 AND c.active = 1;

CREATE TEMP VIEW modules AS
SELECT module_id, course_id, semester_code, course_name, module_name,
       count(*) AS item_count, max(last_seen_at) AS last_seen_at
FROM module_items
GROUP BY module_id, course_id, semester_code, course_name, module_name;

CREATE TEMP VIEW videos AS
SELECT item_id AS video_id, 'external_tool' AS source_type, course_id,
       semester_code, course_name, module_name, title,
       COALESCE(external_url, html_url) AS launch_url,
       NULL AS content_type, NULL AS size_bytes, locked, last_seen_at
FROM module_items
WHERE lower(item_type) = 'externaltool'
UNION ALL
SELECT file_id AS video_id, 'file' AS source_type, course_id,
       semester_code, course_name, folder_path AS module_name, file_name AS title,
       download_url AS launch_url, content_type, size_bytes, 0 AS locked, last_seen_at
FROM files
WHERE content_type LIKE 'video/%' OR content_type LIKE 'audio/%';

CREATE TEMP VIEW syllabi AS
SELECT c.remote_id AS course_id, s.semester_code,
       CASE
         WHEN s.semester_code IS NOT NULL AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       c.syllabus_html,
       c.url || '/assignments/syllabus' AS source_url,
       c.last_seen_at
FROM main.courses AS c
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id
WHERE c.active = 1;

CREATE TEMP VIEW artifacts AS
SELECT a.artifact_type, a.course_id, s.semester_code,
       CASE
         WHEN s.semester_code IS NOT NULL AND c.name LIKE s.semester_code || ' %'
         THEN substr(c.name, length(s.semester_code) + 2)
         ELSE c.name
       END AS course_name,
       a.source_id, a.local_path, a.source_revision, a.sha256,
       a.size_bytes, a.status, a.error, a.last_seen_at
FROM main.pull_artifacts AS a
JOIN main.courses AS c ON c.remote_id = a.course_id
LEFT JOIN main.semesters AS s ON s.semester_id = c.semester_id;

CREATE TEMP VIEW catalog_status AS
SELECT scope, refreshed_at, row_count, complete
FROM main.catalog_refreshes;

CREATE TEMP VIEW sync_runs AS
SELECT id AS run_id, started_at, finished_at, status,
       downloaded, updated, unchanged, failed
FROM main.runs;
"""


LEADING_COMMENT = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)
READ_ONLY_START = re.compile(r"(?:SELECT|WITH|EXPLAIN)\b", re.IGNORECASE)
SHOW_TABLES = re.compile(r"\ASHOW\s+TABLES\s*;?\s*\Z", re.IGNORECASE)


def _normalize_sql(sql: str) -> str:
    if SHOW_TABLES.fullmatch(sql.strip()):
        rows = " UNION ALL ".join(
            f"SELECT {index} AS position, '{item.name}' AS table_name"
            for index, item in enumerate(SCHEMA)
        )
        return f"SELECT table_name FROM ({rows}) ORDER BY position"
    return sql


def _validate_read_only(sql: str) -> str:
    statement = sql.strip()
    if not statement:
        raise QueryError("SQL query cannot be empty")
    start = LEADING_COMMENT.match(statement)
    significant = statement[start.end() :] if start else statement
    if not READ_ONLY_START.match(significant):
        raise QueryError("only read-only SELECT, WITH, or EXPLAIN queries are allowed")
    return statement


def execute_query(database_path: Path, sql: str, *, limit: int = 200) -> QueryResult:
    if limit < 0:
        raise QueryError("query limit cannot be negative")
    # Apply migrations before reopening SQLite in immutable-main-database mode.
    with StateStore(database_path):
        pass
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        connection.executescript(VIEW_SQL)
        connection.execute("PRAGMA query_only=ON")
        cursor = connection.execute(_validate_read_only(_normalize_sql(sql)))
        if cursor.description is None:
            raise QueryError("query did not produce a result set")
        columns = tuple(str(column[0]) for column in cursor.description)
        if limit:
            values = cursor.fetchmany(limit + 1)
            truncated = len(values) > limit
            values = values[:limit]
        else:
            values = cursor.fetchall()
            truncated = False
        rows = tuple(tuple(row) for row in values)
        return QueryResult(columns, rows, truncated)
    except sqlite3.Error as exc:
        raise QueryError(f"SQL error: {exc}") from exc
    finally:
        connection.close()
