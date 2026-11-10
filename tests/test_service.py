"""Behavior tests for persistent research runs; no browser or real personal data."""
from __future__ import annotations

from copy import deepcopy
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from job_market.models import REQUIRED_CHECKS, Assessment, RunConfig
from job_market.service import JobMarket

RESUME_TEXT = "Candidate has researched reinforcement learning and built Python models."
POST_TEXT = (
    "I am hiring for my own team at Example Labs. I am a current engineering manager "
    "based in New York, United States. This is a full-time junior applied scientist "
    "role based in the United States. PhD new graduates are welcome. Python and "
    "reinforcement learning experience are desired. Start dates are flexible and "
    "future visa sponsorship is available. Apply at https://example.com/jobs/123."
)
DRAFT = (
    "Hi Alex, I saw your post about the junior applied scientist opening on your "
    "team at Example Labs. My background includes reinforcement learning research "
    "and building Python models, which seems relevant to the experience you described. "
    "I would appreciate learning more about the team's current research priorities "
    "and how this role combines modeling with applied development. Would you be "
    "open to a brief conversation about whether my background could fit the position? "
    "Thank you for sharing the opportunity and considering my interest."
)


class FixtureCollector:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.evidence_calls = []
        self.closed = False

    async def collect_page(self, query, sort_by, date_posted, page, scroll=0):
        self.calls.append((query, sort_by, date_posted, page, scroll))
        return deepcopy(self.responses.pop(0)) if self.responses else {
            "posts": [], "exhausted": True,
        }

    async def author_evidence(self, profile_url, company_url=None):
        self.evidence_calls.append((profile_url, company_url))
        return {"sources": [{"url": profile_url, "text": "Current manager in New York."}]}

    async def close(self):
        self.closed = True


@pytest.fixture
def resume_dir(tmp_path):
    directory = tmp_path / "resumes"
    directory.mkdir()
    with ZipFile(directory / "research.docx", "w") as archive:
        archive.writestr("word/document.xml", (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{RESUME_TEXT}</w:t></w:r></w:p></w:body></w:document>"
        ))
    return directory


@pytest.fixture
def market(tmp_path):
    app = JobMarket(tmp_path / "data", FixtureCollector())
    yield app
    app.store.db.close()


def post(number=1, content=POST_TEXT):
    return {
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{10000 + number}/",
        "content": content,
        "author_name": "Alex Example",
        "author_url": "https://www.linkedin.com/in/alex-example",
        "author_position": "Engineering Manager at Example Labs",
        "posted_date": "2026-11-09",
    }


def start(market, resume_dir, **overrides):
    config = {"resume_dir": str(resume_dir), "queries": [
        {"keywords": "hiring applied scientist", "track": "tech"},
    ], **overrides}
    return market.start_run(config)["run_id"]


async def prepare(market, resume_dir, posts=None, **overrides):
    market.collector.responses = [{"posts": posts or [post()], "exhausted": True}]
    run = start(market, resume_dir, **overrides)
    await market.collect_posts(run)
    batch = market.get_analysis_batch(run)
    return run, batch


def assessment(batch, index=0, role="Applied Scientist"):
    record = batch["posts"][index]
    resume = batch["resumes"][0]
    evidence = {"source_url": record["url"], "quote": "I am hiring for my own team"}
    return {
        "post_id": record["post_id"], "role": role, "company": "Example Labs",
        "verification": {key: {"status": "pass", "reason": "Supported by source.",
                                "evidence": [deepcopy(evidence)]} for key in REQUIRED_CHECKS},
        "scores": {"role_alignment": 25, "skills": 25, "experience": 20,
                   "education_seniority": 15},
        "selected_resume": resume["id"],
        "match_explanation": "Direct Python and reinforcement learning research match.",
        "eligibility_flags": [], "reach_out_message": DRAFT,
        "draft_evidence": [{"source_url": resume["path"], "quote": RESUME_TEXT}],
        "author_location": "New York, United States",
    }


def submit(market, run, batch, *items):
    return market.save_assessments(run, batch["batch_id"], list(items))


async def test_collects_beyond_initial_page_and_preserves_filters(market, resume_dir):
    market.collector.responses = [
        {"posts": [post(i) for i in range(15)], "next_page": 1, "next_scroll": 1},
        {"posts": [post(i) for i in range(10, 30)], "exhausted": True},
    ]
    run = start(market, resume_dir, target_count=25, sort_by="date_posted", date_posted="all_time")
    result = await market.collect_posts(run)
    assert result["collected"] == 25
    assert len(result["post_ids"]) == 25
    assert result["stop_reason"] == "target_reached"
    assert market.collector.calls == [
        ("hiring applied scientist", "date_posted", None, 1, 0),
        ("hiring applied scientist", "date_posted", None, 1, 1),
    ]


