"""Drive the real game controls through the application's activity catcher."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend
from djconnect_pi.config import Config, save_config


def find(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        match = find(child, name)
        if match is not None:
            return match
    return None


def center(item):
    assert item and item.isVisible() and item.isEnabled()
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def click(window, point):
    QTest.mouseMove(window, point)
    QTest.qWait(40)
    QTest.mousePress(window, Qt.LeftButton, pos=point)
    QTest.qWait(40)
    QTest.mouseRelease(window, Qt.LeftButton, pos=point)


QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
width = int(sys.argv[1])
config_path = Path(sys.argv[2])
save_config(config_path, Config(
    language="nl", release_profile="pi5-arm64" if width == 1200 else "pi-zero-2w-arm64",
    screen_timeout_seconds=30, log_file=str(config_path.with_suffix(".log")),
))
backend = DJConnectBackend(config_path)
backend._audio_devices = None
for timer in backend.findChildren(QTimer):
    timer.stop()
for pool in (backend._executor, backend._session_executor, backend._question_executor):
    pool.submit = Mock()
backend.enterDemoMode()
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("djconnect", backend)
engine.rootContext().setContextProperty("startWindowed", True)
engine.load(str(Path(__file__).resolve().parents[2] / "src/djconnect_pi/qml/Main.qml"))
window, = engine.rootObjects()
window.resize(width, 1920 if width == 1200 else 720)
window.setProperty("splashVisible", False)
window.setProperty("activeScreen", "games")
window.requestActivate()
QTest.qWait(500)

try:
    content = window.contentItem()
    game = find(content, "gamesPanel")
    start = find(game, "gameStart")
    pad = find(game, "gameDirectionPad")
    blanking = []
    start_visibility = []
    window.screenBlankedChanged.connect(lambda: blanking.append(window.property("screenBlanked")))
    start.visibleChanged.connect(lambda: start_visibility.append(start.isVisible()))
    for name, direction, controlled in (
        ("pong", "up", "paddleY"),
        ("asteroids", "left", "shipX"),
        ("fly", "up", "planeY"),
        ("pacman", "up", "pacmanDY"),
    ):
        click(window, center(find(game, f"gameSelect-{name}")))
        assert game.property("gameId") == name and start.isVisible()
        click(window, center(start))
        assert game.property("playing"), (width, name, window.isActive(), game.property("gameplayActive"))
        point = center(find(pad, f"gamePad-{direction}"))
        QTest.mouseMove(window, point)
        QTest.qWait(40)
        start_visibility.clear()
        for _ in range(2):
            previous = game.property(controlled)
            QTest.mousePress(window, Qt.LeftButton, pos=point)
            assert game.property("playing") and not start.isVisible(), (width, name, blanking)
            QTest.qWait(40)
            QTest.mouseRelease(window, Qt.LeftButton, pos=point)
            assert game.property("playing") and not start.isVisible(), (width, name, blanking)
            if name != "pacman":
                assert game.property(controlled) < previous
            else:
                assert game.property(controlled) == -1
        assert not start_visibility, (width, name, start_visibility)
    assert not blanking, blanking

    backend.setScreenTimeoutSeconds(1)
    for _ in range(20):
        if window.property("screenBlanked"):
            break
        QTest.qWait(100)
    assert window.property("screenBlanked")
    assert not game.property("playing")
    with patch("djconnect_pi.app.subprocess.run"):
        click(window, QPointF(width / 2, 80).toPoint())
    assert not window.property("screenBlanked")
    assert window.property("activeScreen") == "session"
finally:
    delete(window)
    delete(engine)
    backend.shutdown()
    delete(backend)
