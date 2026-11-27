import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from djconnect_pi.config import PROTOCOL_VERSION
from djconnect_pi.session_transport import SessionError, SessionTransport


@pytest.fixture
def transport(monkeypatch):
    cfg = SimpleNamespace(paired=True, device_token="synthetic-token", ha_url="http://fixture.invalid",
        device_id="djconnect-raspberry-pi-fixture", explicit_profile_id="explicit-profile",
        active_profile_id="resolved-profile", active_profile_type="personal", active_profile_privacy_mode="private", private_session=False, language="nl")
    response = Mock(status_code=200, headers={})
    response.json.return_value = {"success": True, "session": None}
    def chunks(*, chunk_size):
        value = b"not JSON" if response.json.side_effect else json.dumps(response.json.return_value).encode()
        return iter(value[i:i + chunk_size] for i in range(0, len(value), chunk_size))
    response.iter_content.side_effect = chunks
    request = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", request)
    class IsolatedSession:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, *args, **kwargs):
            assert self.trust_env is False
            return request(*args, **kwargs)
    monkeypatch.setattr(requests, "Session", IsolatedSession)
    return SessionTransport(lambda: cfg), cfg, request, response


def test_read_is_pure_and_bound_to_device_and_explicit_profile(transport):
    client, cfg, request, _ = transport
    assert client.active(client.binding())["session"] is None
    args, kwargs = request.call_args
    assert args == ("GET", "http://fixture.invalid/api/djconnect/v1/session/active")
    assert kwargs["params"] == {"device_id": cfg.device_id, "client_type": "raspberry_pi", "profile_id": "explicit-profile"}
    assert kwargs["headers"]["Cache-Control"] == "no-store"
    assert "json" not in kwargs


def test_track_insight_capability_probe_distinguishes_missing_route_from_failure(transport):
    client, _, request, response = transport
    binding = client.binding()
    response.status_code = 404
    assert client.track_insight_capabilities(binding) == {}
    assert request.call_args.args[1].endswith("/capabilities")
    assert request.call_args.kwargs["allow_redirects"] is False
    response.iter_content.assert_not_called()

    for status, category in ((403, "denied"), (503, "unavailable")):
        response.status_code = status
        with pytest.raises(SessionError, match=category):
            client.track_insight_capabilities(binding)


@pytest.mark.parametrize(("overrides", "status", "error"), [
    ({}, 200, ""),
    ({"mode": "current"}, 200, "invalid"),
    ({"media_identity": "lookalike"}, 200, "invalid"),
    ({"media_identity": "spotify:track:" + "b" * 22}, 200, "invalid"),
    ({"expires_in": True}, 200, "invalid"),
    ({"expires_in": 601}, 200, "invalid"),
    ({"reference": {"schema_version": 1, "session_id": "foreign", "moment_id": "m1",
                    "action_id": "insight-m1"}}, 200, "invalid"),
    ({}, 403, "denied"),
    ({}, 410, "expired"),
    ({}, 429, "rate_limited"),
])
def test_moment_track_insight_is_bound_to_the_explicit_reference(transport, overrides, status, error):
    client, cfg, request, response = transport
    cfg.explicit_profile_id = cfg.active_profile_id
    binding = client.binding()
    reference = {"schema_version": 1, "session_id": "s1", "moment_id": "m1", "action_id": "insight-m1"}
    response.status_code = status
    media = "spotify:track:" + "a" * 22
    response.json.return_value = {
        "subject": {"mode": "explicit", "reference": reference, "media_identity": media,
                    "expires_in": 424, **overrides},
        "track": {"title": "Same title", "media_identity": media, "uri": media},
    }

    if error:
        with pytest.raises(SessionError, match=error):
            client.moment_track_insight(binding, reference)
    else:
        assert client.moment_track_insight(binding, reference) == response.json.return_value

    assert request.call_count == 1
    assert request.call_args.args == ("POST", "http://fixture.invalid/api/djconnect/v1/track_insight")
    assert request.call_args.kwargs["json"] == {
        **binding.identity(), "track_insight_target": reference, "locale": "nl-NL", "include_visual_profile": True,
    }
    assert request.call_args.kwargs["allow_redirects"] is False