async def test_alternates_queries_and_defaults_to_relevance(market, resume_dir):
    market.collector.responses = [{"posts": [post(1)]}, {"posts": [post(2)]}]
    run = start(market, resume_dir, target_count=2, queries=[
        {"keywords": "tech", "track": "tech"}, {"keywords": "quant", "track": "quant"},
    ])
    await market.collect_posts(run)
    assert [c[:3] for c in market.collector.calls] == [
        ("tech", "relevance", "past_week"), ("quant", "relevance", "past_week"),
    ]
    assert {lead["track"] for lead in market.list_leads()["leads"]} == {"tech", "quant"}


async def test_rerun_suppresses_existing_post_and_retains_tracking(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    result = submit(market, run, batch, assessment(batch))
    lead_id = result["lead_ids"][0]
    market.update_lead(lead_id, "contacted", "Sent manually.")
    market.collector.responses = [{"posts": [post()], "exhausted": True}]
    rerun = start(market, resume_dir)
    result = await market.collect_posts(rerun)
    assert result["collected"] == 0
    assert result["pending_analysis"] == 0
    assert market.list_leads(bucket="Shortlist")["total"] == 0
    assert market.list_leads(status="contacted")["leads"][0]["notes"] == "Sent manually."
    assert market.list_leads()["total"] == 1


async def test_url_aliases_and_tracking_parameters_deduplicate(market, resume_dir):
    original = post()
    alias = {**original, "url": "https://www.linkedin.com/posts/alex_hiring-activity-10001-abc?utm=x"}
    run, _ = await prepare(market, resume_dir, [original, alias])
    assert market.get_run_status(run)["collected"] == 1
    assert market.list_leads()["total"] == 1


async def test_changed_post_requires_reassessment_without_losing_notes(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    lead_id = submit(market, run, batch, assessment(batch))["lead_ids"][0]
    market.update_lead(lead_id, "saved", "Check updated requirements.")
    market.collector.responses = [{"posts": [post(content=POST_TEXT + " Updated requirements.")],
                                   "exhausted": True}]
    rerun = start(market, resume_dir)
    result = await market.collect_posts(rerun)
    assert result["collected"] == 0
    assert result["pending_analysis"] == 1
    lead = market.list_leads()["leads"][0]
    assert lead["bucket"] == "Needs Review"
    assert lead["status"] == "saved"
    assert lead["notes"] == "Check updated requirements."
    assert lead["reach_out_message"] == ""
    assert lead["match_score"] is None


@pytest.mark.parametrize(("setting", "value", "reason"), [
    ("max_inspected", 1, "inspection_limit"),
    ("max_loading_actions", 1, "loading_limit"),
])
async def test_collection_budgets(market, resume_dir, setting, value, reason):
    market.collector.responses = [{"posts": [post(1)]}, {"posts": [post(2)]}]
    run = start(market, resume_dir, **{setting: value})
    result = await market.collect_posts(run)
    assert result["stop_reason"] == reason
    assert len(market.collector.calls) == 1


async def test_time_budget_stops_without_browser_call(market, resume_dir):
    run = start(market, resume_dir, max_minutes=1)
    config, state = market.store.run(run)
    state["collection_seconds"] = 60
    with market.store.transaction():
        market.store.put_run(run, config, state)
    assert (await market.collect_posts(run))["stop_reason"] == "time_limit"
    assert market.collector.calls == []


async def test_stalled_search_exhausts_after_three_repeated_loads(market, resume_dir):
    market.collector.responses = [{"posts": [post()]} for _ in range(8)]
    run = start(market, resume_dir)
    result = await market.collect_posts(run)
    assert result["stop_reason"] == "exhausted"
    assert result["collected"] == 1
    assert len(market.collector.calls) == 4


async def test_blocked_run_resumes_at_saved_cursor(market, resume_dir):
    market.collector.responses = [{"posts": [post(1)], "next_page": 2},
                                   {"blocked": "captcha", "posts": []}]
    run = start(market, resume_dir, target_count=2)
    result = await market.collect_posts(run)
    assert result["stop_reason"] == "blocked"
    assert result["collected"] == 1
    market.collector.responses = [{"posts": [post(2)], "exhausted": True}]
    market.start_run(resume_run_id=run)
    result = await market.collect_posts(run)
    assert result["stop_reason"] == "target_reached"
    assert market.collector.calls[-1][3] == 2
    with pytest.raises(ValueError, match="saved config"):
        market.start_run({"target_count": 3}, resume_run_id=run)


async def test_analysis_batches_are_bounded_and_disjoint(market, resume_dir):
    market.collector.responses = [{"posts": [post(i) for i in range(25)], "exhausted": True}]
    run = start(market, resume_dir)
    await market.collect_posts(run)
    batches = [market.get_analysis_batch(run) for _ in range(3)]
    assert [len(b["posts"]) for b in batches] == [10, 10, 5]
    ids = [p["post_id"] for b in batches for p in b["posts"]]
    assert len(set(ids)) == 25
    assert not market.get_analysis_batch(run)["posts"]
    with pytest.raises(ValueError, match="1–10"):
        market.get_analysis_batch(run, limit=11)


@pytest.mark.parametrize("check", ["manager", "us_author", "company_affiliation", "own_team"])
async def test_failed_manager_requirements_exclude(market, resume_dir, check):
    run, batch = await prepare(market, resume_dir)
    item = assessment(batch)
    item["verification"][check]["status"] = "fail"
    item["reach_out_message"] = ""
    submit(market, run, batch, item)
    assert market.list_leads(bucket="Excluded")["total"] == 1
    assert market.list_leads(bucket="Shortlist")["total"] == 0


async def test_unknown_author_location_is_review_not_rejection(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    item = assessment(batch)
    item["verification"]["us_author"] = {
        "status": "unknown", "reason": "Profile location is unavailable.", "evidence": [],
    }
    item["reach_out_message"] = ""
    submit(market, run, batch, item)
    assert market.list_leads(bucket="Needs Review")["total"] == 1


async def test_score_and_flag_only_eligibility_do_not_exclude(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    item = assessment(batch)
    item["verification"]["sponsorship"]["status"] = "fail"
    item["verification"]["start_date"] = {
        "status": "unknown", "reason": "Start date not stated.", "evidence": [],
    }
    submit(market, run, batch, item)
    lead = market.list_leads(bucket="Shortlist")["leads"][0]
    assert lead["match_score"] == 85
    assert any("sponsorship: fail" in flag for flag in lead["eligibility_flags"])
    assert any("start_date: unknown" in flag for flag in lead["eligibility_flags"])


async def test_invalid_evidence_and_overrange_scores_reject_atomically(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    item = assessment(batch)
    item["draft_evidence"][0]["quote"] = "Candidate invented a billion-dollar trading strategy."
    with pytest.raises(ValueError, match="Evidence quote not found"):
        submit(market, run, batch, item)
    assert market.get_run_status(run)["pending_analysis"] == 1
    assert market.list_leads()["leads"][0]["role"] == "Unassessed"
    item = assessment(batch)
    item["scores"]["skills"] = 31
    with pytest.raises(ValidationError):
        submit(market, run, batch, item)


async def test_unknown_resume_and_unbacked_shortlist_draft_reject(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    item = assessment(batch)
    item["selected_resume"] = "not-provided"
    with pytest.raises(ValueError, match="Unknown selected_resume"):
        submit(market, run, batch, item)
    item = assessment(batch)
    item["draft_evidence"] = [{"source_url": batch["posts"][0]["url"],
                               "quote": "I am hiring for my own team"}]
    with pytest.raises(ValueError, match="résumé evidence"):
        submit(market, run, batch, item)


async def test_retries_are_idempotent_and_distinct_roles_stay_separate(market, resume_dir):
    run, batch = await prepare(market, resume_dir)
    items = [assessment(batch), assessment(batch, role="Research Engineer")]
    first = submit(market, run, batch, *items)
    second = submit(market, run, batch, *items)
    assert first["lead_ids"] == second["lead_ids"]
    assert market.list_leads()["total"] == 2
    assert market.get_run_status(run)["pending_analysis"] == 0


async def test_same_requisition_reposts_group_sources(market, resume_dir):
    run, batch = await prepare(market, resume_dir, [post(1), post(2)])
    items = [assessment(batch, index=i) for i in range(2)]
    for item in items:
        item["requisition_url"] = "https://example.com/jobs/123"
    submit(market, run, batch, *items)
    leads = market.list_leads()["leads"]
    assert len(leads) == 1
    assert len(leads[0]["source_posts"]) == 2


async def test_author_evidence_cache_and_explicit_refresh(market):
    url = "https://www.linkedin.com/in/alex-example"
    first = await market.get_author_evidence(url)
    second = await market.get_author_evidence(url)
    assert first == second
    assert len(market.collector.evidence_calls) == 1
    await market.get_author_evidence(url, refresh=True)
    assert len(market.collector.evidence_calls) == 2


def test_model_rejects_missing_checks_and_unsupported_configuration():
    with pytest.raises(ValidationError):
        RunConfig(sort_by="made-up")
    with pytest.raises(ValidationError):
        RunConfig(semantic_filters={"unknown_filter": "enforce"})
    with pytest.raises(ValidationError):
        Assessment.model_validate({"post_id": "fake", "verification": {}})


async def test_persistence_survives_new_service_instance(tmp_path, resume_dir):
    data = tmp_path / "persistent"
    first = JobMarket(data, FixtureCollector())
    run, batch = await prepare(first, resume_dir)
    lead_id = submit(first, run, batch, assessment(batch))["lead_ids"][0]
    first.update_lead(lead_id, "saved", "Persistent note.")
    await first.close()
    second = JobMarket(data, FixtureCollector())
    try:
        assert second.get_run_status(run)["analysis_complete"]
        assert second.list_leads()["leads"][0]["notes"] == "Persistent note."
    finally:
        await second.close()
