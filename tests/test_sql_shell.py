from pathlib import Path

from snuetl.models import Course
from snuetl.sql_shell import run_sql_shell
from snuetl.state import StateStore


def test_interactive_sql_shell_commands_and_query(tmp_path: Path, capsys) -> None:
    database = tmp_path / "state.db"
    with StateStore(database) as store, store.transaction():
        store.upsert_course(Course("101", "Databases", "https://lms.test/courses/101"))
    commands = iter(
        (
            "SHOW TABLES;",
            ".columns courses",
            ".limit 1",
            "SELECT course_id, course_name",
            "FROM courses;",
            ".quit",
        )
    )
    assert run_sql_shell(database, input_fn=lambda _prompt: next(commands)) == 0
    output = capsys.readouterr().out
    assert "semesters" in output
    assert "course_id" in output
    assert "Databases" in output


def test_ctrl_c_exits_interactive_sql_shell(tmp_path: Path, capsys) -> None:
    database = tmp_path / "state.db"

    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    assert run_sql_shell(database, input_fn=interrupt) == 130
    assert "Leaving snuetl SQL" in capsys.readouterr().out


def test_interactive_sql_shell_refresh_callback(tmp_path: Path, capsys) -> None:
    database = tmp_path / "state.db"
    with StateStore(database):
        pass
    called = []

    class Summary:
        courses = 1
        files = 2
        articles = 3
        assignments = 4

    commands = iter((".refresh", ".exit"))
    run_sql_shell(
        database,
        refresh=lambda: called.append(True) or Summary(),
        input_fn=lambda _prompt: next(commands),
    )
    assert called == [True]
    assert "1 courses, 2 files, 3 articles, 4 assignments" in capsys.readouterr().out