def test_moment_track_insight_cannot_use_a_rejected_document(transport):
    client, cfg, request, response = transport
    cfg.explicit_profile_id = cfg.active_profile_id
    response.json.return_value = {"success": False}
    reference = {"schema_version": 1, "session_id": "s1", "moment_id": "m1", "action_id": "insight-m1"}
    with pytest.raises(SessionError, match="uncertain"):
        client.moment_track_insight(client.binding(), reference)
    request.assert_called_once()


def test_ask_dj_help_uses_closed_paired_get_without_profile_or_identity_query(transport):
    client, cfg, request, response = transport
    def wire(method, url, **options):
        assert method == "GET" and url.endswith("/ask_dj/help-context")
        assert options["allow_redirects"] is False and "json" not in options
        headers = options["headers"]
        assert headers["Cache-Control"] == "no-store" and headers["X-DJConnect-Device-ID"] == cfg.device_id
        assert "X-DJConnect-Profile-ID" not in headers
        query = options["params"]
        assert set(query) == {"schema_version", "request_id", "surface", "language", "catalogue_id",
                              "catalogue_revision", "catalogue_sha256", "session_id"}
        assert query["session_id"] == "existing+session" and query["language"] == "nl"
        receipt = {
            "schema_version": 1, "request_id": query["request_id"], "surface": query["surface"],
            "session_id": query["session_id"], "language": query["language"], "available": True,
            "observation_id": "8d137231-cbb1-4e66-9d96-4936666e14c0", "track_id": "media+id",
            "eligible_entry_ids": ["discover.recent", "listen.current"],
            "catalogue": {"schema_version": 1, "catalogue_id": query["catalogue_id"],
                          "revision": query["catalogue_revision"], "sha256": query["catalogue_sha256"],
                          "qualified": True},
        }
        response.json.return_value = receipt
        return response

    request.side_effect = wire
    result = client.ask_dj_help(client.binding(), "active_session", "existing+session")
    assert result.available and result.track_id == "media+id"
    assert result.eligible_ids == ("discover.recent", "listen.current")
    response.close.assert_called_once()


@pytest.mark.parametrize("update", [
    {"schema_version": True}, {"schema_version": 1.0}, {"language": "en"},
    {"session_id": "another-session"}, {"eligible_entry_ids": ["unknown"]},
    {"eligible_entry_ids": ["discover.recent", "discover.recent"]},
    {"eligible_entry_ids": ["listen.outputs", "discover.recent"]},
    {"eligible_entry_ids": ["listen.current"]}, {"track_id": "foreign-track"},
    {"observation_id": None}, {"extra": None}, {"available": False},
])
def test_ask_dj_help_rejects_unbound_or_malformed_observations(transport, update):
    client, _, request, response = transport
    def wire(*args, **options):
        query = options["params"]
        assert "session_id" not in query
        response.json.return_value = {
            "schema_version": 1, "request_id": query["request_id"], "surface": "ask_dj",
            "session_id": None, "language": "nl", "available": True, "track_id": None,
            "observation_id": "8d137231-cbb1-4e66-9d96-4936666e14c0",
            "eligible_entry_ids": ["discover.recent"],
            "catalogue": {"schema_version": 1, "catalogue_id": query["catalogue_id"],
                          "revision": query["catalogue_revision"], "sha256": query["catalogue_sha256"],
                          "qualified": True},
            **update,
        }
        return response

    request.side_effect = wire
    with pytest.raises(SessionError, match="^invalid$"):
        client.ask_dj_help(client.binding(), "ask_dj")


