"""What a live deployment depends on: the optional access key, calls from another origin, and the memory-saving switch."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

VERCEL = "https://rfp-ai-demo.vercel.app"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    stack = ExitStack()

    def make(**env):
        base = {"DB_PATH": str(tmp_path / "t.db"), "UPLOAD_DIR": str(tmp_path / "up"), "EXPORT_DIR": str(tmp_path / "ex"),
                "KB_UPLOAD_DIR": str(tmp_path / "kb"), "AS_OF_DATE": "2026-10-31", "GEMINI_API_KEYS": "", "GROQ_API_KEYS": "",
                "OPENROUTER_API_KEYS": "", "APP_ACCESS_KEY": "", "CORS_ORIGIN_REGEX": "", "EMBEDDINGS_ENABLED": "true"}
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from app.config import get_settings
        from app.db import get_engine
        from app.llm import get_gateway
        from app.rag.embeddings import reset_embedder
        for fn in (get_settings, get_engine, get_gateway):
            fn.cache_clear()
        reset_embedder()
        from app.main import create_app
        return stack.enter_context(TestClient(create_app()))

    yield make
    stack.close()
    from app.config import get_settings
    from app.db import get_engine
    from app.llm import get_gateway
    from app.rag.embeddings import reset_embedder
    for fn in (get_settings, get_engine, get_gateway):
        fn.cache_clear()
    reset_embedder()


# ---------------------------------------------------------------- the access key
def test_with_no_key_set_the_api_is_open_as_before(make_client):
    c = make_client()
    assert c.get("/api/rfps").status_code == 200
    assert c.get("/api/health").json()["access_key_required"] is False


def test_with_a_key_set_every_api_call_needs_it_except_the_health_check(make_client):
    c = make_client(APP_ACCESS_KEY="s3cret-key")
    assert c.get("/api/health").json()["access_key_required"] is True  # the app learns it must ask, without a key
    assert c.get("/api/rfps").status_code == 401
    assert c.get("/api/rfps", headers={"X-Access-Key": "wrong"}).status_code == 401
    assert c.get("/api/rfps", headers={"X-Access-Key": "s3cret-key"}).status_code == 200
    assert c.post("/api/kb/index-samples").status_code == 401  # a write is refused the same way
    assert c.get("/api/kb/status", headers={"X-Access-Key": "s3cret-key"}).status_code == 200


def test_the_key_can_also_come_in_the_address_because_a_progress_stream_cannot_set_headers(make_client):
    c = make_client(APP_ACCESS_KEY="s3cret-key")
    assert c.get("/api/rfps?access_key=wrong").status_code == 401
    assert c.get("/api/rfps?access_key=s3cret-key").status_code == 200


def test_a_refused_request_is_still_readable_by_the_page_that_made_it(make_client):
    c = make_client(APP_ACCESS_KEY="s3cret-key", CORS_ORIGINS=VERCEL)
    r = c.get("/api/rfps", headers={"Origin": VERCEL})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == VERCEL  # otherwise the browser shows a CORS error


def test_a_browsers_preflight_is_never_asked_for_the_key(make_client):
    c = make_client(APP_ACCESS_KEY="s3cret-key", CORS_ORIGINS=VERCEL)
    r = c.options("/api/rfps", headers={"Origin": VERCEL, "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "x-access-key"})
    assert r.status_code == 200 and "x-access-key" in r.headers["access-control-allow-headers"].lower()


# ---------------------------------------------------------------- calls from another origin
def test_the_frontends_origin_is_allowed_by_name_or_by_pattern_and_others_are_not(make_client):
    named = make_client(CORS_ORIGINS=VERCEL)
    assert named.get("/api/health", headers={"Origin": VERCEL}).headers.get("access-control-allow-origin") == VERCEL
    assert "access-control-allow-origin" not in named.get("/api/health", headers={"Origin": "https://evil.example"}).headers

    by_pattern = make_client(CORS_ORIGIN_REGEX=r"https://.*\.vercel\.app")
    for origin in (VERCEL, "https://rfp-ai-git-main-someone.vercel.app"):
        assert by_pattern.get("/api/health", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in by_pattern.get("/api/health", headers={"Origin": "https://vercel.app.evil.com"}).headers


def test_a_page_on_another_origin_can_read_the_file_name_of_a_download(make_client):
    # Without Access-Control-Expose-Headers the browser hides Content-Disposition, and every export would be named "response".
    c = make_client(CORS_ORIGINS=VERCEL)
    r = c.get("/api/health", headers={"Origin": VERCEL})
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "content-disposition" in exposed and "x-export-draft" in exposed


# ---------------------------------------------------------------- the memory-saving switch
def test_embeddings_can_be_switched_off_so_a_small_host_runs_keyword_only(make_client):
    c = make_client(EMBEDDINGS_ENABLED="false")
    from app.rag.embeddings import get_embedder
    assert get_embedder() is None
    c.post("/api/kb/index-samples")
    assert c.get("/api/kb/status").json()["retrieval_mode"] == "keyword"
    assert c.get("/api/kb/search", params={"q": "encryption at rest"}).json()  # search still works
