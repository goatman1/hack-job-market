"""Regression cases discovered during independent integration review."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client
from test_service import (
    POST_TEXT,
    FixtureCollector,
    assessment,
    post,
    prepare,
    start,
    submit,
)
from test_service import resume_dir as _resume_fixture

from job_market.server import create_server
from job_market.service import JobMarket
from job_market.store import Store, canonical_url, digest, now, post_identity

resume_dir = _resume_fixture


@pytest.fixture
def market(tmp_path, monkeypatch):
    # A user's local configuration must not change these synthetic test cases.
    monkeypatch.chdir(tmp_path)
    service = JobMarket(tmp_path / "data", FixtureCollector())
    yield service
    service.store.db.close()


async def test_mcp_partial_override_preserves_personal_configuration(tmp_path, resume_dir, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.local.toml").write_text(
        f"resume_dir = '{resume_dir.as_posix()}'\n"
        'available_from = "2028-02-01"\n'
        "future_sponsorship = false\n",
        encoding="utf-8",
    )
    service = JobMarket(tmp_path / "data", FixtureCollector())
    async with Client(create_server(tmp_path / "data", service)) as client:
        result = await client.call_tool("start_run", {"config": {"target_count": 30}})
        config = result.data["config"]
        assert config["target_count"] == 30
        assert config["resume_dir"] == str(resume_dir.resolve())
        assert config["available_from"] == "2028-02-01"
        assert config["future_sponsorship"] is False


def test_one_semantic_override_preserves_other_default_filters(market, resume_dir):
    run = start(market, resume_dir, semantic_filters={"start_date": "off"})
    filters = market.get_run_status(run)["config"]["semantic_filters"]
    assert filters["start_date"] == "off"
    assert filters.get("location") == "enforce"
    assert filters.get("job_title") == "enforce"
    assert filters.get("sponsorship") == "flag"


async def test_changed_post_releases_completed_old_analysis_lease(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    submit(market, run, batch, assessment(batch))
    market.collector.responses = [{
        "posts": [post(content=POST_TEXT + " Updated role requirements.")],
        "exhausted": True,
    }]
    rerun = start(market, resume_dir)
    await market.collect_posts(rerun)
    assert market.get_run_status(rerun)["pending_analysis"] == 1
    updated_batch = market.get_analysis_batch(rerun)
    assert len(updated_batch["posts"]) == 1
    assert "Updated role requirements" in updated_batch["posts"][0]["content"]


async def test_edit_seen_later_in_same_run_updates_post(market, resume_dir):
    market.collector.responses = [
        {"posts": [post()], "next_page": 2},
        {"posts": [post(content=POST_TEXT + " Role is now closed.")], "exhausted": True},
    ]
    run = start(market, resume_dir)
    await market.collect_posts(run)
    batch = market.get_analysis_batch(run)
    assert len(batch["posts"]) == 1
    assert "Role is now closed." in batch["posts"][0]["content"]
    assert market.get_run_status(run)["collected"] == 1


async def test_retry_does_not_erase_other_repost_sources(market, resume_dir):
    run, batch = await prepare(market, resume_dir, [post(1), post(2)])
    first, second = (assessment(batch, index=i) for i in range(2))
    first["requisition_url"] = second["requisition_url"] = "https://example.com/jobs/123"
    submit(market, run, batch, first, second)
    assert len(market.list_leads()["leads"][0]["source_posts"]) == 2
    submit(market, run, batch, first)
    assert market.list_leads()["total"] == 1
    assert len(market.list_leads()["leads"][0]["source_posts"]) == 2


async def test_distinct_query_identified_jobs_do_not_merge(market, resume_dir):
    first_post = post(1, POST_TEXT + " https://example.com/job?jobId=123")
    second_post = post(2, POST_TEXT + " https://example.com/job?jobId=456")
    run, batch = await prepare(market, resume_dir, [first_post, second_post])
    items = [assessment(batch, index=i) for i in range(2)]
    items[0]["requisition_url"] = "https://example.com/job?jobId=123"
    items[1]["requisition_url"] = "https://example.com/job?jobId=456"
    submit(market, run, batch, *items)
    assert market.list_leads()["total"] == 2


@pytest.mark.parametrize("token", ["activity-", "activity:", "share-", "share:", "ugcPost-", "ugcPost:"])
def test_linkedin_post_alias_kinds_share_numeric_identity(token):
    assert post_identity({"url": f"https://www.linkedin.com/posts/example-{token}123456-xyz"}) == "li:123456"
    assert post_identity({"post_id": f"urn:li:{token}123456"}) == "li:123456"


def test_legacy_url_id_alias_backfill_preserves_lead_and_run_ids(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    raw = {**post(), "url": "https://www.linkedin.com/posts/alex_hiring-share-10001-xyz"}
    old_id = "url:" + digest(canonical_url(raw["url"]))[:24]
    payload = {**raw, "post_id": old_id, "digest": digest(raw["content"] + raw["author_url"]),
               "first_seen": now(), "last_seen": now()}
    initial = Store(path)
    with initial.transaction():
        initial.db.execute("INSERT INTO posts VALUES(?,?,?)", (old_id, payload["digest"], json.dumps(payload)))
        initial.db.execute("INSERT INTO run_posts VALUES(?,?,?)", ("legacy-run", old_id, payload["digest"]))
        initial.put_lead({**payload, "lead_id": "keep-this-id", "role": "Applied Scientist",
                          "status": "contacted", "notes": "Existing note", "bucket": "Shortlist"}, "opportunity")
    initial.db.close()

    migrated = Store(path)
    try:
        with migrated.transaction():
            resolved, is_new, changed = migrated.ingest("new-run", post())
        assert resolved == old_id
        assert is_new is False and changed is False
        assert migrated.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
        assert migrated.db.execute("SELECT post_id FROM run_posts WHERE run_id='legacy-run'").fetchone()[0] == old_id
        lead = migrated.leads()[0]
        assert lead["lead_id"] == "keep-this-id"
        assert lead["status"] == "contacted"
        assert lead["notes"] == "Existing note"
        assert migrated.resolve_post_identity(raw) == old_id
    finally:
        migrated.db.close()


async def test_secondary_repost_edit_invalidates_group_and_preserves_tracking(market, resume_dir):
    run, batch = await prepare(market, resume_dir, [post(1), post(2)])
    items = [assessment(batch, index=i) for i in range(2)]
    for item in items:
        item["requisition_url"] = "https://example.com/jobs/123"
    lead_id = submit(market, run, batch, *items)["lead_ids"][0]
    market.update_lead(lead_id, "saved", "Review closure update.")
    market.collector.responses = [{
        "posts": [post(2, POST_TEXT + " This position is now closed.")], "exhausted": True,
    }]
    rerun = start(market, resume_dir)
    await market.collect_posts(rerun)
    leads = market.list_leads()["leads"]
    assert len(leads) == 1
    assert leads[0]["bucket"] == "Needs Review"
    assert leads[0]["reach_out_message"] == ""
    assert leads[0]["lead_id"] == lead_id
    assert leads[0]["status"] == "saved"

    updated_batch = market.get_analysis_batch(rerun)
    item = assessment(updated_batch)
    item["requisition_url"] = "https://example.com/jobs/123"
    item["verification"]["hiring"] = {
        "status": "fail", "reason": "Author says the position closed.",
        "evidence": [{"source_url": updated_batch["posts"][0]["url"],
                      "quote": "This position is now closed."}],
    }
    item["reach_out_message"] = ""
    submit(market, rerun, updated_batch, item)
    leads = market.list_leads()["leads"]
    assert len(leads) == 1
    assert leads[0]["bucket"] == "Excluded"
    assert leads[0]["lead_id"] == lead_id
    assert leads[0]["notes"] == "Review closure update."
    assert len(leads[0]["source_posts"]) == 2


async def test_partial_challenge_preserves_posts_warnings_and_resume_cursor(market, resume_dir):
    market.collector.responses = [
        {"posts": [post(1)], "next_page": 2, "observed_count": 43,
         "warnings": [f"warning {i}" for i in range(25)]},
        {"posts": [post(2)], "stop_after_batch": "Manual login required during post expansion",
         "next_page": 3, "observed_count": 20, "warnings": ["Partial expansion"]},
    ]
    run = start(market, resume_dir)
    result = await market.collect_posts(run)
    assert result["stop_reason"] == "blocked"
    assert result["collected"] == 2
    assert result["pending_analysis"] == 2
    assert result["max_observed_count"] == 43
    assert len(result["warnings"]) == 20
    assert result["warnings"][-1] == "Partial expansion"
    assert result["warnings"][0] == "warning 6"
    assert "Manual login required" in result["error"]
    assert result["cursors"][0]["page"] == 2
    assert market.list_leads()["total"] == 2

    # Retrying the interrupted page must neither lose nor duplicate partial data.
    market.start_run(resume_run_id=run)
    market.collector.responses = [{"posts": [post(2), post(3)], "exhausted": True}]
    resumed = await market.collect_posts(run)
    assert market.collector.calls[-1][3] == 2
    assert resumed["collected"] == 3
    assert resumed["stop_reason"] == "exhausted"


async def test_partial_challenge_is_reported_even_when_target_reached(market, resume_dir):
    market.collector.responses = [{"posts": [post()], "stop_after_batch": "captcha"}]
    run = start(market, resume_dir, target_count=1)
    result = await market.collect_posts(run)
    assert result["collected"] == 1
    assert result["stop_reason"] == "blocked"