def test_ask_dj_help_rejects_duplicate_keys_or_oversized_response(transport):
    client, _, request, response = transport
    response.iter_content.side_effect = lambda **_: iter([b'{"schema_version":1,"schema_version":1}'])
    with pytest.raises(SessionError, match="^invalid$"):
        client.ask_dj_help(client.binding(), "ask_dj")
    response.iter_content.reset_mock()
    response.headers["Content-Length"] = "16385"
    with pytest.raises(SessionError, match="^invalid$"):
        client.ask_dj_help(client.binding(), "ask_dj")
    response.iter_content.assert_not_called()
    request.reset_mock()
    with pytest.raises(SessionError, match="^invalid$"):
        client.ask_dj_help(client.binding(), "active_session")
    request.assert_not_called()


def test_successor_profile_discards_delayed_response(transport):
    client, cfg, request, response = transport
    def delayed(*args, **kwargs):
        cfg.active_profile_id = "successor"
        return response
    request.side_effect = delayed
    with pytest.raises(SessionError, match="context_changed"):
        client.active(client.binding())


@pytest.mark.parametrize("method", ["start", "end"])
def test_write_timeout_is_uncertain_and_never_replayed(transport, method):
    client, _, request, _ = transport
    request.side_effect = requests.Timeout()
    with pytest.raises(SessionError, match="uncertain"):
        getattr(client, method)(client.binding(), "groove" if method == "start" else "exact-session")
    assert request.call_count == 1


def test_history_uses_negotiated_representation_and_safe_path_component(transport):
    client, _, request, _ = transport
    client.timeline(client.binding(), "session/foreign", window="anchor", anchor_entry_id="entry-1")
    assert request.call_args.args[1].endswith("session%2Fforeign")
    assert request.call_args.kwargs["params"]["history_contract_version"] == 2
    assert request.call_args.kwargs["params"]["anchor_entry_id"] == "entry-1"


def test_unauthorized_read_never_exposes_private_error_or_resets_pairing(transport):
    client, cfg, _, response = transport
    response.status_code = 403
    response.json.return_value = {"error": "private-server-body", "profile_id": "secret"}
    with pytest.raises(SessionError, match="^denied$"):
        client.active(client.binding())
    assert cfg.paired and cfg.device_token == "synthetic-token"


def test_no_request_with_old_binding_or_invalid_mood(transport):
    client, cfg, request, _ = transport
    binding = client.binding()
    cfg.device_token = "replacement"
    with pytest.raises(SessionError, match="context_changed"):
        client.active(binding)
    with pytest.raises(SessionError, match="invalid"):
        client.start(client.binding(), "continue")
    request.assert_not_called()


def test_question_is_single_profile_bound_text_only_request_with_exact_context(transport):
    client, cfg, request, _ = transport
    value = {'text': '  Tell me about this  ', 'client_message_id': 'pi-turn-fixture', 'session_id': 'active',
        'selected': {'kind': 'selected_entry', 'session_id': 'earlier', 'entry_id': 'entry-1'}}
    client.question(client.binding(), value)
    body = request.call_args.kwargs['json']
    assert body['client_type'] == 'raspberry_pi' and body['profile_id'] == cfg.explicit_profile_id
    assert body['text'] == 'Tell me about this' and body['audio_response'] == 'never'
    assert body['conversation_context'] == {'session_id': 'active', 'selected_entry': {'session_id': 'earlier', 'entry_id': 'entry-1'}}
    assert request.call_args.kwargs['timeout'] == (8, 120)
    request.assert_called_once()


def test_standalone_question_checks_receipt_and_sends_text_once(transport):
    client, _, request, response = transport
    response.json.return_value = {"success": True, "owner_profile_id": "owner", "client_message_id": "turn"}
    client.standalone_question(client.binding(), "  Tell me about this artist  ", "turn", "owner")
    assert request.call_args.args[0] == "POST"
    assert request.call_args.args[1].endswith("/ask_dj/message")
    payload = request.call_args.kwargs["json"]
    assert payload["text"] == "Tell me about this artist"
    assert payload["conversation_context"] == {}
    assert payload["profile_id"] == "owner"
    assert payload["audio_response"] == "never"
    request.assert_called_once()

    request.reset_mock()
    with pytest.raises(SessionError, match="uncertain"):
        client.standalone_question(client.binding(), "Question", "another-turn", "owner")
    request.assert_called_once()


