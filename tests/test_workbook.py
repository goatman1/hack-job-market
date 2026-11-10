from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from job_market.workbook import export_workbook, read_edits


def lead(**changes):
    return {"lead_id": "lead-1", "role": "Research scientist", "bucket": "shortlist",
            "content": "Hiring for my team", "status": "new", "notes": "", "match_score": 85,
            **changes}


def edit(book, sheet, key, value, row=2):
    columns = {cell.value: cell.column for cell in book[sheet][1]}
    book[sheet].cell(row, columns[key], value)


def test_roundtrip_and_edit_only_one_duplicate_view(tmp_path):
    path = export_workbook(tmp_path / "leads.xlsx", [lead()], [{"run_id": "run-1"}])
    assert read_edits(path) == []
    book = load_workbook(path)
    assert book.sheetnames == ["Shortlist", "Needs Review", "Candidates", "Excluded", "Runs", "_edit_baseline"]
    assert book["_edit_baseline"].sheet_state == "veryHidden"
    edit(book, "Shortlist", "status", "saved")
    edit(book, "Candidates", "notes", "Follow up next week")
    book.save(path)
    book.close()
    assert read_edits(path) == [{"lead_id": "lead-1", "status": "saved", "notes": "Follow up next week"}]


def test_conflicting_edits_fail_without_silent_overwrite(tmp_path):
    path = export_workbook(tmp_path / "leads.xlsx", [lead()], [])
    book = load_workbook(path)
    edit(book, "Shortlist", "status", "saved")
    edit(book, "Candidates", "status", "dismissed")
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="Conflicting status"):
        read_edits(path)


def test_invalid_status_and_formula_are_rejected(tmp_path):
    path = export_workbook(tmp_path / "leads.xlsx", [lead()], [])
    book = load_workbook(path)
    edit(book, "Shortlist", "status", "wrong")
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="Invalid status"):
        read_edits(path)
    book = load_workbook(path)
    edit(book, "Shortlist", "status", "new")
    edit(book, "Candidates", "notes", "=1+1")
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="Formulas"):
        read_edits(path)


def test_formula_injection_exports_as_literal_text(tmp_path):
    path = export_workbook(tmp_path / "leads.xlsx", [lead(content='=HYPERLINK("https://bad.test")', notes="+cmd")], [])
    book = load_workbook(path, data_only=False)
    assert book["Shortlist"]["C2"].value.startswith("=HYPERLINK")
    assert book["Shortlist"]["C2"].data_type == "s"
    book.close()
    assert read_edits(path) == []


def test_long_content_uses_safe_sidecar(tmp_path):
    text = "a" * 35000
    path = export_workbook(tmp_path / "leads.xlsx", [lead(lead_id="../../outside", content=text)], [])
    files = list((tmp_path / "full_text").glob("*.txt"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == text
    book = load_workbook(path)
    assert len(book["Candidates"]["C2"].value) < 32767
    assert book["Candidates"]["W2"].value.startswith("full_text/")
    book.close()


def test_tracked_leads_do_not_reappear_on_shortlist(tmp_path):
    leads = [lead(lead_id=status, status=status) for status in ("new", "saved", "contacted", "applied", "dismissed", "closed")]
    path = export_workbook(tmp_path / "leads.xlsx", leads, [])
    book = load_workbook(path)
    assert book["Shortlist"].max_row == 3
    assert book["Candidates"].max_row == 7
    book.close()


def test_locked_destination_returns_timestamped_export(tmp_path):
    path = tmp_path / "leads.xlsx"
    path.write_text("locked original")
    import os
    replace = os.replace

    def locked(source, target):
        if Path(target) == path:
            raise PermissionError("Excel has workbook open")
        return replace(source, target)

    with patch("job_market.workbook.os.replace", side_effect=locked):
        actual = export_workbook(path, [lead()], [])
    assert actual != path and actual.exists()
    assert path.read_text() == "locked original"
    assert read_edits(actual) == []


def test_missing_baseline_and_duplicate_ids_fail(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        export_workbook(tmp_path / "leads.xlsx", [lead(), lead()], [])
    path = export_workbook(tmp_path / "leads.xlsx", [lead()], [])
    book = load_workbook(path)
    del book["_edit_baseline"]
    book.save(path)
    book.close()
    with pytest.raises(ValueError, match="baseline"):
        read_edits(path)


def test_import_returns_only_changed_fields(tmp_path):
    path = export_workbook(tmp_path / "leads.xlsx", [lead(notes="baseline")], [])
    book = load_workbook(path)
    edit(book, "Shortlist", "status", "saved")
    book.save(path)
    book.close()
    assert read_edits(path) == [{"lead_id": "lead-1", "status": "saved"}]


def test_service_remembers_fallback_and_imports_its_edits(tmp_path):
    import json
    import os

    from job_market.service import JobMarket
    app = JobMarket(tmp_path)
    original = tmp_path / "leads.xlsx"
    first = app.sync_workbook()
    assert Path(first["path"]) == original
    replacement = os.replace

    def locked(source, target):
        if Path(target) == original:
            raise PermissionError("Excel lock")
        return replacement(source, target)

    with patch("job_market.workbook.os.replace", side_effect=locked):
        result = app.sync_workbook()
    fallback = Path(result["path"])
    assert fallback != original
    assert json.loads((tmp_path / "workbook.json").read_text())["active_workbook"] == fallback.name
    with patch("job_market.workbook.read_edits", return_value=[]) as reader:
        app.sync_workbook()
        reader.assert_called_once_with(fallback)
    app.store.db.close()


@pytest.mark.parametrize("filename", ["../outside.xlsx", "C:/outside.xlsx", "other.xlsx", "leads.xlsx/../secrets"])
def test_service_rejects_tampered_workbook_metadata(tmp_path, filename):
    import json

    from job_market.service import JobMarket
    app = JobMarket(tmp_path)
    (tmp_path / "workbook.json").write_text(json.dumps({"active_workbook": filename}))
    with pytest.raises(ValueError, match="metadata"):
        app.sync_workbook()
    app.store.db.close()
