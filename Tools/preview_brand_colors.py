"""Render actual Pi pages with isolated fixtures; no HA, pairing or service changes."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, QPointF, Qt, QTimer, qInstallMessageHandler
from PySide6.QtGui import QAccessible, QGuiApplication, QKeySequence
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend, parse_ask_dj_messages, parse_playlist_items, parse_queue_items
from djconnect_pi.brand_theme import apply_palette
from djconnect_pi.config import Config, save_config
from djconnect_pi.expiry_clock import ExpiryClock
from djconnect_pi.ha import Playback
from djconnect_pi.session_history_view import presented_history, search_page
from djconnect_pi.session_projection import history_page, timestamp
from djconnect_pi.session_transport import SessionError
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
            if 'Timer' in obj.metaObject().className() and obj.property('interval') in (3000, 7000, 15000):
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
        window.setProperty("activeScreen", "now")
        if profile == "pi5-arm64":
            assert window.property("activeScreen") == "session"
        else:
            capture("now-direct-playback")
            controls = visual_item(window.contentItem(), "nowPlaybackControls")
            artwork = visual_item(window.contentItem(), "nowTrackArtwork")
            assert artwork.mapToScene(QPointF(0, artwork.height())).y() <= controls.mapToScene(QPointF()).y()
            assert controls.mapToScene(QPointF(controls.width(), controls.height())).y() <= height

            def tap_now_control(name):
                button = visual_item(controls, name)
                assert button.property("enabled")
                center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center.toPoint())

            title_before = backend.title
            tap_now_control("nowNextTrack")
            assert backend.title != title_before and window.property("activeScreen") == "now", (
                title_before, backend.title, window.property("activeScreen"), backend.demoMode, len(backend._queue_items),
            )
            tap_now_control("nowPreviousTrack")
            assert backend.title == title_before
            playing_before = backend.playing
            tap_now_control("nowTogglePlayback")
            assert backend.playing != playing_before
            position = visual_item(window.contentItem(), "nowPlaybackPosition")
            progress = visual_item(position, "nowPlaybackProgress")
            time_label = visual_item(position, "nowPlaybackTime")
            duration_before = backend.playback.duration_seconds
            position_before = backend.playback.position_seconds
            backend.playback.duration_seconds = 240
            backend.playback.position_seconds = 75
            backend.progressChanged.emit()
            assert position.property("visible") and progress.property("value") == 75
            assert time_label.property("text") == "1:15 / 4:00"
            assert position.mapToScene(QPointF(0, position.height())).y() <= controls.mapToScene(QPointF()).y()
            capture("now-playback-position")
            tap_now_control("nowSeekBack")
            assert backend.positionSeconds == 60
            tap_now_control("nowSeekForward")
            assert backend.positionSeconds == 75
            start = progress.mapToScene(QPointF(progress.width() * 0.3, progress.height() / 2)).toPoint()
            end = progress.mapToScene(QPointF(progress.width() * 0.6, progress.height() / 2)).toPoint()
            QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
            QTest.mouseMove(window, end)
            assert backend.positionSeconds == 75 and time_label.property("text") != "1:15 / 4:00"
            QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
            assert 135 <= backend.positionSeconds <= 155
            backend.playback.duration_seconds = 0
            backend.progressChanged.emit()
            assert not position.property("visible") and controls.property("visible")
            backend.playback.duration_seconds = duration_before
            backend.playback.position_seconds = position_before
            backend.progressChanged.emit()
            tap_now_control("nowTogglePlayback")
            assert backend.playing == playing_before
            backend.hideToast()
            output = visual_item(window.contentItem(), "nowPlaybackOutput")
            assert output.property("visible") and output.property("text") == backend.t("output_device")
            backend.playback.output_device = "Fixture output"
            backend.outputDeviceChanged.emit()
            assert output.property("text") == "Fixture output"
            capture("now-playback-output")
            center = output.mapToScene(QPointF(output.width() / 2, output.height() / 2))
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center.toPoint())
            assert window.property("activeScreen") == "control" and backend.outputDevice == "Fixture output"
            QTest.qWait(80)
            outputs = visual_item(window.contentItem(), "controlOutputDeviceCombo")
            refresh_outputs = visual_item(window.contentItem(), "controlRefreshOutputs")
            refresh_center = refresh_outputs.mapToScene(QPointF(refresh_outputs.width() / 2, refresh_outputs.height() / 2))
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, refresh_center.toPoint())
            assert backend.toastText == backend.t("session_output_updated") and backend.outputDevice == "Fixture output"
            backend.playback.output_device_details = (
                {"name": "Fixture output", "value": "speaker-a"}, {"name": "Fixture output", "value": "speaker-b"},
            )
            backend.playback.output_device_id = "speaker-b"
            backend.outputDeviceChanged.emit()
            assert outputs.property("currentIndex") == 2 and outputs.property("displayText") == "Fixture output (2)"
            backend._pending_output_choice = ("speaker-a", "New fixture output")
            backend.outputDeviceChanged.emit()
            switching = visual_item(window.contentItem(), "controlOutputSwitching")
            assert switching.property("visible") and not outputs.property("enabled")
            assert "New fixture output" in switching.property("text") and outputs.property("currentIndex") == 2
            window.setProperty("activeScreen", "now")
            capture("now-output-switching")
            assert visual_item(window.contentItem(), "nowPlaybackOutput").property("text") == "Fixture output"
            assert visual_item(window.contentItem(), "nowOutputSwitching").property("visible")
            backend._pending_output_choice = None
            backend._unconfirmed_output_choice = "speaker-a"
            backend.outputDeviceChanged.emit()
            notice = visual_item(window.contentItem(), "nowOutputSwitching")
            assert notice.property("text") == backend.t("session_output_switch_unconfirmed")
            assert visual_item(window.contentItem(), "nowPlaybackOutput").property("text") == "Fixture output"
            capture("now-output-unconfirmed")
            backend._unconfirmed_output_choice = ""
            backend.playback.output_device_details = ()
            backend.playback.output_device_id = ""
            backend.playback.output_device = ""
            backend.outputDeviceChanged.emit()
            backend.hideToast()
            backend._demo_mode = False
            backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
            backend._set_backend_available(True)
            previous_outputs = backend.playback.output_devices
            backend.playback.output_devices = ()
            with patch.object(backend, "_run") as inventory:
                window.setProperty("activeScreen", "control")
                QTest.qWait(80)
                assert backend.controlOutputsRefreshing
                inventory_status = visual_item(window.contentItem(), "controlOutputInventoryState")
                assert inventory_status.property("visible") and inventory_status.property("text") == backend.t("loading")
                backend.outputDeviceChanged.emit()
                inventory.assert_called_once()
                inventory.call_args.kwargs["done"]()
                assert not backend.controlOutputsRefreshing and not backend.toastVisible
                assert inventory_status.property("text") == backend.t("session_output_unavailable")
                with patch.object(backend.client, "command", return_value={"success": True, "devices": []}):
                    backend.refreshControlOutputs()
                    inventory.call_args.args[1]()
                    inventory.call_args.kwargs["done"]()
                assert inventory_status.property("text") == backend.t("playback_outputs_none_found")
                capture("control-outputs-empty")
            backend.playback.output_devices = previous_outputs
            backend._control_outputs_status = ""
            backend.outputDeviceChanged.emit()
            backend.hideToast()
            backend._demo_mode = True
            backend.cfg.paired, backend.cfg.device_token = False, ""
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
        queue_refresh = visual_item(queue_panel, "mediaRefresh")
        queue_accessible = QAccessible.queryAccessibleInterface(queue_refresh)
        assert queue_accessible.text(QAccessible.Text.Name) == backend.t("refresh_queue")
        search_toggle = visual_item(queue_panel, "mediaSearchOpen")
        search_accessible = QAccessible.queryAccessibleInterface(search_toggle)
        assert search_accessible.text(QAccessible.Text.Name) == backend.t("media_search_queue")
        QMetaObject.invokeMethod(search_toggle, "clicked")
        assert search_accessible.text(QAccessible.Text.Name) == backend.t("close_search")
        assert visual_item(queue_panel, "mediaSearchInput").property("activeFocus")
        queue_search_close = visual_item(queue_panel, "mediaSearchClose")
        assert queue_search_close.property("iconName") == "close"
        QMetaObject.invokeMethod(queue_search_close, "clicked")
        assert search_accessible.text(QAccessible.Text.Name) == backend.t("media_search_queue")
        assert search_toggle.property("activeFocus") and not queue_panel.property("searching")
        find = QKeySequence.keyBindings(QKeySequence.StandardKey.Find)[0][0]
        QTest.keyClick(window, find.key(), find.keyboardModifiers())
        assert queue_panel.property("searching") and not queue_panel.property("keyboardOpen")
        search_input = visual_item(queue_panel, "mediaSearchInput")
        assert search_input.property("activeFocus")
        search_input.setProperty("text", "Repeated")
        QTest.keyClick(window, find.key(), find.keyboardModifiers())
        assert search_input.property("text") == "Repeated"
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert not queue_panel.property("searching")
        QTest.qWait(65)
        repeated_row = visual_item(media_rows, "mediaItemRow-2")
        with patch.object(backend, "_run"):
            backend._demo_mode = False
            QMetaObject.invokeMethod(repeated_row, "clicked")
            backend._demo_mode = True
        assert repeated_row.property("starting")
        assert not visual_item(media_rows, "mediaItemRow-0").property("starting")
        queue_panel.setProperty("searching", True)
        search_input = visual_item(queue_panel, "mediaSearchInput")
        search_input.setProperty("text", "Repeated")
        QTest.qWait(80)
        assert media_rows.property("count") == 2
        assert visual_item(media_rows, "mediaItemRow-1").property("starting")
        assert not visual_item(media_rows, "mediaItemRow-0").property("starting")
        backend._finish_media_playback()
        assert not visual_item(media_rows, "mediaItemRow-1").property("starting")
        search_input.setProperty("text", "")
        records = [{"title": f"Fixture {index:02d}", "uri": f"spotify:track:fixture-{index}", "index": index}
                   for index in range(30)]
        backend._queue_items = parse_queue_items({"queue": records})
        backend.mediaListsChanged.emit()
        queue_panel.setProperty("searching", False)
        scroll = QQmlExpression(engine.rootContext(), media_rows, "positionViewAtIndex(8, ListView.Beginning)")
        scroll.evaluate()
        QTest.qWait(80)
        anchor = visual_item(media_rows, "mediaItemRow-8")
        previous_y = anchor.mapToItem(media_rows, QPointF()).y()
        backend._demo_mode = False
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        with patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request:
            QMetaObject.invokeMethod(queue_refresh, "clicked")
            request.assert_called_once()
            inserted = {"title": "New first track", "uri": "spotify:track:inserted", "index": 99}
            backend._queue_items = parse_queue_items({"queue": [inserted, *records]})
            backend.mediaListsChanged.emit()
            request.call_args.kwargs["done"]()
        capture("queue-refresh-keeps-position")
        anchor = visual_item(media_rows, "mediaItemRow-9")
        assert anchor.property("text") == "Fixture 08"
        assert abs(anchor.mapToItem(media_rows, QPointF()).y() - previous_y) < 2
        backend.cfg.paired, backend.cfg.device_token = False, ""
        backend._demo_mode = True
        backend._queue_items = parse_queue_items({"queue": records})
        backend.mediaListsChanged.emit()
        queue_panel.setProperty("searching", True)
        search_input = visual_item(queue_panel, "mediaSearchInput")
        search_input.setProperty("text", "fixture")
        count = visual_item(queue_panel, "mediaSearchMatchCount")
        next_match = visual_item(queue_panel, "mediaSearchNextMatch")
        search_input.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Up)
        assert count.property("text") == "30 / 30"
        QTest.keyClick(window, Qt.Key.Key_Down)
        assert count.property("text") == "1 / 30"
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
        search_input.setProperty("text", "no matching track")
        queue_empty_message = visual_item(queue_panel, "mediaEmptyStateMessage")
        assert queue_empty_message.property("text") == backend.t("media_no_matches")
        backend._queue_items = parse_queue_items({"queue": [
            {"title": "Placeholder", "image_url": f"https://example.test/{index}.jpg"} for index in range(4)
        ]})
        backend.mediaListsChanged.emit()
        assert queue_empty_message.property("text") == backend.t("empty_queue")
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
        assert visual_item(playlist_panel, "mediaSearchClose").property("iconName") == "close"
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
        media_refresh = visual_item(playlist_panel, "mediaRefresh")
        playlist_accessible = QAccessible.queryAccessibleInterface(media_refresh)
        assert playlist_accessible.text(QAccessible.Text.Name) == backend.t("refresh_playlists")
        media_pull_refresh = playlist_panel.findChild(QObject, "mediaPullToRefresh")
        playlist_panel.setProperty("searching", False)
        assert not media_refresh.property("enabled") and not media_pull_refresh.property("canRefresh")
        with patch.object(backend, "_run") as request:
            QMetaObject.invokeMethod(media_refresh, "clicked")
            QMetaObject.invokeMethod(media_pull_refresh, "refreshRequested")
            request.assert_not_called()
        backend._set_backend_available(True)
        assert visual_item(playlist_panel, "mediaItemRow-0").property("enabled")
        assert media_refresh.property("enabled") and media_pull_refresh.property("canRefresh")
        backend._media_loads_in_flight.add("playlists")
        backend.mediaActivityChanged.emit()
        assert media_refresh.property("loading") and not media_refresh.property("enabled")
        assert not media_pull_refresh.property("canRefresh")
        backend._finish_media_list_load("playlists")
        playlist_response = {"success": True, "playlists": [
            {"name": "Refreshed playlist", "uri": "spotify:playlist:fixture-only"},
        ]}
        with (patch.object(backend, "_sync_config_from_disk"),
              patch.object(backend.client, "command", return_value=playlist_response),
              patch.object(backend._executor, "submit") as submit):
            QMetaObject.invokeMethod(media_refresh, "clicked")
            submit.call_args.args[0]()
        assert backend.toastText == backend.t("playlists_updated") and backend.toastIcon == "playlists"
        with patch.object(backend, "_run") as pending_start:
            QMetaObject.invokeMethod(visual_item(playlist_panel, "mediaItemRow-0"), "clicked")
            pending_start.assert_called_once()
            assert visual_item(playlist_panel, "mediaItemRow-0").property("starting")
            backend._playlist_items = parse_playlist_items({"playlists": [
                {"name": "Refreshed playlist", "uri": "spotify:playlist:different-playlist"},
                {"name": "Renamed while starting", "uri": "spotify:playlist:fixture-only"},
            ]})
            backend.mediaListsChanged.emit()
            capture("playlist-starting-after-update")
            assert not visual_item(playlist_panel, "mediaItemRow-0").property("starting")
            assert visual_item(playlist_panel, "mediaItemRow-1").property("starting")
            pending_start.call_args.kwargs["done"]()
            assert not visual_item(playlist_panel, "mediaItemRow-1").property("starting")
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
                backend._apply_playback(Playback(title="Fixture next track", artist="Fixture artist", album="Fixture album", uri="fixture:next-track",
                                                 position_seconds=75, duration_seconds=240))
            QTest.qWait(65)
            assert subject_title.property("text") == "Fixture next track"
            metadata = visual_item(subject, "trackInsightSubjectMetadata")
            assert metadata.property("text") == "Fixture artist · Fixture album"
            corrected = deepcopy(backend.playback)
            corrected.album = "Corrected fixture album"
            backend._apply_playback(corrected)
            assert metadata.property("text") == "Fixture artist · Corrected fixture album"
            time_chip = visual_item(subject, "trackInsightPlaybackTime")
            time_label = visual_item(time_chip, "trackInsightPlaybackTimeLabel")
            assert time_chip.property("visible") and time_label.property("text") == "1:15 / 4:00"
            capture("track-insight-playback-position")
            assert not analysis_text.property("visible")
        backend._set_ask_dj_busy(False)
        with (patch.object(backend, "_sync_config_from_disk"), patch.object(backend, "_run") as request,
              patch("djconnect_pi.app.subprocess.run")):
            backend._apply_playback(Playback(title="Playing fixture", uri="fixture:auto-first", is_playing=True))
            QTest.qWait(65)
            assert not time_chip.property("visible")
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
            first_output = deepcopy(backend.playback)
            first_output.output_device = "Fixture speaker"
            first_output.output_device_id = "fixture:speaker-a"
            backend._apply_playback(first_output)
            QTest.qWait(65)
            assert request.call_count == 4
            backend._set_ask_dj_busy(False)
            backend._apply_track_insight_data({
                "track": {"title": first_output.title},
                "analysis": {"summary": "Analysis from the previous output"}
            })
            assert backend.trackInsightText
            second_output = deepcopy(first_output)
            second_output.output_device_id = "fixture:speaker-b"
            backend._apply_playback(second_output)
            QTest.qWait(65)
            assert not backend.trackInsightText and request.call_count == 5
            backend._set_ask_dj_busy(False)
            second_output_renamed = deepcopy(second_output)
            second_output_renamed.output_device = "Renamed fixture speaker"
            backend._apply_playback(second_output_renamed)
            QTest.qWait(65)
            assert request.call_count == 5
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
        discovery_refresh = visual_item(window.contentItem(), "discoveryRefreshButton")
        discovery_accessible = QAccessible.queryAccessibleInterface(discovery_refresh)
        assert discovery_accessible.text(QAccessible.Text.Name) == backend.t("refresh_discovery")
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
            {"title": "Missing shelf ID", "items": [{"id": "hidden-id", "title": "Hidden"}]},
            {"id": "untitled-shelf", "items": [{"id": "hidden-title", "title": "Hidden"}]},
            {"id": "invalid-shelf", "title": "Missing recommendation ID", "items": [{"title": "Hidden"}]},
        ]})
        assert empty_feed.property("visible")
        long_title = "Cached recommendation with a long title on this Discovery card"
        backend._apply_music_discovery_data({"success": True, "sections": [
            {"id": "fixture-shelf", "title": "Recommendations", "items": [{
                "id": "fixture-track", "kind": "track", "title": long_title, "subtitle": "Artist Name",
                "reason": "A multi-line reason describes why this recommendation fits the listener's current profile.",
                "reason_sources": ["djconnect_music_dna", "spotify_catalog", "fixture_unknown"],
                "quality_score": 74, "quality_factors": [
                    "spotify_recommendation", "fresh_candidate", "favorite_artist_match", "top_artist_match",
                    "has_artwork", "variant_title_penalty", "fixture_unknown", "{fixture_unknown}",
                ],
            }, {"id": "new-kind", "kind": "other", "title": "Future recommendation"}]},
            {"id": "other-shelf", "title": "Another section", "items": [
                {"id": "fixture-track", "kind": "track", "title": "The same recommendation in another section"},
            ]},
        ]})
        assert not empty_feed.property("visible")
        discovery_page = window.findChild(QObject, "musicDiscoveryPanel")
        shelves = discovery_page.property("sections").toVariant()
        assert [item["discoveryItemId"] for item in shelves[0]["items"]] == ["fixture-track", "new-kind"]
        detail = window.findChild(QObject, "discoveryDetail")
        detail.setProperty("selected", {"sectionId": "fixture-shelf", "discoveryItemId": "fixture-track"})
        QMetaObject.invokeMethod(detail, "open")
        backend._set_music_discovery_busy(True)
        QTest.qWait(240)
        artwork = window.findChild(QObject, "discoveryDetailArtwork")
        fallback = artwork.findChild(QObject, "discoveryArtworkFallback")
        spinner = artwork.findChild(QObject, "discoveryArtworkSpinner")
        cover = artwork.findChild(QObject, "discoveryArtworkImage")
        assert fallback.property("visible") and not spinner.property("visible")
        shelf = visual_item(window.contentItem(), "discoveryShelfItems")
        recommendation = visual_item(shelf, "discoveryRecommendation")
        kind_label = visual_item(recommendation, "discoveryCardKind")
        assert kind_label.property("visible") and kind_label.property("text") == backend.t("music_discovery_kind_track")
        content_height = recommendation.property("contentItem").implicitHeight()
        assert recommendation.height() >= content_height, (width, recommendation.height(), content_height, shelf.height())
        label_bottom = kind_label.mapToItem(shelf, QPointF(0, kind_label.height())).y()
        assert label_bottom <= shelf.height(), (width, label_bottom, shelf.height())
        quality_strip = window.findChild(QObject, "discoveryQualityStrip")
        assert quality_strip.property("visible") and quality_strip.property("contentWidth") > quality_strip.width()
        strip_values = quality_strip.property("values").toVariant()
        assert strip_values[0] == "74/100" and len(strip_values) == 7
        assert strip_values[-2] == backend.t("music_discovery_quality_has_artwork")
        assert strip_values[-1] == backend.t("music_discovery_quality_variant_title_penalty")
        source_strip = window.findChild(QObject, "discoverySourceStrip")
        assert source_strip.property("visible")
        assert source_strip.property("values").toVariant() == [
            backend.t("music_discovery_source_djconnect_music_dna"), backend.t("music_discovery_source_spotify_catalog"),
        ]
        flick = QQmlExpression(engine.rootContext(), quality_strip, "flick(-1200, 0)")
        flick.evaluate()
        assert not flick.hasError(), flick.error().toString()
        QTest.qWait(150)
        assert quality_strip.property("contentX") > 0 and quality_strip.property("contentY") == 0
        card = visual_item(window.contentItem(), "discoveryCardArtwork")
        card_spinner = card.findChild(QObject, "discoveryArtworkSpinner")
        other_shelf = visual_item(window.contentItem(), "discoveryShelf-other-shelf")
        other_card = visual_item(other_shelf, "discoveryCardArtwork")
        other_spinner = other_card.findChild(QObject, "discoveryArtworkSpinner")
        playing = ("fixture-shelf", "fixture-track")
        backend._music_discovery_playing = playing
        backend.musicDiscoveryChanged.emit()
        assert card_spinner.property("visible") and not other_spinner.property("visible")
        assert not spinner.property("visible")
        backend._clear_music_discovery_playing(playing)
        assert not card_spinner.property("visible")
        current_item, *other_items = backend.musicDiscoveryItems
        refreshed_title = "Updated recommendation title"
        backend._set_music_discovery_items([{
            **current_item, "title": refreshed_title,
            "imageUrl": (ROOT / "src/djconnect_pi/qml/brand-mark-128.png").as_uri(),
        }, *other_items])
        QTest.qWait(120)
        assert cover.property("visible") and not fallback.property("visible") and not spinner.property("visible")
        assert detail.property("opened")
        assert window.findChild(QObject, "discoveryDetailTitle").property("text") == refreshed_title
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
        assert backend.musicDiscoveryItems[0]["title"] == refreshed_title
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
            backend._apply_music_discovery_data({"success": True, "sections": [
                {"id": "fixture-shelf", "title": "Recommendations", "items": [
                    {"id": f"expiring-{index}", "title": f"Expiring cover {index}"} for index in range(2)
                ]},
            ]})
            cover_url = (ROOT / "src/djconnect_pi/qml/brand-mark-128.png").as_uri()
            expires_at = int(datetime.now(UTC).timestamp() * 1000) + 500
            backend._set_music_discovery_items([
                {**item, "imageUrl": cover_url, "imageExpiresAt": expires_at} for item in backend.musicDiscoveryItems
            ])
            QTest.qWait(1250)
            assert request.call_count == 3 and backend.musicDiscoveryBusy
            backend._set_music_discovery_busy(False)
            QTest.qWait(1150)
            assert request.call_count == 3
        backend._apply_music_discovery_data({"success": True, "feedback_supported": True, "sections": [
            {"id": "fixture-shelf", "title": "Recommendations", "items": [
                {"id": "fixture-track", "kind": "track", "title": "Closeable recommendation",
                 "uri": "spotify:track:fixture", "reason": "A long recommendation explanation with listening context. " * 110,
                 "quality_factors": ["spotify_recommendation", "fresh_candidate", "favorite_artist_match", "top_artist_match", "has_artwork", "variant_title_penalty"],
                 "reason_sources": ["djconnect_music_dna", "spotify_catalog", "spotify_top_tracks", "spotify_top_artists", "spotify_recently_played", "accepted_recommendations"]},
            ]},
        ]})
        QTest.qWait(65)
        for locale in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(locale)
            shelf = visual_item(window.contentItem(), "discoveryShelf-fixture-shelf")
            recommendation = visual_item(shelf, "discoveryRecommendation")
            recommendation.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Space)
            capture("discovery-detail-close-" + locale)
            assert detail.property("opened")
            close_detail = visual_item(window.contentItem(), "closeDiscoveryDetail")
            assert close_detail.property("visible") and close_detail.property("iconName") == "close"
            assert QAccessible.queryAccessibleInterface(close_detail).text(QAccessible.Text.Name) == backend.t("close")
            assert close_detail.property("activeFocus")
            QTest.keyClick(window, Qt.Key.Key_Space)
            assert not detail.property("opened") and window.property("activeScreen") == "discover"
            assert recommendation.property("activeFocus")
            QTest.keyClick(window, Qt.Key.Key_Space)
            assert detail.property("opened")
            scroller = visual_item(window.contentItem(), "discoveryDetailScroll").property("contentItem")
            play = visual_item(window.contentItem(), "discoveryDetailPlay")
            feedback = visual_item(window.contentItem(), "discoveryDetailFeedback")
            assert play.mapToItem(scroller, QPointF()).y() > scroller.height()
            strips = [visual_item(window.contentItem(), name) for name in ("discoveryQualityStrip", "discoverySourceStrip")]
            for strip, label_key in zip(strips, ("music_discovery_quality_labels", "music_discovery_source_labels")):
                QTest.keyClick(window, Qt.Key.Key_Tab)
                assert strip.property("activeFocus") and strip.property("contentWidth") > strip.width()
                assert QAccessible.queryAccessibleInterface(strip).text(QAccessible.Text.Name) == backend.t(label_key)
                start_x = strip.property("originX")
                assert abs(strip.property("contentX") - start_x) < 1
                QTest.keyClick(window, Qt.Key.Key_Right)
                assert strip.property("contentX") > start_x and strip.property("activeFocus")
                QTest.keyClick(window, Qt.Key.Key_Left)
                assert abs(strip.property("contentX") - start_x) < 1
            for action in (play, feedback):
                QTest.keyClick(window, Qt.Key.Key_Tab)
                top = action.mapToItem(scroller, QPointF()).y()
                assert action.property("activeFocus") and top >= -1 and top + action.height() <= scroller.height() + 1
            capture("discovery-detail-keyboard-actions-" + locale)
            feedback_menu = window.findChild(QObject, "discoveryFeedbackMenu")
            with patch.object(backend, "_run") as request:
                QTest.keyClick(window, Qt.Key.Key_Space)
                QTest.qWait(180)
                assert feedback_menu.property("opened") and feedback_menu.property("currentIndex") == 0
                first_option = visual_item(window.contentItem(), "discoveryFeedbackFirstOption")
                middle_option = visual_item(window.contentItem(), "discoveryFeedbackMiddleOption")
                last_option = visual_item(window.contentItem(), "discoveryFeedbackLastOption")
                for option, key in zip((first_option, middle_option, last_option),
                                       ("music_discovery_not_for_me", "music_discovery_less_like_this", "music_discovery_hide_artist")):
                    assert option.height() >= 48 and option.property("text") == backend.t(key)
                button_top = feedback.mapToScene(QPointF()).y()
                menu_top_left = first_option.mapToScene(QPointF())
                menu_end = last_option.mapToScene(QPointF(last_option.width(), last_option.height()))
                assert 0 <= menu_top_left.y() < menu_end.y() <= button_top + 1
                assert 0 <= menu_top_left.x() < menu_end.x() <= width
                QTest.keyClick(window, Qt.Key.Key_Down)
                assert feedback_menu.property("currentIndex") == 1
                QTest.keyClick(window, Qt.Key.Key_Escape)
                QTest.qWait(180)
                assert not feedback_menu.property("opened") and detail.property("opened") and feedback.property("activeFocus")
                QTest.keyClick(window, Qt.Key.Key_Space)
                capture("discovery-feedback-menu-" + locale)
                assert feedback_menu.property("opened") and feedback_menu.property("currentIndex") == 0
                QTest.keyClick(window, Qt.Key.Key_Escape)
                QTest.qWait(180)
                assert not feedback_menu.property("opened") and detail.property("opened") and feedback.property("activeFocus")
                request.assert_not_called()
            for action in (play, *reversed(strips), close_detail):
                QTest.keyClick(window, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
                top = action.mapToItem(scroller, QPointF()).y()
                assert action.property("activeFocus") and top >= -1 and top + action.height() <= scroller.height() + 1
                if action in strips:
                    QTest.keyClick(window, Qt.Key.Key_Right)
                    assert action.property("contentX") > action.property("originX")
            QTest.keyClick(window, Qt.Key.Key_Escape)
            assert not detail.property("opened") and recommendation.property("activeFocus")
            QTest.keyClick(window, Qt.Key.Key_Space)
            assert detail.property("opened") and close_detail.property("activeFocus")
            for strip in strips:
                assert abs(strip.property("contentX") - strip.property("originX")) < 1
            QTest.keyClick(window, Qt.Key.Key_Escape)
        backend.setLanguage("nl")
        backend._demo_mode = True
        backend.demoModeChanged.emit()
        backend.cfg.paired, backend.cfg.device_token, backend.cfg.active_profile_id = False, "", ""
        backend.pairedChanged.emit()
        backend._set_demo_music_dna(True)
        backend._ask_dj_messages = parse_ask_dj_messages({"messages": [
            {"id": "ask-first", "role": "user", "text": "First question"},
            {"id": "ask-second", "role": "user", "text": "Second question"},
            {"id": "ask-third", "role": "user", "text": "Third question"},
            {"id": "ask-unrelated", "role": "assistant", "text": "An unrelated reply"},
        ]})
        backend.askDjChanged.emit()
        window.setProperty("activeScreen", "askdj")
        QTest.qWait(65)
        ask_dj = visual_item(window.contentItem(), "askDjPanel")
        draft = visual_item(ask_dj, "askDjChatInput")
        ask_toggle = visual_item(ask_dj, "askDjSearchToggle")
        ask_toggle_accessible = QAccessible.queryAccessibleInterface(ask_toggle)
        assert ask_toggle_accessible.text(QAccessible.Text.Name) == backend.t("ask_dj_search_history")
        draft.setProperty("text", "Unsent draft")
        draft.forceActiveFocus()
        QTest.keyClick(window, find.key(), find.keyboardModifiers())
        find_input = visual_item(ask_dj, "askDjSearchInput")
        assert ask_dj.property("searchOpen") and not ask_dj.property("searchKeyboardOpen")
        assert ask_toggle_accessible.text(QAccessible.Text.Name) == backend.t("close_search")
        assert find_input.property("activeFocus") and draft.property("text") == "Unsent draft"
        find_input.setProperty("text", "question")
        ask_count = visual_item(ask_dj, "askDjSearchCount")
        assert ask_count.property("text") == "1/3"
        QTest.keyClick(window, Qt.Key.Key_Up)
        assert ask_count.property("text") == "3/3" and ask_dj.property("selectedMessageId") == "ask-third"
        QTest.keyClick(window, Qt.Key.Key_Down)
        assert ask_count.property("text") == "1/3" and ask_dj.property("selectedMessageId") == "ask-first"
        QTest.keyClick(window, Qt.Key.Key_Return)
        assert ask_count.property("text") == "2/3" and ask_dj.property("selectedMessageId") == "ask-second"
        find_input.setProperty("text", "No such message")
        assert ask_count.property("visible") and ask_count.property("text") == "0"
        assert QAccessible.queryAccessibleInterface(ask_count).text(QAccessible.Text.Name) == backend.t("media_no_matches")
        assert not visual_item(ask_dj, "askDjPreviousMatch").property("enabled")
        assert not visual_item(ask_dj, "askDjNextMatch").property("enabled")
        assert visual_item(ask_dj, "askDjMessagesView").property("count") == 4
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert not ask_dj.property("searchOpen") and draft.property("text") == "Unsent draft"
        assert draft.property("activeFocus")
        QMetaObject.invokeMethod(ask_toggle, "clicked")
        ask_close = visual_item(ask_dj, "askDjSearchClose")
        assert ask_close.property("iconName") == "close"
        assert QAccessible.queryAccessibleInterface(ask_close).text(QAccessible.Text.Name) == backend.t("close_search")
        QMetaObject.invokeMethod(ask_close, "clicked")
        assert ask_toggle.property("activeFocus") and not ask_dj.property("searchOpen")
        offline_notice = visual_item(ask_dj, "askDjOfflineNotice")
        assert not offline_notice.property("visible")
        backend.cfg.paired, backend.cfg.device_token = True, "synthetic-not-a-credential"
        backend.pairedChanged.emit()
        backend._demo_mode = False
        backend.demoModeChanged.emit()
        draft.setProperty("text", "Keep this offline draft")
        send_question = visual_item(ask_dj, "askDjSend")
        assert send_question.property("enabled")
        backend._set_backend_available(False)
        offline_history = visual_item(ask_dj, "askDjOfflineHistory")
        assert offline_notice.property("visible") and offline_history.property("visible")
        assert offline_history.property("text") == backend.t("ask_dj_offline_history")
        assert visual_item(ask_dj, "askDjMessagesView").property("count") == 4
        assert draft.property("enabled") and not send_question.property("enabled")
        draft.forceActiveFocus()
        with patch.object(backend._question_executor, "submit") as dispatch:
            QTest.keyClick(window, Qt.Key.Key_Return)
            dispatch.assert_not_called()
            backend._set_backend_available(True)
            dispatch.assert_not_called()
        assert send_question.property("enabled") and draft.property("text") == "Keep this offline draft", (
            send_question.property("enabled"), draft.property("text"), backend.paired, backend.backendAvailable,
        )
        assert not offline_notice.property("visible")
        pending_question = visual_item(ask_dj, "askDjPendingQuestion")
        assert not pending_question.property("visible")
        transport = backend.djSession.transport
        with (patch.object(backend, "_sync_config_from_disk"),
              patch.object(transport, "binding", return_value=object()),
              patch.object(transport, "private_scope_owner", return_value="fixture-owner"),
              patch.object(transport, "standalone_question") as question_request,
              patch.object(transport, "standalone_history", side_effect=SessionError("unavailable")),
              patch.object(backend._question_executor, "submit") as dispatch):
            QMetaObject.invokeMethod(send_question, "clicked")
            dispatch.assert_called_once()
            assert pending_question.property("visible") and draft.property("text") == ""
            assert visual_item(ask_dj, "askDjPendingQuestionText").property("text") == "Keep this offline draft"
            assert visual_item(ask_dj, "askDjMessagesView").property("count") == 4
            dispatch.call_args.args[0]()
            question_request.assert_called_once()
        assert backend.toastText == backend.t("session_question_accepted_unavailable")
        assert not pending_question.property("visible")
        clear_ask = window.findChild(QObject, "askDjClearButton")
        QMetaObject.invokeMethod(clear_ask, "clicked")
        assert window.property("askDjClearConfirmOpen")
        confirm_clear = window.findChild(QObject, "askDjClearConfirmButton")
        assert confirm_clear.property("enabled")
        with (patch.object(backend, "_sync_config_from_disk"),
              patch.object(backend.client, "ask_dj_history_clear", return_value={"success": True}),
              patch.object(backend._executor, "submit") as dispatch):
            QMetaObject.invokeMethod(confirm_clear, "clicked")
            dispatch.call_args.args[0]()
        assert backend.toastText == backend.t("ask_dj_clear_unconfirmed")
        assert window.property("askDjClearConfirmOpen")
        backend._set_backend_available(False)
        assert not confirm_clear.property("enabled")
        backend._set_backend_available(True)
        backend._music_dna_context_revision += 1
        backend.musicDnaChanged.emit()
        assert not window.property("askDjClearConfirmOpen")
        assert visual_item(ask_dj, "askDjMessagesView").property("count") == 4
        backend._apply_ask_dj_clear_result({"success": True, "cleared": True, "clear_revision": 2, "history_revision": 10})
        ask_history = visual_item(ask_dj, "askDjMessagesView")
        assert ask_history.property("count") == 0
        backend._apply_ask_dj_data({"success": True, "clear_revision": 1, "history_revision": 9, "messages": [
            {"id": "late-old-answer", "role": "assistant", "text": "Deleted answer"},
        ]})
        assert ask_history.property("count") == 0
        refresh_ask = visual_item(ask_dj, "askDjRefreshButton")
        ask_response = {"success": True, "clear_revision": 2, "history_revision": 11, "messages": [
            {"id": "new-answer", "role": "assistant", "text": "New answer"},
        ]}
        with (patch.object(backend, "_sync_config_from_disk"),
              patch.object(backend.client, "ask_dj_history", return_value=ask_response),
              patch.object(backend._executor, "submit") as dispatch):
            QMetaObject.invokeMethod(refresh_ask, "clicked")
            assert backend.toastText == backend.t("refreshing")
            dispatch.call_args.args[0]()
        assert ask_history.property("count") == 1
        assert backend.toastText == backend.t("ask_dj_history_updated")
        backend._apply_ask_dj_data({"success": True, "clear_revision": 2, "history_revision": 12, "messages": [
            {"id": f"chat-row-{index}", "role": "assistant", "text": f"Earlier message {index}"}
            for index in range(30)
        ]})
        QTest.qWait(65)
        ask_history.setProperty("contentY", ask_history.property("originY") + 440)
        QTest.qWait(65)
        reading_offset = ask_history.property("contentY") - ask_history.property("originY")
        backend.askDjChanged.emit()
        QTest.qWait(65)
        assert reading_offset > 400
        assert abs(ask_history.property("contentY") - ask_history.property("originY") - reading_offset) < 10
        visible_message = QQmlExpression(engine.rootContext(), ask_history, """
            let index = indexAt(width / 2, contentY)
            if (index < 0) index = indexAt(width / 2, contentY + spacing + 1)
            model[index] ? model[index].id : ""
        """)
        message_before, _ = visible_message.evaluate()
        assert message_before.startswith("chat-row-")
        backend._apply_ask_dj_data({"success": True, "clear_revision": 2, "history_revision": 13, "messages": [
            {"id": "incoming-answer", "role": "assistant", "text": "A new answer above the reading position"},
        ]})
        QTest.qWait(100)
        assert backend.askDjMessages[0]["id"] == "incoming-answer" and ask_history.property("count") == 32
        assert visible_message.evaluate()[0] == message_before, (width, message_before, visible_message.evaluate()[0])
        jump_top = visual_item(ask_dj, "askDjJumpTop")
        assert jump_top.property("visible")
        assert QAccessible.queryAccessibleInterface(jump_top).text(QAccessible.Text.Name) == backend.t("session_back_top")
        jump_position = jump_top.mapToScene(QPointF(jump_top.width() / 2, jump_top.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, jump_position)
        QTest.qWait(65)
        assert ask_history.property("atYBeginning") and not jump_top.property("visible")
        draft.setProperty("text", "Draft stays while reading")
        ask_dj.setProperty("chatKeyboardOpen", True)
        assert QMetaObject.invokeMethod(ask_history, "dragStarted")
        assert not ask_dj.property("chatKeyboardOpen") and draft.property("text") == "Draft stays while reading"
        QMetaObject.invokeMethod(ask_toggle, "clicked")
        find_input.setProperty("text", "Earlier")
        assert ask_dj.property("searchKeyboardOpen")
        assert QMetaObject.invokeMethod(ask_history, "dragStarted")
        assert not ask_dj.property("searchKeyboardOpen") and find_input.property("text") == "Earlier"
        window.setProperty("activeScreen", "more")
        QTest.qWait(65)
        window.setProperty("activeScreen", "askdj")
        QTest.qWait(65)
        ask_dj = visual_item(window.contentItem(), "askDjPanel")
        draft = visual_item(ask_dj, "askDjChatInput")
        assert draft.property("text") == "Draft stays while reading" and not ask_dj.property("chatKeyboardOpen")
        window.setProperty("activeScreen", "more")
        backend.profileChanged.emit()
        window.setProperty("activeScreen", "askdj")
        QTest.qWait(65)
        assert visual_item(window.contentItem(), "askDjChatInput").property("text") == ""
        backend._demo_mode = True
        backend.demoModeChanged.emit()
        backend.cfg.paired, backend.cfg.device_token = False, ""
        backend.pairedChanged.emit()
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
        backend.cfg.log_level = "INFO"
        log_path = Path(backend.cfg.log_file)
        log_lines = ["sample first", "<repeat> & repeat", "sample second", "sample third", "♪ café and cafe\u0301 and cafe!"]
        log_path.write_text("\n".join(reversed(log_lines)), encoding="utf-8")
        backend.hideToast()
        backend.showLogs()
        assert not backend.toastVisible
        capture("logs")
        logs = visual_item(window.contentItem(), "logsView")
        search_toggle = visual_item(logs, "logsSearchOpen")
        logs_refresh = visual_item(logs, "logsRefresh")
        search_toggle_accessible = QAccessible.queryAccessibleInterface(search_toggle)
        log_find = QKeySequence.keyBindings(QKeySequence.StandardKey.Find)[0][0]
        for locale in ("en", "nl", "de", "fr", "es"):
            backend.setLanguage(locale)
            assert search_toggle_accessible.text(QAccessible.Text.Name) == backend.t("logs_search")
            assert QAccessible.queryAccessibleInterface(logs_refresh).text(QAccessible.Text.Name) == backend.t("refresh_logs")
            QMetaObject.invokeMethod(search_toggle, "clicked")
            assert search_toggle.property("checked") and logs.property("keyboardOpen")
            assert search_toggle_accessible.text(QAccessible.Text.Name) == backend.t("close_search")
            search_close = visual_item(logs, "logsSearchClose")
            assert search_close.property("visible") and search_close.property("iconName") == "close"
            assert QAccessible.queryAccessibleInterface(search_close).text(QAccessible.Text.Name) == backend.t("close_search")
            search_input = visual_item(logs, "logsSearchInput")
            search_input.setProperty("text", "sample")
            capture("logs-search-" + locale)
            QMetaObject.invokeMethod(search_close, "clicked")
            assert not logs.property("searching") and search_input.property("text") == ""
            assert search_toggle.property("activeFocus")
            QMetaObject.invokeMethod(search_toggle, "clicked")
            search_input.setProperty("text", "another sample")
            QMetaObject.invokeMethod(search_toggle, "clicked")
            assert not search_toggle.property("checked") and not logs.property("searching")
            assert not logs.property("keyboardOpen") and search_input.property("text") == ""
            assert search_toggle.property("activeFocus")
            assert search_toggle_accessible.text(QAccessible.Text.Name) == backend.t("logs_search")
            QTest.keyClick(window, log_find.key(), log_find.keyboardModifiers())
            assert logs.property("searching") and not logs.property("keyboardOpen") and search_input.property("activeFocus")
            search_input.setProperty("text", "keep query")
            QTest.keyClick(window, log_find.key(), log_find.keyboardModifiers())
            assert search_input.property("text") == "keep query" and not logs.property("keyboardOpen")
            search_count = visual_item(logs, "logsSearchMatchCount")
            accessible_count = QAccessible.queryAccessibleInterface(search_count)
            assert search_count.property("visible") and search_count.property("text") == "0"
            assert accessible_count.text(QAccessible.Text.Name) == backend.t("logs_no_matches")
            capture("logs-search-no-results-" + locale)
            empty_count_width = search_count.width()
            QTest.keyClick(window, Qt.Key.Key_Down)
            assert logs.property("matchIndex") == -1
            search_input.setProperty("text", "sample")
            assert logs.property("matchIndex") == 0
            QTest.keyClick(window, Qt.Key.Key_Up)
            assert logs.property("matchIndex") == 2
            QTest.keyClick(window, Qt.Key.Key_Down)
            assert logs.property("matchIndex") == 0
            QTest.keyClick(window, Qt.Key.Key_Down)
            assert logs.property("matchIndex") == 1 and search_input.property("activeFocus")
            search_input.setProperty("text", "  sample  ")
            assert logs.property("matchIndex") == 1 and search_count.property("text") == "2/3"
            assert accessible_count.text(QAccessible.Text.Name) == "2/3"
            assert search_count.width() == empty_count_width
            assert search_input.property("text") == "  sample  " and search_count.property("visible")
            search_input.setProperty("text", "repeat")
            same_line = QQmlExpression(engine.rootContext(), logs, "lineText(1, lines[1])")
            highlighted = same_line.evaluate()[0]
            assert not same_line.hasError() and highlighted.count("</font>") == 2
            assert highlighted.startswith("&lt;<b>") and "&gt; &amp; " in highlighted
            capture("logs-search-same-line-" + locale)
            QTest.keyClick(window, Qt.Key.Key_Down)
            highlighted = same_line.evaluate()[0]
            assert highlighted.count("</font>") == 2 and "&amp; <b>" in highlighted
            accent_line = QQmlExpression(engine.rootContext(), logs, "lineText(4, lines[4])")
            for accent_query in ("cafe", "CAFÉ"):
                search_input.setProperty("text", accent_query)
                assert search_count.property("text") == "1/3"
                highlighted = accent_line.evaluate()[0]
                assert not accent_line.hasError() and highlighted.startswith("♪ <b>")
                assert "café</font>" in highlighted and "cafe\u0301</font>" in highlighted and "cafe</font>!" in highlighted
            search_input.setProperty("text", "sample")
            QTest.keyClick(window, Qt.Key.Key_Up)
            assert logs.property("matchIndex") == 2
            log_path.write_text("\n".join(reversed(log_lines[:3])), encoding="utf-8")
            QMetaObject.invokeMethod(logs_refresh, "clicked")
            assert search_count.property("text") == "2/2"
            assert backend.toastVisible and backend.toastText == backend.t("logs_refreshed")
            log_path.write_text("\n".join(reversed(log_lines)), encoding="utf-8")
            QMetaObject.invokeMethod(logs_refresh, "clicked")
            assert search_count.property("text") == "2/3" and search_input.property("text") == "sample"
            search_input.setProperty("text", "   ")
            assert logs.property("matchIndex") == -1 and not search_count.property("visible")
            QTest.keyClick(window, Qt.Key.Key_Escape)
            assert not logs.property("searching") and search_input.property("text") == ""
            assert search_toggle.property("activeFocus")
            QTest.keyClick(window, log_find.key(), log_find.keyboardModifiers())
            search_input.setProperty("text", "sample")
            logs_clear = visual_item(logs, "logsClear")
            original_contents = log_path.read_text(encoding="utf-8")
            original_entries = backend.logsText
            QMetaObject.invokeMethod(logs_clear, "clicked")
            confirm_clear = visual_item(window.contentItem(), "confirmClearLogs")
            with patch.object(Path, "write_text", side_effect=PermissionError("fixture denied")):
                QMetaObject.invokeMethod(confirm_clear, "clicked")
            assert backend.logsText == original_entries and log_path.read_text(encoding="utf-8") == original_contents
            assert search_input.property("text") == "sample" and search_count.property("text") == "1/3"
            toast = visual_item(window.contentItem(), "appToast")
            assert toast.z() > logs.parentItem().z() and backend.toastText == backend.t("logs_clear_failed")
            capture("logs-clear-error-" + locale)
            assert toast.property("visible")
            QMetaObject.invokeMethod(logs_clear, "clicked")
            assert window.property("clearLogsConfirmOpen") and log_path.stat().st_size > 0
            QMetaObject.invokeMethod(confirm_clear, "clicked")
            assert not window.property("clearLogsConfirmOpen") and log_path.stat().st_size == 0
            assert backend.toastVisible and backend.toastText == backend.t("logs_cleared")
            empty_logs = visual_item(logs, "logsEmptyMessage")
            assert empty_logs.property("visible") and empty_logs.property("text") == backend.t("logs_empty")
            assert not logs.property("searching") and search_input.property("text") == ""
            assert not search_toggle.property("enabled") and logs_refresh.property("enabled")
            QTest.keyClick(window, log_find.key(), log_find.keyboardModifiers())
            assert not logs.property("searching")
            capture("logs-empty-" + locale)
            log_path.unlink()
            QMetaObject.invokeMethod(logs_refresh, "clicked")
            assert not log_path.exists() and not backend.logsText
            assert not backend.toastVisible
            assert empty_logs.property("visible") and empty_logs.property("text") == backend.t("logs_empty")
            assert not search_toggle.property("enabled") and not visual_item(logs, "logsCopy").property("enabled")
            capture("logs-missing-" + locale)
            log_path.mkdir()
            QMetaObject.invokeMethod(logs_refresh, "clicked")
            assert not backend.logsText and backend.logsReadError
            assert not backend.toastVisible
            assert empty_logs.property("visible") and empty_logs.property("text") == backend.t("logs_read_unavailable")
            error_detail = visual_item(logs, "logsReadErrorDetail")
            assert error_detail.property("visible") and str(log_path) in error_detail.property("text")
            assert not search_toggle.property("enabled") and not visual_item(logs, "logsCopy").property("enabled")
            capture("logs-read-error-" + locale)
            log_path.rmdir()
            log_path.write_text("\n".join(reversed(log_lines)), encoding="utf-8")
            QMetaObject.invokeMethod(logs_refresh, "clicked")
            assert not empty_logs.property("visible") and search_toggle.property("enabled")
            assert not backend.logsReadError and not error_detail.property("visible")
        backend.hideLogs()
        backend.cfg.log_level = "OFF"
        backend.setLanguage("nl")
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