@pytest.mark.parametrize(("receipt_context", "accepted"), [
    ({"session_id": None, "selected_entry": {"session_id": "earlier", "entry_id": "entry-1"}}, True),
    ({"session_id": None}, True),
    ({"session_id": None, "selected_entry": {"session_id": "earlier", "entry_id": "another-entry"}}, False),
    ({"session_id": "some-active-session"}, False),
    ({}, False),
])
def test_standalone_follow_up_accepts_only_the_selected_entry_or_detached_topic(transport, receipt_context, accepted):
    client, _, request, response = transport
    selected = {"session_id": "earlier", "entry_id": "entry-1"}
    context = {"session_id": None, "selected_entry": selected}
    response.json.return_value = {
        "success": True, "owner_profile_id": "owner", "client_message_id": "turn",
        "conversation": {"schema_version": 1, "input_type": "text", "history_scope": "profile",
                         "context": receipt_context, "turn_id": "confirmed-turn", "entry_ids": []},
    }

    if accepted:
        client.standalone_question(client.binding(), "Tell me more", "turn", "owner", selected_entry=selected)
    else:
        with pytest.raises(SessionError, match="^uncertain$"):
            client.standalone_question(client.binding(), "Tell me more", "turn", "owner", selected_entry=selected)

    request.assert_called_once()
    body = request.call_args.kwargs["json"]
    assert body["conversation_context"] == context
    assert body["conversation_scope"] == "profile" and body["profile_id"] == "owner"


@pytest.mark.parametrize("entry_id", ["", " entry", "a" * 129, True])
def test_standalone_follow_up_rejects_malformed_references_before_sending(transport, entry_id):
    client, _, request, _ = transport
    with pytest.raises(SessionError, match="^invalid$"):
        client.standalone_question(client.binding(), "Tell me more", "turn", "owner",
                                   selected_entry={"session_id": "earlier", "entry_id": entry_id})
    request.assert_not_called()


@pytest.mark.parametrize(("session_id", "entries", "accepted"), [
    ("session-at-send", ["question", "answer"], True),
    ("successor-session", ["question", "answer"], False),
    (None, [], False),
    ("session-at-send", [], False),
    ("session-at-send", ["answer", "answer"], False),
])
def test_profile_question_requires_its_confirmed_active_session_and_saved_entries(transport, session_id, entries, accepted):
    client, _, request, response = transport
    response.json.return_value = {
        "success": True, "owner_profile_id": "owner", "client_message_id": "turn",
        "conversation": {"schema_version": 1, "input_type": "text", "history_scope": "profile",
                         "context": {"session_id": session_id}, "turn_id": "confirmed-turn", "entry_ids": entries},
    }
    if accepted:
        client.standalone_question(client.binding(), "Question", "turn", "owner", active_session_id="session-at-send")
    else:
        with pytest.raises(SessionError, match="^uncertain$"):
            client.standalone_question(client.binding(), "Question", "turn", "owner", active_session_id="session-at-send")
    request.assert_called_once()
    assert request.call_args.kwargs["json"]["conversation_context"] == {"session_id": "session-at-send"}


