"""Paired, Profile-bound Session HTTP transport shared by both Pi targets.

Core owns every identity, ordering, grant and effect. This transport has no
persistent Session cache and never automatically retries a write.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import requests

from .ask_dj_help import HELP_RESPONSE_BYTES, HELP_ROUTE, HelpObservation, HelpRequest
from .config import CLIENT_TYPE, PROTOCOL_VERSION
from .i18n import locale_for_language

PREFIX = "/api/djconnect/v1/"
# Pi resource ceilings, not producer wire limits or display permissions. History
# allows 50 x 32768-character escaped Unicode bodies (~19 MiB) plus metadata.
JSON_CONTROL_BYTES = 2 * 1024 * 1024
JSON_SESSION_BYTES = 8 * 1024 * 1024
JSON_HISTORY_BYTES = 32 * 1024 * 1024
JSON_CHUNK_BYTES = 16 * 1024


def response_budget(route: str) -> int:
    if route == HELP_ROUTE:
        return HELP_RESPONSE_BYTES
    if route in {"session/history", "ask_dj/history"} or (route.startswith("session/history/") and route != "session/history/open"):
        return JSON_HISTORY_BYTES
    if route in {"session/active", "session/broadcast/snapshot", "session/start", "ask_dj/message"}:
        return JSON_SESSION_BYTES
    return JSON_CONTROL_BYTES


class _JSONShape:
    """Bound structural allocation before the JSON decoder sees a complete body."""
    def __init__(self):
        self.quoted = False
        self.escaped = False
        self.depth = 0
        self.tokens = 0

    def accept(self, chunk: bytes) -> bool:
        for value in chunk:
            if self.quoted:
                if self.escaped:
                    self.escaped = False
                elif value == 92:
                    self.escaped = True
                elif value == 34:
                    self.quoted = False
            elif value == 34:
                self.quoted = True
            elif value in (123, 91, 125, 93, 44, 58):
                self.tokens += 1
                if value in (123, 91):
                    self.depth += 1
                elif value in (125, 93):
                    self.depth -= 1
                if not 0 <= self.depth <= 64 or self.tokens > 100_000:
                    return False
        return True


# Closed producer codes. Never retain an arbitrary personal server error string.
HISTORY_DELETE_ERRORS = {
    400: frozenset({"invalid_history_delete_request"}),
    404: frozenset({"history_unavailable"}),
    409: frozenset({"session_not_archived", "history_revision_conflict", "history_delete_operation_conflict", "history_delete_snapshot_conflict"}),
    503: frozenset({"history_unavailable", "history_delete_outcome_unknown", "history_delete_capacity"}),
}


def deletion_identifier(value, maximum):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SessionError("invalid")
    return value


def deletion_operation(value):
    try:
        parsed = UUID(value)
        if parsed.version != 7 or str(parsed) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SessionError("invalid") from None
    return value


MOODS = frozenset({"chill", "groove", "energy", "party"})


class SessionError(Exception):
    def __init__(self, category: str, code: str = ""):
        self.category = category
        self.code = code
        super().__init__(category)  # Never surface private JSON, URLs or identifiers.


def session_profile_id(cfg) -> str:
    """Use the server-resolved device mapping without a second client selection.

    An existing explicit choice must still agree. This is only a request/context
    fence: Core freshly authenticates the device and verifies the mapped owner.
    No mapping, Profile permission or persistent selection is created here.
    """
    active = getattr(cfg, "active_profile_id", "")
    explicit = getattr(cfg, "explicit_profile_id", "")
    if (not isinstance(active, str) or not 1 <= len(active) <= 512
            or active != active.strip() or any(ord(c) < 32 or ord(c) == 127 for c in active)
            or not isinstance(explicit, str) or (explicit and explicit != active)):
        return ""
    return active


@dataclass(frozen=True)
class SessionBinding:
    ha_url: str
    device_id: str
    token: str = field(repr=False)
    profile_id: str
    resolved_profile_id: str
    private_session: bool
    profile_type: str
    privacy_mode: str
    language: str
    profile_selection: str = ""
    private_authorized: bool = False
    owner_epoch: int = 0

    @classmethod
    def capture(cls, cfg):
        if not cfg.paired or not cfg.device_token or not cfg.ha_url:
            raise SessionError("unpaired")
        return cls(cfg.ha_url.rstrip("/"), cfg.device_id, cfg.device_token,
                   cfg.explicit_profile_id or session_profile_id(cfg), cfg.active_profile_id,
                   cfg.private_session, cfg.active_profile_type, cfg.active_profile_privacy_mode, cfg.language, cfg.explicit_profile_id)

    def identity(self) -> dict[str, Any]:
        result = {"device_id": self.device_id, "client_type": CLIENT_TYPE}
        if self.profile_id:
            result["profile_id"] = self.profile_id
        if self.private_session:
            result["private_session"] = True
        return result


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 512 or any(ord(c) < 32 for c in value):
        raise SessionError("invalid")
    return value


def moment_insight_reference(value: object) -> bool:
    if (not isinstance(value, dict) or set(value) != {"schema_version", "session_id", "moment_id", "action_id"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        return False
    identifiers = (value["session_id"], value["moment_id"], value["action_id"])
    return (all(isinstance(item, str) and 1 <= len(item) <= 128
                and not any(ord(char) < 32 or ord(char) == 127 for char in item) for item in identifiers)
            and value["action_id"] == "insight-" + value["moment_id"])


class SessionTransport:
    def __init__(self, config: Callable, timeout: float = 8.0, *, owner_scope=None):
        self.config = config
        self.timeout = timeout
        self.owner_scope = owner_scope

    def binding(self) -> SessionBinding:
        binding = SessionBinding.capture(self.config())
        if self.owner_scope is not None and not binding.profile_id and not binding.resolved_profile_id:
            scope = self.owner_scope()
            if scope is not None:
                owner, private, epoch = scope
                identifier(owner)
                if owner != owner.strip() or any(ord(c) == 127 for c in owner) or type(private) is not bool or type(epoch) is not int or epoch < 0:
                    raise SessionError("invalid")
                binding = replace(binding, profile_id=owner, resolved_profile_id=owner,
                                  profile_type="", privacy_mode="", private_authorized=private, owner_epoch=epoch)
        return binding

    def _request(self, binding: SessionBinding, method: str, route: str, payload=None, *, timeout=None, isolated=False, archive_delete=False, content_delete=False, private_probe=False, display_owner=False, optional_capabilities=False, help_request=None) -> dict | HelpObservation:
        if binding != self.binding():
            raise SessionError("context_changed")
        if private_probe:
            if (method != "GET" or route != "ask_dj/history"
                    or payload != {"conversation_scope": "profile", "history_contract_version": 2}):
                raise SessionError("invalid")
            isolated = True
        if archive_delete:
            # Only these released typed archive routes get closed identity and
            # bounded error bodies; callers cannot select an arbitrary effect.
            shapes = {
                ("POST", "session/history/delete"): {"session_id", "expected_revision", "operation_id", "confirmed", "history_contract_version"},
                ("POST", "session/history/delete_all"): {"snapshot_id", "operation_id", "confirmed", "history_contract_version"},
                ("GET", "session/history/delete_snapshot"): {"history_contract_version"},
            }
            expected = shapes.get((method, route))
            if method == "GET" and route == "session/history/delete/status" and isinstance(payload, dict):
                mode = payload.get("operation")
                if isinstance(mode, str) and mode in {"single", "all"}:
                    expected = {"contract_version", "operation_id", "operation"} | (
                        {"session_id", "expected_revision"} if mode == "single" else {"snapshot_id"})
            if not isinstance(payload, dict) or set(payload) != expected:
                raise SessionError("invalid")
            isolated = True
        if content_delete:
            shapes = {
                ("POST", "session/content/delete/prepare"): {"contract_version", "target"},
                ("POST", "session/content/delete"): {"contract_version", "confirmation_id", "operation_id", "confirmed"},
                ("GET", "session/content/delete/status"): {"contract_version", "confirmation_id", "operation_id"},
            }
            if (archive_delete or not isinstance(payload, dict)
                    or set(payload) != shapes.get((method, route))
                    or type(payload.get("contract_version")) is not int or payload["contract_version"] != 1):
                raise SessionError("invalid")
            isolated = True
        if display_owner:
            shapes = {
                "display/owner/context": {"session_id"},
                "display/owner/resolve": {"code"},
                "display/owner/approve": {"session_id", "issuer_epoch", "claim_id", "browser_generation", "code", "share"},
                "display/owner/revoke": {"session_id", "share_id"},
            }
            if method != "POST" or not isinstance(payload, dict) or set(payload) != shapes.get(route):
                raise SessionError("invalid")
            isolated = True
        if isolated:
            try:
                base = urlsplit(binding.ha_url)
                if (base.scheme not in {"http", "https"} or not base.hostname
                        or base.username is not None or base.password is not None
                        or base.query or base.fragment
                        or any(ord(c) <= 32 or ord(c) == 127 for c in binding.ha_url)):
                    raise ValueError
                _ = base.port  # Reject malformed/out-of-range ports before request preparation.
            except ValueError:
                raise SessionError("invalid") from None
        headers = {
            "Authorization": "Bearer " + binding.token,
            "X-DJConnect-Device-ID": binding.device_id,
            "X-DJConnect-Client-Type": CLIENT_TYPE,
            "Accept": "application/json", "Cache-Control": "no-store",
            "Accept-Language": locale_for_language(binding.language),
        }
        if binding.profile_id and not display_owner and help_request is None:
            headers["X-DJConnect-Profile-ID"] = binding.profile_id
        identity = ({"device_id": binding.device_id, "client_type": CLIENT_TYPE}
                    if archive_delete or content_delete else binding.identity())
        body = {**identity, **(payload or {})}
        if help_request is not None:
            body = help_request.query()
        if display_owner:
            body = {"contract_version": 1, "identity": {"device_id": binding.device_id,
                    "client_type": CLIENT_TYPE}, **payload}
        if method == "GET":
            body = {key: str(value).lower() if isinstance(value, bool) else value for key, value in body.items()}
        category = "invalid" if method == "GET" else "uncertain"
        try:
            with ExitStack() as stack:
                options = {"headers": headers, "timeout": self.timeout if timeout is None else timeout,
                           "allow_redirects": False, "stream": True,
                           **({"params": body} if method == "GET" else {"json": body})}
                if isolated:
                    # Keep the isolated session alive until its streamed response closes.
                    session = stack.enter_context(requests.Session())
                    session.trust_env = False
                    response = session.request(method, binding.ha_url + PREFIX + route, **options)
                else:
                    response = requests.request(method, binding.ha_url + PREFIX + route, **options)
                stack.callback(response.close)
                if binding != self.binding():
                    raise SessionError("context_changed")
                # Denial is authoritative before any header/body allocation or JSON decode.
                if response.status_code in (401, 403):
                    # This closed read can deny personal access while a separately
                    # authenticated current Session remains a shared display.
                    if private_probe and response.status_code == 403:
                        raise SessionError("private_scope_denied")
                    raise SessionError("denied")
                if method == "POST" and route == "track_insight":
                    if response.status_code == 410:
                        raise SessionError("expired")
                    if response.status_code == 429:
                        raise SessionError("rate_limited")
                if help_request is not None and response.status_code != 200:
                    raise SessionError("unavailable")
                if optional_capabilities and method == "GET" and route == "capabilities" and response.status_code == 404:
                    return {}
                if 300 <= response.status_code < 400 or (response.status_code >= 500 and not archive_delete):
                    raise SessionError("unavailable" if method == "GET" else "uncertain")
                # A proxy may return a plain 404 for the current route while HA
                # reloads. Decode bounded JSON when present to retain explicit absence.
                if method == "GET" and route == "session/active" and response.status_code == 404:
                    category = "unavailable"
                maximum = JSON_CONTROL_BYTES if archive_delete else response_budget(route)
                declared = response.headers.get("Content-Length")
                if declared is not None and (not isinstance(declared, str) or not declared.isascii()
                        or not declared.isdecimal() or len(declared) > 20 or int(declared) > maximum):
                    raise SessionError(category)
                raw = bytearray()
                shape = _JSONShape()
                for chunk in response.iter_content(chunk_size=JSON_CHUNK_BYTES):
                    if binding != self.binding():
                        raise SessionError("context_changed")
                    if not isinstance(chunk, bytes) or len(chunk) > maximum - len(raw):
                        raise SessionError(category)
                    if not shape.accept(chunk):
                        raise SessionError(category)
                    raw.extend(chunk)  # iter_content counts decoded bytes, including compressed responses.
                if binding != self.binding():
                    raise SessionError("context_changed")
                try:
                    data = (HelpObservation.decode(raw, help_request) if help_request is not None
                            else json.loads(raw))
                except (ValueError, TypeError, RecursionError, OSError):
                    raise SessionError(category) from None
                if binding != self.binding():
                    raise SessionError("context_changed")
                if help_request is not None:
                    return data
                if not isinstance(data, dict):
                    raise SessionError(category)
                code = data.get("error") if isinstance(data.get("error"), str) else ""
                if archive_delete:
                    if response.status_code == 200 and data.get("success") is True:
                        return data  # The typed method below validates the exact receipt.
                    admitted = code if (data.get("success") is False
                        and code in HISTORY_DELETE_ERRORS.get(response.status_code, ())) else ""
                    category = ("conflict" if response.status_code == 409 else "unavailable"
                        if admitted and admitted != "history_delete_outcome_unknown" else "uncertain")
                    raise SessionError(category, admitted)
                if response.status_code in (404, 405, 426) or code == "history_contract_upgrade_required":
                    raise SessionError("unavailable", code if code in {"history_unavailable", "active_session_not_found", "history_contract_upgrade_required"} else "")
                if response.status_code == 409:
                    if code in {"device_not_mapped", "profile_required", "invalid_profile", "profile_access_denied"}:
                        raise SessionError("profile_unavailable")
                    raise SessionError("conflict")
                # Track Insight returns its document directly on HTTP 200.
                direct_insight = method == "POST" and route == "track_insight" and response.status_code == 200
                if response.status_code >= 400 or data.get("success", direct_insight) is not True:
                    raise SessionError("unavailable" if method == "GET" or response.status_code >= 400 else "uncertain")
                # Parsing creates a fresh exclusively owned tree; no second deep-copy allocation.
                return data
        except requests.RequestException:
            if binding != self.binding():
                raise SessionError("context_changed") from None
            raise SessionError("offline" if method == "GET" else "uncertain") from None

    def private_scope_owner(self, binding):
        """Existing Apple/Core Profile conversation read; discard all message bodies.

        A successful owner-scoped read confirms personal-read permission, not a
        Profile type, permanent grant, response lifetime or permission for media.
        Each consuming resource retains its own fresh authority and admission.
        """
        try:
            data = self._request(binding, "GET", "ask_dj/history",
                {"conversation_scope": "profile", "history_contract_version": 2}, private_probe=True)
        except SessionError as exc:
            if exc.category == "private_scope_denied":
                return None
            raise
        owner = identifier(data.get("owner_profile_id"))
        if (owner != owner.strip() or any(ord(c) == 127 for c in owner)
                or data.get("history_scope") != "profile" or data.get("user_id") is not None
                or not isinstance(data.get("messages"), list) or len(data["messages"]) > 1000
                or any(not isinstance(row, dict) for row in data["messages"])
                or (binding.profile_id and owner != binding.profile_id)):
            raise SessionError("invalid")
        return owner

    def prepare_content_delete(self, binding, selected):
        from .session_content_delete import target, wire_target
        selected = wire_target(target(selected))
        return self._request(binding, "POST", "session/content/delete/prepare",
                             {"contract_version": 1, "target": selected}, content_delete=True)

    def delete_content(self, binding, intent, values):
        if (not isinstance(values, dict) or values.get("confirmed") is not True
                or binding != intent.preview.binding or values != {"contract_version": 1,
                "confirmation_id": intent.preview.confirmation, "operation_id": intent.operation, "confirmed": True}):
            raise SessionError("invalid")
        result = self._request(binding, "POST", "session/content/delete", values, content_delete=True)
        intent.receipt(result)

    def content_delete_status(self, binding, intent):
        if binding != intent.preview.binding:
            raise SessionError("context_changed")
        result = self._request(binding, "GET", "session/content/delete/status", {
            "contract_version": 1, "confirmation_id": intent.preview.confirmation,
            "operation_id": intent.operation}, content_delete=True)
        return intent.outcome(result)

    def display_owner(self, binding, action, payload):
        if binding.ha_url.startswith("http://"):
            raise SessionError("https_required")
        return self._request(binding, "POST", "display/owner/" + action, payload, display_owner=True)

    def capabilities(self, binding, *, isolated=False):
        return self._request(binding, "GET", "capabilities", isolated=isolated)

    def track_insight_capabilities(self, binding):
        return self._request(binding, "GET", "capabilities", isolated=True, optional_capabilities=True)

    def moment_track_insight(self, binding, reference):
        """Read one existing Moment via the private owner route without playback fallback."""
        if not moment_insight_reference(reference):
            raise SessionError("invalid")
        if not binding.profile_id or binding.profile_id != binding.resolved_profile_id:
            raise SessionError("denied")
        data = self._request(binding, "POST", "track_insight", {
            "track_insight_target": dict(reference),
            "locale": locale_for_language(binding.language),
            "include_visual_profile": True,
        }, timeout=max(self.timeout, 10.0), isolated=True)
        subject = data.get("subject")
        if (not isinstance(subject, dict) or subject.get("mode") != "explicit"
                or not moment_insight_reference(subject.get("reference")) or subject["reference"] != reference):
            raise SessionError("invalid")
        lifetime = subject.get("expires_in")
        media = subject.get("media_identity")
        if (type(lifetime) not in (int, float) or not 0 < lifetime <= 600
                or not isinstance(media, str) or re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", media) is None):
            raise SessionError("invalid")
        track = data.get("track")
        if not isinstance(track, dict) or track.get("media_identity") != media or track.get("uri") != media:
            raise SessionError("invalid")
        return data

    def ask_dj_help(self, binding, surface, session_id=None) -> HelpObservation:
        try:
            request = HelpRequest(surface, binding.language, session_id)
        except (ValueError, TypeError):
            raise SessionError("invalid") from None
        return self._request(binding, "GET", HELP_ROUTE, isolated=True, help_request=request)

    def active(self, binding, *, isolated=False):
        return self._request(binding, "GET", "session/active", isolated=isolated)

    def start(self, binding, mood: str):
        if not isinstance(mood, str) or mood not in MOODS:
            raise SessionError("invalid")
        return self._request(binding, "POST", "session/start", {
            "mood": mood, "language": locale_for_language(binding.language)})

    def prepare_artwork(self, binding, session_id: str, moment_id: str):
        sid, mid = identifier(session_id), identifier(moment_id)
        if len(sid) > 128 or len(mid) > 128:
            raise SessionError("invalid")
        return self._request(binding, "POST", "session/media/prepare", {
            "contract_version": 1, "session_id": sid, "targets": [{"moment_id": mid}]}, timeout=24.0)

    def playback_status(self, binding):
        return self._request(binding, "POST", "command", {
            "command": "status", "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_command(self, binding, command: str):
        if not isinstance(command, str) or command not in {"play", "pause", "previous", "next", "save_current_track"}:
            raise SessionError("invalid")
        return self._request(binding, "POST", "command", {
            "command": command, "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_adjustment(self, binding, command: str, value: int | bool | str):
        if not (
            command == "seek_relative" and type(value) is int and value in (-15000, 15000)
            or command == "set_shuffle" and type(value) is bool
            or command == "set_repeat" and isinstance(value, str) and value in ("off", "context", "track")
        ):
            raise SessionError("invalid")
        return self._request(binding, "POST", "command", {
            "command": command, "value": value,
            "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_seek_offset(self, binding, milliseconds: int):
        if type(milliseconds) is not int or milliseconds == 0:
            raise SessionError("invalid")
        return self._request(binding, "POST", "command", {
            "command": "seek_relative", "value": milliseconds,
            "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_volume(self, binding, value: int, backend: str):
        if (type(value) is not int or not 0 <= value <= 60
                or not isinstance(backend, str) or backend not in {"spotify_direct", "music_assistant"}):
            raise SessionError("invalid")
        # The released MA adapter accepts a fraction; integer1 means100% there.
        # Spotify accepts integer percentages. Keep this consumer's0..60 limit.
        wire_value = value / 100 if backend == "music_assistant" else value
        return self._request(binding, "POST", "command", {
            "command": "set_volume", "value": wire_value,
            "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_favorite(self, binding, value: bool):
        if type(value) is not bool:
            raise SessionError("invalid")
        return self._request(binding, "POST", "command", {
            "command": "set_current_track_favorite", "value": value,
            "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_outputs(self, binding):
        return self._request(binding, "POST", "command", {
            "command": "devices", "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def playback_output(self, binding, target: str, play: bool):
        if (not isinstance(target, str) or not 1 <= len(target) <= 512 or target != target.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in target) or type(play) is not bool):
            raise SessionError("invalid")
        return self._request(binding, "POST", "command", {
            "command": "set_output", "value": target, "play": play,
            "request_source": "device_command", "app_version": PROTOCOL_VERSION}, isolated=True)

    def delete_history(self, binding, session_id: str, expected_revision: str,
                       operation_id: str, *, confirmed: bool):
        """One explicit archive mutation; no retries or optimistic projection.

        The caller must bind a fresh detail read and confirmation to its original
        epoch and UUIDv7 lifetime. This adapter cannot create/renew consent.
        List SQL revisions are deliberately rejected, never converted to CAS.
        """
        sid = deletion_identifier(session_id, 200)
        revision = deletion_identifier(expected_revision, 4096)
        op = deletion_operation(operation_id)
        if (confirmed is not True or not binding.resolved_profile_id
                or binding.profile_id != binding.resolved_profile_id):
            raise SessionError("invalid")
        data = self._request(binding, "POST", "session/history/delete", {
            "session_id": sid, "expected_revision": revision,
            "operation_id": op, "confirmed": True, "history_contract_version": 2}, archive_delete=True)
        if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or data.get("operation_id") != op or data.get("owner_profile_id") != binding.resolved_profile_id
                or data.get("deleted_session_id") != sid or data.get("deleted") is not True):
            raise SessionError("uncertain")
        return {"operation_id": op, "owner_profile_id": binding.resolved_profile_id,
                "deleted_session_id": sid, "deleted": True}

    def history_delete_snapshot(self, binding):
        if not binding.profile_id or binding.profile_id != binding.resolved_profile_id:
            raise SessionError("invalid")
        return self._request(binding, "GET", "session/history/delete_snapshot",
                             {"history_contract_version": 2}, archive_delete=True)

    def delete_all_history(self, binding, snapshot_id, operation_id, target_count, *, confirmed):
        token = deletion_identifier(snapshot_id, 200)
        op = deletion_operation(operation_id)
        if (confirmed is not True or type(target_count) is not int or target_count <= 0
                or not binding.profile_id or binding.profile_id != binding.resolved_profile_id):
            raise SessionError("invalid")
        data = self._request(binding, "POST", "session/history/delete_all", {
            "snapshot_id": token, "operation_id": op, "confirmed": True,
            "history_contract_version": 2}, archive_delete=True)
        if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or data.get("operation_id") != op or data.get("owner_profile_id") != binding.resolved_profile_id
                or data.get("snapshot_id") != token or data.get("deleted") is not True
                or type(data.get("deleted_session_count")) is not int
                or data["deleted_session_count"] != target_count):
            raise SessionError("uncertain")
        return {"operation_id": op, "owner_profile_id": binding.resolved_profile_id,
                "snapshot_id": token, "deleted": True, "deleted_session_count": target_count}

    def history_delete_status(self, binding, operation, operation_id, body, *, target_count=None):
        """Read only the ORIGINAL archive body/ID; never claim a new send ticket."""
        op = deletion_operation(operation_id)
        if (not binding.profile_id or binding.profile_id != binding.resolved_profile_id
                or not isinstance(body, dict) or not isinstance(operation, str) or operation not in {"single", "all"}
                or set(body) != ({"session_id", "expected_revision"} if operation == "single" else {"snapshot_id"})):
            raise SessionError("invalid")
        for key, value in body.items():
            deletion_identifier(value, 4096 if key == "expected_revision" else 200)
        if operation == "all" and (type(target_count) is not int or target_count <= 0):
            raise SessionError("invalid")
        digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        data = self._request(binding, "GET", "session/history/delete/status", {
            "contract_version": 1, "operation_id": op, "operation": operation, **body}, archive_delete=True)
        state = data.get("state")
        if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or data.get("owner_profile_id") != binding.resolved_profile_id or data.get("operation_id") != op
                or data.get("operation") != operation or data.get("request_digest") != digest
                or any(data.get(key) != value for key, value in body.items())
                or not isinstance(state, str) or state not in {"unknown", "pending", "completed", "expired_unknown"}
                or data.get("definitive") is not (state == "completed")):
            raise SessionError("uncertain")
        receipt = data.get("receipt")
        if state == "completed":
            if (not isinstance(receipt, dict) or receipt.get("success") is not True
                    or type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
                    or receipt.get("owner_profile_id") != binding.resolved_profile_id
                    or receipt.get("operation_id") != op or receipt.get("deleted") is not True
                    or (operation == "single" and receipt.get("deleted_session_id") != body["session_id"])
                    or (operation == "all" and (receipt.get("snapshot_id") != body["snapshot_id"]
                        or type(receipt.get("deleted_session_count")) is not int
                        or receipt["deleted_session_count"] != target_count))):
                raise SessionError("uncertain")
        elif receipt is not None:
            raise SessionError("uncertain")
        return {"state": state, "definitive": state == "completed"}

    def end(self, binding, session_id: str):
        return self._request(binding, "POST", "session/end", {"session_id": identifier(session_id)})

    def broadcast(self, binding, session_id: str):
        return self._request(binding, "GET", "session/broadcast/snapshot", {"session_id": identifier(session_id)})

    def history(self, binding, *, cursor: str = "", limit: int = 20):
        return self._request(binding, "GET", "session/history", self._page(cursor, limit))

    def timeline(self, binding, session_id: str, *, cursor: str = "", limit: int = 20,
                 window: str | None = "tail", anchor_entry_id: str = "", isolated=False):
        if window not in {None, "tail", "anchor"} or (cursor and window is not None):
            raise SessionError("invalid")
        page = self._page(cursor, limit)
        if window is not None:
            page["window"] = window
        if window == "anchor":
            page["anchor_entry_id"] = identifier(anchor_entry_id)
        route = "session/history/" + quote(identifier(session_id), safe="")
        return self._request(binding, "GET", route, page, isolated=isolated)

    def search(self, binding, session_id: str, query: str, *, cursor: str = "", limit: int = 20):
        if not isinstance(query, str):
            raise SessionError("invalid")
        query = query.strip()
        if not 1 <= len(query) <= 200:
            raise SessionError("invalid")
        route = "session/history/" + quote(identifier(session_id), safe="") + "/search"
        return self._request(binding, "GET", route, {**self._page(cursor, limit), "q": query})

    def standalone_history(self, binding, owner: str):
        result = self._request(binding, "GET", "ask_dj/history", {
            "conversation_scope": "profile", "profile_id": identifier(owner),
        }, isolated=True)
        if result.get("owner_profile_id") != owner or result.get("history_scope") != "profile":
            raise SessionError("context_changed")
        return result

    def standalone_question(self, binding, text: str, message_id: str, owner: str, *, selected_entry=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise SessionError("invalid")
        context = {}
        if selected_entry is not None:
            if not isinstance(selected_entry, dict) or set(selected_entry) != {"session_id", "entry_id"}:
                raise SessionError("invalid")
            if any(len(identifier(value)) > 128 or value != value.strip()
                   for value in (*selected_entry.values(), message_id)):
                raise SessionError("invalid")
            context = {"session_id": None, "selected_entry": dict(selected_entry)}
        result = self._request(binding, "POST", "ask_dj/message", {
            "text": text.strip(), "client_message_id": identifier(message_id),
            "conversation_context": context, "conversation_scope": "profile", "profile_id": identifier(owner),
            "language": locale_for_language(binding.language), "audio_response": "never",
        }, timeout=(8, 120), isolated=True)
        if result.get("owner_profile_id") != owner or result.get("client_message_id") != message_id:
            raise SessionError("uncertain")
        if selected_entry is not None:
            conversation = result.get("conversation")
            if (result.get("success") is not True or not isinstance(conversation, dict)
                    or type(conversation.get("schema_version")) is not int or conversation["schema_version"] != 1
                    or conversation.get("input_type") != "text" or conversation.get("history_scope") != "profile"
                    or conversation.get("context") != context):
                raise SessionError("uncertain")

    def question(self, binding, request):
        body = request["text"].strip() if isinstance(request.get("text"), str) else ""
        if not body or len(body) > 4000:
            raise SessionError("invalid")
        context = {"session_id": identifier(request["session_id"])}
        selected = request.get("selected") or {}
        if selected:
            kind = selected.get("kind")
            if kind not in {"selected_entry", "selected_moment"}:
                raise SessionError("invalid")
            key = "entry_id" if kind == "selected_entry" else "moment_id"
            context[kind] = {"session_id": identifier(selected["session_id"]), key: identifier(selected[key])}
        values = [request["client_message_id"], context["session_id"]]
        if selected:
            values.extend(context[selected["kind"]].values())
        if any(len(identifier(value)) > 128 or value != value.strip() for value in values):
            raise SessionError("invalid")
        return self._request(binding, "POST", "ask_dj/message", {
            "text": body, "client_message_id": request["client_message_id"],
            "conversation_context": context, "conversation_scope": "profile",
            "language": locale_for_language(binding.language), "audio_response": "never"}, timeout=(8, 120))

    def open_entry(self, binding, session_id: str, entry_id: str):
        return self._request(binding, "POST", "session/history/open", {
            "history_contract_version": 2,
            "action": {"kind": "open_session", "session_id": identifier(session_id),
                       "entry_id": identifier(entry_id), "navigation_only": True}})

    @staticmethod
    def _page(cursor, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise SessionError("invalid")
        page = {"limit": limit, "history_contract_version": 2}
        if cursor:
            if not isinstance(cursor, str) or len(cursor) > 8192 or any(ord(c) < 32 for c in cursor):
                raise SessionError("invalid")
            page["cursor"] = cursor
        return page


def private_binding_allowed(binding):
    return (not binding.private_session and bool(binding.profile_id)
        and binding.profile_id == binding.resolved_profile_id
        and (binding.private_authorized or (binding.profile_type == "personal"
            and binding.privacy_mode == "private")))


def private_scope_allowed(owner):
    cfg = owner.backend.cfg
    if cfg.private_session:
        return False
    if getattr(owner, "_bootstrap_binding", None) is not None:
        return owner._bootstrap_current() and owner._bootstrap_private
    return (bool(session_profile_id(cfg)) and cfg.active_profile_type == "personal"
        and cfg.active_profile_privacy_mode == "private")
