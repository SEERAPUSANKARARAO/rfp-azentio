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
    assert "kbdocument" in str(e.value) and "display_title" in str(e.value) and "delete" in str(e.value)