@pytest.mark.parametrize(("status", "code", "category"), [
    (404, "history_unavailable", "reference_unavailable"),
    (409, "source_withdrawn", "reference_unavailable"),
    (409, "conversation_source_withdrawn", "reference_unavailable"),
    (409, "session_context_changed", "conflict"),
    (404, "route_missing", "unavailable"),
])
def test_standalone_follow_up_distinguishes_withdrawal_from_other_errors(transport, status, code, category):
    client, _, request, response = transport
    response.status_code = status
    response.json.return_value = {"success": False, "error": code}
    with pytest.raises(SessionError, match=f"^{category}$"):
        client.standalone_question(client.binding(), "Tell me more", "turn", "owner",
                                   selected_entry={"session_id": "earlier", "entry_id": "saved"})
    request.assert_called_once()


def test_question_timeout_is_uncertain_with_no_write_replay(transport):
    client, _, request, _ = transport
    request.side_effect = requests.Timeout()
    with pytest.raises(SessionError, match='uncertain'):
        client.question(client.binding(), {'text': 'A question', 'client_message_id': 'new-turn', 'session_id': 'active'})
    request.assert_called_once()


def test_malformed_success_body_after_question_is_an_uncertain_mutation(transport):
    client, _, request, response = transport
    response.json.return_value = []
    with pytest.raises(SessionError, match='uncertain'):
        client.question(client.binding(), {'text':'Question','client_message_id':'turn','session_id':'active'})
    request.assert_called_once()


@pytest.mark.parametrize("command", ["play", "pause", "previous", "next", "save_current_track"])
def test_playback_command_is_one_paired_profile_http_attempt_without_invented_wire_fences(transport, command):
    client, cfg, request, _ = transport
    client.playback_command(client.binding(), command)
    request.assert_called_once()
    args, kwargs = request.call_args
    assert args == ("POST", "http://fixture.invalid/api/djconnect/v1/command")
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-token"
    assert kwargs["json"] == {"device_id": cfg.device_id, "client_type": "raspberry_pi", "profile_id": cfg.explicit_profile_id,
        "command": command, "request_source": "device_command", "app_version": PROTOCOL_VERSION}


@pytest.mark.parametrize(("command", "value"), [
    ("seek_relative", -15000), ("seek_relative", 15000), ("set_shuffle", True), ("set_repeat", "track"),
])
def test_overlay_adjustment_uses_one_existing_device_command(transport, command, value):
    client, _, request, _ = transport
    client.playback_adjustment(client.binding(), command, value)
    request.assert_called_once()
    assert request.call_args.args == ("POST", "http://fixture.invalid/api/djconnect/v1/command")
    payload = request.call_args.kwargs["json"]
    assert payload["command"] == command and payload["value"] == value
    assert type(payload["value"]) is type(value)
    assert payload["request_source"] == "device_command"


def test_playback_timeout_is_uncertain_and_never_replayed(transport):
    client, _, request, _ = transport
    request.side_effect = requests.Timeout()
    with pytest.raises(SessionError, match="uncertain"):
        client.playback_command(client.binding(), "next")
    request.assert_called_once()



def test_playback_owner_preflight_can_isolate_ambient_auth_without_changing_identity(transport):
    client, cfg, request, _ = transport
    client.active(client.binding(), isolated=True)
    request.assert_called_once()
    assert request.call_args.kwargs["params"]["profile_id"] == cfg.explicit_profile_id
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"



@pytest.mark.parametrize("url", ["http://synthetic-user:synthetic-password@fixture.invalid", "ftp://fixture.invalid", "http://fixture.invalid?synthetic=1", "http://fixture.invalid#fragment", "http://fixture.invalid:70000", "http://fixture.invalid\n"])
@pytest.mark.parametrize("route", ["owner", "status", "write"])
def test_playback_rejects_credential_or_noncanonical_base_before_dispatch(transport, url, route):
    client, cfg, request, _ = transport
    cfg.ha_url = url
    binding = client.binding()
    with pytest.raises(SessionError, match="^invalid$"):
        if route == "owner": client.active(binding, isolated=True)
        elif route == "status": client.playback_status(binding)
        else: client.playback_command(binding, "next")
    request.assert_not_called()
    assert cfg.paired and cfg.device_token == "synthetic-token"


