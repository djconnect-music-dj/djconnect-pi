"""Encode one bounded Track Insight clip from successive QML card captures."""
from PySide6.QtCore import QIODevice, QObject, QSaveFile, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QImage


FRAME_RATE = 24
FRAME_COUNT = 6 * FRAME_RATE


def clip_format():
    try:
        from PySide6.QtMultimedia import QMediaFormat
    except ImportError:
        return None
    fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)
    return fmt if fmt.isSupported(QMediaFormat.ConversionMode.Encode) else None


class TrackInsightClip(QObject):
    frameRequested = Signal(float)
    finished = Signal(str)

    def __init__(self, path, height, fmt, current, parent=None):
        from PySide6.QtMultimedia import QMediaCaptureSession, QMediaRecorder, QVideoFrame, QVideoFrameInput

        super().__init__(parent)
        self._current = current
        self._video_frame = QVideoFrame
        self._stopped_state = QMediaRecorder.RecorderState.StoppedState
        self._size = QSize(1080, height)
        self._target = QSaveFile(path, self)
        self._session = QMediaCaptureSession(self)
        self._recorder = QMediaRecorder(self)
        self._input = QVideoFrameInput(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(lambda: self.stop("failed"))
        self._index = 0
        self._pending_frame = None
        self._waiting_for_image = False
        self._end_sent = False
        self._outcome = ""
        self._finished = False

        self._session.setRecorder(self._recorder)
        self._session.setVideoFrameInput(self._input)
        self._recorder.setVideoFrameRate(FRAME_RATE)
        self._recorder.setVideoResolution(self._size)
        self._recorder.setMediaFormat(fmt)
        self._recorder.setAutoStop(True)
        self._recorder.errorOccurred.connect(lambda *_: self.stop("failed"))
        self._recorder.recorderStateChanged.connect(self._state_changed)
        self._input.readyToSendVideoFrame.connect(self._request_or_send, Qt.ConnectionType.QueuedConnection)

    def start(self):
        if not self._current():
            self._finish("cancelled")
            return
        if not self._target.open(QIODevice.OpenModeFlag.WriteOnly):
            self._finish("failed")
            return
        self._recorder.setOutputDevice(self._target)
        if self._recorder.outputDevice() is not self._target:
            self._finish("failed")
            return
        self._timer.start()
        self._recorder.record()

    def supply(self, image: QImage):
        if not self._waiting_for_image or self._outcome:
            return
        self._waiting_for_image = False
        if image.size() != self._size:
            self.stop("failed")
            return
        frame = self._video_frame(image)
        frame.setStartTime(self._index * 1_000_000 // FRAME_RATE)
        frame.setEndTime((self._index + 1) * 1_000_000 // FRAME_RATE)
        self._pending_frame = frame
        self._request_or_send()

    def _request_or_send(self):
        if self._finished or self._outcome or self._waiting_for_image or self._end_sent:
            return
        if not self._current():
            self.stop()
            return
        if self._pending_frame is not None:
            if not self._input.sendVideoFrame(self._pending_frame):
                return
            self._pending_frame = None
            self._index += 1
            self._timer.start()
        if self._index == FRAME_COUNT:
            self._end_sent = True
            if not self._input.sendVideoFrame(self._video_frame()):
                self._end_sent = False
            return
        self._waiting_for_image = True
        self.frameRequested.emit(self._index / FRAME_COUNT)

    def stop(self, outcome="cancelled"):
        if self._finished or self._outcome:
            return
        self._outcome = outcome
        if self._recorder.recorderState() != self._stopped_state:
            self._recorder.stop()
        else:
            self._finish(outcome)

    def _state_changed(self, state):
        if state != self._stopped_state:
            return
        outcome = self._outcome or ("saved" if self._end_sent else "failed")
        self._finish(outcome)

    def _finish(self, outcome):
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        if outcome == "saved":
            outcome = "cancelled" if not self._current() else "saved" if self._target.commit() else "failed"
        if outcome != "saved" and self._target.isOpen():
            self._target.cancelWriting()
            self._target.commit()
        self.finished.emit(outcome)
