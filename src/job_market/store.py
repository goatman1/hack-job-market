from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_url(url: str) -> str:
    if url.startswith("/"):
        url = "https://www.linkedin.com" + url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def canonical_job_url(url: str) -> str:
    """Keep requisition-bearing query values while removing known tracking tags.

    LinkedIn post identity does not need query strings, but many job sites use
    ``jobId`` or similar query keys as their only requisition identifier.
    """
    parts = urlsplit(url)
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith("utm_")
                   and key.lower() not in {"trk", "trackingid", "refid"})
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(query), ""))


def post_identity(post: dict) -> str:
    url = post.get("url", "")
    match = re.search(r"(?:activity|ugcPost|share)[:\-](\d+)", url)
    if match:
        return "li:" + match.group(1)
    supplied = str(post.get("post_id") or "")
    match = re.search(r"(?:activity|ugcPost|share)[:\-](\d+)", supplied)
    if match:
        return "li:" + match.group(1)
    if supplied.startswith("li:"):
        return supplied
    if url and ("/posts/" in url or "/feed/update/" in url):
        return "url:" + digest(canonical_url(url))[:24]
    author = post.get("author_url") or post.get("author_name", "")
    content = " ".join(post.get("content", "").lower().split())
    return "fp:" + digest(author.lower() + "|" + content)[:24]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, config TEXT NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS run_posts(run_id TEXT, post_id TEXT, assessed_digest TEXT,
            PRIMARY KEY(run_id,post_id));
        CREATE TABLE IF NOT EXISTS leads(id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
            opportunity_key TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS lead_opportunity ON leads(opportunity_key);
        CREATE TABLE IF NOT EXISTS evidence(url TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, run_id TEXT, expires REAL, payload TEXT);
        CREATE TABLE IF NOT EXISTS post_aliases(alias TEXT PRIMARY KEY, post_id TEXT NOT NULL);
        ''')
        # Backfill aliases without renaming persisted IDs: existing run/batch/
        # workbook references remain valid when a formerly unrecognized URL now
        # exposes a canonical share/activity ID.
        for row in self.db.execute("SELECT id, payload FROM posts ORDER BY rowid").fetchall():
            self._record_aliases(row["id"], json.loads(row["payload"]))
        self.db.commit()

    def _record_aliases(self, post_id: str, raw: dict):
        aliases = {post_id, post_identity(raw)}
        if raw.get("url"):
            aliases.add("url:" + digest(canonical_url(raw["url"]))[:24])
        for alias in aliases:
            self.db.execute("INSERT OR IGNORE INTO post_aliases VALUES(?,?)", (alias, post_id))

    def resolve_post_identity(self, raw: dict) -> str:
        identity = post_identity(raw)
        row = self.db.execute("SELECT post_id FROM post_aliases WHERE alias=?", (identity,)).fetchone()
        return row["post_id"] if row else identity

    def lead_has_post(self, lead: dict, post: dict) -> bool:
        if lead["post_id"] == post["post_id"]:
            return True
        return any(self.resolve_post_identity({"url": url}) == post["post_id"]
                   for url in lead.get("source_posts", []))

    @contextmanager
    def transaction(self):
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def put_run(self, run_id, config, state):
        self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)",
                        (run_id, json.dumps(config), json.dumps(state)))

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Unknown run_id")
        return json.loads(row["config"]), json.loads(row["state"])

    def runs(self):
        return [{"run_id": r["id"], **json.loads(r["state"])}
                for r in self.db.execute("SELECT * FROM runs ORDER BY rowid DESC")]

    def post(self, post_id):
        row = self.db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown post_id")
        return json.loads(row["payload"])

    def ingest(self, run_id: str, raw: dict) -> tuple[str, bool, bool]:
        post_id = self.resolve_post_identity(raw)
        old = self.db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        payload = dict(raw)
        payload.update(post_id=post_id, url=canonical_url(raw.get("url", "")), last_seen=now())
        payload["first_seen"] = json.loads(old["payload"])["first_seen"] if old else now()
        content_digest = digest(raw.get("content", "") + raw.get("author_url", ""))
        payload["digest"] = content_digest
        changed = bool(old and old["digest"] != content_digest)
        self.db.execute("INSERT OR REPLACE INTO posts VALUES(?,?,?)",
                        (post_id, content_digest, json.dumps(payload)))
        self._record_aliases(post_id, raw)
        if not old or changed:
            self.db.execute("INSERT OR IGNORE INTO run_posts VALUES(?,?,NULL)", (run_id, post_id))
        # A changed source invalidates prior judgments, never tracking status/notes.
        for row in self.db.execute("SELECT * FROM leads").fetchall():
            lead = json.loads(row["payload"])
            if not self.lead_has_post(lead, payload):
                continue
            lead["last_seen"] = payload["last_seen"]
            if lead["post_id"] == post_id:
                lead["content"] = payload["content"]
            if changed:
                lead.update(bucket="Needs Review", reach_out_message="", match_score=None,
                            match_explanation="Source changed; reassessment required")
                lead["requires_reassessment"] = list(dict.fromkeys(
                    lead.get("requires_reassessment", []) + [post_id]))
            self.db.execute("UPDATE leads SET payload=? WHERE id=?", (json.dumps(lead), row["id"]))
        return post_id, old is None, changed

    def leads(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM leads ORDER BY rowid DESC")]

    def put_lead(self, lead: dict, opportunity_key: str):
        self.db.execute("INSERT OR REPLACE INTO leads VALUES(?,?,?,?)",
                        (lead["lead_id"], lead["post_id"], opportunity_key, json.dumps(lead)))

    def update_lead(self, lead_id, status=None, notes=None):
        from .models import STATUSES
        row = self.db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            raise ValueError("Unknown lead_id")
        lead = json.loads(row["payload"])
        if status is not None:
            if status not in STATUSES:
                raise ValueError("Invalid tracking status")
            lead["status"] = status
        if notes is not None:
            if len(notes) > 10000:
                raise ValueError("Notes exceed 10000 characters")
            lead["notes"] = notes
        self.put_lead(lead, row["opportunity_key"])
        return lead

    def save_evidence(self, url, payload):
        self.db.execute("INSERT OR REPLACE INTO evidence VALUES(?,?)", (url, json.dumps(payload)))

    def evidence(self, url):
        row = self.db.execute("SELECT payload FROM evidence WHERE url=?", (url,)).fetchone()
        return json.loads(row[0]) if row else None
