"""Bounded PDF text extraction in a separate, time-limited local process."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

EXTRACTION_VERSION = "pdf-layout.v1"


def _extract(raw: bytes) -> dict[str, Any]:
    from pypdf import PdfReader, overwrite_configuration

    if len(raw) > 5_000_000 or not raw.startswith(b"%PDF-"):
        raise ValueError("PDF signature or file size is unsupported.")
    # Apply decoder bounds before PdfReader touches streams. Checking the
    # resulting length alone would allocate an entire compressed bomb first.
    overwrite_configuration(maximum_declared_stream_length=5_000_000,
        array_based_stream_maximum_output_length=2_000_000,
        zlib_maximum_output_length=2_000_000,lzw_maximum_output_length=2_000_000,
        run_length_maximum_output_length=2_000_000,jbig2_maximum_output_length=2_000_000,
        image_maximum_buffer_size=2_000_000,flate_maximum_row_length=2_000_000,
        page_tree_maximum_entries=1000,xform_maximum_invocations_per_extraction=50)
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF: supply an accessible public document.")
    if not 1 <= len(reader.pages) <= 150:
        raise ValueError("PDF must contain between 1 and 150 pages.")
    metadata: dict[str, Any] = {"mime_type":"application/pdf", "original_hash":hashlib.sha256(raw).hexdigest(), "extraction_version":EXTRACTION_VERSION, "page_count":len(reader.pages), "pages":[], "warnings":[], "table_handling":"Fixed-width layout is retained; row/column meaning must still pass fact validation."}
    lines = ["ResearchCouncil archived PDF", f"Original SHA256: {metadata['original_hash']}", f"Extraction: {EXTRACTION_VERSION}; page/line locators refer to this immutable text."]
    characters = 0
    readable = 0
    for page_no, page in enumerate(reader.pages,1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 2_000_000:
            raise ValueError(f"PDF page {page_no} exceeds the decompressed content limit.")
        extracted = (page.extract_text(extraction_mode="layout",layout_mode_space_vertically=False) or "") if contents is not None else ""
        characters += len(extracted)
        if characters > 1_000_000:
            raise ValueError("PDF extracted text exceeds the local archive limit.")
        start = len(lines)+1
        lines.append(f"[PDF page {page_no}]")
        text_lines = [line.rstrip() for line in extracted.splitlines() if line.strip()]
        if text_lines:
            readable += 1
            lines.extend(text_lines)
        else:
            lines.append("[Text extraction unavailable on this page; image-only content was not read.]")
            metadata["warnings"].append(f"Page {page_no}: no readable text; OCR was not performed.")
        metadata["pages"].append({"page":page_no,"start_line":start,"end_line":len(lines),"readable":bool(text_lines)})
    if not readable:
        raise ValueError("PDF has no extractable text. Image-only pages require an accessible text source.")
    return {"content":"\n".join(lines), "metadata":metadata}


def extract_pdf(raw: bytes, *, timeout: float = 20) -> dict[str, Any]:
    try:
        process = subprocess.run([sys.executable,str(Path(__file__).resolve())],input=raw,capture_output=True,timeout=max(1,min(timeout,20)),check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF extraction exceeded its time limit.") from exc
    try:
        result = json.loads(process.stdout)
    except (ValueError,TypeError):
        raise ValueError("PDF could not be extracted within local resource limits.") from None
    if process.returncode or result.get("error"):
        raise ValueError("PDF extraction unavailable or unsupported: " + str(result.get("error") or "extraction failed"))
    return result


if __name__ == "__main__":
    try:
        import resource
        def bounded_limit(kind: int, maximum: int) -> None:
            _, hard = resource.getrlimit(kind)
            limit = min(maximum,hard) if hard != resource.RLIM_INFINITY else maximum
            resource.setrlimit(kind,(limit,limit))
        bounded_limit(resource.RLIMIT_CPU,15)
        # Bound decoded object/stream memory independently of compressed size.
        try:
            bounded_limit(resource.RLIMIT_DATA,768*1024*1024)
        except (ValueError,OSError):
            # macOS may expose RLIMIT_DATA but reject setting it. The worker
            # still has file/page/stream/text bounds and CPU/wall time limits.
            pass
        result = _extract(sys.stdin.buffer.read(5_000_001))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error":str(exc)[:300] or type(exc).__name__}))
        sys.exit(1)
