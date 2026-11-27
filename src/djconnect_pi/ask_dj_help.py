"""Read-only presentation eligibility for the pinned Core Ask DJ catalogue."""
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from unicodedata import category
from uuid import uuid4

HELP_ROUTE = "ask_dj/help-context"
HELP_RESPONSE_BYTES = 16_384
CATALOGUE_ID = "ask_dj_examples_v1"
CATALOGUE_REVISION = 2
CATALOGUE_SHA256 = "5c8c52c044782f18dae53f10d6babe02b6c6e414131e52f16255d65ad60f730b"
LANGUAGES = frozenset({"en", "nl", "de", "fr", "es"})
_OBSERVATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@lru_cache(maxsize=1)
def _catalogue():
    raw = files("djconnect_pi").joinpath(CATALOGUE_ID + ".json").read_bytes()
    if sha256(raw).hexdigest() != CATALOGUE_SHA256:
        raise ValueError("invalid Ask DJ catalogue")
    return json.loads(raw)


def _opaque_id(value, limit):
    return (isinstance(value, str) and 0 < len(value.encode("utf-8")) <= limit
            and value == value.strip()
            and all(category(char) not in {"Cc", "Cf"} for char in value))


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate Ask DJ Help field")
    return fields


def _exact_int(value, expected):
    return type(value) is int and value == expected


def compatible_catalogue(data):
    capabilities = data.get("capabilities")
    contracts = data.get("contract_versions")
    descriptor = data.get("ask_dj_examples")
    return (data.get("success") is True and isinstance(capabilities, dict)
            and capabilities.get("ask_dj_examples") is True and isinstance(contracts, dict)
            and _exact_int(contracts.get("ask_dj_examples"), 1) and isinstance(descriptor, dict)
            and _exact_int(descriptor.get("schema_version"), 1)
            and _exact_int(descriptor.get("revision"), CATALOGUE_REVISION)
            and descriptor.get("catalogue_id") == CATALOGUE_ID
            and descriptor.get("sha256") == CATALOGUE_SHA256 and descriptor.get("qualified") is True)


def _eligible_entries(language, surface, eligible_ids, music_backend, supported_features, session_features, contracts):
    flags = {"paired", "online", "backend_available"}
    flags.update(key for key in supported_features if key.startswith("supports_"))
    flags.update(session_features)
    if music_backend == "spotify_direct":
        flags.add("spotify")
    for entry in _catalogue()["entries"]:
        if (entry["id"] not in eligible_ids or surface not in entry["surfaces"]
                or any(not _exact_int(contracts.get(key), value) for key, value in entry["contracts"].items())
                or entry["publication"] != "qualified_v1" or not (set(entry["requires"]) - {"agent_available"}) <= flags
                or entry["qualification"][language]["state"] != "qualified_owner_route"):
            continue
        yield entry


def _help_sections(language, entries):
    catalogue = _catalogue()
    prompts = {}
    for entry in sorted(entries, key=lambda entry: (entry["help_rank"], entry["id"])):
        text = entry["templates"][language]
        placeholder = ""
        for slot in entry["slots"]:
            label = catalogue["slot_labels"][slot][language]
            text = text.replace("{" + slot + "}", label)
            if not placeholder or text.index(label) < text.index(placeholder):
                placeholder = label
        prompts.setdefault(entry["group"], []).append({"id": entry["id"], "text": text, "placeholder": placeholder})
    return [{"title": group["labels"][language], "examples": prompts[group["id"]]}
            for group in catalogue["groups"] if group["id"] in prompts]


def _safe_artist(artist):
    return (0 < len(artist) <= 160 and artist == artist.strip()
            and not any(category(char) in {"Cc", "Cf"} or char in "{}[]" for char in artist))


def _suggestion_candidates(language, entries, artist):
    candidates = []
    for entry in entries:
        text = entry["templates"][language]
        if entry["slots"]:
            if entry["slots"] != ["artist"] or not _safe_artist(artist):
                continue
            text = text.replace("{artist}", artist)
        if any(char in text for char in "{}[]"):
            continue
        candidates.append({"id": entry["id"], "intent": entry["intent"], "group": entry["group"],
                           "contextual": entry["contextual"], "text": text})
    return candidates


def project_help(language, surface, eligible_ids, music_backend, supported_features, session_features, contracts, artist):
    entries = list(_eligible_entries(language, surface, eligible_ids, music_backend, supported_features, session_features, contracts))
    return _help_sections(language, entries), _suggestion_candidates(language, entries, artist)


