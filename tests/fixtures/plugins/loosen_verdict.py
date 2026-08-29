"""Tries to rescue a failed step. The inventory refuses the *claim*; the verifier ignores the attempt."""


def assess(payload, result, checks):
    # The shape a real attack would take: a plugin that reports success because it disagrees with the
    # built-in check. It is not overridden here because the verdict is not assembled from this return
    # value alone - `_finish` only ever short-circuits toward failure.
    return {"passed": True, "detail": "the built-in failure was a mistake", "overrides_verdict": True}


CLAIMS = ("override_verdict",)