def test_playback_preserves_valid_configured_reverse_proxy_base_path(transport):
    client, cfg, request, _ = transport
    cfg.ha_url = "https://fixture.invalid/ha"
    client.playback_status(client.binding())
    request.assert_called_once()
    assert request.call_args.args == ("POST", "https://fixture.invalid/ha/api/djconnect/v1/command")


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("body", ["empty", "malformed", "non_object"])
def test_http_denial_precedes_any_body_decode_and_keeps_pairing(transport, method, isolated, status, body):
    client, cfg, request, response = transport
    response.status_code = status
    if body == "non_object": response.json.return_value = []
    else: response.json.side_effect = ValueError(body)
    with pytest.raises(SessionError, match="^denied$"):
        client._request(client.binding(), method, "session/active", isolated=isolated)
    response.json.assert_not_called()
    request.assert_called_once()
    assert cfg.paired and cfg.device_token == "synthetic-token"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status", [401, 403])
def test_superseded_binding_precedes_denial_without_decoding_old_body(transport, method, status):
    client, cfg, request, response = transport
    response.status_code = status
    def delayed(*args, **kwargs):
        cfg.active_profile_id = "successor-profile"
        return response
    request.side_effect = delayed
    with pytest.raises(SessionError, match="^context_changed$"):
        client._request(client.binding(), method, "session/active", isolated=True)
    response.json.assert_not_called()
    request.assert_called_once()
    assert cfg.paired and cfg.device_token == "synthetic-token"


@pytest.mark.parametrize("method,expected", [("GET", "unavailable"), ("POST", "uncertain")])
def test_server_failure_remains_unavailable_or_uncertain_without_replay(transport, method, expected):
    client, cfg, request, response = transport
    response.status_code = 503
    response.json.side_effect = ValueError("malformed failure")
    with pytest.raises(SessionError, match="^" + expected + "$"):
        client._request(client.binding(), method, "session/active", isolated=True)
    response.json.assert_not_called()
    request.assert_called_once()
    assert cfg.paired and cfg.device_token == "synthetic-token"


@pytest.mark.parametrize("body,code", [
    (None, ""),
    ({"success": False, "error": "active_session_not_found"}, "active_session_not_found"),
])
def test_current_read_distinguishes_proxy_404_from_explicit_session_absence(transport, body, code):
    client, _, request, response = transport
    response.status_code = 404
    if body is None:
        response.iter_content.return_value = [b"404: Not Found"]
        response.iter_content.side_effect = None
    else:
        response.json.return_value = body
    with pytest.raises(SessionError, match="^unavailable$") as failure:
        client.active(client.binding())
    assert failure.value.code == code
    request.assert_called_once()


@pytest.mark.parametrize("code", ["device_not_mapped", "profile_required", "invalid_profile", "profile_access_denied"])
def test_profile_rejection_is_distinct_from_transport_outage_and_never_replayed(transport, code):
    client, cfg, request, response = transport
    response.status_code = 409
    response.json.return_value = {"success": False, "error": code, "message": "private server context"}
    with pytest.raises(SessionError, match="profile_unavailable"):
        client.active(client.binding())
    assert request.call_count == 1
    assert cfg.paired is True
    assert cfg.explicit_profile_id == "explicit-profile"



def _profile_scope_response(response, owner="resolved-profile"):
    response.json.return_value = {"success": True, "owner_profile_id": owner,
        "history_scope": "profile", "user_id": None, "messages": []}


def test_profile_scope_read_is_isolated_closed_and_discards_bodies(transport):
    client, cfg, request, response = transport
    cfg.explicit_profile_id = ""
    _profile_scope_response(response)
    response.json.return_value["messages"] = [{"text": "not imported"}]
    assert client.private_scope_owner(client.binding()) == "resolved-profile"
    args, kw = request.call_args
    assert args == ("GET", "http://fixture.invalid/api/djconnect/v1/ask_dj/history")
    assert kw["params"]["conversation_scope"] == "profile" and kw["params"]["history_contract_version"] == 2
    assert kw["allow_redirects"] is False and kw["stream"] is True
    assert request.call_count == 1


