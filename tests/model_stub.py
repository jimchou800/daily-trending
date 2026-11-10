"""External model response fixtures for live pipeline integration tests."""

import json


def classification_response(payload, *, is_ai=None, evidence=""):
    entries = json.loads(payload["messages"][0]["content"].split("\n", 1)[1])
    result = {
        "classifications": [
            {
                "repository": entry["repository"],
                "is_ai": is_ai,
                "ai_relevance_evidence": evidence,
                "ai_core_purpose": "tool" if is_ai else None,
            }
            for entry in entries
        ]
    }
    return {"choices": [{"message": {"content": json.dumps(result)}}]}