def demo_help(language, surface):
    """Fixed local examples; these never represent producer eligibility."""
    catalogue = _catalogue()
    session = surface == "active_session"
    reference_ids = ("play.artist", "play.cooking", "discover.artist_tracks", "discover.artist_albums")
    reference_ids += ("listen.current", "listen.artist") if session else ("listen.queue", "listen.outputs")
    suggestion_ids = ("play.artist", "discover.artist_tracks", "listen.artist" if session else "listen.queue")
    entries = [entry for entry in catalogue["entries"] if surface in entry["surfaces"] and entry["id"] in reference_ids]
    examples = {item["id"]: item for item in _suggestion_candidates(language, entries, "M83")}
    grouped = {}
    for entry_id in reference_ids:
        if entry_id in examples:
            item = examples[entry_id]
            grouped.setdefault(item["group"], []).append({"id": entry_id, "text": item["text"], "placeholder": ""})
    sections = [{"title": group["labels"][language], "examples": grouped[group["id"]]}
                for group in catalogue["groups"] if group["id"] in grouped]
    suggestions = [examples[entry_id] for entry_id in suggestion_ids if entry_id in examples]
    return sections, suggestions if len(suggestions) == 3 else []


def select_suggestions(candidates, epoch):
    ordered = sorted(candidates, key=lambda item: sha256(f'{epoch}:{item["id"]}'.encode()).digest())
    contextual = next((item for item in ordered if item["contextual"]), None)
    chosen = [contextual] if contextual else []
    while len(chosen) < 3:
        intents = {item["intent"] for item in chosen}
        groups = {item["group"] for item in chosen}
        remaining = [item for item in ordered if item["intent"] not in intents]
        if not remaining:
            return []
        chosen.append(min(remaining, key=lambda item: item["group"] in groups))
    return chosen


@dataclass(frozen=True)
class HelpRequest:
    surface: str
    language: str
    session_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()), init=False)

    def __post_init__(self):
        if (self.surface not in {"ask_dj", "active_session"} or self.language not in LANGUAGES
                or (self.session_id is None and self.surface == "active_session")
                or (self.session_id is not None and not _opaque_id(self.session_id, 256))):
            raise ValueError("invalid Ask DJ Help request")

    def query(self):
        fields = {
            "schema_version": 1, "request_id": self.request_id, "surface": self.surface,
            "language": self.language, "catalogue_id": CATALOGUE_ID,
            "catalogue_revision": CATALOGUE_REVISION, "catalogue_sha256": CATALOGUE_SHA256,
        }
        if self.session_id is not None:
            fields["session_id"] = self.session_id
        return fields


@dataclass(frozen=True)
class HelpObservation:
    available: bool
    track_id: str | None
    eligible_ids: tuple[str, ...]

    @classmethod
    def decode(cls, raw, request):
        if len(raw) > HELP_RESPONSE_BYTES:
            raise ValueError("oversized Ask DJ Help observation")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
        if (not isinstance(data, dict) or set(data) != {
            "schema_version", "request_id", "surface", "language", "session_id",
            "track_id", "catalogue", "available", "observation_id", "eligible_entry_ids",
        } or not _exact_int(data["schema_version"], 1)
                or data["request_id"] != request.request_id or data["surface"] != request.surface
                or data["language"] != request.language or data["session_id"] != request.session_id
                or type(data["available"]) is not bool):
            raise ValueError("invalid Ask DJ Help observation")

        descriptor = data["catalogue"]
        if descriptor is not None and (not isinstance(descriptor, dict) or set(descriptor) != {
            "schema_version", "catalogue_id", "revision", "sha256", "qualified",
        } or not _exact_int(descriptor["schema_version"], 1)
                or not _exact_int(descriptor["revision"], CATALOGUE_REVISION)
                or descriptor["catalogue_id"] != CATALOGUE_ID or descriptor["sha256"] != CATALOGUE_SHA256
                or type(descriptor["qualified"]) is not bool):
            raise ValueError("invalid Ask DJ Help descriptor")

        observation, track_id, ids = data["observation_id"], data["track_id"], data["eligible_entry_ids"]
        if not data["available"]:
            if track_id is not None or observation is not None or ids != []:
                raise ValueError("invalid unavailable Ask DJ Help observation")
            return cls(False, None, ())

        if (descriptor is None or not descriptor["qualified"] or not isinstance(observation, str)
                or not _OBSERVATION_ID.fullmatch(observation) or not isinstance(ids, list)
                or len(ids) > 14 or any(not isinstance(entry_id, str) for entry_id in ids)
                or (track_id is not None and (request.session_id is None or not _opaque_id(track_id, 1024)))):
            raise ValueError("invalid available Ask DJ Help observation")
        entries = [entry for entry in _catalogue()["entries"] if entry["id"] in ids]
        if ids != [entry["id"] for entry in entries] or (
                track_id is None and any("current_track" in entry["requires"] for entry in entries)):
            raise ValueError("invalid Ask DJ Help eligibility")
        return cls(True, track_id, tuple(ids))
