"""A plugin that behaves: returns a real boolean verdict and asks for nothing."""


def assess(payload, result, checks):
    step = payload.get("step") or {}
    if "danger" in str(step.get("description", "")).lower():
        return {"passed": False, "detail": "fixture policy: a step that says 'danger' is refused by name"}
    return {"passed": True, "detail": "fixture policy: nothing to add"}
