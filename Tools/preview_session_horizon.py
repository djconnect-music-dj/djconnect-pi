"""Exercise the real Session mode strip and Horizon layout with offline fixtures."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QSignalBlocker,
    Qt,
    QTimer,
    qInstallMessageHandler,
)
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend
from djconnect_pi.brand_theme import apply_palette
from djconnect_pi.config import Config, save_config
from djconnect_pi.expiry_clock import ExpiryClock
from djconnect_pi.i18n import TRANSLATIONS
from djconnect_pi.session_history_view import presented_history
from djconnect_pi.session_projection import history_page, timestamp

ROOT = Path(__file__).resolve().parents[1]
# Producer-authored format samples; synthetic titles/artists, no provider rights.
LANGUAGES = {
    "en": ("Let’s look a little ahead:", "and", "That is the queue as it stands; if you change it, I’ll follow your lead."),
    "nl": ("Ik kijk even met je vooruit:", "en", "Zo ziet de wachtrij er nu uit; als je iets verandert, beweeg ik met je mee."),
    "de": ("Schauen wir kurz nach vorn:", "und", "So sieht die Warteschlange gerade aus; wenn du etwas änderst, gehe ich mit."),
    "fr": ("Regardons un peu la suite :", "et", "Voilà la file actuelle ; si tu la modifies, je suivrai ton choix."),
    "es": ("Miremos un poco hacia delante:", "y", "Así está la cola ahora; si la cambias, seguiré tu elección."),
}
MOODS = {
    "chill": (None, "#ffffff"),
    "groove": ("#06c7ad", "#ffffff"),
    "energy": ("#ddff00", "#0f1433"),
    "party": ("#db226d", "#ffffff"),
}


def visual_items(item, name):
    if item.objectName() == name:
        yield item
    for child in item.childItems():
        yield from visual_items(child, name)


def one(item, name):
    return next(visual_items(item, name))


def click(window, button):
    point = button.mapToScene(button.boundingRect().center()).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.qWait(65)


def pull_down(window, scroll, touch, distance, *, retract=False):
    start = scroll.mapToScene(QPointF(scroll.width() / 2, 100)).toPoint()
    QTest.touchEvent(window, touch).press(0, start, window).commit()
    offsets = [round(step * distance / 5) for step in range(1, 6)]
    if retract:
        offsets += [round(step * distance / 5) for step in range(4, -1, -1)]
    for offset in offsets:
        point = start + QPoint(0, offset)
        QTest.touchEvent(window, touch).move(0, point, window).commit()
        QTest.qWait(20)
    QTest.touchEvent(window, touch).release(0, point, window).commit()
    QTest.qWait(380)


def capture(window, output, captures, label):
    target = output / f"{label}-{window.width()}x{window.height()}.png"
    assert window.grabWindow().save(str(target))
    captures.append(target.name)


def show(owner, session, caps, stamp, guidance, copy, mood, window, screen, horizon, flow):
    session["selected_mood"] = mood
    session["mood_revision"] += 1
    session["broadcast"]["session_forecast"]["revision"] += 1
    guidance["text"] = copy
    owner._forecast.receive(session, caps, stamp)
    owner.changed.emit()
    QTest.qWait(65)
    if not screen.property("showForecast"):
        click(window, horizon)
    assert screen.property("showForecast")
    assert horizon.property("checked") and not flow.property("checked")
    return one(screen, "sessionForecastSection-guidance")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    apply_palette(app)
    touch = QTest.createTouchDevice()
    fixtures = json.loads((ROOT / "tests/fixtures/session_core/forecast-transport-v1.json").read_text())
    captures = []
    track_names = [
        "“<b>One</b>” — Fixture Artist",
        "“Two, with a comma” — Another Fixture Artist",
        "“" + "LongTitle" * 9 + "” — Artist",
        "“Four” — Artist — Ensemble",
        "“Five” — Artist",
    ]
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Unexpected network")) as requests:
        for width, height, profile in [(720, 720, "pi-zero-2w-arm64"), (1200, 1920, "pi5-arm64")]:
            config = output / f"fixture-{width}.json"
            save_config(config, Config(
                language="nl", release_profile=profile, log_file=str(output / "fixture.log"),
                paired=True, device_token="synthetic-not-a-credential", device_id="djconnect-raspberry-pi-fixture",
                ha_url="http://fixture.invalid", active_profile_id="profile-fixture",
                explicit_profile_id="profile-fixture", active_profile_type="personal",
                active_profile_privacy_mode="private", screen_timeout_seconds=0,
            ))
            backend = DJConnectBackend(config)
            for timer in backend.findChildren(QTimer):
                timer.stop()
            with (
                patch.object(backend._executor, "submit"),
                patch.object(backend._session_executor, "submit"),
                patch.object(backend._question_executor, "submit"),
            ):
                owner = backend.djSession
                image_jobs = []
                owner._forecast_images.executor = SimpleNamespace(submit=image_jobs.append, shutdown=lambda **kwargs: None)
                engine = QQmlApplicationEngine()
                engine.rootContext().setContextProperty("djconnect", backend)
                engine.rootContext().setContextProperty("startWindowed", True)
                engine.load(str(ROOT / "src/djconnect_pi/qml/Main.qml"))
                assert engine.rootObjects(), warnings
                window = engine.rootObjects()[0]
                window.setWidth(width)
                window.setHeight(height)
                window.setProperty("splashVisible", False)
                window.setProperty("activeScreen", "session")
                QTest.qWait(450)  # Let the startup overlay finish its exit animation.
                assert one(window.contentItem(), "navIcon-session").property("iconColor").name() == "#ffffff"
                screen = window.findChild(QObject, "djSessionScreen")
                mode_strip = one(screen, "sessionModeStrip")
                horizon = one(screen, "sessionForecastMode")
                flow = one(screen, "sessionFlowMode")
                vibescape = one(screen, "sessionVibeScapeMode")
                caps = {"capabilities": {"session_forecast": True}, "contract_versions": {"session_forecast": 1}}

                for language, (intro, conjunction, note) in LANGUAGES.items():
                    backend.setLanguage(language)
                    backend._toast_visible = False
                    backend.toastChanged.emit()
                    sample = next(f for f in fixtures if f["language"] == language and f["persona"] == "home_dj")
                    snapshot = deepcopy(sample["reconnect"]["snapshot"])
                    forecast = deepcopy(sample["owner_read"]["forecast"])
                    assert forecast["imagery"]  # The Pi should not fetch an offered image lease.
                    snapshot["session_forecast"] = forecast
                    guidance = next(section for section in forecast["sections"] if section["id"] == "guidance")
                    prose = guidance["text"]
                    stamp = timestamp(forecast["issued_at"]) + 0.001
                    owner._clock = ExpiryClock(lambda stamp=stamp: stamp, lambda: 0.0)
                    session = {**snapshot["session"], "broadcast": snapshot, "dj_persona": sample["persona"],
                               "persona_revision": 0, "session_direction": snapshot["planner"]["session_direction"]}
                    owner.invalidate()
                    owner._session, owner._caps, owner._loaded = session, caps, True

                    for mood, (tint, foreground) in MOODS.items():
                        selected_tracks = track_names[:1] if mood == "chill" else track_names
                        joined = selected_tracks[0] if len(selected_tracks) == 1 else (
                            ", ".join(selected_tracks[:-1]) + f" {conjunction} " + selected_tracks[-1]
                        )
                        section = show(owner, session, caps, stamp, guidance, f"{intro} {joined}. {note}", mood, window, screen, horizon, flow)
                        assert not one(screen, "sessionForecastEmpty").property("visible")
                        banner = one(screen, "sessionHorizonBanner")
                        assert banner.property("source").fileName() == f"horizon-hero-{mood}.png"
                        assert QQmlExpression(engine.rootContext(), banner, "status === 1").evaluate()[0]  # Image.Ready
                        assert not list(visual_items(screen, "sessionForecastArtistCard"))
                        assert not owner.forecastImage and not image_jobs
                        assert not window.property("trackArtworkViewOpen")
                        expression = QQmlExpression(engine.rootContext(), window, "showTrackArtwork(false, true)")
                        expression.evaluate()
                        assert not expression.hasError() and not window.property("trackArtworkOpen")
                        notice = one(screen, "sessionForecastNotice")
                        assert banner.mapToItem(notice, 0, banner.height()).y() < 0
                        if language == "nl":
                            assert notice.property("text") == (
                                "Een richting voor de komende minuten. Muziek en bijdragen kunnen veranderen."
                            )
                        rows = list(visual_items(section, "sessionForecastQueueTrack"))
                        assert [item.property("text") for item in rows] == selected_tracks
                        for item in rows:
                            expr = QQmlExpression(QQmlEngine.contextForObject(item), item, "textFormat === Text.PlainText")
                            assert expr.evaluate()[0] and not expr.hasError()
                        assert all(item.property("contentWidth") <= item.width() + 1 for item in rows)
                        assert one(section, "sessionForecastSectionBody").property("text") == intro
                        assert one(section, "sessionForecastQueueNote").property("text") == note
                        assert one(section, "sessionForecastSectionIcon").property("iconName") == "forecast_clock"
                        assert horizon.property("foregroundColor").name() == foreground
                        if tint:
                            fill = horizon.property("selectionFill")
                            assert fill.name() == QColor(tint).name() and abs(fill.alphaF() - 0.9) < 0.01
                        else:
                            assert horizon.property("selectionFill").alpha() == 0
                        assert vibescape.property("visible") == (profile == "pi5-arm64")
                        assert horizon.property("text") == "Horizon"
                        assert mode_strip.property("visible") and mode_strip.width() == 420
                        assert abs(mode_strip.mapToItem(screen, mode_strip.width() / 2, 0).x() - screen.width() / 2) <= 1
                        buttons = [button for button in (flow, horizon, vibescape) if button.property("visible")]
                        for button in buttons:
                            bounds = button.mapToItem(mode_strip, 0, 0)
                            assert bounds.x() >= 7 and bounds.y() >= 7 and button.height() == 48
                            assert bounds.x() + button.width() <= mode_strip.width() - 7
                            labels = [item for item in button.property("contentItem").childItems() if item.property("text") == button.property("text")]
                            assert len(labels) == 1 and not labels[0].property("truncated")
                        assert max(button.width() for button in buttons) - min(button.width() for button in buttons) <= 1
                        scroll = window.findChild(QObject, "sessionForecastScroll").property("contentItem")
                        if language == "nl":
                            scroll.setProperty("contentY", 0)
                            QTest.qWait(60)
                            capture(window, output, captures, f"horizon-banner-{mood}")
                        scroll.setProperty("contentY", scroll.property("contentY") + section.mapToItem(scroll, 0, 0).y())
                        QTest.qWait(60)
                        capture(window, output, captures, f"horizon-{language}-{mood}")
                        click(window, flow)
                        assert not screen.property("showForecast") and flow.property("checked")
                        assert window.property("trackArtworkViewOpen") and banner.property("source").isEmpty()
                        if mood == "party":
                            show(owner, session, caps, stamp, guidance, ("Fixture forecast paragraph.\n" * 70).strip(),
                                 mood, window, screen, horizon, flow)
                            max_offset = max(0, scroll.property("contentHeight") - scroll.height())
                            offset = min(120, max_offset)
                            assert offset > 0
                            scroll.setProperty("contentY", scroll.property("originY") + offset)
                            click(window, flow)
                            click(window, horizon)
                            assert abs(scroll.property("contentY") - scroll.property("originY") - offset) <= 1
                            click(window, flow)
                            if language == "nl":
                                forecast_scroll = window.findChild(QObject, "sessionForecastScroll")
                                poll_timer = screen.findChild(QObject, "sessionRuntimePollTimer")
                                assert poll_timer is not None
                                flow_view = window.findChild(QObject, "sessionHistoryScroll")
                                spinner = one(screen, "sessionPullRefreshSpinner")
                                with QSignalBlocker(poll_timer), patch.object(owner, "_launch", side_effect=(
                                    lambda action, **kwargs: owner._set_running(1, action, **kwargs)
                                )) as refresh:
                                    owner.poll()
                                    assert owner.refreshing and not spinner.property("visible")
                                    owner._set_running()
                                    refresh.reset_mock()
                                    for view, switch in ((flow_view, horizon), (forecast_scroll, flow)):
                                        view_flickable = view.property("contentItem")
                                        view_flickable.setProperty("contentY", view_flickable.property("originY"))
                                        QTest.qWait(65)
                                        pull_down(window, view, touch, 24)
                                        refresh.assert_not_called()
                                        assert not spinner.property("visible")
                                        pull_down(window, view, touch, 230)
                                        refresh.assert_called_once_with("refresh", background=True)
                                        assert spinner.property("visible") and spinner.property("running")
                                        assert spinner.y() == view.y() + 8
                                        click(window, switch)
                                        assert not spinner.property("visible")
                                        owner._set_running()
                                        refresh.reset_mock()

                    # Ordinary prose and a malformed six-track lookalike stay verbatim.
                    for fallback in (prose, f"{intro} {', '.join([track_names[0]] * 6)}. {note}"):
                        section = show(owner, session, caps, stamp, guidance, fallback, "chill", window, screen, horizon, flow)
                        assert not list(visual_items(section, "sessionForecastQueueTrack"))
                        assert one(section, "sessionForecastSectionBody").property("text") == fallback
                        assert not one(section, "sessionForecastQueueNote").property("visible")
                    owner._forecast.expire(stamp + 121)
                    owner.changed.emit()
                    QTest.qWait(65)
                    assert not list(visual_items(screen, "sessionForecastQueueTrack"))
                    empty = one(screen, "sessionForecastEmpty")
                    assert empty.property("visible")
                    assert one(empty, "sessionForecastEmptyIcon").property("iconName") == "forecast_horizon"
                    assert one(empty, "sessionForecastEmptyMessage").property("text") == TRANSLATIONS[language]["session_forecast_empty"]
                    forecast_view = window.findChild(QObject, "sessionForecastScroll")
                    forecast_flick = forecast_view.property("contentItem")
                    if language == "nl":
                        assert forecast_flick.property("contentHeight") <= forecast_flick.height()
                        forecast_flick.setProperty("contentY", forecast_flick.property("originY"))
                        QTest.qWait(65)
                        with QSignalBlocker(poll_timer), patch.object(owner, "_launch") as refresh:
                            pull_down(window, forecast_view, touch, 230, retract=True)
                            refresh.assert_not_called()
                            pull_down(window, forecast_view, touch, 230)
                            refresh.assert_called_once_with("refresh", background=True)
                    click(window, flow)
                    sid = session["session_id"]
                    row = {
                        "entry_id": "fixture-withdrawn", "session_id": sid, "order": 1,
                        "occurred_at": forecast["issued_at"], "retained_until": forecast["valid_until"],
                        "kind": "conversation_dj", "role": "assistant", "turn_id": "turn-a",
                        "origin_message_id": "message-a", "input_type": "text",
                        "state": "source_withdrawn", "text": "", "context": {"session_id": sid},
                        "historical_view_only": True, "current_display_allowed": False,
                    }
                    page = history_page({
                        "success": True, "schema_version": 2, "history_contract_version": 2,
                        "session": {"session_id": sid}, "revision": "fixture-revision", "entries": [row],
                    }, session_id=sid, now=stamp)
                    owner._retained_flow = presented_history(page)["entries"]
                    owner.changed.emit()
                    QTest.qWait(65)
                    card = one(screen, "sessionConversationCard")
                    assert card.property("sourceWithdrawn")
                    expr = QQmlExpression(QQmlEngine.contextForObject(card), card, "!bodyItem.visible")
                    assert expr.evaluate()[0] and not expr.hasError()
                    assert not one(card, "sessionRetainedAskAbout").property("visible")
                    scroll = window.findChild(QObject, "sessionHistoryScroll").property("contentItem")
                    scroll.setProperty("contentY", scroll.property("contentY") + card.mapToItem(scroll, 0, 0).y())
                    QTest.qWait(60)
                    capture(window, output, captures, f"withdrawn-{language}")
                    if language == "nl":
                        owner._session = {**session, "broadcast": {
                            **session["broadcast"], "native_delivery": {}, "dj_moments": [],
                        }}
                        older = [{**row, "entry_id": f"older-{index}", "order": index}
                                 for index in range(1, 16)]
                        history = history_page({
                            "success": True, "schema_version": 2, "history_contract_version": 2,
                            "session": {"session_id": sid}, "revision": "older", "entries": older,
                        }, session_id=sid, now=stamp)
                        owner._retained_flow = presented_history(history)["entries"]
                        owner.changed.emit()
                        QTest.qWait(80)
                        attribution = one(screen, "sessionPlaybackAttribution")
                        jump = one(screen, "sessionJumpCurrent")
                        assert attribution.property("visible")
                        attribution_y = attribution.y()
                        scroll.setProperty("contentY", attribution_y + attribution.height() / 2)
                        assert not jump.property("visible")
                        scroll.setProperty("contentY", attribution_y + attribution.height() + 1)
                        assert jump.property("visible")
                        click(window, jump)
                        assert abs(scroll.property("contentY") - scroll.property("originY")) <= 1
                        assert not jump.property("visible")
                    forecast_flick.setProperty("contentY", forecast_flick.property("originY") + 85)
                    owner.invalidate()
                    QTest.qWait(65)
                    assert abs(forecast_flick.property("contentY") - forecast_flick.property("originY")) <= 1

                window.setProperty("activeScreen", "history")
                QTest.qWait(65)
                archive_empty = one(screen, "sessionArchiveEmptyState")
                archive_window_empty = one(screen, "sessionArchiveEmpty")
                archive_title = one(screen, "sessionArchiveEmptyTitle")
                assert not archive_empty.property("visible")
                owner._set_running()
                owner._history_loaded = True
                owner.changed.emit()
                archive_scroll = window.findChild(QObject, "sessionHistoryScroll")
                for language in LANGUAGES:
                    backend.setLanguage(language)
                    QTest.qWait(65)
                    assert archive_empty.property("visible") and not archive_window_empty.property("visible")
                    assert archive_title.property("text") == TRANSLATIONS[language]["session_history_empty"]
                    assert archive_title.property("contentWidth") <= archive_title.width() + 1
                    assert one(screen, "sessionArchiveEmptyIcon").property("iconName") == "history"
                    center = archive_empty.mapToItem(archive_scroll, archive_empty.width() / 2, archive_empty.height() / 2)
                    assert abs(center.y() - archive_scroll.height() / 2) <= 1
                owner._history_start = "fixture-older-window"
                owner.changed.emit()
                assert not archive_empty.property("visible") and archive_window_empty.property("visible")
                owner._error = "unavailable"
                owner.changed.emit()
                assert not archive_empty.property("visible") and not archive_window_empty.property("visible")
                backend.shutdown()
                delete(window)
                delete(engine)
                delete(backend)
                QTest.qWait(20)
        requests.assert_not_called()
    errors = [warning for warning in warnings if "Populating font family aliases" not in warning]
    (output / "readback.json").write_text(json.dumps({"captures": captures, "warnings": warnings}, indent=2))
    assert not errors, errors
    print(f"{len(captures)} actual QML views, both formats and five locales; no network")


if __name__ == "__main__":
    main()
