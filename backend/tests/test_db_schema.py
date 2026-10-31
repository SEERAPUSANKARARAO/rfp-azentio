import sqlite3

import pytest

from app.db import StaleSchemaError, init_db, make_engine


def test_a_fresh_database_starts(tmp_path):
    init_db(make_engine(tmp_path / "fresh.db"))


def test_a_database_from_an_older_version_is_refused_with_a_clear_message(tmp_path):
    # A file made before KbDocument had display_title: create_all leaves it alone, and every KB request then returned a 500.
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE kbdocument (id INTEGER PRIMARY KEY, title VARCHAR, kind VARCHAR)")
    con.commit()
    con.close()
    with pytest.raises(StaleSchemaError) as e:
        init_db(make_engine(path))
    assert "kbdocument" in str(e.value) and "filename" in str(e.value) and "delete" in str(e.value)


def test_columns_added_by_a_newer_version_are_added_without_losing_data(tmp_path):
    from sqlalchemy import text as sql

    path = tmp_path / "grown.db"
    eng = make_engine(path)
    init_db(eng)
    with eng.begin() as c:  # make it look like a database from before 'priority' and 'answer' existed
        c.execute(sql("INSERT INTO rfp (name, customer, filename, file_type, stored_path, status, submission_instructions,"
                      " format_rules, parse_report, created_at, updated_at) VALUES ('R', '', 'f.xlsx', 'xlsx', 'p', 'imported',"
                      " '', '{}', '{}', '2026-01-01', '2026-01-01')"))
        c.execute(sql("INSERT INTO clarificationquery (rfp_id, text, reason, requirement_ids, status, priority, impact, "
                      "source) VALUES (1, 'Q?', 'why', '[]', 'open', 'low', '', 'missing_evidence')"))
        c.execute(sql("ALTER TABLE clarificationquery DROP COLUMN priority"))
        c.execute(sql("ALTER TABLE clarificationquery DROP COLUMN answer"))
    init_db(make_engine(path))  # reopening must repair it, not refuse it
    with make_engine(path).connect() as c:
        row = c.execute(sql("SELECT text, priority, answer FROM clarificationquery")).one()
    assert tuple(row) == ("Q?", "medium", None)
