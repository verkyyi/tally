"""Reversible publication holds. Raw observations and recipe trials remain untouched.

These two incident-specific holds require an explicit reviewed release, not an
automatic successful read. Removing a hold must not republish the invalid changes.
"""

LABEL = "Pending verification"
PENDING = {
    235: {
        "title": "Tavily · headcount",
        "reason": "The cited report describes an approximate headcount on 2025-08-06, not a verified current employee count.",
        "observation_id": 26893,
        "change_id": 490,
    },
    314: {
        "title": "Profound · employee count",
        "reason": "The cited report dated 2026-02-24 says fewer than 120 employees, an upper bound rather than an exact current employee count.",
        "observation_id": 27465,
        "change_id": 495,
    },
}
INVALID_CHANGES = frozenset({490, 495})


def pending(mid):
    return mid in PENDING


def status(mid):
    if not pending(mid):
        return {}
    return {"publication_status": "pending_verification", "publication_label": LABEL,
            "publication_reason": PENDING[mid]["reason"]}


def visible_change(row):
    return not pending(row["metric_id"]) and row.get("id") not in INVALID_CHANGES


def corrections():
    return [{"metric_id": mid, "title": item["title"], "at": "2026-11-19T00:53:00Z",
             "what": "Pending verification: current value and comparison withheld.",
             "why": item["reason"] + " Original observations, quotes and recipe trials are retained; no replacement value has been inferred."}
            for mid, item in PENDING.items()]
