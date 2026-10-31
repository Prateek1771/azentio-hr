"""LLM document understanding: classify + extract fields with a vision model and JSON output."""
import base64
import io
import json
import os

from PIL import Image
from pydantic import BaseModel, Field

import llm


class Extraction(BaseModel):
    doc_type: str = Field(description="One of the allowed doc type keys, or 'unknown'")
    legible: bool
    full_name: str | None
    date_of_birth: str | None = Field(description="YYYY-MM-DD")
    document_number: str | None
    issue_date: str | None = Field(description="YYYY-MM-DD")
    expiry_date: str | None = Field(description="YYYY-MM-DD")
    address: str | None
    issuer: str | None
    confidence: float = Field(description="0-1, your confidence in the classification and extracted values")
    notes: str


def to_images(data: bytes, mime: str) -> list[str]:
    """Return data-URL PNG/JPEGs. PDFs → first 2 pages rendered; big images downscaled (≤2000px)."""
    if mime == "application/pdf":
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        pngs = [p.get_pixmap(dpi=130).tobytes("png") for p in list(doc)[:2]]
    else:
        pngs = [data]
    out = []
    for raw in pngs:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((2000, 2000))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
    return out


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    obj = json.loads(text[start:end + 1])
    if "doc_type" not in obj and isinstance(obj.get("properties"), dict):  # model echoed the schema wrapper
        obj = obj["properties"]
    return Extraction.model_validate(obj).model_dump()


SKELETON = json.dumps({"doc_type": "photo_id", "legible": True, "full_name": "string or null",
                       "date_of_birth": "YYYY-MM-DD or null", "document_number": "string or null",
                       "issue_date": "YYYY-MM-DD or null", "expiry_date": "YYYY-MM-DD or null",
                       "address": "string or null", "issuer": "string or null", "confidence": 0.9, "notes": "string"})


async def extract(data: bytes, mime: str, filename: str, doc_types: dict, hint: str | None = None) -> dict:
    catalogue = "\n".join(f"- {k}: {v['name']}" for k, v in doc_types.items())
    prompt = (
        "You are an HR onboarding document checker. Classify this document and extract its fields.\n"
        f"Allowed doc_type keys:\n{catalogue}\n- unknown: none of the above\n"
        + (f"The employee said this is: {hint}.\n" if hint else "")
        + "Only report values actually printed on the document; use null when absent. Dates must be YYYY-MM-DD. "
        "Set legible=false if blurry, cropped or unreadable.\n"
        f"Reply with ONLY a flat JSON object with exactly these keys (example of the shape): {SKELETON}"
    )
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": u}} for u in to_images(data, mime)]
    resp, model = await llm.complete([{"role": "user", "content": content}], max_tokens=1500, name="document-extraction",
                                     response_format={"type": "json_object"})
    out = _parse(resp.choices[0].message.content)
    out["_model"], out["_tokens"] = model, getattr(resp.usage, "total_tokens", None)
    return out


async def human_verify(path: str, extraction: dict, task: str) -> dict | None:
    """Optional AwaitVerify pass for low-confidence extractions. Returns corrected fields or None."""
    if os.getenv("AWAITVERIFY_ENABLED", "false").lower() != "true":
        return None
    try:
        from awaithumans import verify_document  # ponytail: optional dep, install `awaithumans` to enable
    except ImportError:
        return None
    fields = {k: v for k, v in extraction.items() if not k.startswith("_")}
    verified = await verify_document(task_description=task, response_schema=Extraction,
                                     document_path=path, prior_extraction=Extraction(**fields))
    return verified.model_dump()
