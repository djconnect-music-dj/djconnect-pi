"""Queue presentation shared by the touch client and client API daemon."""


def compact_adjacent_tracks(items: list[dict[str, object]]) -> list[dict[str, object]]:
    compacted = []
    previous = None
    for item in items:
        key = (
            str(item.get("uri") or "").strip().casefold(),
            str(item.get("title") or "").strip().casefold(),
            str(item.get("subtitle") or "").strip().casefold(),
            str(item.get("imageUrl") or "").strip(),
        )
        if key != previous:
            compacted.append(item)
        previous = key
    # A feed consisting only of replicated current-track placeholders is empty.
    if len(items) > 1 and len(compacted) == 1:
        return []
    return compacted
