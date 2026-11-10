from html import escape
from zipfile import ZipFile

import pytest

from job_market.resumes import load_resumes


def docx(path, lines):
    paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in lines)
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '</w:body></w:document>'
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def test_docx_preferred_over_pdf_with_source_evidence(tmp_path):
    docx(tmp_path / "RL.docx", ["Candidate", "me@example.com | 555-555-5555", "PhD in Engineering", "PyTorch RL research"])
    (tmp_path / "RL.pdf").write_bytes(b"not used")
    result = load_resumes(tmp_path)
    assert len(result) == 1
    resume = result[0]
    assert resume["id"] == "RL" and resume["path"].endswith("RL.docx")
    assert len(resume["digest"]) == 64
    assert "me@example.com" not in resume["content"]
    assert resume["evidence_lines"][3] == {"id": "L0004", "text": "PyTorch RL research"}


def test_variants_are_not_merged(tmp_path):
    docx(tmp_path / "Agent.docx", ["Paper submitted to ICLR"])
    docx(tmp_path / "OR.docx", ["Paper under review at AISTATS"])
    records = load_resumes(tmp_path)
    assert len(records) == 2
    assert "AISTATS" not in records[0]["content"]
    assert "ICLR" not in records[1]["content"]


def test_pdf_fallback_for_corrupted_docx(tmp_path, monkeypatch):
    (tmp_path / "Resume.docx").write_bytes(b"corrupt")
    (tmp_path / "Resume.pdf").write_bytes(b"pdf stub")
    monkeypatch.setattr("job_market.resumes._pdf_text", lambda path: "Python research")
    record = load_resumes(tmp_path)[0]
    assert record["path"].endswith("Resume.pdf")
    assert record["content"] == "Python research"


def test_missing_empty_or_unreadable_resumes_report_problem(tmp_path):
    with pytest.raises(ValueError, match="No DOCX"):
        load_resumes(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        load_resumes(tmp_path / "missing")
    docx(tmp_path / "Empty.docx", [])
    with pytest.raises(ValueError, match="Could not read"):
        load_resumes(tmp_path)
