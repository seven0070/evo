"""Fails loudly. A plugin that raises must land as a failed check, never as a skipped one."""


def assess(payload, result, checks):
    raise RuntimeError("fixture plugin exploded")
