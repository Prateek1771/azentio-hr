"""Deterministic document checks. The LLM extracts; these rules decide."""
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 10 * 1024 * 1024
LOW_CONFIDENCE = 0.6


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", s.lower()).split()))


def names_match(a: str, b: str) -> bool:
    """Order-insensitive; tolerates a missing middle name and small OCR noise."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.85


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def check_file(mime: str, size: int) -> list[str]:
    issues = []
    if mime not in ALLOWED_MIME:
        issues.append(f"Incorrect file type ({mime or 'unknown'}). Please send a PDF, JPG or PNG.")
    if size > MAX_BYTES:
        issues.append("File is larger than 10 MB. Please send a smaller scan.")
    return issues


def evaluate(ex: dict, emp: dict, doc_type: dict) -> dict:
    """ex: extraction dict. Returns {status, issues[], mismatches[]}."""
    issues, mismatches = [], []
    if not ex.get("legible", True):
        issues.append("The document is illegible or cut off. Please send a clear scan with all corners visible.")
    for f in doc_type.get("fields", []):
        if not ex.get(f):
            issues.append(f"Could not find the {f.replace('_', ' ')} on the document.")
    if ex.get("full_name") and not names_match(ex["full_name"], emp["name"]):
        mismatches.append({"field": "name", "expected": emp["name"], "found": ex["full_name"]})
        issues.append(f"Name on document ('{ex['full_name']}') does not match your profile ('{emp['name']}').")
    dob_doc, dob_emp = parse_date(ex.get("date_of_birth")), parse_date(emp.get("date_of_birth"))
    if dob_doc and dob_emp and dob_doc != dob_emp:
        mismatches.append({"field": "date_of_birth", "expected": dob_emp.isoformat(), "found": dob_doc.isoformat()})
        issues.append(f"Date of birth on document ({dob_doc}) does not match your profile ({dob_emp}).")
    exp = parse_date(ex.get("expiry_date"))
    joining = parse_date(emp.get("joining_date")) or date.today()
    if doc_type.get("expires") and exp and exp < joining:
        issues.append(f"The document expired on {exp} (before your joining date {joining}). Please send a valid one.")

    if issues:
        status = "Correction Requested"
    elif float(ex.get("confidence", 1)) < LOW_CONFIDENCE:
        status = "Under Review"
    else:
        status = "Accepted"
    return {"status": status, "issues": issues, "mismatches": mismatches}
