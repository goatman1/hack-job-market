"""Load résumé variants with stable evidence references, without merging claims."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


def _docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n".join("".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
                     for paragraph in root.findall(".//w:p", ns))


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except PyPdfError as exc:
        raise ValueError(f"PDF extraction failed: {exc}") from exc


def _evidence_text(text: str) -> str:
    lines = []
    for original in text.splitlines():
        line = re.sub(r"\s+", " ", original).strip()
        if not line:
            continue
        # Remove contact headers only; leave the source document unmodified.
        if "@" in line or re.search(r"linkedin\.com/in/", line, re.IGNORECASE):
            line = "[Contact details omitted]"
        lines.append(line)
    return "\n".join(lines)


def load_resumes(directory: Path) -> list[dict]:
    """Prefer DOCX per filename stem, falling back to its PDF if necessary.

    ``digest`` identifies the source bytes. Evidence IDs such as L0001 are stable
    within that digest; consumers must store both variant ID and digest.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"Resume directory does not exist: {directory}")
    variants: dict[str, dict[str, Path]] = {}
    for path in sorted(directory.iterdir()):
        suffix = path.suffix.lower()
        if path.is_file() and suffix in {".docx", ".pdf"} and not path.name.startswith("~$"):
            variants.setdefault(path.stem, {})[suffix] = path
    result = []
    for identifier, files in sorted(variants.items()):
        errors = []
        for suffix in (".docx", ".pdf"):
            path = files.get(suffix)
            if path is None:
                continue
            try:
                raw = _docx_text(path) if suffix == ".docx" else _pdf_text(path)
                content = _evidence_text(raw)
                if not content:
                    raise ValueError("No extractable text; scanned PDFs require OCR")
            except (OSError, ValueError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            result.append({
                "id": identifier,
                "path": str(path),
                "content": content,
                "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
                "evidence_lines": [{"id": f"L{index:04d}", "text": line}
                                   for index, line in enumerate(content.splitlines(), 1)],
            })
            break
        else:
            raise ValueError(f"Could not read resume {identifier}: {'; '.join(errors)}")
    if not result:
        raise ValueError(f"No DOCX or PDF resumes found in {directory}")
    return result
