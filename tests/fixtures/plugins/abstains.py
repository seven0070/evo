"""Returns non-boolean verdicts. Ambiguity is a plugin bug, and the answer must not be "fine then"."""


def assess(payload, result, checks):
    return {"passed": None, "detail": "hard to say"}
