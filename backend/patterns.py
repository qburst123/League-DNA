"""Descriptive, team-relative fingerprints. No predictions or wagering actions."""
from __future__ import annotations

KINDS = {
    "parity": {"name": "Odd / Even", "short": "O / E", "description": "Parity of the combined full-time goals. Zero is even.", "labels": {"O": "ODD", "E": "EVEN"}},
    "btts": {"name": "Both teams to score", "short": "BTTS", "description": "Yes only when both teams score at least one full-time goal.", "labels": {"Y": "YES", "N": "NO"}},
    "dnb": {"name": "Draw no bet", "short": "DNB", "description": "Team-relative win or loss; a draw is a separate VOID state, not a win.", "labels": {"W": "WIN", "L": "LOSS", "V": "VOID"}},
    "total": {"name": "Over / Under 2.5", "short": "O / U", "description": "Combined full-time goals: over means three or more, under means two or fewer.", "labels": {"O": "O2.5", "U": "U2.5"}},
}


def outcome(kind: str, home: int, away: int, is_home: bool = True) -> str:
    if home is None or away is None:
        raise ValueError("Unfinished scores cannot form a fingerprint")
    if kind == "parity":
        return "O" if (home + away) % 2 else "E"
    if kind == "btts":
        return "Y" if home > 0 and away > 0 else "N"
    if kind == "total":
        return "O" if home + away >= 3 else "U"
    if kind == "dnb":
        if home == away:
            return "V"
        return "W" if (home > away) == is_home else "L"
    raise ValueError("Unknown blueprint")


def scan_windows(prefix: str, historical: str, minimum: float = 100.0):
    """Slide a fixed, contiguous prefix over ONE season, never over a gap."""
    n = len(prefix)
    if not n or "." in prefix or n > len(historical):
        return [], 0
    found, scanned = [], 0
    for offset in range(len(historical) - n + 1):
        window = historical[offset:offset + n]
        if "." in window:
            continue
        scanned += 1
        equal = sum(a == b for a, b in zip(prefix, window))
        similarity = equal / n * 100
        if similarity + 1e-9 >= minimum:
            found.append({"start": offset + 1, "end": offset + n, "matched": equal,
                          "length": n, "similarity": round(similarity, 2), "exact": equal == n})
    return found, scanned
