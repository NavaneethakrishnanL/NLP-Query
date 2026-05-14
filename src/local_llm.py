from __future__ import annotations

import json
import urllib.error
import urllib.request

from schema_model import SemanticCatalog


def ollama_available(base_url: str = "http://localhost:11434") -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def suggest_query_plan(question: str, catalog: SemanticCatalog, model: str, base_url: str = "http://localhost:11434") -> str:
    """Ask a local Ollama model for a query-building suggestion.

    This never calls a paid API. The response is shown as guidance and the app
    still uses the deterministic builder for final SQL.
    """
    schema_summary = {
        table.name: {
            "columns": [f"{column.name} {column.type}" for column in table.columns],
            "metrics": [metric.name for metric in catalog.metrics],
        }
        for table in catalog.tables
    }
    prompt = f"""
You help map business questions to a deterministic BigQuery query builder.
Return concise JSON-like suggestions with base_table, joined_tables, dimensions,
metrics, filters, and notes. Do not invent columns.

Schema:
{json.dumps(schema_summary, indent=2)}

Question:
{question}
""".strip()

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("response", "").strip()