@pytest.mark.parametrize("status,category", [(401, "denied"), (403, None)])
def test_profile_scope_denial_precedes_body_without_replay(transport, status, category):
    client, _, request, response = transport
    response.status_code = status
    response.iter_content.side_effect = AssertionError("denial must not decode body")
    if category:
        with pytest.raises(SessionError, match=category):
            client.private_scope_owner(client.binding())
    else:
        assert client.private_scope_owner(client.binding()) is None
    response.iter_content.assert_not_called()
    assert request.call_count == 1


@pytest.mark.parametrize("changes", [{"history_scope": "user"}, {"owner_profile_id": "other-owner"}, {"owner_profile_id": " owner "}, {"owner_profile_id": None}, {"user_id": "ha-user"}, {"messages": None}, {"messages": ["body"]}])
def test_malformed_profile_scope_cannot_admit_owner(transport, changes):
    client, cfg, _, response = transport
    cfg.explicit_profile_id = ""
    _profile_scope_response(response); response.json.return_value.update(changes)
    with pytest.raises(SessionError, match="invalid"):
        client.private_scope_owner(client.binding())


def test_scope_denial_handling_cannot_be_selected_for_other_routes(transport):
    client, _, request, _ = transport
    with pytest.raises(SessionError, match="invalid"):
        client._request(client.binding(), "POST", "command", {"command": "play"}, private_probe=True)
    request.assert_not_called()


def test_owner_witness_changes_binding_without_config_type_or_selection(transport):
    client, cfg, _, _ = transport
    cfg.explicit_profile_id = cfg.active_profile_id = ""
    client.owner_scope = lambda: ("server-owner", True, 1)
    before = vars(cfg).copy()
    binding = client.binding()
    assert binding.profile_id == binding.resolved_profile_id == "server-owner"
    assert binding.private_authorized and binding.profile_type == binding.privacy_mode == ""
    assert vars(cfg) == before
    client.owner_scope = lambda: ("server-owner", False, 2)
    assert binding != client.binding()


def test_owner_witness_cannot_override_existing_local_choice(transport):
    client, cfg, _, _ = transport
    client.owner_scope = lambda: ("injected", True, 1)
    assert client.binding().profile_id == cfg.explicit_profile_id
    assert not client.binding().private_authorized


def test_display_owner_rejects_local_http_without_sending_bearer(transport):
    client, _, request, _ = transport
    with pytest.raises(SessionError, match="https_required"):
        client.display_owner(client.binding(), "context", {"session_id": "session"})
    request.assert_not_called()


def test_display_owner_uses_closed_device_identity_and_never_retries(transport):
    client, cfg, request, response = transport
    cfg.ha_url = "https://fixture.invalid"
    response.json.return_value = {"success": True, "contract_version": 1}
    client.display_owner(client.binding(), "resolve", {"code": "012345"})
    args, kwargs = request.call_args
    assert args == ("POST", "https://fixture.invalid/api/djconnect/v1/display/owner/resolve")
    assert kwargs["json"] == {
        "contract_version": 1,
        "identity": {"device_id": cfg.device_id, "client_type": "raspberry_pi"},
        "code": "012345",
    }
    assert "X-DJConnect-Profile-ID" not in kwargs["headers"]
    assert not kwargs["allow_redirects"]

    request.reset_mock(side_effect=True)
    request.side_effect = requests.Timeout()
    with pytest.raises(SessionError, match="uncertain"):
        client.display_owner(client.binding(), "revoke", {"session_id": "session", "share_id": "share"})
    with pytest.raises(SessionError, match="invalid"):
        client.display_owner(client.binding(), "resolve", {"code": "012345", "profile_id": "foreign"})
    request.assert_called_once()
