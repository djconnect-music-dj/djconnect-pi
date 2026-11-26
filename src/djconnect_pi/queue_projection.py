"""Queue presentation shared by the touch client and client API daemon."""


def _display_name(item: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            joined = ", ".join(name.strip() for name in value if name.strip())
            if joined:
                return joined
    return ""


def queue_artist_and_album(item: dict[str, object]) -> tuple[str, str]:
    artist = _display_name(item, ("artist", "artist_name", "artistName", "subtitle",
                                  "media_artist", "mediaArtist", "artists"))
    album = _display_name(item, ("album", "album_name", "albumName", "media_album", "mediaAlbum"))
    return artist, album


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
