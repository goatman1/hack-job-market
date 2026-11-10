"""Excel review views with an explicit, non-destructive edit round trip.

SQLite remains authoritative. Only status and notes are imported, and a hidden
baseline lets edits on one view coexist with untouched duplicate audit rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

STATUSES = ("new", "saved", "contacted", "applied", "dismissed", "closed")
TABS = ("Shortlist", "Needs Review", "Candidates", "Excluded")
COLUMNS = (
    ("role", "role"), ("posted date", "posted_date"),
    ("post original content", "content"), ("poster name", "author_name"),
    ("poster position", "author_position"), ("match score", "match_score"),
    ("reach out message", "reach_out_message"), ("lead ID", "lead_id"),
    ("company", "company"), ("track", "track"), ("post URL", "url"),
    ("profile URL", "author_url"), ("author location", "author_location"),
    ("verification", "verification"), ("selected resume", "selected_resume"),
    ("match explanation", "match_explanation"), ("eligibility flags", "eligibility_flags"),
    ("first seen", "first_seen"), ("last seen", "last_seen"),
    ("status", "status"), ("notes", "notes"), ("bucket", "bucket"),
    ("full text file", "full_text_file"),
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _cell(cell: Any, value: Any) -> None:
    """Force untrusted content to literal text, including =, +, -, and @."""
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        cell.value = value
    else:
        cell.value = ILLEGAL_CHARACTERS_RE.sub("", _text(value))[:32767]
        cell.data_type = "s"
    cell.alignment = Alignment(vertical="top", wrap_text=True)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".text-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_lead(lead: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    row = dict(lead)
    row["status"] = row.get("status") or "new"
    if row["status"] not in STATUSES:
        raise ValueError(f"Invalid status for {row.get('lead_id')}: {row['status']}")
    row["notes"] = _text(row.get("notes"))
    if len(row["notes"]) > 32767:
        raise ValueError(f"Notes exceed Excel's cell limit for {row.get('lead_id')}")
    content = _text(row.get("content"))
    if len(content) > 30000 or ILLEGAL_CHARACTERS_RE.search(content):
        # Never use a remote/user-controlled lead ID as a filesystem path.
        name = hashlib.sha256(str(row["lead_id"]).encode()).hexdigest() + ".txt"
        relative = Path("full_text") / name
        _atomic_text(output_dir / relative, content)
        row["full_text_file"] = relative.as_posix()
        if len(content) > 30000:
            row["content"] = content[:29500] + "\n[Full original content: see full text file column]"
    return row


def _style_sheet(sheet: Any, columns: tuple = COLUMNS) -> None:
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.zoomScale = 85
    for index, (_, key) in enumerate(columns, 1):
        header = sheet.cell(1, index)
        header.font = Font(color="FFFFFF", bold=True)
        header.fill = PatternFill("solid", fgColor="17365D")
        header.alignment = Alignment(wrap_text=True, vertical="center")
        width = 24
        if key in {"content", "reach_out_message", "notes", "verification", "match_explanation"}:
            width = 64 if key == "content" else 44
        elif key in {"match_score", "status", "track"}:
            width = 16
        elif key in {"role", "author_position"}:
            width = 32
        sheet.column_dimensions[get_column_letter(index)].width = width
        if key in {"status", "notes"}:
            header.fill = PatternFill("solid", fgColor="2E7548")
    sheet.row_dimensions[1].height = 32
    for row_index in range(2, sheet.max_row + 1):
        if row_index % 2 == 0:
            for cell in sheet[row_index]:
                cell.fill = PatternFill("solid", fgColor="F1F5F9")
    if columns == COLUMNS:
        index = next(i for i, (_, key) in enumerate(columns, 1) if key == "status")
        validation = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"',
                                    allow_blank=False)
        validation.errorTitle = "Choose a tracking status"
        validation.error = ", ".join(STATUSES)
        validation.showErrorMessage = True
        validation.errorStyle = "stop"
        sheet.add_data_validation(validation)
        validation.add(f"{get_column_letter(index)}2:{get_column_letter(index)}{max(2, sheet.max_row)}")


def export_workbook(path: Path, leads: list[dict], runs: list[dict]) -> Path:
    """Write review views atomically; return actual destination if Excel locks it."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = [str(lead.get("lead_id", "")) for lead in leads]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Every exported lead must have a unique nonempty lead_id")
    prepared = [_prepare_lead(lead, path.parent) for lead in leads]
    book = Workbook()
    book.remove(book.active)
    for name in TABS:
        sheet = book.create_sheet(name)
        for col, (label, _) in enumerate(COLUMNS, 1):
            _cell(sheet.cell(1, col), label)
        accepted = []
        for lead in prepared:
            bucket = str(lead.get("bucket", "")).lower().replace(" ", "_")
            selected = name == "Candidates"
            if name == "Shortlist":
                selected = bucket == "shortlist" and lead["status"] in {"new", "saved"}
            elif name == "Needs Review":
                selected = bucket in {"needs_review", "review"}
            elif name == "Excluded":
                selected = bucket == "excluded"
            if selected:
                accepted.append(lead)
        if name == "Shortlist":
            accepted.sort(key=lambda value: -(value.get("match_score") or 0))
        for index, lead in enumerate(accepted, 2):
            for col, (_, key) in enumerate(COLUMNS, 1):
                _cell(sheet.cell(index, col), lead.get(key))
                if key == "full_text_file" and lead.get(key):
                    sheet.cell(index, col).hyperlink = lead[key]
                    sheet.cell(index, col).font = Font(color="0563C1", underline="single")
        _style_sheet(sheet)
    run_sheet = book.create_sheet("Runs")
    run_keys = list(dict.fromkeys(key for run in runs for key in run)) or ["run_id", "status"]
    for index, key in enumerate(run_keys, 1):
        _cell(run_sheet.cell(1, index), key)
    for row, run in enumerate(runs, 2):
        for col, key in enumerate(run_keys, 1):
            _cell(run_sheet.cell(row, col), run.get(key))
    _style_sheet(run_sheet, tuple((key, key) for key in run_keys))
    baseline = book.create_sheet("_edit_baseline")
    baseline.sheet_state = "veryHidden"
    for col, key in enumerate(("lead_id", "status", "notes"), 1):
        _cell(baseline.cell(1, col), key)
    for index, lead in enumerate(prepared, 2):
        for col, key in enumerate(("lead_id", "status", "notes"), 1):
            _cell(baseline.cell(index, col), lead.get(key))
    book.active = 0
    descriptor, name = tempfile.mkstemp(prefix=".workbook-", suffix=".xlsx", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        book.save(temporary)
        try:
            os.replace(temporary, path)
            return path
        except PermissionError:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            fallback = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
            os.replace(temporary, fallback)
            return fallback
    finally:
        book.close()
        temporary.unlink(missing_ok=True)


def read_edits(path: Path) -> list[dict]:
    """Return only changed status/notes; reject conflicting edits across views."""
    path = Path(path)
    if not path.exists():
        return []
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        if "_edit_baseline" not in book.sheetnames:
            raise ValueError("Workbook has no edit baseline; cannot safely import edits")
        originals = {}
        for row in book["_edit_baseline"].iter_rows(min_row=2, values_only=True):
            lead_id, status, notes = row[:3]
            if lead_id is None:
                continue
            originals[str(lead_id)] = {"status": _text(status), "notes": _text(notes)}
        changes: dict[str, dict[str, str]] = {}
        for name in TABS:
            if name not in book.sheetnames:
                continue
            sheet = book[name]
            header_cells = next(sheet.iter_rows(min_row=1, max_row=1))
            headers = {cell.value: i for i, cell in enumerate(header_cells)}
            if not {"lead ID", "status", "notes"}.issubset(headers):
                raise ValueError(f"Missing edit columns in {name}")
            for row in sheet.iter_rows(min_row=2):
                lead_id = _text(row[headers["lead ID"]].value)
                if not lead_id:
                    continue
                if lead_id not in originals:
                    raise ValueError(f"Unknown lead ID in {name}: {lead_id}")
                for key in ("status", "notes"):
                    cell = row[headers[key]]
                    if cell.data_type == "f":
                        raise ValueError(f"Formulas are not allowed in editable {key} for {lead_id}")
                    value = _text(cell.value)
                    if key == "status" and value not in STATUSES:
                        raise ValueError(f"Invalid status for {lead_id}: {value}")
                    if value == originals[lead_id][key]:
                        continue
                    fields = changes.setdefault(lead_id, {})
                    if key in fields and fields[key] != value:
                        raise ValueError(f"Conflicting {key} edits for {lead_id}; reconcile workbook views")
                    fields[key] = value
        return [{"lead_id": lead_id, **fields}
                for lead_id, fields in changes.items()]
    finally:
        book.close()
