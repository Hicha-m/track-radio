"""Canonical station sets shared by rankings and exports."""


def station_ids(value: str) -> tuple[int, ...]:
    if not value:
        return ()
    parts = value.split(",")
    if len(parts) > 100 or any(not p.isascii() or not p.isdecimal() for p in parts):
        raise ValueError("Choisissez au maximum 100 identifiants de radios positifs.")
    result = tuple(sorted({int(p) for p in parts}))
    if any(not 1 <= p <= 2**63 - 1 for p in result):
        raise ValueError("Identifiant de radio invalide.")
    return result
