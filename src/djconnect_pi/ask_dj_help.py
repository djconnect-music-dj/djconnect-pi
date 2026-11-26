"""Read-only presentation eligibility for the pinned Core Ask DJ catalogue."""
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
import json
import re
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
