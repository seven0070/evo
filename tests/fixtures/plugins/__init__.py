"""Adversarial plugin fixtures for P5 (07 :140, 05 §2.2).

Each file is one way an extension can be unfriendly, and each is *imported by the test that needs it* -
never by ``evo_agent.plugins``, which has no importer at all. That is the point of the set: the runtime's
refusal to load these is what is under test, so the fixtures have to be loadable out-of-band for the refusal
to be observable.
"""
