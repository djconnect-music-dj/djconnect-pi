"""Queue presentation shared by the touch client and client API daemon."""


def compact_adjacent_tracks(items: list[dict[str, object]]) -> list[dict[str, object]]:
    compacted = []
    previous = None
    signatures = set()
    for item in items:
        signature = (
            str(item.get("uri") or "").strip().casefold(),
            str(item.get("title") or "").strip().casefold(),
            str(item.get("subtitle") or "").strip().casefold(),
        )
        signatures.add(signature)
        uri = signature[0]
        index = item.get("index")
        position = (item.get("contextUri"), index) if uri and type(index) is int and index >= 0 else None
        key = (
            signature,
            str(item.get("album") or "").strip().casefold(),
            str(item.get("imageUrl") or "").strip(),
            position,
        )
        if not uri or key != previous:
            compacted.append(item)
        previous = key
    # Keep an actionable row if the provider supplied a queue position.
    if len(items) > 1 and len(compacted) == 1 and position is None:
        return []
    if len(items) >= 4 and len(signatures) == 1 and not any(signature[0] for signature in signatures):
        return []
    return compacted
