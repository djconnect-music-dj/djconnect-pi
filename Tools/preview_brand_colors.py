"""Render actual Pi pages with isolated fixtures; no HA, pairing or service changes."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, QPointF, QTimer, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend, parse_playlist_items, parse_queue_items
from djconnect_pi.brand_theme import apply_palette
from djconnect_pi.config import Config, save_config
from djconnect_pi.expiry_clock import ExpiryClock
from djconnect_pi.ha import Playback
from djconnect_pi.session_history_view import presented_history, search_page
from djconnect_pi.session_projection import history_page, timestamp
from djconnect_pi.update_ui import UpdateUiBackend

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    apply_palette(app)
    checks = []
    for width, height, profile in [(720, 720, "pi-zero-2w-arm64"), (1200, 1920, "pi5-arm64")]:
        config = args.output / f"fixture-{width}.json"
        save_config(config, Config(language="nl", release_profile=profile, log_file=str(args.output / "fixture.log"), screen_timeout_seconds=0))
        with patch.object(QTimer, "singleShot"):
            backend = DJConnectBackend(config)
        for timer in backend.findChildren(QTimer):
            timer.stop()
        backend._executor.submit = Mock()
        backend._session_executor.submit = backend._executor.submit
        backend._question_executor.submit = backend._executor.submit
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("djconnect", backend)
        engine.rootContext().setContextProperty("startWindowed", True)
        engine.load(str(ROOT / "src/djconnect_pi/qml/Main.qml"))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.setWidth(width)
        window.setHeight(height)
        window.setProperty("splashVisible", False)
        # Keep fixture reads explicit; wall-clock polling would race language/scope probes.
        for obj in window.findChildren(QObject):
            if 'Timer' in obj.metaObject().className() and obj.property('interval') in (3000,15000):
                pause = QQmlExpression(engine.rootContext(), obj, 'running = false')
                pause.evaluate()
                assert not pause.hasError(), pause.error().toString()

        def visual_item(item, name):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                found = visual_item(child, name)
                if found is not None:
                    return found
            return None

        def capture(name):
            QTest.qWait(200)
            image = window.grabWindow()
            assert not image.isNull(), name
            target = args.output / f"{name}-{width}x{height}.png"
            assert image.save(str(target))
            checks.append({"screen": name, "size": [image.width(), image.height()], "file": target.name})

        capture("pairing")
        backend.enterDemoMode()
        backend._toast_visible = False
        backend.toastChanged.emit()
        for screen in ["session", "history", "now", "control", "queue", "playlists", "askdj", "trackinsight", "discover", "musicdna", "more", "settings", "games", "legal", "privacy", "feedback"]:
            window.setProperty("activeScreen", screen)
            capture(screen)
        track = {"title": "Repeated track title with more detail " * 12, "uri": "spotify:track:fixture-a", "index": 4}
        backend._queue_items = parse_queue_items({"queue": [
            track, track, {"title": "Other track", "uri": "spotify:track:fixture-b", "index": 5},
            {**track, "index": 6},
        ]})
        backend.mediaListsChanged.emit()
        window.setProperty("activeScreen", "queue")
        QTest.qWait(120)
        media_rows = visual_item(window.contentItem(), "mediaRows")
        assert media_rows.property("count") == 3
        repeated_row = visual_item(media_rows, "mediaItemRow-2")
        assert repeated_row.property("text") == track["title"]
        payload = QQmlExpression(engine.rootContext(), repeated_row, "modelData.index")
        assert payload.evaluate()[0] == 6 and not payload.hasError()
        long_title = visual_item(repeated_row, "mediaTrackTitle")
        short_title = visual_item(visual_item(media_rows, "mediaItemRow-1"), "mediaTrackTitle")
        assert long_title.property("scrolling") and not short_title.property("scrolling")
        QTest.qWait(1450)
        assert long_title.property("shift") > 0
        long_title.setProperty("reduceMotion", True)
        label = visual_item(long_title, "mediaOverflowLabel")
        assert not long_title.property("scrolling") and long_title.property("shift") == 0
        assert label.property("lineCount") > 1 and not label.property("truncated")
        queue_panel = visual_item(window.contentItem(), "queuePanel")
        records = [{"title": f"Fixture {index:02d}", "uri": f"spotify:track:fixture-{index}", "index": index}
                   for index in range(30)]
        backend._queue_items = parse_queue_items({"queue": records})
        backend.mediaListsChanged.emit()
        queue_panel.setProperty("searching", True)
        search_input = visual_item(queue_panel, "mediaSearchInput")
        search_input.setProperty("text", "fixture")
        count = visual_item(queue_panel, "mediaSearchMatchCount")
        next_match = visual_item(queue_panel, "mediaSearchNextMatch")
        for _ in range(16):
            QMetaObject.invokeMethod(next_match, "clicked")
        QTest.qWait(120)
        assert count.property("text") == "17 / 30"
        selected = visual_item(media_rows, "mediaItemRow-16")
        center = selected.mapToItem(media_rows, 0, selected.height() / 2).y()
        assert abs(center - media_rows.height() / 2) < selected.height()
        backend._queue_items = parse_queue_items({"queue": records[:18]})
        backend.mediaListsChanged.emit()
        QTest.qWait(120)
        assert count.property("text") == "17 / 18"
        search_input.setProperty("text", "fixture 0")
        QTest.qWait(80)
        assert count.property("text") == "1 / 10"
        backend._playlist_items = parse_playlist_items({"playlists": [{
            "name": "Long playlist title with more detail " * 12,
            "owner": "Fixture curator", "uri": "spotify:playlist:fixture-only",
        }]})
        backend.mediaListsChanged.emit()
        window.setProperty("activeScreen", "playlists")
        QTest.qWait(120)
        playlist_panel = visual_item(window.contentItem(), "playlistsPanel")
        playlist_row = visual_item(playlist_panel, "mediaItemRow-0")
        playlist_title = visual_item(playlist_row, "mediaTrackTitle")
        assert not playlist_title.property("scrolling")
        assert visual_item(playlist_title, "mediaOverflowLabel").property("lineCount") == 2
        assert not visual_item(playlist_row, "mediaTrackSubtitle").property("visible")
        assert visual_item(playlist_row, "mediaPlayIndicator").property("visible")
        playlist_panel.setProperty("searching", True)
        search_input = visual_item(playlist_panel, "mediaSearchInput")
        for term in ("curator", "fixture-only"):
            search_input.setProperty("text", term)
            QTest.qWait(80)
            assert visual_item(playlist_panel, "mediaSearchMatchCount").property("text") == "1 / 1"
        backend._demo_mode = False
        backend.demoModeChanged.emit()
        assert not visual_item(playlist_panel, "mediaItemRow-0").property("enabled")
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        backend.pairedChanged.emit()
        backend._set_backend_available(False)
        QTest.qWait(65)
        assert not visual_item(playlist_panel, "mediaItemRow-0").property("enabled")
        assert visual_item(playlist_panel, "mediaRefresh").property("enabled")
        backend._set_backend_available(True)
        assert visual_item(playlist_panel, "mediaItemRow-0").property("enabled")
        backend.playback = Playback(title="Confirmed previous track", uri="fixture:previous")
        with (patch.object(backend.client, "command", return_value={"success": True}),
              patch.object(backend, "_persist_backend_summary"),
              patch.object(backend, "_load_queue_worker"),
              patch.object(backend, "_refresh_worker") as refresh,
              patch("djconnect_pi.app.subprocess.run")):
            backend._play_media_item_worker("start_playlist", {"uri": "fixture:playlist"})
            refresh.assert_called_once_with()
            assert backend.title == "Confirmed previous track"
            assert window.property("activeScreen") == "playlists"
            backend._apply_playback(Playback(title="Confirmed playlist track", uri="fixture:started", is_playing=True))
            assert backend.title == "Confirmed playlist track" and window.property("activeScreen") == "playlists"
            assert not window.property("trackArtworkOpen")
        with patch.object(backend, "_sync_config_from_disk"):
            window.setProperty("activeScreen", "queue")
            QTest.qWait(80)
        with (patch.object(backend.client, "command", side_effect=[
                  {"success": True}, {"queue": [{"title": "Next after selection", "uri": "fixture:next"}]},
              ]), patch.object(backend, "_persist_backend_summary"), patch.object(backend, "_refresh_worker"),
              patch.object(backend, "_cache_media_artwork_async")):
            backend._play_media_item_worker("play_context_at", {"uri": "fixture:chosen"})
            QTest.qWait(80)
            row = visual_item(window.contentItem(), "mediaItemRow-0")
            assert row.property("text") == "Next after selection" and window.property("activeScreen") == "queue"
        window.setProperty("activeScreen", "playlists")
        QTest.qWait(80)
        playlist_panel = visual_item(window.contentItem(), "playlistsPanel")
        backend.cfg.music_backend_available = False
        backend.settingsChanged.emit()
        assert not visual_item(playlist_panel, "mediaItemRow-0").property("enabled")
        backend.cfg.paired, backend.cfg.device_token, backend.cfg.music_backend_available = False, "", True
        backend._demo_mode = True
        backend.pairedChanged.emit()
        backend.demoModeChanged.emit()
        backend.settingsChanged.emit()
        toast_text = visual_item(window.contentItem(), "appToastText")
        for language in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(language)
            for command, uri, key in (
                ("play_context_at", "demo:track:2", "media_track_starting"),
                ("start_playlist", "demo:playlist:night", "media_playlist_starting"),
            ):
                backend.playMediaItem(command, uri)
                QTest.qWait(35)
                assert toast_text.property("text") == backend.t(key)
                assert not toast_text.property("truncated") and backend.toastIcon == "play"
        backend.hideToast()
        backend.setLanguage("nl")
        window.setProperty("activeScreen", "trackinsight")
        backend._track_insight_text = "Fixture interpretation"
        backend._track_insight_audio_notice = True
        backend._track_insight_sections = [
            {"title": "Production", "body": "Fixture analysis", "source": "Fixture <source>"},
            {"title": "Genre", "body": "Fixture description"},
        ]
        backend.trackInsightChanged.emit()
        QTest.qWait(120)
        sourced = visual_item(window.contentItem(), "trackInsightSection-0")
        unsourced = visual_item(window.contentItem(), "trackInsightSection-1")
        label = visual_item(sourced, "trackInsightSectionSource")
        assert label.property("visible") and label.property("text") == "Bronnen · Fixture <source>"
        assert not visual_item(unsourced, "trackInsightSectionSource").property("visible")
        plain = QQmlExpression(engine.rootContext(), label, "textFormat === 0")
        assert plain.evaluate()[0] and not plain.hasError()
        analysis_heading = visual_item(window.contentItem(), "trackInsightAnalysisHeading")
        notice = visual_item(window.contentItem(), "trackInsightUnmeasuredNotice")
        for language in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(language)
            QTest.qWait(35)
            assert notice.property("visible") and notice.property("text") == backend.t("track_insight_unmeasured_audio")
            assert analysis_heading.property("text") == backend.t("track_insight_analysis_heading")
        backend._track_insight_audio_notice = False
        backend._track_insight_verified_credit = {
            "context": "Fixture verified credit", "provider": "Fixture catalogue",
            "url": "https://example.test/fixture-credit", "license": "CC0-1.0",
        }
        backend.trackInsightChanged.emit()
        QTest.qWait(65)
        credit_card = visual_item(window.contentItem(), "trackInsightVerifiedCredits")
        assert credit_card.property("visible")
        assert visual_item(credit_card, "trackInsightVerifiedContext").property("text") == "Fixture verified credit"
        link = visual_item(credit_card, "sessionAttributionSourceLink")
        assert link.property("targetUrl") == "https://example.test/fixture-credit"
        for language in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(language)
            QTest.qWait(35)
            assert analysis_heading.property("text") == backend.t("track_insight_interpretation")
        backend._track_insight_text = "Fixture verified credit"
        backend.trackInsightChanged.emit()
        assert credit_card.property("visible") and not analysis_heading.property("visible")
        assert not notice.property("visible")
        backend.setLanguage("nl")
        backend._demo_mode = False
        backend._apply_track_insight_data({
            "track": {"title": "Fixture metadata"},
            "analysis": {"basis": "unmeasured_metadata_only_v1", "measured_audio": False,
                         "summary": "Geen audioanalyse beschikbaar."},
            "visualisation": {"bars": [0.3, 0.7]},
        })
        backend._demo_mode = True
        QTest.qWait(65)
        visualizer = visual_item(window.contentItem(), "trackInsightVisualizer")
        assert not visualizer.property("visible")
        analysis_text = visual_item(window.contentItem(), "trackInsightAnalysisText")
        assert analysis_text.property("visible") and analysis_text.property("text") == "Geen audioanalyse beschikbaar."
        assert not credit_card.property("visible") and not notice.property("visible")
        backend._demo_mode = False
        backend.playback.is_playing = False
        backend.playingChanged.emit()
        backend.demoModeChanged.emit()
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        backend.pairedChanged.emit()
        backend._apply_track_insight_data({"error": "transient"})
        QTest.qWait(65)
        retry = visual_item(window.contentItem(), "trackInsightRetry")
        assert retry.property("visible") and retry.property("enabled")
        for language in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(language)
            backend._apply_track_insight_data({"error": "transient"})
            QTest.qWait(35)
            assert retry.property("text") == backend.t("track_insight_retry_action")
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            QMetaObject.invokeMethod(retry, "clicked")
            request.assert_called_once()
            assert not retry.property("enabled")
            subject = visual_item(window.contentItem(), "trackInsightSubjectHero")
            subject_title = visual_item(subject, "trackInsightSubjectTitle")
            assert subject.property("visible") and subject_title.property("text") == backend.title
            with patch("djconnect_pi.app.subprocess.run"):
                backend._apply_playback(Playback(title="Fixture next track", artist="Fixture artist", uri="fixture:next-track"))
            QTest.qWait(65)
            assert subject_title.property("text") == "Fixture next track"
            assert visual_item(subject, "trackInsightSubjectMetadata").property("text") == "Fixture artist"
            assert not analysis_text.property("visible")
        backend._set_ask_dj_busy(False)
        with (patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request,
              patch("djconnect_pi.app.subprocess.run")):
            backend._apply_playback(Playback(title="Playing fixture", uri="fixture:auto-first", is_playing=True))
            QTest.qWait(65)
            request.assert_called_once()
            backend._apply_playback(Playback(title="Following fixture", uri="fixture:auto-next", is_playing=True))
            QTest.qWait(65)
            request.assert_called_once()
            backend._set_ask_dj_busy(False)
            QTest.qWait(65)
            assert request.call_count == 2
            backend._set_ask_dj_busy(False)
            backend._apply_playback(Playback(title="Following fixture, corrected", uri="fixture:auto-next", is_playing=True))
            QTest.qWait(65)
            assert request.call_count == 2
            backend._apply_track_insight_data({
                "track": {"title": "Following fixture, corrected"},
                "analysis": {"summary": "Previous language"}
            })
            assert backend.trackInsightText
            backend.setLanguage("nl")
            QTest.qWait(65)
            assert not backend.trackInsightText and request.call_count == 3
            backend._set_ask_dj_busy(False)
        backend.cfg.paired, backend.cfg.device_token = False, ""
        backend._demo_mode = True
        backend.pairedChanged.emit()
        backend.demoModeChanged.emit()
        backend.hideToast()
        backend.setLanguage("nl")
        window.setProperty("activeScreen", "musicdna")
        backend._demo_mode = False
        backend._music_dna_loaded = False
        backend._music_dna_enabled = False
        backend._set_music_dna_busy(True)
        QTest.qWait(100)
        loading = visual_item(window.contentItem(), "musicDnaLoadingCard")
        opt_in = visual_item(window.contentItem(), "musicDnaOptInHero")
        assert loading.property("visible") and not opt_in.property("visible")
        assert visual_item(loading, "musicDnaLoadingText").property("text") == backend.t("music_dna_loading_profile")
        backend._apply_music_dna_data({"success": True})
        backend._set_music_dna_busy(False)
        unavailable = visual_item(window.contentItem(), "musicDnaErrorCard")
        assert unavailable.property("visible") and not opt_in.property("visible")
        assert visual_item(unavailable, "musicDnaUnavailableText").property("text") == backend.t("music_dna_profile_unavailable")
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        backend.pairedChanged.emit()
        reload_button = visual_item(unavailable, "musicDnaReloadProfile")
        assert reload_button.property("enabled")
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            QMetaObject.invokeMethod(reload_button, "clicked")
            request.assert_called_once()
            assert loading.property("visible") and not reload_button.property("enabled")
        backend.cfg.paired, backend.cfg.device_token = False, ""
        backend.pairedChanged.emit()
        backend._apply_music_dna_data({"success": True, "enabled": False, "profile": {}})
        backend._set_music_dna_busy(False)
        assert not loading.property("visible") and opt_in.property("visible")
        backend._apply_music_dna_data({"success": True, "enabled": True, "profile": {}})
        backend._demo_mode = True
        QTest.qWait(100)
        no_profile = visual_item(window.contentItem(), "musicDnaNoProfile")
        heading = visual_item(no_profile, "musicDnaNoProfileHeading")
        detail = visual_item(no_profile, "musicDnaNoProfileDetail")
        for language in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(language)
            QTest.qWait(35)
            assert no_profile.property("visible")
            assert heading.property("text") == backend.t("music_dna_no_profile_yet")
            assert detail.property("text") == backend.t("music_dna_building_profile")
            assert detail.property("contentWidth") <= detail.width() + 1
        backend._set_demo_music_dna(False)
        assert not no_profile.property("visible")
        backend._set_demo_music_dna(True)
        assert not no_profile.property("visible")
        backend._demo_mode = False
        backend._apply_music_dna_data({
            "success": True, "enabled": True,
            "profile": {"summary": "Fixture profile", "updated_at": "2026-10-19T14:10:00Z"},
        })
        backend._demo_mode = True
        QTest.qWait(65)
        footer = visual_item(window.contentItem(), "musicDnaUpdatedFooter")
        assert footer.property("visible")
        assert footer.property("text") == backend.t("music_dna_updated_at").replace(
            "%1", backend.djSession.formatTimestamp("2026-10-19T14:10:00Z", True))
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            backend._demo_mode = False
            backend.demoModeChanged.emit()
            backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
            backend.cfg.active_profile_id = "profile-first"
            backend.pairedChanged.emit()
            QTest.qWait(65)
            request.assert_called_once()
            backend.cfg.active_profile_id = "profile-next"
            backend._clear_profile_scoped_caches(preserve_session=True)
            backend.profileChanged.emit()
            QTest.qWait(65)
            assert not footer.property("visible") and backend.musicDnaSummary == ""
            request.assert_called_once()
            backend._set_music_dna_busy(False)
            QTest.qWait(65)
            assert request.call_count == 2 and backend.musicDnaBusy
            backend.cfg.paired, backend.cfg.device_token, backend.cfg.active_profile_id = False, "", ""
            backend.pairedChanged.emit()
            backend._set_music_dna_busy(False)
            backend._demo_mode = True
            backend.demoModeChanged.emit()
        backend._set_demo_music_dna(True)
        assert not footer.property("visible")
        window.setProperty("activeScreen", "settings")
        QTest.qWait(65)
        status = visual_item(window.contentItem(), "musicDnaSettingsStatusText")
        toggle = visual_item(window.contentItem(), "musicDnaSettingsToggle")
        clear = visual_item(window.contentItem(), "musicDnaSettingsClear")
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            backend._clear_profile_scoped_caches(preserve_session=True)
            assert status.property("text") == backend.t("music_dna_profile_unavailable")
            assert not toggle.property("enabled") and not clear.property("enabled")
            backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
            backend.pairedChanged.emit()
            backend._demo_mode = False
            backend.demoModeChanged.emit()
            QTest.qWait(65)
            request.assert_called_once()
            assert status.property("text") == backend.t("music_dna_loading_profile")
            assert not toggle.property("enabled") and not clear.property("enabled")
            backend._apply_music_dna_data({"success": True, "enabled": True})
            backend._set_music_dna_busy(False)
            assert status.property("text") == backend.t("music_dna_settings_enabled_description")
            assert toggle.property("enabled") and clear.property("enabled")
            backend._apply_music_dna_data({"success": True, "enabled": False})
            assert status.property("text") == backend.t("music_dna_settings_disabled_description")
            assert toggle.property("enabled") and not clear.property("enabled")
            backend._apply_music_dna_data({"success": True, "enabled": True})
            QMetaObject.invokeMethod(toggle, "clicked")
            assert window.property("musicDnaDisableConfirmOpen")
            backend._clear_profile_scoped_caches(preserve_session=True)
            assert not window.property("musicDnaDisableConfirmOpen")
            backend._apply_music_dna_data({"success": True, "enabled": True})
            QMetaObject.invokeMethod(clear, "clicked")
            assert window.property("musicDnaClearConfirmOpen")
            backend.pairedChanged.emit()
            assert not window.property("musicDnaClearConfirmOpen")
            request.assert_called_once()
            backend._demo_mode = True
            backend.demoModeChanged.emit()
            backend.cfg.paired, backend.cfg.device_token = False, ""
            backend.pairedChanged.emit()
        backend._set_demo_music_dna(False)
        opt_in = window.findChild(QObject, "musicDnaOptInSheet")
        accept = window.findChild(QObject, "musicDnaConfirmEnable")
        QMetaObject.invokeMethod(toggle, "clicked")
        QTest.qWait(280)
        assert opt_in.property("opened") and accept.property("enabled")
        backend._clear_profile_scoped_caches(preserve_session=True)
        assert not opt_in.property("opened") and not accept.property("enabled")
        QTest.qWait(280)
        backend._set_demo_music_dna(False)
        QMetaObject.invokeMethod(toggle, "clicked")
        QTest.qWait(280)
        QMetaObject.invokeMethod(accept, "clicked")
        assert backend.musicDnaEnabled
        backend._set_demo_music_dna(True)
        window.setProperty("activeScreen", "discover")
        QTest.qWait(65)
        gate = visual_item(window.contentItem(), "discoveryMusicDnaGate")
        empty_feed = visual_item(window.contentItem(), "discoveryEmptyFeedText")
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        backend.pairedChanged.emit()
        backend._demo_mode = False
        backend.demoModeChanged.emit()
        backend._clear_profile_scoped_caches(preserve_session=True)
        backend._set_music_discovery_busy(True)
        assert not gate.property("visible") and not empty_feed.property("visible")
        backend._set_music_discovery_busy(False)
        assert not gate.property("visible") and empty_feed.property("visible")
        backend._apply_music_discovery_data({"success": False, "error": "music_dna_context_changed"})
        assert not gate.property("visible") and empty_feed.property("text") == backend.t("session_profile_missing")
        backend._apply_music_discovery_data({"success": False, "error": "music_dna_disabled"})
        assert gate.property("visible") and not empty_feed.property("visible")
        backend._apply_music_dna_data({"success": True, "enabled": True})
        assert not gate.property("visible") and empty_feed.property("text") == backend.t("music_discovery_empty")
        backend._apply_music_discovery_data({"success": True, "sections": [
            {"id": "fixture-shelf", "items": [{"id": "fixture-track", "title": "Cached recommendation"}]},
        ]})
        assert not empty_feed.property("visible")
        detail = window.findChild(QObject, "discoveryDetail")
        detail.setProperty("selected", backend.musicDiscoveryItems[0])
        QMetaObject.invokeMethod(detail, "open")
        backend._set_music_discovery_busy(True)
        QTest.qWait(240)
        backend._set_music_discovery_busy(False)
        assert detail.property("opened")
        backend._apply_music_discovery_data({"success": False, "error": "music_discovery_play_failed"})
        detail_error = window.findChild(QObject, "discoveryDetailError")
        assert detail.property("opened") and detail_error.property("visible")
        assert detail_error.property("text") == backend.t("music_discovery_play_failed")
        backend._apply_music_discovery_data({"success": False, "error": "music_discovery_feedback_failed"})
        assert detail.property("opened") and detail_error.property("visible")
        assert detail_error.property("text") == backend.t("music_discovery_feedback_failed")
        backend.musicDiscoveryPlayed.emit("fixture-shelf", "fixture-track")
        assert not detail.property("opened")
        backend._apply_music_discovery_data({"success": False, "error": "music_discovery_unavailable"})
        assert empty_feed.property("visible") and empty_feed.property("text") == backend.t("music_discovery_load_failed")
        assert backend.musicDiscoveryItems[0]["title"] == "Cached recommendation"
        backend._apply_music_discovery_data({"success": True, "sections": []})
        assert empty_feed.property("text") == backend.t("music_discovery_empty")
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            QTest.qWait(65)
            request.assert_called_once()
            backend.cfg.active_profile_id = "discovery-next-profile"
            backend._clear_profile_scoped_caches(preserve_session=True)
            backend.profileChanged.emit()
            QTest.qWait(65)
            request.assert_called_once()
            backend._set_music_discovery_busy(False)
            QTest.qWait(65)
            assert request.call_count == 2 and backend.musicDiscoveryBusy
            backend._apply_music_discovery_data({"success": True, "sections": []})
            backend._set_music_discovery_busy(False)
            QTest.qWait(65)
            assert request.call_count == 2 and backend.musicDiscoveryLoaded
        backend._demo_mode = True
        backend.demoModeChanged.emit()
        backend.cfg.paired, backend.cfg.device_token, backend.cfg.active_profile_id = False, "", ""
        backend.pairedChanged.emit()
        backend._set_demo_music_dna(True)
        backend.setLanguage("nl")
        window.setProperty("activeScreen", "session")
        for runtime_state in ("opening", "ending"):
            backend.djSession._session["runtime_state"] = runtime_state
            backend.djSession.changed.emit()
            capture("session-" + runtime_state)
        backend.djSession._session["runtime_state"] = "active"
        backend.djSession.changed.emit()
        # Explicit synthetic current-grant fixture: decode -> controller -> actual QML,
        # then move elapsed time beyond its original expiry while wall time rolls back.
        for language in ['en','nl','de','fr','es']:
            backend.setLanguage(language);backend._toast_visible=False;backend.toastChanged.emit()
            for destination,button_name in [('legal','openLegal'),('privacy','openPrivacy'),('feedback','openFeedback')]:
                window.setProperty('activeScreen','more');QTest.qWait(50)
                QMetaObject.invokeMethod(window.findChild(QObject,button_name),'clicked')
                assert window.property('activeScreen')==destination
                assert visual_item(window.contentItem(),'appTab-more').property('checked')
                capture(destination+'-menu-'+language)
                if destination == 'feedback':
                    with patch('djconnect_pi.app.QDesktopServices.openUrl', return_value=True) as browser:
                        assert browser.call_count == 0
                        QMetaObject.invokeMethod(window.findChild(QObject,'feedbackOpenIssue'),'clicked')
                        browser.assert_called_once()
                        from urllib.parse import parse_qs, urlsplit
                        url=browser.call_args.args[0].toString();parsed=urlsplit(url)
                        assert parsed.netloc=='github.com' and parsed.path=='/djconnect-music-dj/djconnect-pi/issues/new'
                        assert parse_qs(parsed.query)=={'title':[backend.t('feedback_issue_title')],'body':[backend.t('feedback_issue_template')]}
                        assert not window.findChild(QObject,'feedbackOpenError').property('visible')
                    capture('feedback-open-draft-only-'+language)
                    with patch('djconnect_pi.app.QDesktopServices.openUrl', return_value=False) as browser:
                        QMetaObject.invokeMethod(window.findChild(QObject,'feedbackOpenIssue'),'clicked')
                        assert window.findChild(QObject,'feedbackOpenError').property('visible')
                        browser.assert_called_once()
                    capture('feedback-no-browser-'+language)

                info_scroll=window.findChild(QObject,'informationScroll').property('contentItem')
                if language=='nl':
                    info_scroll.setProperty('contentY',max(0,info_scroll.property('contentHeight')-info_scroll.property('height')))
                    capture(destination+'-scrolled-end')
                QMetaObject.invokeMethod(window.findChild(QObject,'informationBack'),'clicked')
                assert window.property('activeScreen')=='more'
        backend.setLanguage('nl');backend._toast_visible=False;backend.toastChanged.emit()
        capture('more-adaptive-menu')
        window.setProperty('activeScreen','session')
        owner = backend.djSession
        original_clock = owner._clock
        now = datetime.now(UTC)
        clock_host = SimpleNamespace(wall=now.timestamp(), tick=0)
        owner._clock = ExpiryClock(lambda: clock_host.wall, lambda: clock_host.tick)
        deadline = (now + timedelta(seconds=3)).isoformat()
        owner._session = {"session_id": "preview-native-session", "runtime_state": "active", "broadcast": {
            "session_flow": {"items": [{"item_type": "dj_moment", "moment_id": "preview-moment"}]},
            "dj_moments": [{"moment_id": "preview-moment", "session_id": "preview-native-session",
                "title": "Offline fixture", "content": "Synthetic contribution for expiry rendering.",
                "presentation_intent": {"visibility": "owner_only"}}],
            "native_delivery": {"schema_version": 1, "session_id": "preview-native-session",
                "revision": "preview-native-revision", "revocation_scope": "session",
                "current_moment_id": "preview-moment", "active_flow_moment_ids": ["preview-moment"],
                "admissions": [{"moment_id": "preview-moment", "qualification": "qualified",
                    "executable_actions": [], "current_display_allowed": True, "active_flow_display_allowed": True,
                    "source_expires_at": deadline, "display_expires_at": deadline}]}}}
        owner._expire()
        QTest.qWait(100)
        live_rows = window.findChild(QObject, "sessionLiveMomentRows")
        assert owner.state["moments"] and live_rows is not None and live_rows.property("count") == 1
        scroll = window.findChild(QObject, "sessionHistoryScroll").property("contentItem")
        scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.property("height")))
        capture("session-native-grant")
        clock_host.wall -= 3600
        clock_host.tick = 4
        owner._expire()
        capture("session-native-expired")
        assert live_rows.property("count") == 0, "Expired source remained rendered"
        owner._expire()  # same cached wire projection cannot resurrect after rollback
        assert not owner.state["moments"]
        owner.setForeground(False)
        capture("session-background-cleared")
        assert not owner.state["session_id"] and not owner.state["timeline"]
        owner.setForeground(True)
        owner._clock = original_clock
        # Unchanged published Core snapshots, frozen at their original render time.
        native_frames = json.loads((ROOT / "tests/fixtures/session_core/native-expressive-snapshots.json").read_text())
        for family, persona in [("birth", "home_dj"), ("formation", "home_dj"), ("description", "home_dj"),
                                ("edition", "home_dj"), ("genre", "home_dj"), ("genre", "club_dj")]:
            snapshot = native_frames[family]["nl"][persona][-1]
            authority = snapshot["native_delivery"]
            grant = next((row for row in authority["admissions"] if row["moment_id"] == authority["current_moment_id"]), None)
            fixture_time = timestamp(grant["display_expires_at"]) - 1 if grant else max(timestamp(row["created_at"]) for row in snapshot["dj_moments"]) + 1
            owner._clock = ExpiryClock(lambda stamp=fixture_time: stamp, lambda: 0)
            owner._session = {**snapshot["session"], "broadcast": snapshot}
            owner._loaded = True
            owner._expire()
            owner.changed.emit()
            QTest.qWait(100)
            scroll.setProperty("contentY", 0)
            capture("core-native-" + family + "-" + persona)
            assert live_rows.property("count") == len(owner.state["moments"])
            if owner.state["moments"]:
                current_card = visual_item(window.contentItem(), "sessionLiveMomentCard")
                assert current_card is not None
                top = current_card.mapToItem(scroll, QPointF(0, 0)).y()
                assert 0 <= top < scroll.property("height") / 2, (width, family, "contribution below fold", top)
            if family == "genre" and persona == "home_dj":
                assert not owner.state["moments"]
        owner._clock = original_clock
        owner._expire()
        # Render retained source/receipt kinds and the real search popup; data remains a fixture.
        tail = json.loads((ROOT / "tests/fixtures/session_core/retention-v2-touch.json").read_text())["tail_window"]
        sid = tail["session"]["session_id"]
        owner = backend.djSession
        owner._timeline = presented_history(history_page(tail, session_id=sid))
        owner._caps = {"capabilities": {"session_conversation_history": True, "session_flow_text_search": True},
                       "contract_versions": {"session_history_entries": 2, "session_flow_text_search": 1}}
        owner.changed.emit()
        window.setProperty("activeScreen", "history")
        capture("history-detail")
        session_item = window.findChild(QObject, "djSessionScreen")
        assert session_item is not None
        # Synthetic extra retained rows ensure both viewports can position a non-first anchor.
        extended = deepcopy(owner._timeline)
        for i in range(10):
            extra = deepcopy(tail["entries"][-1])
            extra.update(entry_id=f"preview-extra-{i}", order=100 + i)
            extended["entries"].append(extra)
        owner._timeline = extended
        owner.changed.emit()
        QTest.qWait(100)
        # Recreate real layout replacement and confirm the visible reading identity survives.
        anchor = tail["entries"][1]["entry_id"]
        session_item.setProperty("readingOffset", 0)
        owner.timelineUpdated.emit(anchor)
        QTest.qWait(100)
        actual_anchor = QMetaObject.invokeMethod(session_item, "readingAnchor", Q_RETURN_ARG("QVariant"))
        assert actual_anchor == anchor, (width, actual_anchor, anchor)
        refreshed = deepcopy(owner._timeline)
        refreshed["entries"][0]["text"] = None
        owner._timeline = refreshed
        owner.changed.emit()
        owner.timelineUpdated.emit(anchor)
        QTest.qWait(100)
        actual_anchor = QMetaObject.invokeMethod(session_item, "readingAnchor", Q_RETURN_ARG("QVariant"))
        assert actual_anchor == anchor, (width, "reading position lost", actual_anchor, anchor)
        capture("history-reading-position")
        owner._timeline = presented_history(history_page(tail, session_id=sid))
        owner.changed.emit()
        assert QMetaObject.invokeMethod(session_item, "showSearch")
        keyboard = window.findChild(QObject, "searchTouchKeyboard")
        search_input = window.findChild(QObject, "sessionSearchInput")
        assert keyboard is not None and search_input is not None
        assert QMetaObject.invokeMethod(keyboard, "insertKey", Q_ARG("QVariant", "é"))
        assert search_input.property("text") == "é"
        assert QMetaObject.invokeMethod(keyboard, "deleteKey")
        assert search_input.property("text") == ""
        assert QMetaObject.invokeMethod(keyboard, "insertKey", Q_ARG("QVariant", "synthetic"))
        assert search_input.property("text") == "synthetic"
        matches = [dict(tail["entries"][i], highlights=[{"start_utf16": 2, "length_utf16": 9}]) for i in (0, 3)]
        owner._search = search_page({"success": True, "schema_version": 2, "history_contract_version": 2,
            "session_id": sid, "query": "synthetic", "revision": tail["revision"], "matches": matches,
            "returned_count": 2, "total_count": 2, "complete": True, "next_cursor": None,
            "normalization": "NFKC-casefold", "highlight_units": "utf16"}, sid, "synthetic")
        owner.changed.emit()
        panel = window.findChild(QObject, "sessionSearchPanel")
        panel.setProperty("keyboardVisible", False)
        capture("history-search")
        displayed = visual_item(window.contentItem(), "sessionSearchHighlightedBody")
        assert displayed is not None and "<b><u>synthetic</u></b>" in displayed.property("text")
        search_result = deepcopy(owner._search)
        for locale in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(locale)
            backend._toast_visible = False
            backend.toastChanged.emit()
            QMetaObject.invokeMethod(panel, "close")
            scroll.setProperty("contentY", 0)
            capture("history-dates-" + locale)
            date_label = visual_item(window.contentItem(), "sessionHistoryEntryDate")
            assert date_label is not None and "2026" in date_label.property("text")
            assert "2026-10-18T" not in date_label.property("text")
            QMetaObject.invokeMethod(session_item, "showSearch")
            panel.setProperty("keyboardVisible", False)
            capture("history-highlights-" + locale)
        backend.setLanguage("nl")
        literal = deepcopy(search_result)
        literal["matches"] = [deepcopy(matches[0])]
        literal["matches"][0].update(text='😀 <img src="https://untrusted.invalid/leak">\nA & B',
            highlights=[{"start_utf16": 0, "length_utf16": 2}])
        literal["returned_count"] = 1
        literal["total_count"] = 1
        owner._search = search_page(literal, sid, "synthetic")
        owner.changed.emit()
        backend._toast_visible = False
        backend.toastChanged.emit()
        capture("history-highlight-literal")
        owner.invalidate()
        assert search_input.property("text") == "", "Private query survives context invalidation"
        owner.closeSaved()
        window.setProperty("activeScreen", "now")
        window.setProperty("aboutOpen", True)
        capture("about")
        window.setProperty("aboutOpen", False)
        backend.showLogs()
        capture("logs")
        backend.hideLogs()
        window.setProperty("rebootConfirmOpen", True)
        capture("confirmation")
        backend.shutdown()
        delete(window)
        delete(engine)
        updater = UpdateUiBackend(args.output / "update-fixture.json", "http://127.0.0.1:18080", "nl", profile)
        updater._progress = 42
        updater._current_version = "fixture"
        updater._target_version = "fixture-next"
        updater._message = "DJConnect"
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("updater", updater)
        engine.rootContext().setContextProperty("startWindowed", True)
        engine.load(str(ROOT / "src/djconnect_pi/qml/UpdateProgress.qml"))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.setWidth(width)
        window.setHeight(height)
        QTest.qWait(600)
        capture("updater")
        delete(window)
        delete(engine)
    errors = [message for message in warnings if any(word in message for word in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "is not a type", "failed to load", "Binding loop detected"))]
    (args.output / "readback.json").write_text(json.dumps({"checks": checks, "qml_errors": errors, "proof": "offline fixture rendering, not installed or physical acceptance"}, indent=2))
    assert not errors, errors
    print(f"{len(checks)} actual QML page views; no QML errors")


if __name__ == "__main__":
    main()
