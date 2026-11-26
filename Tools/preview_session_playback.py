"""Real shared Qt buttons/controller; synthetic owner transport, no HA/player actions."""
from __future__ import annotations

import argparse
import base64
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QTimer,
    qInstallMessageHandler,
)
from PySide6.QtGui import (
    QAccessible,
    QAccessibleActionInterface,
    QGuiApplication,
    QImage,
)
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend
from djconnect_pi.brand_theme import apply_palette
from djconnect_pi.config import Config, save_config
from djconnect_pi.expiry_clock import ExpiryClock
from djconnect_pi.ha import Playback
from djconnect_pi.i18n import TRANSLATIONS
from djconnect_pi.session_projection import timestamp
from djconnect_pi.session_transport import (
    JSON_CONTROL_BYTES,
    SessionError,
    SessionTransport,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    QQuickStyle.setStyle('Basic')
    app = QGuiApplication([])
    apply_palette(app)
    captures = []
    art = QImage(16, 16, QImage.Format.Format_RGB32)
    art.fill(Qt.GlobalColor.cyan)
    art_buffer = QBuffer()
    assert art_buffer.open(QIODevice.OpenModeFlag.WriteOnly) and art.save(art_buffer, 'PNG')
    art_source = 'data:image/png;base64,' + base64.b64encode(bytes(art_buffer.data())).decode()
    art_path = '/api/djconnect/v1/image_proxy/' + 'a' * 24
    original = json.loads((ROOT/'tests/fixtures/session_core/native-expressive-snapshots.json').read_text())
    with patch('requests.request', side_effect=AssertionError('Unexpected network')) as network:
        for width, height, profile in [(720, 720, 'pi-zero-2w-arm64'), (1200, 1920, 'pi5-arm64')]:
            for language in ['en', 'nl', 'de', 'fr', 'es']:
                path = output/f'fixture-{width}-{language}.json'
                save_config(path, Config(language=language, release_profile=profile, log_file=str(output/'fixture.log'),
                    paired=True, device_token='synthetic-not-a-credential', device_id='djconnect-raspberry-pi-fixture',
                    ha_url='http://fixture.invalid', explicit_profile_id='profile-a', active_profile_id='profile-a',
                    active_profile_type='personal', active_profile_privacy_mode='private', screen_timeout_seconds=0,
                    music_backend='spotify_direct', music_backend_available=True, music_backend_revision=4,
                    music_backend_capabilities={'supports_favorites': True}))
                with patch.object(QTimer, 'singleShot'):
                    backend = DJConnectBackend(path)
                for timer in backend.findChildren(QTimer):
                    timer.stop()
                reads = []
                intents = []
                backend._executor.submit = Mock()
                backend._session_executor = SimpleNamespace(submit=reads.append, shutdown=lambda **kw: None)
                backend._question_executor.submit = Mock()
                owner = backend.djSession
                owner._playback_control.executor = SimpleNamespace(submit=intents.append, shutdown=lambda **kw: None)
                snapshot = deepcopy(original['formation'][language]['home_dj'][-1])
                snapshot['playback']['target_name'] = 'Synthetic loudspeaker'
                grant = next(row for row in snapshot['native_delivery']['admissions'] if row['moment_id'] == snapshot['native_delivery']['current_moment_id'])
                owner._clock = ExpiryClock(lambda: timestamp(grant['display_expires_at'])-1, lambda: 0)
                session = {**snapshot['session'], 'broadcast': snapshot}
                owner._session = deepcopy(session)
                owner._loaded = True
                owner.transport.active = Mock(return_value={'success': True, 'session': deepcopy(session)})
                owner.transport.capabilities = Mock(return_value={'success': True})
                owner.transport.playback_status = Mock(return_value={'success': True, 'music_backend': 'spotify_direct',
                    'music_backend_revision': 4, 'music_backend_available': True, 'backend_available': True,
                    'music_backend_capabilities': {'supports_favorites': True},
                    'playback': {'uri': 'spotify:track:'+snapshot['playback']['source_url'].rsplit('/',1)[-1], 'has_playback': True, 'is_playing': True, 'favorite_status': False, 'is_liked': False}})
                owner.transport.playback_status.return_value['playback']['device'] = {
                    'id': 'synthetic-output', 'name': 'Synthetic loudspeaker',
                    'active': True, 'is_restricted': False, 'supports_volume': True,
                }
                owner.transport.playback_command = Mock(return_value={'success': True, 'playback': {'title': 'Never import command response'}})
                owner.transport.playback_favorite = Mock(return_value={'success': True})
                engine = QQmlApplicationEngine()
                engine.rootContext().setContextProperty('djconnect', backend)
                engine.rootContext().setContextProperty('startWindowed', True)
                engine.load(str(ROOT/'src/djconnect_pi/qml/Main.qml'))
                assert engine.rootObjects(), warnings
                window = engine.rootObjects()[0]
                window.setWidth(width); window.setHeight(height); window.setProperty('splashVisible', False)
                window.setProperty('activeScreen', 'session')
                for obj in window.findChildren(QObject):
                    if (obj.objectName() == 'sessionRuntimePollTimer'
                            or ('Timer' in obj.metaObject().className() and obj.property('interval') == 3000)):
                        expression = QQmlExpression(engine.rootContext(), obj, 'running=false')
                        expression.evaluate(); assert not expression.hasError()
                def drain():
                    for _ in range(4):
                        if not reads:
                            break
                        reads.pop(0)()
                    assert not reads
                    QTest.qWait(80)
                drain()
                owner.refresh(); drain()
                assert owner.state['moments']
                QTest.qWait(700)  # Let the existing startup fade complete before real mouse input.
                backend._toast_visible = False; backend.toastChanged.emit()
                screen = window.findChild(QObject, 'djSessionScreen')
                popup = window.findChild(QObject, 'sessionVolumePanel')
                def click(name):
                    button = window.findChild(QObject, name)
                    assert button is not None and button.property('visible') and button.property('enabled'), name
                    point = button.mapToScene(QPointF(button.width()/2, button.height()/2))
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(point.x()), round(point.y())))
                    QTest.qWait(60)
                def capture(name):
                    QTest.qWait(60)
                    file = output/f'{width}-{language}-{name}.png'
                    assert window.grabWindow().save(str(file))
                    captures.append({'width': width, 'height': height, 'locale': language, 'case': name,
                        'status': owner.state['playback_control']['status'], 'file': file.name})
                def visual(item, name):
                    if item.objectName() == name:
                        return item
                    for child in item.childItems():
                        found = visual(child, name)
                        if found is not None:
                            return found
                    return None
                current_card = visual(window.contentItem(), 'sessionLiveMomentCard')
                scroll = window.findChild(QObject, 'sessionHistoryScroll').property('contentItem')
                top = current_card.mapToItem(scroll, QPointF(0, 0)).y()
                assert 0 <= top < scroll.property('height'), (width, language, 'contribution below fold', top)
                artwork_button = visual(window.contentItem(), 'sessionExpandTrackArtwork')
                metadata = visual(window.contentItem(), 'sessionPlaybackMetadata')
                heading = window.findChild(QObject, 'sessionFlowHeading')
                assert not heading.property('visible')
                assert 0 <= metadata.mapToItem(scroll, 0, 0).y() < 1
                attribution = visual(window.contentItem(), 'sessionPlaybackAttribution')
                assert attribution.property('visible') and attribution.property('ready')
                card_bottom = metadata.mapToItem(scroll, 0, metadata.height()).y()
                assert attribution.mapToItem(scroll, 0, 0).y() >= card_bottom
                gap = top - attribution.mapToItem(scroll, 0, attribution.height()).y()
                assert abs(gap - (22 if width == 720 else 26)) <= 1, (width, language, gap)
                artist = visual(window.contentItem(), 'sessionPlaybackArtist')
                album = visual(window.contentItem(), 'sessionPlaybackAlbum')
                assert artist.property('visible') and album.property('visible')
                assert artist.property('text') == owner.state['playback']['artist']
                assert album.property('text') == owner.state['playback']['album']
                assert album.mapToItem(metadata, 0, 0).y() > artist.mapToItem(metadata, 0, artist.height()).y()
                assert artist.property('font').pixelSize() >= 22 and album.property('font').pixelSize() >= 21
                scroll_view = window.findChild(QObject, 'sessionHistoryScroll')
                nav_tab = visual(window.contentItem(), 'appTab-session')
                composer = visual(window.contentItem(), 'sessionComposerDock')
                viewport_bottom = scroll_view.mapToScene(QPointF(0, scroll_view.height())).y()
                assert viewport_bottom > nav_tab.mapToScene(QPointF(0, nav_tab.height())).y()
                original_caps = owner._caps
                owner._caps = {
                    'capabilities': {'session_conversation_history': True, 'ask_dj_contextual_dialogue': True},
                    'contract_versions': {'session_history_entries': 2, 'ask_dj_contextual_dialogue': 1},
                }
                owner.changed.emit()
                QTest.qWait(60)
                assert composer.property('visible') and viewport_bottom > composer.mapToScene(QPointF(0, composer.height())).y()
                assert scroll_view.property('dockClearance') >= viewport_bottom - composer.mapToScene(QPointF()).y()
                assert abs(scroll.property('contentHeight') - metadata.parentItem().implicitHeight()
                           - scroll_view.property('dockClearance')) <= 1
                owner._caps = original_caps
                owner.changed.emit()
                QTest.qWait(60)
                mode_strip = visual(window.contentItem(), 'sessionModeStrip')
                if mode_strip.property('visible'):
                    lead_gap = metadata.mapToScene(QPointF()).y() - mode_strip.mapToScene(QPointF(0, mode_strip.height())).y()
                    assert abs(lead_gap - (20 if width == 720 else 26)) <= 1, (width, language, lead_gap)
                assert visual(window.contentItem(), 'sessionPlaybackVinyl') is None
                cover_width_limit = metadata.property('availableWidth') / 2
                expected_cover_size = min(120, int(cover_width_limit)) if width == 720 else int(cover_width_limit)
                assert artwork_button.width() == artwork_button.height() == expected_cover_size
                assert artwork_button.width() <= cover_width_limit
                assert artwork_button.mapToItem(metadata, 0, 0).x() >= 0
                assert artwork_button.mapToItem(metadata, artwork_button.width(), artwork_button.height()).x() <= metadata.width()
                meter = window.findChild(QObject, 'sessionPlaybackMeter')
                bars = [item for item in meter.childItems() if item.property('level') is not None]
                assert meter.property('visible') and meter.property('playing') and len(bars) == 4
                assert max(bar.height() for bar in bars) > min(bar.height() for bar in bars)
                phase = meter.property('phase')
                QTest.qWait(100)
                assert (meter.property('phase') != phase) == meter.property('animating')
                capture('inline-track-art')
                owner._playback_artwork.fetch = Mock(return_value=art_source)
                art_jobs = []
                owner._playback_artwork.executor = SimpleNamespace(submit=art_jobs.append, shutdown=lambda **kw: None)
                cover = visual(window.contentItem(), 'sessionTrackArtwork')
                current_playback = owner._session['broadcast']['playback']
                original_metadata = {key: current_playback[key] for key in ('artist', 'album')}
                current_playback.update(
                    artist='Nora Vale and the Midnight House Orchestra with the East Coast Quartet',
                    album='Live at the Independent Listening Room and Other Recordings from the Summer Tour',
                )
                owner.changed.emit()
                QTest.qWait(60)
                for key, label in [('artist', artist), ('album', album)]:
                    assert label.property('text') == current_playback[key]
                    assert label.property('lineCount') > 1 and not label.property('truncated')
                    assert label.mapToItem(metadata, label.width(), 0).x() <= metadata.width()
                capture('wrapped-track-metadata')
                current_playback.update(original_metadata)
                original_item = current_playback['item_id']
                current_playback['artwork_url'] = art_path
                owner.changed.emit()
                assert len(art_jobs) == 1
                art_jobs.pop(0)()
                expression = QQmlExpression(engine.rootContext(), cover, 'Number(status) === 1')
                for _ in range(20):
                    if expression.evaluate()[0] is True:
                        break
                    QTest.qWait(50)
                assert expression.evaluate()[0] is True, warnings
                capture('current-track-art')
                click('sessionExpandTrackMetadata')
                overlay = visual(window.contentItem(), 'trackArtworkOverlay')
                expanded = visual(overlay, 'expandedTrackArtwork')
                QTest.qWait(100)
                assert overlay.width() == width and overlay.height() == height
                title = visual(overlay, 'expandedTrackTitle')
                artist = visual(overlay, 'expandedTrackArtist')
                card = expanded.parentItem()
                column = card.parentItem()
                assert expanded.width() == expanded.height() and expanded.width() > width * 0.65
                assert title.y() == card.height() + column.property('spacing')
                assert card.width() == column.width() or abs(column.height() - column.parentItem().height()) < 1
                assert expanded.property('source') == cover.property('source')
                assert title.property('text') == owner.state['playback']['title']
                assert artist.property('text') == owner.state['playback']['artist']
                capture('fullscreen-track-art')
                shade = visual(window.contentItem(), 'trackArtworkControlsShade')
                assert shade.property('visible')
                previous = visual(window.contentItem(), 'sessionPlaybackPrevious')
                toggle = visual(window.contentItem(), 'sessionPlaybackToggle')
                controls = visual(overlay, 'trackArtworkTransportControls')
                for name in ('sessionPlaybackRepeat', 'sessionPlaybackShuffle', 'sessionArtworkFavorite',
                             'sessionPlaybackPrevious', 'sessionPlaybackToggle', 'sessionPlaybackNext',
                             'sessionPlaybackSeekBack', 'sessionPlaybackSeekForward'):
                    button = visual(controls, name)
                    assert button is not None and button.width() == button.height()
                    icon = button.property('contentItem').childItems()[0]
                    scale = 1 if width == 720 else min(1.45, width / 835)
                    assert icon.width() >= 77 * scale
                    start = button.mapToItem(card, QPointF())
                    end = button.mapToItem(card, QPointF(button.width(), button.height()))
                    assert start.x() >= 0 and start.y() >= 0 and end.x() <= card.width() and end.y() <= card.height()
                back = visual(controls, 'sessionPlaybackSeekBack')
                forward = visual(controls, 'sessionPlaybackSeekForward')
                assert back.y() == forward.y() > previous.y() and back.x() < toggle.x() < forward.x()
                gap = previous.mapToScene(QPointF(previous.width(), previous.height()/2))
                gap_x = (gap.x() + toggle.mapToScene(QPointF(0, 0)).x()) / 2
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                 QPoint(round(gap_x), round(gap.y())))
                QTest.qWait(60)
                assert not shade.property('visible') and window.property('trackArtworkOpen') and not intents
                image = window.grabWindow()
                scale = image.devicePixelRatio()
                for x, y in ((3, 3), (expanded.width() - 3, 3),
                             (3, expanded.height() - 3), (expanded.width() - 3, expanded.height() - 3)):
                    corner = expanded.mapToScene(QPointF(x, y))
                    assert image.pixelColor(round(corner.x() * scale), round(corner.y() * scale)).name() != '#00ffff'
                center = expanded.mapToScene(QPointF(expanded.width() / 2, expanded.height() / 2))
                assert image.pixelColor(round(center.x() * scale), round(center.y() * scale)).name() == '#00ffff'
                click('expandedTrackArtwork')
                assert shade.property('visible')
                click('closeTrackArtwork')
                assert not window.property('trackArtworkOpen')
                for x, y in ((8, 8), (artwork_button.width() - 8, artwork_button.height() - 8)):
                    origin = artwork_button.mapToScene(QPointF(x, y))
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                     QPoint(round(origin.x()), round(origin.y())))
                    QTest.qWait(60)
                    assert window.property('trackArtworkOpen') and not popup.property('opened') and not intents
                    owner.transport.playback_command.assert_not_called()
                    click('closeTrackArtwork')
                    assert not window.property('trackArtworkOpen')
                current_playback['item_id'] = 'synthetic-cover-replacement'
                current_playback['artwork_url'] = art_path[:-1] + 'b'
                owner.changed.emit()
                assert len(art_jobs) == 1 and cover.property('source').isEmpty()
                current_playback['artwork_url'] = 'https://external.invalid/album.jpg'
                owner.changed.emit()
                art_jobs.pop(0)()
                QTest.qWait(80)
                assert not art_jobs and cover.property('source').isEmpty()
                current_playback['item_id'] = original_item
                del current_playback['artwork_url']
                owner.changed.emit()

                waiting = window.findChild(QObject, 'sessionWaitingCard')
                waiting_vinyl = window.findChild(QObject, 'sessionWaitingVinyl')
                moments, retained = owner._moments, owner._retained_flow
                owner._moments, owner._retained_flow = [], []
                owner.changed.emit()
                QTest.qWait(80)
                assert waiting.property('visible') and not waiting_vinyl.property('visible')
                assert metadata.mapToItem(scroll, 0, metadata.height()).y() < waiting.mapToItem(scroll, 0, 0).y()
                capture('inline-art-before-waiting')
                assert not waiting.property('compactIdle')  # A hidden moment is not an idle receipt.
                active_session = owner._session
                owner._session = deepcopy(active_session)
                idle_broadcast = owner._session['broadcast']
                idle_broadcast['session']['runtime_state'] = 'active'
                idle_broadcast['native_delivery'].update(current_moment_id=None, current_silence_moment_id=None)
                owner.changed.emit()
                QTest.qWait(80)
                assert waiting.property('compactIdle') and waiting.width() < 120
                waiting_avatar = window.findChild(QObject, 'sessionWaitingAvatar')
                paused_badge = window.findChild(QObject, 'sessionWaitingPauseBadge')
                assert waiting_avatar.property('visible') and not paused_badge.property('visible')
                assert window.findChild(QObject, 'sessionCompactEnd').property('enabled')
                assert window.findChild(QObject, 'sessionCompactEnd').property('visible')
                capture('confirmed-idle-compact')
                idle_broadcast['playback']['state'] = 'paused'
                owner.changed.emit()
                QTest.qWait(80)
                paused_detail = window.findChild(QObject, 'sessionWaitingPausedDetail')
                waiting_title = window.findChild(QObject, 'sessionWaitingTitle')
                assert paused_detail.property('visible') and paused_badge.property('visible')
                assert not waiting_avatar.property('visible')
                assert paused_badge.width() == 64 and paused_badge.height() == 64
                assert waiting_title.property('text') == TRANSLATIONS[language]['session_playback_paused']
                assert paused_detail.property('text') == TRANSLATIONS[language]['session_playback_paused_detail']
                assert window.findChild(QObject, 'sessionExpandedEnd').property('enabled')
                assert metadata.property('visible')
                start_hint = window.findChild(QObject, 'sessionWaitingStartHint')
                assert not start_hint.property('visible')
                capture('waiting-paused')
                owner._session['broadcast']['playback'] = {}
                owner.changed.emit()
                QTest.qWait(80)
                assert waiting_vinyl.property('visible') and not metadata.property('visible')
                assert not paused_detail.property('visible')
                assert waiting_avatar.property('visible') and not paused_badge.property('visible')
                assert waiting_title.property('text') == TRANSLATIONS[language]['session_start_here']
                hint_key = 'session_start_here_ask' if owner.state['composer_available'] else 'session_start_here_music'
                assert start_hint.property('visible') and start_hint.property('text') == TRANSLATIONS[language][hint_key]
                assert start_hint.property('contentWidth') <= start_hint.width() + 1
                with patch.object(owner, '_composer_supported', return_value=True):
                    owner.changed.emit()
                    QTest.qWait(80)
                    assert start_hint.property('text') == TRANSLATIONS[language]['session_start_here_ask']
                    assert start_hint.property('contentWidth') <= start_hint.width() + 1
                capture('waiting-vinyl-no-current-item')
                owner._session = {}
                owner.changed.emit()
                QTest.qWait(80)
                assert not heading.property('visible')
                assert window.findChild(QObject, 'sessionStartCard').property('visible')
                assert window.findChild(QObject, 'sessionWelcomeTitle').property('text') == TRANSLATIONS[language]['session_welcome_title']
                capture('heading-start-session')
                owner._session = active_session
                owner._moments, owner._retained_flow = moments, retained
                owner.changed.emit()
                QTest.qWait(80)
                paused = deepcopy(session)
                paused['broadcast']['playback']['state'] = 'paused'
                owner.transport.active.return_value = {'success': True, 'session': paused}
                owner.transport.playback_status.return_value['playback']['is_playing'] = False
                owner.refresh(); drain()
                assert visual(window.contentItem(), 'sessionLiveMomentCard').property('visible')
                assert not paused_detail.property('visible')
                assert not meter.property('playing') and not meter.property('animating')
                assert max(bar.height() for bar in bars) == min(bar.height() for bar in bars)
                phase = meter.property('phase')
                QTest.qWait(100)
                assert meter.property('phase') == phase
                owner.transport.active.return_value = {'success': True, 'session': deepcopy(session)}
                owner.transport.playback_status.return_value['playback']['is_playing'] = True
                owner.refresh(); drain()
                progress = window.findChild(QObject, 'sessionPlaybackProgress')
                assert progress.height() > 0
                artwork_right = artwork_button.mapToItem(metadata, artwork_button.width(), 0).x()
                progress_row = window.findChild(QObject, 'sessionPlaybackProgressRow')
                progress_origin = progress_row.mapToItem(metadata, 0, 0)
                if width == 720:
                    assert abs(progress_origin.x() - artwork_button.mapToItem(metadata, 0, 0).x()) < 1
                    assert progress_origin.y() >= artwork_button.mapToItem(metadata, 0, artwork_button.height()).y()
                else:
                    assert progress_origin.x() > artwork_right
                    assert progress_origin.y() < artwork_button.mapToItem(metadata, 0, artwork_button.height()).y()
                capture('progress-layout')
                original_session = owner._session
                owner._session = deepcopy(original_session)
                progress_playback = owner._session['broadcast']['playback']
                progress_playback.update(duration_ms=720000, position_ms=599000)
                owner.changed.emit()
                QTest.qWait(60)
                playback_time = window.findChild(QObject, 'sessionPlaybackTime')
                playback_bar = window.findChild(QObject, 'sessionPlaybackBar')
                assert playback_time.property('text') == '9:59 / 12:00'
                bar_width = playback_bar.width()
                progress_playback['position_ms'] = 600000
                owner.changed.emit()
                QTest.qWait(60)
                assert playback_time.property('text') == '10:00 / 12:00'
                assert abs(playback_bar.width() - bar_width) <= 1
                owner._session = original_session
                owner.changed.emit()
                QTest.qWait(60)
                output_panel = window.findChild(QObject, 'sessionOutputPanel')
                def assert_popup_anchor(trigger, panel):
                    top = trigger.mapToItem(screen, 0, 0)
                    bottom = trigger.mapToItem(screen, trigger.width(), trigger.height())
                    popup_width = panel.property('width')
                    popup_height = panel.property('height')
                    x, y = panel.property('x'), panel.property('y')
                    expected_x = max(16, min((top.x() + bottom.x() - popup_width)/2,
                                             width - popup_width - 16))
                    assert abs(x - expected_x) < 2, (width, x, expected_x)
                    assert 15 <= y and y + popup_height <= height - 15
                    if bottom.y() + 8 + popup_height <= height - 16:
                        assert abs(y - (bottom.y() + 8)) < 2

                transport_panel = window.findChild(QObject, 'sessionTransportPanel')
                click('sessionPlaybackProgress')
                assert transport_panel.property('opened') and not window.property('trackArtworkOpen')
                assert not popup.property('opened') and not output_panel.property('opened') and not intents
                assert_popup_anchor(progress, transport_panel)
                assert progress.height() >= 44
                transport_title = visual(transport_panel.property('contentItem'), 'sessionTransportTrackTitle')
                transport_subtitle = visual(transport_panel.property('contentItem'), 'sessionTransportTrackSubtitle')
                assert transport_title.property('visible') and transport_title.property('text') == owner.state['playback']['title']
                assert transport_subtitle.property('visible') and transport_subtitle.property('text') == owner.state['playback']['artist']
                assert transport_subtitle.y() >= transport_title.y() + transport_title.height()
                displayed_playback = owner._session['broadcast']['playback']
                artist = displayed_playback['artist']
                displayed_playback['artist'] = ' '
                owner.changed.emit()
                target = owner.state['playback']['target_name']
                assert target and transport_subtitle.property('text') == target
                subtitle_accessible = QAccessible.queryAccessibleInterface(transport_subtitle)
                assert subtitle_accessible.text(QAccessible.Text.Name) == f'{TRANSLATIONS[language]["output_device"]}: {target}'
                displayed_playback['artist'] = artist
                owner.changed.emit()
                transport_position = visual(transport_panel.property('contentItem'), 'sessionTransportPosition')
                transport_progress = visual(transport_panel.property('contentItem'), 'sessionTransportProgress')
                assert transport_position.property('visible') and transport_position.property('text') == owner.state['playback']['time_label']
                assert transport_progress.property('value') == owner.state['playback']['progress']
                seek_back = visual(transport_panel.property('contentItem'), 'sessionPlaybackSeekBack')
                seek_forward = visual(transport_panel.property('contentItem'), 'sessionPlaybackSeekForward')
                previous_track = visual(transport_panel.property('contentItem'), 'sessionPlaybackPrevious')
                assert abs(seek_back.y() - seek_forward.y()) <= 1
                assert seek_back.y() + seek_back.height() < previous_track.y()
                assert transport_position.mapToItem(seek_back, 0, transport_position.height()).y() <= 0
                capture('direct-playback-controls')

                original_session = owner._session
                owner._session = deepcopy(original_session)
                current_playback = owner._session['broadcast']['playback']
                panel_position = (transport_panel.property('x'), transport_panel.property('y'))
                transport_bar_width = transport_progress.width()
                current_playback['position_ms'] += 750
                owner.changed.emit()
                QTest.qWait(60)
                assert transport_panel.property('opened') and not intents
                assert (transport_panel.property('x'), transport_panel.property('y')) == panel_position
                assert abs(transport_progress.width() - transport_bar_width) <= 1
                assert transport_position.property('text') == owner.state['playback']['time_label']
                click('sessionPlaybackPrevious')
                assert len(intents) == 1
                intents.pop()()
                owner.transport.playback_command.assert_called_once()
                assert owner.transport.playback_command.call_args.args[1] == 'previous'
                owner.transport.playback_command.reset_mock()
                drain()
                assert transport_panel.property('opened')

                owner._session = deepcopy(original_session)
                owner._session['broadcast']['playback']['item_id'] = 'synthetic-new-track'
                owner.changed.emit()
                assert not transport_panel.property('opened') and not intents
                owner._session = original_session
                owner.changed.emit()
                QTest.qWait(120)

                accessible_progress = QAccessible.queryAccessibleInterface(progress)
                assert accessible_progress.role() == QAccessible.Role.Button
                assert accessible_progress.text(QAccessible.Text.Name) == TRANSLATIONS[language]['session_playback_controls']
                assert accessible_progress.text(QAccessible.Text.Description) == playback_time.property('text')
                assert accessible_progress.childCount() == 0
                accessible_progress.actionInterface().doAction(QAccessibleActionInterface.pressAction())
                QTest.qWait(60)
                assert transport_panel.property('opened') and not intents
                QTest.keyClick(window, Qt.Key.Key_Escape)
                QTest.qWait(120)
                assert not transport_panel.property('opened') and not intents

                insight = window.findChild(QObject, 'sessionPlaybackInsight')
                original_playback = backend.playback
                original_request = backend._request_track_insight
                backend._request_track_insight = Mock()
                backend.playback = Playback(title='Another track', uri='spotify:track:mismatch', is_playing=True)
                backend.titleChanged.emit()
                backend.trackInsightPlaybackItemChanged.emit()
                assert not insight.property('enabled')
                backend.playback.uri = 'spotify:track:' + session['broadcast']['playback']['source_url'].rsplit('/', 1)[-1]
                backend.trackInsightPlaybackItemChanged.emit()
                assert insight.property('enabled') and insight.width() >= 44
                click('sessionPlaybackInsight')
                assert window.property('activeScreen') == 'trackinsight'
                backend._request_track_insight.assert_called_once_with(show_toast=False)
                assert not transport_panel.property('opened') and not intents
                window.setProperty('activeScreen', 'session')
                backend.playback = original_playback
                backend._request_track_insight = original_request
                backend.titleChanged.emit()
                backend.trackInsightPlaybackItemChanged.emit()
                drain()

                initial_caps = backend.cfg.music_backend_capabilities
                backend.cfg.music_backend_capabilities = {
                    **initial_caps, 'supports_volume': True, 'supports_outputs': True,
                    'supports_transfer_or_output_selection': True,
                }
                original_session = owner._session
                owner._session = deepcopy(original_session)
                original_status = owner.transport.playback_status.return_value
                observed = deepcopy(original_status)
                speaker_name = 'Living room and dining room speakers with the hallway and balcony grouped together'
                owner._session['broadcast']['playback']['target_name'] = speaker_name
                observed['music_backend_capabilities'].update(
                    supports_volume=True, supports_outputs=True, supports_transfer_or_output_selection=True)
                observed['playback'].update(volume_percent=11, device={
                    'id': 'synthetic-output', 'name': speaker_name, 'active': True,
                    'is_restricted': False, 'supports_volume': True,
                })
                owner.transport.playback_status.return_value = observed
                owner.transport.playback_outputs = Mock(return_value={**observed, 'devices': [
                    {'id': 'synthetic-tenth', 'name': 'Speaker 10', 'active': False, 'cached': False, 'is_restricted': False},
                    {'id': 'synthetic-output', 'name': speaker_name, 'active': True, 'cached': False, 'is_restricted': False,
                     'supports_volume': True, 'volume_percent': 11},
                    {'id': 'synthetic-alternative', 'name': 'Speaker 2', 'active': False, 'cached': False, 'is_restricted': False},
                ]})
                owner._session['broadcast']['playback']['target_name'] = ''
                owner.changed.emit()
                QTest.qWait(80)
                output_shortcut = window.findChild(QObject, 'sessionCurrentOutput')
                assert output_shortcut.property('visible') and output_shortcut.property('enabled')
                expected_output_action = TRANSLATIONS[language]['session_output_choose']
                assert output_shortcut.property('text') == expected_output_action
                assert QAccessible.queryAccessibleInterface(output_shortcut).text(QAccessible.Text.Name) == expected_output_action
                click('sessionCurrentOutput')
                assert output_panel.property('opened') and len(intents) == 1
                intents.pop()()
                QTest.qWait(80)
                owner.transport.playback_outputs.assert_called_once()
                assert visual(window.contentItem(), 'sessionOutputChoice-synthetic-output').property('checked')
                QQmlExpression(engine.rootContext(), output_panel, 'close()').evaluate()
                owner.transport.playback_outputs.reset_mock()
                owner._session['broadcast']['playback']['target_name'] = speaker_name
                owner.changed.emit()
                QTest.qWait(80)
                click('sessionCurrentOutput')
                assert output_panel.property('opened') and not popup.property('opened') and len(intents) == 1
                output_refresh = window.findChild(QObject, 'sessionOutputRead')
                output_spinner = window.findChild(QObject, 'sessionOutputReadSpinner')
                assert output_spinner.property('visible') and not output_refresh.property('enabled')
                assert output_refresh.property('opacity') == 1
                refresh_accessible = QAccessible.queryAccessibleInterface(output_refresh)
                assert refresh_accessible.text(QAccessible.Text.Name) == TRANSLATIONS[language]['session_output_refresh']
                refresh_size = output_refresh.size()
                intents.pop()()
                QTest.qWait(80)
                assert output_refresh.property('iconName') == 'refresh' and not output_spinner.property('visible')
                assert output_refresh.size() == refresh_size and output_refresh.property('enabled')
                assert not backend.toastVisible
                output_choices = window.findChild(QObject, 'sessionOutputChoices')
                assert output_refresh.mapToScene(QPointF(0, output_refresh.height())).y() <= output_choices.mapToScene(QPointF()).y()
                output_shortcut = window.findChild(QObject, 'sessionCurrentOutput')
                assert_popup_anchor(output_shortcut, output_panel)
                assert output_shortcut.property('iconName') == 'speaker'
                output_content = output_shortcut.property('contentItem')
                output_icon = next(item for item in output_content.childItems() if item.property('iconName') == 'speaker')
                output_label = next(item for item in output_content.childItems() if item.property('text') == output_shortcut.property('text'))
                left_aligned = QQmlExpression(engine.rootContext(), output_label,
                    f'Number(horizontalAlignment) === {Qt.AlignmentFlag.AlignLeft.value}')
                assert left_aligned.evaluate()[0] is True, left_aligned.error().toString()
                assert abs(output_label.x() - output_icon.x() - output_icon.width() - 10) <= 1
                assert output_label.property('text') == speaker_name
                assert output_label.property('lineCount') > 1 and not output_label.property('truncated')
                assert output_label.mapToItem(output_shortcut, 0, output_label.height()).y() <= output_shortcut.height()
                selected_output = visual(window.contentItem(), 'sessionOutputChoice-synthetic-output')
                assert selected_output.property('checked') and not selected_output.property('enabled')
                alternative_output = visual(window.contentItem(), 'sessionOutputChoice-synthetic-alternative')
                tenth_output = visual(window.contentItem(), 'sessionOutputChoice-synthetic-tenth')
                assert selected_output.y() < alternative_output.y() < tenth_output.y()
                assert alternative_output.property('enabled') and not alternative_output.property('checked')
                for row, name, volume, active in (
                    (selected_output, speaker_name, '11%', True),
                    (alternative_output, 'Speaker 2', '–', False),
                ):
                    speaker_icon = visual(row, 'sessionOutputChoiceSpeaker')
                    row_name = visual(row, 'sessionOutputChoiceName')
                    row_volume = visual(row, 'sessionOutputChoiceVolume')
                    check = visual(row, 'sessionOutputChoiceCheck')
                    assert speaker_icon.property('visible') and speaker_icon.property('iconName') == 'speaker'
                    assert row_name.property('text') == name and not row_name.property('truncated')
                    assert row_volume.property('text') == volume and check.property('visible') == active
                    assert row_name.mapToItem(row, 0, 0).x() == row_volume.mapToItem(row, 0, 0).x()
                capture('direct-output-inventory')
                click('sessionOutputRead')
                assert len(intents) == 1 and output_spinner.property('visible')
                point = output_refresh.mapToScene(QPointF(output_refresh.width()/2, output_refresh.height()/2))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                 QPoint(round(point.x()), round(point.y())))
                assert len(intents) == 1
                intents.pop()()
                QTest.qWait(80)
                assert owner.transport.playback_outputs.call_count == 2 and not output_spinner.property('visible')
                assert backend.toastText == TRANSLATIONS[language]['session_output_updated']
                owner.transport.playback_outputs.side_effect = SessionError('unavailable')
                click('sessionOutputRead')
                assert len(intents) == 1
                intents.pop()()
                QTest.qWait(80)
                assert backend.toastText == TRANSLATIONS[language]['session_output_unavailable']
                owner.transport.playback_outputs.side_effect = None
                backend.hideToast()
                click('sessionOutputRead')
                assert len(intents) == 1
                click('sessionOutputClose')
                intents.pop()()
                QTest.qWait(120)
                assert not backend.toastVisible
                click('sessionCurrentOutput')
                intents.pop()()
                QTest.qWait(80)
                with patch.object(owner.transport, 'playback_output') as transfer:
                    alternative = visual(window.contentItem(), 'sessionOutputChoice-synthetic-alternative')
                    assert alternative is not None and alternative.property('enabled')
                    point = alternative.mapToScene(QPointF(alternative.width() / 2, alternative.height() / 2)).toPoint()
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                    QTest.qWait(65)
                    assert len(intents) == 1
                    output_status = window.findChild(QObject, 'sessionOutputStatus')
                    assert output_status.property('text') == TRANSLATIONS[language]['session_output_switching'].replace('%1', 'Speaker 2')
                    click('sessionOutputClose')
                    intents.pop()()
                    QTest.qWait(80)
                    transfer.assert_not_called()
                    assert not output_panel.property('pendingOutputName')
                observed['playback']['volume_percent'] = 0
                owner.transport.playback_status.side_effect = SessionError('unavailable')
                click('sessionCurrentVolume')
                assert len(intents) == 1
                volume_icon = window.findChild(QObject, 'sessionVolumeObservedIcon')
                volume_read = window.findChild(QObject, 'sessionVolumeRead')
                volume_read_spinner = window.findChild(QObject, 'sessionVolumeReadSpinner')
                assert volume_read_spinner.property('running') and not volume_read.property('enabled')
                volume_read_size = volume_read.size()
                assert not volume_icon.property('visible')
                intents.pop()()
                QTest.qWait(80)
                assert window.findChild(QObject, 'sessionVolumeObserved').property('text') == TRANSLATIONS[language]['session_volume_unavailable']
                assert volume_read.size() == volume_read_size and volume_read.property('enabled')
                assert volume_read.property('text') == TRANSLATIONS[language]['session_volume_read']
                owner.transport.playback_status.side_effect = None
                click('sessionVolumeRead')
                assert len(intents) == 1 and volume_read_spinner.property('running')
                intents.pop()()
                QTest.qWait(80)
                volume_shortcut = window.findChild(QObject, 'sessionCurrentVolume')
                assert volume_shortcut.property('text') == '0%' and volume_shortcut.property('iconName') == 'voice_muted'
                assert volume_icon.property('visible') and volume_icon.property('iconName') == 'voice_muted'
                zero_width = volume_shortcut.width()
                click('sessionVolumeClose')
                QTest.qWait(120)
                assert volume_shortcut.property('text') == '0%'
                assert not owner.state['playback_control']['volume']
                observed['playback']['volume_percent'] = 11
                click('sessionCurrentVolume')
                assert popup.property('opened') and not output_panel.property('opened') and len(intents) == 1
                intents.pop()()
                QTest.qWait(80)
                slider = window.findChild(QObject, 'sessionVolumeSlider')
                assert slider.property('visible') and slider.property('value') == 11
                volume_accessible = QAccessible.queryAccessibleInterface(slider)
                assert volume_accessible.text(QAccessible.Text.Name) == TRANSLATIONS[language]['volume']
                assert volume_accessible.text(QAccessible.Text.Description) == speaker_name
                assert volume_shortcut.property('text') == '11%' and volume_shortcut.property('iconName') == 'voice'
                assert volume_icon.property('visible') and volume_icon.property('iconName') == 'voice'
                assert abs(volume_shortcut.width() - zero_width) <= 1
                assert_popup_anchor(volume_shortcut, popup)
                volume_pointer = window.findChild(QObject, 'sessionVolumePointer')
                volume_readout = window.findChild(QObject, 'sessionVolumeObserved')
                original_readout = volume_readout.property('text')
                start = volume_pointer.mapToScene(QPointF(volume_pointer.width() * 0.25, volume_pointer.height() / 2))
                end = volume_pointer.mapToScene(QPointF(volume_pointer.width() * 0.75, volume_pointer.height() / 2))
                start_point = QPoint(round(start.x()), round(start.y()))
                end_point = QPoint(round(end.x()), round(end.y()))
                QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_point)
                QTest.mouseMove(window, end_point)
                assert volume_pointer.property('draft') > 11
                expected_draft = f"{TRANSLATIONS[language]['volume']}: {volume_pointer.property('draft')}%"
                assert volume_readout.property('text') == expected_draft
                assert volume_shortcut.property('text') == '11%' and not intents
                volume_pointer.ungrabMouse()
                QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end_point)
                QTest.qWait(60)
                assert volume_readout.property('text') == original_readout and not intents
                capture('direct-volume-observed')
                popup_position = (popup.property('x'), popup.property('y'))
                owner._session['broadcast']['playback']['position_ms'] += 750
                owner.changed.emit()
                QTest.qWait(60)
                assert popup.property('opened') and not intents
                assert (popup.property('x'), popup.property('y')) == popup_position
                owner._session['broadcast']['playback']['item_id'] = 'synthetic-new-track'
                owner.changed.emit()
                assert not popup.property('opened') and not intents
                QTest.qWait(160)
                owner.transport.playback_status.return_value = original_status
                owner._session = original_session
                backend.cfg.music_backend_capabilities = initial_caps
                owner.changed.emit()
                QTest.qWait(80)
                click('sessionExpandTrackArtwork')
                click('sessionArtworkFavorite')
                assert len(intents) == 1
                intents.pop()()
                owner.transport.playback_favorite.assert_called_once()
                assert owner.transport.playback_favorite.call_args.args[1] is True
                drain()
                image = window.grabWindow()
                for name in ['sessionPlaybackPrevious', 'sessionPlaybackToggle', 'sessionPlaybackNext']:
                    button = window.findChild(QObject, name)
                    icon = next(child for child in button.property('contentItem').childItems() if child.property('iconName') in ['previous', 'pause', 'next'])
                    origin = icon.mapToScene(QPointF(0, 0))
                    bright = sum(1 for yy in range(round(origin.y()), round(origin.y()+icon.height()))
                        for xx in range(round(origin.x()), round(origin.x()+icon.width()))
                        if min(image.pixelColor(xx, yy).red(), image.pixelColor(xx, yy).green(), image.pixelColor(xx, yy).blue()) > 180)
                    assert bright > 10, (width, language, name, 'missing visible glyph', bright)
                original_playback = deepcopy(owner.state['playback'])
                click('sessionPlaybackToggle')
                assert owner.state['playback_control']['status'] == 'sending' and len(intents) == 1
                assert not window.findChild(QObject, 'sessionPlaybackNext').property('enabled')
                capture('pending-pause')
                intents.pop()()
                assert owner.transport.playback_command.call_args.args[1] == 'pause'
                assert owner.state['playback'] == original_playback and owner.state['playback_control']['status'] == 'accepted'
                capture('accepted-no-optimism')
                fresh = deepcopy(session); fresh['broadcast']['playback']['state'] = 'paused'
                owner.transport.active.return_value = {'success': True, 'session': fresh}
                drain()
                owner.transport.playback_status.return_value['playback']['is_playing'] = False
                assert owner.state['playback']['state'] == 'paused'
                click('sessionPlaybackToggle'); intents.pop()()
                assert owner.transport.playback_command.call_args.args[1] == 'play'
                capture('play-sent'); drain()
                owner.transport.playback_command.side_effect = SessionError('uncertain')
                click('sessionPlaybackNext'); intents.pop()()
                assert owner.state['playback_control']['status'] == 'uncertain' and not reads
                count = owner.transport.playback_command.call_count
                assert not owner.playbackCommand(owner.state['session_id'], owner.state['playback']['item_id'], 'next')
                capture('uncertain-no-replay')
                click('trackArtworkPlaybackRefresh'); assert len(reads) == 1
                drain(); assert owner.transport.playback_command.call_count == count
                owner.transport.playback_command.side_effect = None
                click('sessionPlaybackPrevious'); assert len(intents) == 1
                click('closeTrackArtwork')
                intents.pop()()  # No GUI tick/exit-animation wait may authorize queued dispatch.
                assert owner.transport.playback_command.call_count == count
                capture('closed-before-dispatch')
                screen.setProperty('historical', True); QTest.qWait(40)
                assert heading.property('visible')
                assert heading.property('text') == TRANSLATIONS[language]['session_history']
                capture('heading-session-archive')
                screen.setProperty('historical', False); drain()
                click('sessionExpandTrackArtwork')
                backend.cfg.music_backend_capabilities = {'supports_favorites': False}; owner.changed.emit(); QTest.qWait(60)
                assert not window.findChild(QObject, 'sessionArtworkFavorite').property('enabled')
                capture('favorite-unsupported')
                # Exercise real streamed command transport under existing real Qt controls.
                owner.transport.playback_command = SessionTransport.playback_command.__get__(owner.transport, SessionTransport)
                for mode in ('declared-oversize', 'streamed-oversize', 'exact-boundary'):
                    size = JSON_CONTROL_BYTES + (0 if mode == 'exact-boundary' else 1)
                    base = b'{"success":true,"padding":""}'
                    raw = base[:-2] + b'x' * (size - len(base)) + base[-2:]
                    bounded_response = Mock(status_code=200, headers={})
                    if mode == 'declared-oversize':
                        bounded_response.headers = {'Content-Length': str(size)}
                    bounded_response.iter_content.side_effect = lambda *, chunk_size: (
                        raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size))
                    bounded_calls = []
                    class BoundedSession:
                        def __enter__(self): return self
                        def __exit__(self, *args): bounded_response.close.assert_called_once()
                        def request(self, *args, **kwargs):
                            assert self.trust_env is False and kwargs['stream'] is True
                            bounded_calls.append((args, kwargs))
                            return bounded_response
                    with patch('requests.Session', BoundedSession):
                        click('sessionPlaybackNext'); assert len(intents) == 1
                        intents.pop()()
                    QTest.qWait(60)
                    assert len(bounded_calls) == 1, (mode, len(bounded_calls))
                    assert len(reads) == (1 if mode == 'exact-boundary' else 0), (mode, len(reads))
                    bounded_response.json.assert_not_called()
                    assert owner.state['session_id'] and window.property('trackArtworkOpen')
                    if mode == 'exact-boundary':
                        assert owner.state['playback_control']['status'] != 'uncertain'
                    else:
                        assert owner.state['playback_control']['status'] == 'uncertain'
                        assert not owner.playbackCommand(owner.state['session_id'], owner.state['playback']['item_id'], 'next')
                    if mode == 'declared-oversize': bounded_response.iter_content.assert_not_called()
                    capture(mode)
                    if mode == 'exact-boundary': drain()  # Existing successful-command owner read, no write replay.
                    if mode != 'exact-boundary':
                        click('trackArtworkPlaybackRefresh'); assert len(reads) == 1
                        drain()
                    assert len(bounded_calls) == 1
                screen.setProperty('draft', 'Synthetic private draft before denial')
                response = Mock(status_code=401 if width == 720 else 403)
                response.json.side_effect = ValueError('Synthetic malformed denial body')
                denial_calls = []
                class DenialSession:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def request(self, *args, **kwargs):
                        assert self.trust_env is False
                        denial_calls.append((args, kwargs))
                        return response
                owner.transport.playback_command = SessionTransport.playback_command.__get__(owner.transport, SessionTransport)
                token = backend.cfg.device_token
                with patch('requests.Session', DenialSession):
                    click('sessionPlaybackNext'); assert len(intents) == 1
                    intents.pop()()
                QTest.qWait(60)
                assert not owner.state['session_id'] and owner.state['error'] == 'denied'
                assert not screen.property('draft') and not window.property('trackArtworkOpen')
                assert backend.cfg.paired and backend.cfg.device_token == token
                assert len(denial_calls) == 1 and not reads
                response.json.assert_not_called()
                assert heading.property('visible') and not progress.property('visible')
                capture('malformed-http-denial-withdrawn')
                owner.setForeground(False); QTest.qWait(60)
                assert not popup.property('visible') and not owner.state['playback_control']['available']
                assert not meter.property('animating')
                capture('foreground-withdrawn')
                if language == 'en':
                    window.setProperty('activeScreen', 'more')
                    backend.cfg.screen_timeout_seconds = 1
                    backend.screenTimeoutChanged.emit()
                    QTest.qWait(1150)
                    assert window.property('screenBlanked')
                    with patch('djconnect_pi.app.subprocess.run') as display_command:
                        backend._apply_playback(Playback(title='Next track', artist='Next artist',
                                                         uri='fixture:track:2', image_url=art_source, is_playing=True))
                    assert display_command.call_args_list[-1].args[0] == ['xset', 'dpms', 'force', 'on']
                    assert window.property('activeScreen') == 'session' and not window.property('screenBlanked')
                    assert window.property('trackArtworkOpen')
                    assert visual(window.contentItem(), 'expandedTrackTitle').property('text') == 'Next track'
                    QTest.qWait(4300)
                    assert window.property('trackArtworkOpen')
                    for _ in range(17):
                        if not window.property('trackArtworkOpen'):
                            break
                        QTest.qWait(100)
                    assert not window.property('trackArtworkOpen') and window.property('activeScreen') == 'session'
                    backend._apply_playback(Playback(title='Next track', artist='Next artist',
                                                     uri='fixture:track:2', image_url='', is_playing=True))
                    assert not window.property('trackArtworkOpen')
                    backend.cfg.screen_timeout_seconds = 0
                    backend.screenTimeoutChanged.emit()
                    backend._apply_playback(Playback())
                if language == 'en':
                    for destination in ('queue', 'playlists', 'more'):
                        window.setProperty('activeScreen', destination)
                        backend._apply_playback(Playback(title=destination, uri='fixture:' + destination,
                                                         image_url=art_source))
                        assert window.property('activeScreen') == destination
                        assert not window.property('trackArtworkOpen')
                    window.setProperty('activeScreen', 'session')
                    backend._apply_playback(Playback(title='New track', uri='fixture:session', image_url=art_source))
                    assert window.property('trackArtworkOpen')
                    window.setProperty('activeScreen', 'more')
                    assert not window.property('trackArtworkOpen')
                if width == 720 and language == 'en':
                    window.setProperty('activeScreen', 'now')
                    QTest.qWait(80)
                    click('nowTrackArtwork')
                    assert window.property('activeScreen') == 'control' and not window.property('trackArtworkOpen')
                    click('controlTrackMetadata')
                    assert not window.property('trackArtworkOpen')
                backend.shutdown(); delete(window); delete(engine); delete(backend)
                app.processEvents()
        network.assert_not_called()
    errors = [x for x in warnings if any(word in x for word in ['Error:', 'ReferenceError', 'TypeError', 'Binding loop', 'Cannot assign', 'QGridLayoutEngine'])]
    (output/'readback.json').write_text(json.dumps({'captures': captures, 'warnings': warnings, 'errors': errors,
        'proof': 'Real Qt mouse buttons/controller; original native frame bytes replayed at original clock. Backend metadata, commands and owner playback-state updates synthetic; no network/provider/ARM/install/physical effects.'}, indent=2))
    assert not errors, errors[:10]
    print(json.dumps({'actual_views': len(captures), 'errors': errors}))


if __name__ == '__main__':
    main()
