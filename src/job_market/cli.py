from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

from .service import JobMarket


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def configure_codex(root: Path, data_dir: Path) -> Path:
    # Generate local absolute paths; never touch user-wide Codex configuration.
    folder = root / ".codex"
    folder.mkdir(exist_ok=True)
    path = folder / "config.toml"
    if path.exists():
        existing = read_config(path)
        if set(existing) - {"mcp_servers"} or set(existing.get("mcp_servers", {})) - {"job-market"}:
            raise ValueError("Existing project configuration has other settings; merge the example manually")
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    text = '\n'.join([
        '[mcp_servers.job-market]',
        'command = ' + quote(sys.executable),
        'args = ["-m", "job_market", "--data-dir", ' + quote(data_dir.resolve()) + ', "serve"]',
        'cwd = ' + quote(root.resolve()),
        'startup_timeout_sec = 30',
        'tool_timeout_sec = 180',
        'enabled_tools = ["start_run", "collect_posts", "get_author_evidence", "get_analysis_batch", "save_assessments", "list_leads", "update_lead", "sync_workbook", "get_run_status"]',
        '',
    ])
    path.write_text(text, encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Codex hiring-manager research")
    parser.add_argument("--data-dir", type=Path, default=Path(".local"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--resume-dir", type=Path, default=Path("../resumes"))
    sub.add_parser("configure-codex")
    sub.add_parser("login")
    sub.add_parser("serve")
    start = sub.add_parser("start")
    start.add_argument("--config", type=Path, default=Path("config.local.toml"))
    start.add_argument("--target-count", type=int)
    start.add_argument("--sort-by", choices=["relevance", "date_posted"])
    start.add_argument("--date-posted", choices=["past_24_hours", "past_week", "past_month", "all_time"])
    start.add_argument("--resume-dir")
    start.add_argument("--resume-run-id")
    collect = sub.add_parser("collect")
    collect.add_argument("run_id")
    collect.add_argument("--batch-size", type=int, default=1)
    status = sub.add_parser("status")
    status.add_argument("run_id")
    sub.add_parser("export")
    leads = sub.add_parser("leads")
    leads.add_argument("--bucket")
    leads.add_argument("--status")
    leads.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        if args.command == "configure-codex":
            output({"config": configure_codex(Path.cwd(), args.data_dir),
                    "next": "Open this repository in Codex, trust the project, and reconnect its MCP server."})
            return
        if args.command == "serve":
            from .server import create_server
            # Project-local configuration supplies defaults to start_run via service.
            create_server(args.data_dir).run(transport="stdio", show_banner=False)
            return
        app = JobMarket(args.data_dir)
        if args.command == "init":
            from .resumes import load_resumes
            resumes = load_resumes(args.resume_dir.resolve())
            config_path = Path("config.local.toml")
            if not config_path.exists():
                config_path.write_text("resume_dir = " + json.dumps(str(args.resume_dir.resolve())) + "\n", encoding="utf-8")
            output({"database": str(app.data_dir / "jobs.sqlite3"), "resume_count": len(resumes),
                    "config": str(config_path.resolve())})
        elif args.command == "start":
            config = read_config(args.config)
            for name in ["target_count", "sort_by", "date_posted", "resume_dir"]:
                if getattr(args, name) is not None:
                    config[name] = getattr(args, name)
            output(app.start_run(None if args.resume_run_id else config, args.resume_run_id))
        elif args.command == "status":
            output(app.get_run_status(args.run_id))
        elif args.command == "export":
            output(app.sync_workbook())
        elif args.command == "leads":
            output(app.list_leads(args.bucket, args.status, args.limit))
        elif args.command in {"login", "collect"}:
            async def run():
                try:
                    if args.command == "login":
                        return await app.browser().login()
                    return await app.collect_posts(args.run_id, args.batch_size)
                finally:
                    await app.close()
            output(asyncio.run(run()))
    except (ValueError, OSError) as exc:
        print(f"job-market: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
