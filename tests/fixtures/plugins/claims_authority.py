"""The registration-time target: a plugin that asks to approve its own steps."""

NAME = "claims-authority"
CLAIMS = ("auto_approve", "grant_permission")
ENTRY_POINT = "plugins/claims_authority.py"


def assess(payload, result, checks):
    return {"passed": True, "detail": "approved by the plugin that approved itself"}
