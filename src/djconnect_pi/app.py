from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode, urlparse, urlsplit
from uuid import uuid4

import requests
from PySide6.QtCore import (
    Property,
    QBuffer,
    QByteArray,
    QCoreApplication,
    QIODevice,
    QObject,
    QSaveFile,
    QStandardPaths,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QPixmapCache
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from . import __version__
from .artwork_palette import displayed_artwork_tint
from .ask_dj_help import compatible_catalogue, demo_help, project_help, select_suggestions
from .brand_theme import apply_palette
from .config import (
    CLIENT_TYPE,
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_PATH,
    SESSION_REFRESH_CHOICES,
    Config,
    effective_glass_enabled,
    effective_session_refresh_seconds,
    load_config,
    normalize_dj_announcement_output,
    normalize_log_level,
    save_config,
)
from .ha import (
    DJCONNECT_API_PREFIX,
    PROFILE_ERROR_CODES,
    AuthenticationError,
    BackendUnavailable,
    DJConnectError,
    HAClient,
    Playback,
    ProfileContextError,
    ProtocolVersionMismatch,
    StaleBackendAction,
    StatusObservation,
    UnsupportedBackendCapability,
    playlist_command_target,
)
from .i18n import LANGUAGES, normalize_language, translate
from .logging_config import clear_log_files, setup_logging
from .navigation_gesture import EdgeBackGesture
from .queue_projection import compact_adjacent_tracks, queue_artist_and_album
from .session_controller import SessionController
from .session_history_view import (
    ask_dj_history_matches, contribution_question, conversation_entries, local_highlighted_text,
    presented_history, retained_contribution_preview,
)
from .session_playback_control import REPEAT_NEXT, favorite_observation, output_id, sorted_output_choices
from .session_playback_control import reference as session_playback_reference
from .session_projection import active_session, history_page, safe_link, timestamp, track_insight_verified_credit, volume_observation
from .session_projection import text as bounded_text
from .expiry_clock import elapsed_time
from .session_transport import SessionError, session_profile_id
from .track_insight_clip import TrackInsightClip, clip_format
from .shared_settings import SettingsError
from .system_info import log_raspberry_pi_system_info

_LOGGER = logging.getLogger(__name__)
LOCAL_TERMINAL_UNIT = Path("/etc/systemd/system/djconnect-local-terminal.service")
LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+(?P<time>\d{2}:\d{2}:\d{2})(?:[,.]\d+)?\s+(?P<level>[A-Z]+)\s+(?P<rest>.*)$"
)
LOG_LEVEL_SHORT = {"DEBUG": "DBG", "INFO": "INF", "WARNING": "WRN", "ERROR": "ERR", "CRITICAL": "ERR"}
LOG_DISPLAY_MAX_BYTES = 80_000
GAME_SOUND_SAMPLE_RATE = 16_000
DISPLAY_WAKE_DEBOUNCE_SECONDS = 0.8
MEDIA_ARTWORK_CACHE_LIMIT = 6
MUSIC_DNA_IMPORT_MAX_BYTES = 8 * 1024 * 1024
QT_PIXMAP_CACHE_LIMIT_KB = 4096
UI_WORKER_COUNT = 1
TOAST_ICON_FALLBACK = "music"
TOAST_CONTEXT_ICONS = {
    "playback": "music",
    "media_start": "play",
    "now": "music",
    "queue": "queue",
    "playlists": "playlists",
    "settings": "settings",
    "connectivity": "info",
    "refresh": "refresh",
    "home_assistant": "info",
    "updates": "logs",
    "diagnostics": "logs",
    "logs": "logs",
    "askdj": "chat",
    "trackinsight": "trackInsight",
    "discover": "discover",
    "musicdna": "heart",
    "games": "gamepad",
}


class SaveCurrentTrackError(DJConnectError):
    pass


class _ProfileReadResult(dict):
    """GUI delivery ticket; never a JSON field, grant or persisted cache."""
    def __init__(self, data, context):
        super().__init__(data)
        self.owner_context = context


class _MusicDnaProfileResult(_ProfileReadResult):
    """Profile read outcome and local refresh behavior, outside the wire payload."""

    def __init__(self, data, context, *, manual: bool, retryable: bool):
        super().__init__(data, context)
        self.manual = manual
        self.retryable = retryable


class _DiscoveryReadResult(_ProfileReadResult):
    """A Discover feed bound to the original Music DNA consent."""

    def __init__(self, data, context, consent_epoch, *, manual=False, used_existing_feed=False):
        super().__init__(data, context)
        self.consent_epoch = consent_epoch
        self.manual = manual
        self.used_existing_feed = used_existing_feed


class _TrackInsightResult(dict):
    """Track Insight delivery scope captured before dispatch, outside the wire payload."""

    def __init__(self, data, scope, proof=None, *, manual: bool = False):
        super().__init__(data)
        self.scope = scope
        self.proof = proof
        self.manual = manual


class DJConnectBackend(QObject):
    statusTextChanged = Signal()
    backendAvailableChanged = Signal()
    aboutMusicAvailabilityChanged = Signal()
    aboutConnectionOriginChanged = Signal()
    titleChanged = Signal()
    artistChanged = Signal()
    albumChanged = Signal()
    imageUrlChanged = Signal()
    playbackTrackChanged = Signal()
    playingChanged = Signal()
    favoriteChanged = Signal()
    favoriteBusyChanged = Signal()
    _favoriteRemovalFinished = Signal(object, str)
    volumeChanged = Signal()
    controlVolumeChanged = Signal()
    _controlVolumeFinished = Signal()
    shuffleChanged = Signal()
    repeatChanged = Signal()
    playbackModePendingChanged = Signal()
    outputDeviceChanged = Signal()
    progressChanged = Signal()
    playbackSeekCancelled = Signal()
    pairedChanged = Signal()
    busyChanged = Signal()
    settingsChanged = Signal()
    localApiUrlChanged = Signal()
    pairingCodeChanged = Signal()
    pairingSuccessChanged = Signal()
    djResponseChanged = Signal()
    toastChanged = Signal()
    versionMismatchChanged = Signal()
    logsChanged = Signal()
    mediaListsChanged = Signal()
    mediaActivityChanged = Signal()
    _mediaListFinished = Signal(str, object, bool)
    _mediaPlaybackFinished = Signal()
    _mediaPlaybackObserved = Signal(object, object, bool)
    askDjChanged = Signal()
    askDjUnsentDraft = Signal(str)
    _askDjUnsentDraftReady = Signal(object, str)
    _askDjDeliveryReady = Signal(object, str)
    musicDnaChanged = Signal()
    profileExportProgress = Signal(int, bool, str)
    musicDiscoveryChanged = Signal()
    musicDiscoveryItemsChanged = Signal()
    musicDiscoveryPlayed = Signal(str, str)
    trackInsightChanged = Signal()
    trackInsightClipChanged = Signal()
    trackInsightClipFrameRequested = Signal(float)
    trackInsightExportFinished = Signal(str, bool)
    trackInsightPlaybackItemChanged = Signal()
    trackInsightObservationChanged = Signal()
    demoModeChanged = Signal()
    glassChanged = Signal()
    screenTimeoutChanged = Signal()
    returnToNowChanged = Signal()
    screenBrightnessChanged = Signal()
    updateChannelChanged = Signal()
    updateCheckChanged = Signal()
    logFileChanged = Signal()
    logLevelChanged = Signal()
    languageChanged = Signal()
    translationsChanged = Signal()
    moodChanged = Signal()
    sharedSettingsChanged = Signal()
    _sharedSettingsReady = Signal(object)
    profileChanged = Signal()
    djAnnouncementChanged = Signal()
    localAudioDevicesChanged = Signal()
    gameHighScoresChanged = Signal()
    wakeScreenRequested = Signal()
    temporaryWakeRequested = Signal(int, bool)
    screenshotRequested = Signal()
    debugScreenRequested = Signal(str)

    _playbackReady = Signal(object)
    _playbackModeFinished = Signal(object, object)
    _statusReady = Signal(str)
    _pairingReady = Signal(bool, str)
    _busyReady = Signal(bool)
    _versionMismatchReady = Signal(str, str)
    _mediaListReady = Signal(str, object, object)
    _mediaArtworkReady = Signal(str, object, object)
    _outputDeviceRejected = Signal(str, str)
    _outputChoiceFinished = Signal(object, bool)
    _controlOutputsFinished = Signal(object, object, bool)
    _backendAvailableReady = Signal(bool)
    _aboutMusicAvailabilityReady = Signal(object, object, float)
    _aboutConnectionReady = Signal(object, str, float, bool)
    _toastReady = Signal(str, int, str)
    _updateCheckFinished = Signal(str)
    _askDjReady = Signal(object)
    _askDjContextReady = Signal(object, object, object)
    _askDjContextWithdrawn = Signal(object, object)
    _askDjRefreshReady = Signal(object)
    _askDjClearReady = Signal(object)
    askDjHelpChanged = Signal()
    askDjHelpSelected = Signal(str, str, int)
    askDjSuggestionSelected = Signal(str, int)
    _askDjHelpReady = Signal(object, object, object)
    _musicDnaReady = Signal(object)
    _musicDnaImportPrepared = Signal(object, object, object)
    _musicDnaImportFinished = Signal(object, object, str)
    _musicDiscoveryReady = Signal(object)
    _musicDiscoveryPlayConfirmed = Signal(object, str, str, object)
    _musicDiscoveryFeedbackAccepted = Signal(object, str, str, object)
    _trackInsightReady = Signal(object)

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self._shutting_down = False
        self._refresh_pending = False
        self._audio_devices = None
        self._edge_back_gesture = EdgeBackGesture(self)
        self.cfg: Config = load_config(config_path)
        self.client = HAClient(self.cfg)
        self._shared_settings_state = {}
        self._shared_settings_epoch = 0
        self._shared_settings_inflight = False
        self._shared_settings = self.client.shared_settings
        self._shared_owner_context = self._shared_settings.context()
        self.playback = Playback()
        self._dj_response_text = ""
        self._dj_response_visible = False
        self._toast_text = ""
        self._toast_icon = TOAST_ICON_FALLBACK
        self._toast_visible = False
        self._translation_version = 0
        self._version_mismatch_visible = False
        self._version_mismatch_text = ""
        self._pairing_success_visible = False
        self._update_service_triggered = False
        self._favorite_busy = False
        self._toast_timer = QTimer(self)
        self._toast_timer.setInterval(2000)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.hideToast)
        self._logs_text = ""
        self._logs_read_error = ""
        self._logs_visible = False
        self._demo_mode = False
        self._demo_favorite_uris: set[str] = set()
        self._demo_progress = QTimer(self)
        self._demo_progress.setInterval(1000)
        self._demo_progress.timeout.connect(self._tick_demo_playback)
        self._queue_items: list[dict[str, object]] = []
        self._playlist_items: list[dict[str, object]] = []
        self._ask_dj_messages: list[dict[str, object]] = []
        self._ask_dj_withdrawn_ids: set[str] = set()
        self._ask_dj_withdrawn_entries: set[tuple[str, str]] = set()
        self._ask_dj_history_open = None
        self._ask_dj_context_request = None
        self._ask_dj_context_page = None
        self._ask_dj_destination_loading = False
        self._ask_dj_history_revision = 0
        self._ask_dj_delivery_key = ""
        self._ask_dj_clear_revision = 0
        self._ask_dj_busy = False
        self._ask_dj_help_request = None
        self._ask_dj_help_busy = False
        self._ask_dj_help_sections = []
        self._ask_dj_suggestions = []
        self._music_dna_enabled = False
        self._music_dna_loaded = False
        self._music_dna_context_revision = 0
        self._music_dna_updated_at = ""
        self._music_dna_summary = ""
        self._music_dna_error = ""
        self._music_dna_needs_recovery = False
        self._music_dna_import = None
        self._music_dna_sections: list[dict[str, object]] = []
        self._music_dna_busy = False
        self._music_discovery_items: list[dict[str, object]] = []
        self._music_discovery_empty_text = ""
        self._music_discovery_error = ""
        self._music_discovery_dna_disabled = False
        self._music_discovery_loaded = False
        self._music_discovery_feed_deadline = None
        self._music_discovery_busy = False
        self._music_discovery_playing: tuple[str, str] | None = None
        self._music_discovery_feedback_supported = False
        self._music_discovery_consent_rejected = False
        self._music_discovery_consent_epoch = 0
        self._track_insight_title = ""
        self._track_insight_loading = False
        self._track_insight_audio_notice = False
        self._track_insight_verified_credit: dict[str, str] = {}
        self._track_insight_artist = ""
        self._track_insight_album = ""
        self._track_insight_image_url = ""
        self._track_insight_text = ""
        self._track_insight_error = ""
        self._track_insight_items: list[dict[str, object]] = []
        self._track_insight_sections: list[dict[str, object]] = []
        self._track_insight_visualizer: dict[str, object] = {}
        self._application_foreground = True
        self._track_insight_revision = 0
        self._track_insight_result_scope = None
        self._track_insight_last_proof = None
        self._track_insight_deadline = None
        self._track_insight_clip = None
        self._track_insight_retryable = False
        self._ask_dj_unavailable_until = 0.0
        self._ask_dj_poll_error_count = 0
        self._ask_dj_poll_in_flight = False
        self._suppress_next_playback_navigation = False
        self._media_loads_in_flight: set[str] = set()
        self._media_list_load_failures: set[str] = set()
        self._media_playback_busy = False
        self._queue_read_lock = Lock()
        self._media_artwork_cache_in_flight: set[str] = set()
        self._pending_output_device = ""
        self._pending_output_until = 0.0
        self._pending_output_choice = None
        self._unconfirmed_output_choice = ""
        self._control_outputs_request = None
        self._control_outputs_status = ""
        self._last_display_wake_at = 0.0
        self._game_sound_objects: list[tuple[object, QBuffer]] = []
        self._game_settings = QSettings(str(config_path.with_suffix(".games.ini")), QSettings.Format.IniFormat, self)
        self._game_high_scores = {}
        for game in ("pong", "asteroids", "fly", "pacman"):
            try:
                score = self._game_settings.value(f"highScores/{game}", 0, type=int)
            except (TypeError, ValueError):
                score = 0
            self._game_high_scores[game] = score if type(score) is int and 0 <= score <= 2_147_483_647 else 0
        self._status_text = self.tr_key("paired" if self.cfg.paired else "not_paired")
        self._backend_available = True
        self._update_check_running = False
        self._update_check_status = ""
        self._about_music_availability = None
        self._about_connection_origin = None
        self._about_connection_request_time = 0.0
        self._about_music_request_time = 0.0
        self._busy = False
        self._executor = ThreadPoolExecutor(max_workers=UI_WORKER_COUNT, thread_name_prefix="djconnect")
        # Bounded independent lanes: slow text generation cannot occupy Session control.
        self._session_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="djconnect-session")
        self._question_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="djconnect-question")
        self._session_controller = SessionController(self, view_active=False)
        self._shared_settings.owner_context = self._session_controller.scope_context
        self._shared_owner_context = self._shared_settings.context()
        self._session_controller.ownerScopeChanged.connect(self._session_owner_changed)
        self._session_controller.askDjEntryOpenFailed.connect(self._show_ask_dj_history_open_failed)
        self._control_volume_serial = 0
        self._playback_seek_epoch = 0
        self._playback_seek_gesture = None
        self._control_volume_intent = None
        self._control_volume_pending = False
        self._pending_playback_mode: tuple[tuple, str, bool | str] | None = None
        self._controlVolumeFinished.connect(self._finish_control_volume)
        self.settingsChanged.connect(self.controlVolumeChanged.emit)
        self.profileChanged.connect(self.cancelControlVolume)
        self.demoModeChanged.connect(self.cancelControlVolume)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self.refresh)
        self._poll_timer.start()
        self._event_timer = QTimer(self)
        self._event_timer.setInterval(1000)
        self._event_timer.timeout.connect(self._poll_local_events)
        self._event_timer.start()
        self._playbackReady.connect(self._apply_playback)
        self._playbackModeFinished.connect(self._finish_playback_mode)
        self._favoriteRemovalFinished.connect(self._finish_favorite_removal)
        for signal in (self.outputDeviceChanged, self.profileChanged, self.pairedChanged,
                       self.demoModeChanged, self.settingsChanged):
            signal.connect(self._invalidate_playback_mode)
            signal.connect(self._invalidate_playback_seek)
        self.playbackTrackChanged.connect(self._invalidate_playback_seek)
        self._statusReady.connect(self._set_status_text)
        self._pairingReady.connect(self._apply_pairing)
        self._busyReady.connect(self._set_busy)
        self._versionMismatchReady.connect(self._apply_version_mismatch)
        self._mediaListReady.connect(self._apply_media_list)
        self._mediaArtworkReady.connect(self._apply_cached_media_list)
        self._mediaListFinished.connect(self._finish_media_list_load)
        self._mediaPlaybackFinished.connect(self._finish_media_playback)
        self._mediaPlaybackObserved.connect(self._apply_media_playback_observation)
        self._outputDeviceRejected.connect(self._apply_output_device_rejection)
        self._outputChoiceFinished.connect(self._finish_output_choice)
        self._controlOutputsFinished.connect(self._finish_control_outputs)
        self._backendAvailableReady.connect(self._set_backend_available)
        self._aboutMusicAvailabilityReady.connect(self._apply_about_music_availability)
        self._aboutConnectionReady.connect(self._apply_about_connection_origin)
        for signal in (self.profileChanged, self.pairedChanged, self.demoModeChanged,
                       self.versionMismatchChanged, self.backendAvailableChanged, self.settingsChanged):
            signal.connect(self._reconcile_about_status)
        self._toastReady.connect(self._show_toast)
        self._updateCheckFinished.connect(self._finish_update_check)
        self._askDjReady.connect(self._apply_ask_dj_data)
        self._askDjContextReady.connect(self._apply_ask_dj_context_rows)
        self._askDjContextWithdrawn.connect(self._apply_ask_dj_context_withdrawal)
        self._askDjUnsentDraftReady.connect(self._restore_unsent_ask_dj_draft)
        self._askDjDeliveryReady.connect(self._apply_ask_dj_delivery)
        self._askDjRefreshReady.connect(self._apply_ask_dj_refresh_result)
        self._askDjClearReady.connect(self._apply_ask_dj_clear_result)
        self._askDjHelpReady.connect(self._apply_ask_dj_help)
        self._ask_dj_help_timer = QTimer(self)
        self._ask_dj_help_timer.setSingleShot(True)
        self._ask_dj_help_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ask_dj_help_timer.timeout.connect(self.closeAskDjHelp)
        for signal in (self.profileChanged, self.pairedChanged, self.languageChanged,
                       self.backendAvailableChanged, self.demoModeChanged, self.versionMismatchChanged,
                       self.settingsChanged):
            signal.connect(self._invalidate_ask_dj_help_if_changed)
        self._musicDnaReady.connect(self._apply_music_dna_data)
        self._musicDnaImportPrepared.connect(self._apply_music_dna_import)
        self._musicDnaImportFinished.connect(self._finish_music_dna_import)
        self._musicDiscoveryReady.connect(self._apply_music_discovery_data)
        self._musicDiscoveryPlayConfirmed.connect(self._apply_music_discovery_play)
        self._musicDiscoveryFeedbackAccepted.connect(self._remove_music_discovery_item)
        self._trackInsightReady.connect(self._apply_track_insight_data)
        self._track_insight_expiry_timer = QTimer(self)
        self._track_insight_expiry_timer.setSingleShot(True)
        self._track_insight_expiry_timer.timeout.connect(self._expire_track_insight)
        self._track_insight_style = self._current_track_insight_style()
        self._session_controller.changed.connect(self._observe_track_insight_style)
        self._session_controller.changed.connect(self._invalidate_ask_dj_help_if_changed)
        self.sharedSettingsChanged.connect(self._observe_track_insight_style)
        self._sharedSettingsReady.connect(self._apply_shared_settings)
        self._shared_settings_timer = QTimer(self)
        self._shared_settings_timer.setInterval(15000)
        self._shared_settings_timer.timeout.connect(self.pollSharedSettings)
        self._shared_settings_timer.start()
        # Owned timers cannot outlive this backend or continue after shutdown.
        self._startup_settings_timer = QTimer(self)
        self._startup_settings_timer.setSingleShot(True)
        self._startup_settings_timer.setInterval(350)
        self._startup_settings_timer.timeout.connect(self.pollSharedSettings)
        self._startup_settings_timer.start()
        self._startup_refresh_timer = QTimer(self)
        self._startup_refresh_timer.setSingleShot(True)
        self._startup_refresh_timer.setInterval(250)
        self._startup_refresh_timer.timeout.connect(self.refresh)
        self._startup_refresh_timer.start()
        self._settings_reconcile_timer = QTimer(self)
        self._settings_reconcile_timer.setSingleShot(True)
        self._settings_reconcile_timer.setInterval(0)
        self._settings_reconcile_timer.timeout.connect(self.pollSharedSettings)
        _LOGGER.info("DJConnect Pi client backend started for %s", self.cfg.device_id)

    @Property("QVariantMap", notify=sharedSettingsChanged)
    def sharedSettings(self) -> dict:
        return deepcopy(self._shared_settings_state)

    @Property(bool, notify=sharedSettingsChanged)
    def sharedSettingsManaged(self) -> bool:
        return self.cfg.paired or self._shared_settings.managed

    @Property(QObject, constant=True)
    def edgeBackGesture(self):
        return self._edge_back_gesture

    @Property(QObject, constant=True)
    def djSession(self):
        return self._session_controller

    @Property(bool, notify=sharedSettingsChanged)
    def sharedSettingsBusy(self) -> bool:
        return self._shared_settings_inflight

    def _invalidate_shared_settings_if_context_changed(self) -> None:
        context = self._shared_settings.context()
        if context != self._shared_owner_context:
            self._shared_owner_context = context
            self._shared_settings_epoch += 1
            self._shared_settings_state = {}
            self.sharedSettingsChanged.emit()

    @Slot()
    def pollSharedSettings(self) -> None:
        if self._shutting_down:
            return
        self._invalidate_shared_settings_if_context_changed()
        if self._demo_mode or not self.paired or self._shared_settings_inflight:
            return
        self._start_shared_settings()

    @Slot("QVariantMap")
    def saveSharedSettings(self, patch: dict) -> None:
        if self._shutting_down:
            return
        if self._demo_mode or not self.paired or self._shared_settings_inflight:
            return
        self._start_shared_settings(deepcopy(patch))

    @Slot()
    def useConfirmedSharedSettings(self) -> None:
        if self._shutting_down:
            return
        self._invalidate_shared_settings_if_context_changed()
        if (self._demo_mode or not self.paired or self._shared_settings_inflight
                or not self._shared_settings_state.get("operation_unknown")):
            return
        self._start_shared_settings(accept_current=True)

    def _start_shared_settings(self, patch: dict | None = None, *, accept_current: bool = False) -> None:
        self._shared_settings_inflight = True
        self.sharedSettingsChanged.emit()
        epoch = self._shared_settings_epoch
        context = self._shared_settings.context()

        def worker() -> None:
            result = {"epoch": epoch, "context": context}
            try:
                owner = self._shared_settings
                if accept_current:
                    result["projection"] = owner.read(accept_current=True)
                elif patch is None:
                    result["projection"] = owner.read()
                else:
                    token = patch.pop("_edit_token", self._shared_settings_state.get("edit_token", ""))
                    result["projection"] = owner.update(patch, token)
            except SettingsError as exc:
                result["error"] = exc.category
            except Exception:  # noqa: BLE001 - never expose unexpected private transport content
                result["error"] = "invalid"
            self._sharedSettingsReady.emit(result)

        self._executor.submit(worker)

    @Slot(object)
    def _apply_shared_settings(self, result: dict) -> None:
        if self._shutting_down:
            return
        self._shared_settings_inflight = False
        if result["epoch"] != self._shared_settings_epoch or tuple(result["context"]) != self._shared_settings.context():
            self.sharedSettingsChanged.emit()
            self._settings_reconcile_timer.start()
            return
        if "projection" in result:
            self._shared_settings_state = {**result["projection"], "available": True, "error": ""}
        else:
            error = result.get("error", "invalid")
            if error in {"denied", "unsupported"}:
                self._shared_settings_state = {}
            self._shared_settings_state = {**self._shared_settings_state, "available": False, "error": error}
        self.sharedSettingsChanged.emit()
        self.moodChanged.emit()

    @Property(str, notify=profileChanged)
    def activeProfileId(self) -> str:
        return self.cfg.active_profile_id

    @Property(str, notify=profileChanged)
    def activeProfileName(self) -> str:
        return self.cfg.active_profile_name or self.tr_key("household_profile")

    @Property(str, notify=profileChanged)
    def activeProfileType(self) -> str:
        return self.cfg.active_profile_type

    @Property(bool, notify=profileChanged)
    def personalProfileActive(self) -> bool:
        return self.cfg.active_profile_type == "personal"

    @Property(bool, notify=profileChanged)
    def personalProfileExportAllowed(self) -> bool:
        return bool(self.personalProfileActive and session_profile_id(self.cfg) and not self.cfg.private_session)

    @Property(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @Property(bool, notify=backendAvailableChanged)
    def backendAvailable(self) -> bool:
        return self._backend_available

    @Property(str, notify=titleChanged)
    def title(self) -> str:
        return self.playback.title or self.tr_key("nothing_playing")

    @Property(bool, notify=titleChanged)
    def hasTrackTitle(self) -> bool:
        return bool(self.playback.title)

    @Property(str, notify=artistChanged)
    def artist(self) -> str:
        return self.playback.artist

    @Property(str, notify=albumChanged)
    def album(self) -> str:
        return self.playback.album

    @Property(str, notify=imageUrlChanged)
    def imageUrl(self) -> str:
        return self.playback.image_url

    @Property("QVariantList", notify=mediaListsChanged)
    def queueItems(self) -> list[dict[str, object]]:
        return self._queue_items

    @Property("QVariantList", notify=mediaListsChanged)
    def playlistItems(self) -> list[dict[str, object]]:
        return self._playlist_items

    @Property(bool, notify=mediaActivityChanged)
    def queueLoading(self) -> bool:
        return "queue" in self._media_loads_in_flight

    @Property(bool, notify=mediaActivityChanged)
    def playlistsLoading(self) -> bool:
        return "playlists" in self._media_loads_in_flight

    @Property(bool, notify=mediaActivityChanged)
    def queueLoadFailed(self) -> bool:
        return "queue" in self._media_list_load_failures

    @Property(bool, notify=mediaActivityChanged)
    def playlistsLoadFailed(self) -> bool:
        return "playlists" in self._media_list_load_failures

    @Property(bool, notify=mediaActivityChanged)
    def mediaPlaybackBusy(self) -> bool:
        return self._media_playback_busy

    @Property("QVariantList", notify=askDjChanged)
    def askDjMessages(self) -> list[dict[str, object]]:
        return self._ask_dj_messages

    @Slot(result=float)
    def askDjHistoryClock(self) -> float:
        return self._session_controller._clock.now()

    @Slot(str, result="QVariantMap")
    def askDjContextEntry(self, message_id):
        if (self._demo_mode or not self._application_foreground or not self.paired
                or not self._backend_available or self._version_mismatch_visible
                or not self.personalProfileExportAllowed or self.cfg.active_profile_privacy_mode != "private"
                or not any(message.get("id") == message_id and message.get("contextEligible") is True
                           for message in self._ask_dj_messages)):
            return {}
        page = self._ask_dj_context_page
        rows = page[2] if page and page[0] == self._private_read_context() else []
        reference = self._session_controller.ask_dj_context_entry(message_id, rows)
        if reference and (reference["session_id"], reference["entry_id"]) in self._ask_dj_withdrawn_entries:
            return {}
        return reference

    @Slot(str, str, str, result="QVariantMap")
    def askDjSelectedContributionPreview(self, message_id, session_id, entry_id):
        selected = {"session_id": session_id, "entry_id": entry_id}
        if message_id:
            if self.askDjContextEntry(message_id) != selected:
                return {}
        elif not self.canAskAboutSavedEntry(session_id, entry_id):
            return {}
        page = self._ask_dj_context_page
        rows = page[2] if page and page[0] == self._private_read_context() else []
        return self._session_controller.ask_dj_selection_preview(selected, rows, saved=not message_id)

    @Slot()
    def cancelAskDjHistoryOpen(self):
        self._ask_dj_history_open = None
        self._ask_dj_context_request = None
        self._ask_dj_context_page = None
        if self._ask_dj_destination_loading:
            self._ask_dj_destination_loading = False
            self.askDjChanged.emit()

    @Property(bool, notify=askDjChanged)
    def askDjDestinationLoading(self):
        return self._ask_dj_destination_loading

    @Property(bool, notify=askDjChanged)
    def askDjDestinationUnavailable(self):
        page = self._ask_dj_context_page
        return bool(not self._ask_dj_destination_loading and page
                    and page[0] == self._private_read_context() and page[1] is None)

    @Property(str, notify=askDjChanged)
    def askDjContributionQuestion(self):
        page = self._ask_dj_context_page
        return page[3] if page and page[0] == self._private_read_context() else ""

    @Slot(str, str, str, result=bool)
    def exploreAskDjAnswer(self, reply_id, session_id, entry_id):
        question = self.askDjContributionQuestion
        if not question or not reply_id or not session_id or not entry_id:
            return False
        return self._send_ask_dj_question(question, reply_id, session_id, entry_id, restore_draft=False)

    @Slot(str, str, result=bool)
    def exploreSavedAskDjEntry(self, session_id, entry_id):
        selected = {"session_id": session_id, "entry_id": entry_id}
        question = self._session_controller.saved_exploration_question(selected)
        return self._send_ask_dj_question(question, "", session_id, entry_id,
                                          restore_draft=False, from_archive=True)

    @Slot(str, str, result=bool)
    def canAskAboutSavedEntry(self, session_id, entry_id):
        return ((session_id, entry_id) not in self._ask_dj_withdrawn_entries
                and self._session_controller.saved_question_available({"session_id": session_id, "entry_id": entry_id}))

    @Slot(str, str, str, result=bool)
    def sendAskDjSavedQuestion(self, text, session_id, entry_id):
        return self._send_ask_dj_question(text, "", session_id, entry_id, restore_draft=True, from_archive=True)

    @Slot(str, str, str)
    def openAskDjHistoryMatch(self, message_id, session_id, entry_id):
        ticket = self._private_read_context()
        request = self._ask_dj_history_open = object()

        def current():
            return (self._ask_dj_history_open is request and ticket == self._private_read_context()
                    and self._application_foreground and self.paired and self._backend_available
                    and not self._demo_mode and not self._version_mismatch_visible and self.personalProfileExportAllowed
                    and any(match["session_id"] == session_id and match["entry_id"] == entry_id
                            and match["can_open"] and match["expires_at"] > self.askDjHistoryClock()
                            for message in self._ask_dj_messages if message.get("id") == message_id
                            for match in message.get("historicalMatches", [])))

        if current() and not self._session_controller.open_ask_dj_entry(session_id, entry_id, current):
            self._show_ask_dj_history_open_failed("", "")

    @Slot(str, str)
    def _show_ask_dj_history_open_failed(self, session_id, entry_id):
        if session_id and entry_id:
            reference = (session_id, entry_id)
            self._ask_dj_withdrawn_entries.add(reference)
            self._ask_dj_messages = [
                {**message, "historicalMatches": [
                    match for match in message["historicalMatches"]
                    if (match["session_id"], match["entry_id"]) != reference
                ]} if message.get("historicalMatches") else message
                for message in self._ask_dj_messages
            ]
            self.askDjChanged.emit()
        self._show_toast(self.tr_key("ask_dj_saved_session_open_failed"), 3000, "askdj")

    @Slot(str, str, result=str)
    def highlightAskDjMessage(self, body: str, query: str) -> str:
        return local_highlighted_text(body, query)

    @Slot(str, float, result=str)
    def formatAskDjTimestamp(self, created_at: str, now_ms: float) -> str:
        stamp = timestamp(created_at)
        if stamp is None:
            return ""
        elapsed = max(0, now_ms / 1000 - stamp)
        if elapsed < 3600:
            return self.tr_key("ask_dj_minutes_ago", count=max(1, int(elapsed // 60)))
        created = datetime.fromtimestamp(stamp)
        days = (datetime.fromtimestamp(now_ms / 1000).date() - created.date()).days
        if days <= 0:
            return created.strftime("%H:%M")
        if days == 1:
            return self.tr_key("ask_dj_yesterday")
        if days < 7:
            return self.tr_key("ask_dj_days_ago", count=days)
        if days < 14:
            return self.tr_key("ask_dj_last_week")
        if days <= 30:
            return self.tr_key("ask_dj_weeks_ago", count=days // 7)
        if days <= 61:
            return self.tr_key("ask_dj_last_month")
        return self.tr_key("ask_dj_months_ago", count=days // 30)

    @Property(bool, notify=askDjChanged)
    def askDjBusy(self) -> bool:
        return self._ask_dj_busy

    @Property(bool, notify=askDjChanged)
    def trackInsightLoading(self) -> bool:
        return self._track_insight_loading

    @Property("QVariantList", notify=askDjHelpChanged)
    def askDjHelpSections(self) -> list:
        if self._ask_dj_help_is_current(self._ask_dj_help_request):
            return self._ask_dj_help_sections
        return []

    @Property(bool, notify=askDjHelpChanged)
    def askDjHelpBusy(self) -> bool:
        return self._ask_dj_help_busy

    @Property("QVariantList", notify=askDjHelpChanged)
    def askDjSuggestions(self) -> list:
        if self._ask_dj_help_is_current(self._ask_dj_help_request):
            return self._ask_dj_suggestions
        return []

    @Property(bool, notify=musicDnaChanged)
    def musicDnaEnabled(self) -> bool:
        return self._music_dna_enabled

    @Property(bool, notify=musicDnaChanged)
    def musicDnaLoaded(self) -> bool:
        return self._music_dna_loaded

    @Property(int, notify=musicDnaChanged)
    def musicDnaContextRevision(self) -> int:
        return self._music_dna_context_revision

    @Property(str, notify=musicDnaChanged)
    def musicDnaUpdatedAt(self) -> str:
        return self._music_dna_updated_at

    @Property(bool, notify=musicDnaChanged)
    def musicDnaBusy(self) -> bool:
        return self._music_dna_busy

    @Property(str, notify=musicDnaChanged)
    def musicDnaError(self) -> str:
        return self._music_dna_error

    @Property(bool, notify=musicDnaChanged)
    def musicDnaNeedsRecovery(self) -> bool:
        return self._music_dna_needs_recovery

    @Property("QVariantMap", notify=musicDnaChanged)
    def musicDnaImportPreview(self) -> dict[str, object]:
        request = self._music_dna_import
        return dict(request["preview"]) if request and self._music_dna_import_current(request) else {}

    @Property(bool, notify=musicDnaChanged)
    def musicDnaImportUploading(self) -> bool:
        request = self._music_dna_import
        return bool(request and request.get("uploading") and self._music_dna_import_current(request))

    @Property(bool, notify=musicDnaChanged)
    def musicDnaImportSaved(self) -> bool:
        request = self._music_dna_import
        return bool(request and request.get("saved") and self._music_dna_import_current(request))

    @Property(str, notify=musicDnaChanged)
    def musicDnaImportFailureKey(self) -> str:
        request = self._music_dna_import
        return request.get("failure", "") if request and self._music_dna_import_current(request) else ""

    @Property(str, notify=musicDnaChanged)
    def musicDnaSummary(self) -> str:
        return self._music_dna_summary

    @Property("QVariantList", notify=musicDnaChanged)
    def musicDnaSections(self) -> list[dict[str, object]]:
        return self._music_dna_sections

    @Property("QVariantList", notify=musicDiscoveryItemsChanged)
    def musicDiscoveryItems(self) -> list[dict[str, object]]:
        return self._music_discovery_items

    @Property(bool, notify=musicDiscoveryChanged)
    def musicDiscoveryBusy(self) -> bool:
        return self._music_discovery_busy

    @Property(str, notify=musicDiscoveryChanged)
    def musicDiscoveryEmptyText(self) -> str:
        return self._music_discovery_empty_text

    @Property(str, notify=musicDiscoveryChanged)
    def musicDiscoveryError(self) -> str:
        return self._music_discovery_error

    @Property(bool, notify=musicDiscoveryChanged)
    def musicDiscoveryDnaDisabled(self) -> bool:
        return self._music_discovery_dna_disabled

    @Property(bool, notify=musicDiscoveryChanged)
    def musicDiscoveryLoaded(self) -> bool:
        return self._music_discovery_loaded

    @Property(str, notify=musicDiscoveryChanged)
    def musicDiscoveryPlayingId(self) -> str:
        return self._music_discovery_playing[1] if self._music_discovery_playing else ""

    @Property(str, notify=musicDiscoveryChanged)
    def musicDiscoveryPlayingSectionId(self) -> str:
        return self._music_discovery_playing[0] if self._music_discovery_playing else ""

    @Property(bool, notify=musicDiscoveryChanged)
    def musicDiscoveryFeedbackSupported(self) -> bool:
        return self._music_discovery_feedback_supported

    @Property(bool, notify=musicDiscoveryChanged)
    def musicDiscoveryConsentRejected(self) -> bool:
        return self._music_discovery_consent_rejected

    @Property(str, notify=trackInsightChanged)
    def trackInsightTitle(self) -> str:
        return self._track_insight_title

    @Property(bool, notify=trackInsightChanged)
    def trackInsightMatchesPlayback(self) -> bool:
        return self._demo_mode or self._track_insight_result_scope == self._track_insight_scope()

    @Property(bool, notify=trackInsightChanged)
    def trackInsightRenewalNeeded(self) -> bool:
        previous = self._track_insight_last_proof
        return bool(previous and self._track_insight_result_scope is None
                    and previous[0] == self._track_insight_scope()
                    and previous[2] <= self._session_controller._clock.now()
                    and self._track_insight_retryable)

    @Property(str, notify=trackInsightPlaybackItemChanged)
    def trackInsightPlaybackItemId(self) -> str:
        uri = self.playback.uri.strip()
        return hashlib.sha256(uri.encode()).hexdigest()[:24] if uri else ""

    @Slot(str, str, result=bool)
    def canOpenSessionTrackInsight(self, session_id: str, item_id: str) -> bool:
        current = session_playback_reference(self._session_controller)
        return bool(self.paired and self.hasTrackTitle and not self.askDjBusy and item_id
                    and current and current[:2] == (session_id, item_id)
                    and item_id == self.trackInsightPlaybackItemId)

    @Property(str, notify=trackInsightChanged)
    def trackInsightArtist(self) -> str:
        return self._track_insight_artist

    @Property(str, notify=trackInsightChanged)
    def trackInsightAlbum(self) -> str:
        return self._track_insight_album

    @Property(str, notify=trackInsightChanged)
    def trackInsightImageUrl(self) -> str:
        return self._track_insight_image_url

    @Property(str, notify=trackInsightChanged)
    def trackInsightText(self) -> str:
        return self._track_insight_text

    @Property("QVariantMap", notify=trackInsightChanged)
    def trackInsightShareDescriptors(self) -> dict[str, str]:
        descriptors = {}
        for section in self._track_insight_sections:
            kind = section.get("kind")
            if kind not in {"genre", "vibe"} or kind in descriptors:
                continue
            candidates = [section.get("body"), *section.get("details", [])]
            value = next((text for item in candidates if (text := bounded_text(item, 128).strip())), "")
            if value:
                descriptors[kind] = value
        return descriptors

    @Property(str, notify=trackInsightChanged)
    def trackInsightShareText(self) -> str:
        if not self._track_insight_title or not self._track_insight_text:
            return ""
        if not self._demo_mode:
            if (not self.paired or not self._backend_available or self._version_mismatch_visible
                    or self._track_insight_result_scope != self._track_insight_scope()):
                return ""
            if (self._track_insight_deadline is not None
                    and self._track_insight_deadline <= self._session_controller._clock.now()):
                return ""
        heading = " — ".join(value for value in (self._track_insight_title, self._track_insight_artist) if value)
        descriptors = self.trackInsightShareDescriptors
        descriptor_line = " · ".join(descriptors[key] for key in ("genre", "vibe") if key in descriptors)
        parts = [value for value in (heading, descriptor_line, self._track_insight_text) if value]
        if self._demo_mode:
            parts.insert(0, self.tr_key("demo_mode"))
        credit = self._track_insight_verified_credit
        if credit.get("context") and credit["context"] != self._track_insight_text:
            parts.append(credit["context"])
        if credit.get("url"):
            parts.append("\n".join(credit[key] for key in ("provider", "license", "url") if credit.get(key)))
        if self._track_insight_audio_notice:
            parts.append(self.tr_key("track_insight_unmeasured_audio"))
        return "\n\n".join(parts)

    @Slot(result=bool)
    def copyTrackInsight(self) -> bool:
        text = self.trackInsightShareText if self._application_foreground else ""
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        return True

    @Slot(QUrl, bool, bool, result=QUrl)
    def trackInsightExportDestination(self, folder: QUrl, story: bool, clip: bool) -> QUrl:
        words = re.findall(r"[^\W_]+", self.trackInsightTitle[:200])[:4]
        title = "-".join(words)[:40].rstrip("-") or "Track-Insight"
        shape = "story" if story else "square"
        extension = "mp4" if clip else "png"
        return self._local_export_destination(folder, f"DJConnect-{title}-{shape}.{extension}",
                                              QStandardPaths.StandardLocation.PicturesLocation)

    @Slot(QUrl, bool, result=QUrl)
    def profileExportDestination(self, folder: QUrl, music_dna: bool) -> QUrl:
        data_kind = "music-dna" if music_dna else "ask-dj-history"
        date = datetime.now().strftime("%Y%m%d-%H%M%S")
        return self._local_export_destination(folder, f"djconnect-{data_kind}-{CLIENT_TYPE}-{date}.json",
                                              QStandardPaths.StandardLocation.DocumentsLocation)

    @staticmethod
    def _local_export_destination(folder: QUrl, filename: str, fallback: QStandardPaths.StandardLocation) -> QUrl:
        directory = folder.toLocalFile() or QStandardPaths.writableLocation(fallback)
        if not directory or not Path(directory).is_absolute():
            return QUrl()
        return QUrl.fromLocalFile(str(Path(directory) / filename))

    @Slot(QImage, QUrl, str)
    def saveTrackInsightCard(self, image: QImage, destination: QUrl, expected_text: str) -> None:
        if (not self._application_foreground or not expected_text or expected_text != self.trackInsightShareText):
            return
        path = destination.toLocalFile()
        if (not path or Path(path).suffix.lower() != ".png" or image.width() != 1080
                or image.height() not in {1080, 1920}):
            self.trackInsightExportFinished.emit("track_insight_card_save_failed", False)
            return
        target = QSaveFile(path)
        if target.open(QIODevice.OpenModeFlag.WriteOnly) and image.save(target, "PNG") and target.commit():
            self.trackInsightExportFinished.emit("track_insight_card_saved", True)
        else:
            target.cancelWriting()
            self.trackInsightExportFinished.emit("track_insight_card_save_failed", False)

    @Property(bool, constant=True)
    def trackInsightClipSupported(self) -> bool:
        return clip_format() is not None

    @Property(bool, notify=trackInsightClipChanged)
    def trackInsightClipRunning(self) -> bool:
        return self._track_insight_clip is not None

    @Slot(QUrl, bool, str)
    def startTrackInsightClip(self, destination: QUrl, story: bool, expected_text: str) -> None:
        scope = self._track_insight_scope()
        item = self.trackInsightPlaybackItemId

        def current() -> bool:
            return (self._application_foreground and not self._shutting_down and bool(expected_text)
                    and expected_text == self.trackInsightShareText and self._track_insight_scope() == scope
                    and self.trackInsightPlaybackItemId == item)

        if self._track_insight_clip is not None or not current():
            return
        path = destination.toLocalFile()
        fmt = clip_format()
        if fmt is None:
            self.trackInsightExportFinished.emit("track_insight_clip_unavailable", False)
            return
        if not path or Path(path).suffix.lower() != ".mp4":
            self.trackInsightExportFinished.emit("track_insight_clip_save_failed", False)
            return
        clip = TrackInsightClip(path, 1920 if story else 1080, fmt, current, self)
        self._track_insight_clip = clip
        clip.frameRequested.connect(self.trackInsightClipFrameRequested.emit)
        clip.finished.connect(lambda outcome: self._finish_track_insight_clip(clip, outcome))
        self.trackInsightClipChanged.emit()
        clip.start()

    @Slot(QImage)
    def supplyTrackInsightClipFrame(self, image: QImage) -> None:
        if self._track_insight_clip is not None:
            self._track_insight_clip.supply(image)

    @Slot()
    def cancelTrackInsightClip(self) -> None:
        if self._track_insight_clip is not None:
            self._track_insight_clip.stop()

    def _finish_track_insight_clip(self, clip, outcome: str) -> None:
        if self._track_insight_clip is not clip:
            return
        self._track_insight_clip = None
        clip.deleteLater()
        self.trackInsightClipChanged.emit()
        if outcome != "cancelled":
            saved = outcome == "saved"
            key = {"saved": "track_insight_clip_saved", "encoding_failed": "track_insight_clip_encoding_failed"}.get(
                outcome, "track_insight_clip_save_failed")
            self.trackInsightExportFinished.emit(key, saved)

    @Property(bool, notify=trackInsightChanged)
    def trackInsightAudioNotice(self) -> bool:
        return self._track_insight_audio_notice

    @Property("QVariantMap", notify=trackInsightChanged)
    def trackInsightVerifiedCredit(self) -> dict[str, str]:
        return self._track_insight_verified_credit

    @Property(str, notify=trackInsightChanged)
    def trackInsightError(self) -> str:
        return self._track_insight_error

    @Property("QVariantList", notify=trackInsightChanged)
    def trackInsightItems(self) -> list[dict[str, object]]:
        return self._track_insight_items

    @Property("QVariantList", notify=trackInsightChanged)
    def trackInsightShareMeters(self) -> list[dict[str, object]]:
        return [item for item in self._track_insight_items if "shareFraction" in item]

    @Property("QVariantList", notify=trackInsightChanged)
    def trackInsightSections(self) -> list[dict[str, object]]:
        return self._track_insight_sections

    @Property("QVariantMap", notify=trackInsightChanged)
    def trackInsightVisualizer(self) -> dict[str, object]:
        return self._track_insight_visualizer

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self.playback.is_playing

    @Property(bool, notify=favoriteChanged)
    def currentTrackFavorite(self) -> bool:
        return self.playback.is_favorite

    @Property(bool, notify=favoriteChanged)
    def favoriteAvailable(self) -> bool:
        if self._demo_mode:
            return self.playback.uri.startswith("demo:track:")
        capabilities = self.cfg.music_backend_capabilities or {}
        return bool(self.paired and capabilities.get("supports_favorites") and self.playback.uri)

    @Property(bool, notify=favoriteChanged)
    def favoriteRemovalAvailable(self) -> bool:
        return self.favoriteAvailable and (self._demo_mode or self.cfg.music_backend == "spotify_direct")

    @Property(bool, notify=favoriteBusyChanged)
    def favoriteBusy(self) -> bool:
        return self._favorite_busy

    @Property(int, notify=volumeChanged)
    def volume(self) -> int:
        return self.playback.volume

    @Property(bool, notify=shuffleChanged)
    def shuffle(self) -> bool:
        if self._pending_playback_mode and self._pending_playback_mode[1] == "shuffle":
            return bool(self._pending_playback_mode[2])
        return self.playback.shuffle

    @Property(str, notify=repeatChanged)
    def repeat(self) -> str:
        if self._pending_playback_mode and self._pending_playback_mode[1] == "repeat":
            return str(self._pending_playback_mode[2])
        return self.playback.repeat

    @Property(bool, notify=playbackModePendingChanged)
    def playbackModePending(self) -> bool:
        return self._pending_playback_mode is not None

    @Property(int, notify=progressChanged)
    def positionSeconds(self) -> int:
        return self.playback.position_seconds

    @Property(int, notify=progressChanged)
    def durationSeconds(self) -> int:
        return self.playback.duration_seconds

    @Property(bool, notify=settingsChanged)
    def musicBackendSupportsSeek(self) -> bool:
        return self.cfg.music_backend_capabilities.get("supports_seek") is True

    @Property(bool, notify=settingsChanged)
    def musicBackendSupportsShuffle(self) -> bool:
        return self.cfg.music_backend_capabilities.get("supports_shuffle") is True

    @Property(bool, notify=settingsChanged)
    def musicBackendSupportsRepeat(self) -> bool:
        return self.cfg.music_backend_capabilities.get("supports_repeat") is True

    @Property(bool, notify=outputDeviceChanged)
    def playbackSeekOutputKnown(self) -> bool:
        return bool(output_id(self.playback.output_device_id))

    @Property(float, notify=progressChanged)
    def trackProgress(self) -> float:
        if self.playback.duration_seconds <= 0:
            return 0.0
        return max(0.0, min(1.0, self.playback.position_seconds / self.playback.duration_seconds))

    @Property(str, notify=progressChanged)
    def progressLabel(self) -> str:
        return f"{_format_duration(self.playback.position_seconds)}/{_format_duration(self.playback.duration_seconds)}"

    @Property(str, notify=outputDeviceChanged)
    def outputDevice(self) -> str:
        return self.playback.output_device

    @Property(str, notify=outputDeviceChanged)
    def outputDeviceId(self) -> str:
        return self.playback.output_device_id

    @Property(str, notify=outputDeviceChanged)
    def pendingOutputChoiceName(self) -> str:
        return self._pending_output_choice[1] if self._pending_output_choice else ""

    @Property(bool, notify=outputDeviceChanged)
    def outputChoiceUnconfirmed(self) -> bool:
        return bool(self._unconfirmed_output_choice)

    @Property(bool, notify=outputDeviceChanged)
    def controlOutputsRefreshing(self) -> bool:
        return self._control_outputs_request is not None

    @Property(str, notify=outputDeviceChanged)
    def controlOutputsStatus(self) -> str:
        return self._control_outputs_status

    @Property("QVariantList", notify=outputDeviceChanged)
    def outputDevices(self) -> list[str]:
        return list(self.playback.output_devices)

    @Property("QVariantList", notify=outputDeviceChanged)
    def outputDeviceChoices(self) -> list[dict[str, object]]:
        if self.playback.output_device_details:
            choices = [dict(item) for item in self.playback.output_device_details]
        else:
            choices = [{"name": name, "value": name} for name in self.playback.output_devices]
        return sorted_output_choices(choices, self.cfg.language)

    @Property(bool, notify=pairedChanged)
    def paired(self) -> bool:
        return bool(self.cfg.paired and self.cfg.device_token)

    @Property(bool, notify=pairingSuccessChanged)
    def pairingSuccessVisible(self) -> bool:
        return self._pairing_success_visible

    @Property(bool, notify=demoModeChanged)
    def demoMode(self) -> bool:
        return self._demo_mode

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=settingsChanged)
    def haUrl(self) -> str:
        return self.cfg.ha_url

    @Property(str, notify=localApiUrlChanged)
    def localApiUrl(self) -> str:
        return self.cfg.local_url

    @Property(str, notify=localApiUrlChanged)
    def webPortalUrl(self) -> str:
        return self.cfg.local_url

    @Property(str, notify=settingsChanged)
    def deviceId(self) -> str:
        return self.cfg.device_id

    @Property(str, notify=settingsChanged)
    def deviceName(self) -> str:
        return self.cfg.device_name

    @Property(str, notify=pairingCodeChanged)
    def pairingCode(self) -> str:
        return self.cfg.pairing_code

    @Property(str, constant=True)
    def version(self) -> str:
        return __version__

    @Property(str, constant=True)
    def protocolVersion(self) -> str:
        return self.cfg.version

    @Property(str, constant=True)
    def clientType(self) -> str:
        return CLIENT_TYPE

    @Property(str, constant=True)
    def transportMode(self) -> str:
        return self.tr_key("local_only")

    @Property(str, notify=settingsChanged)
    def connectionType(self) -> str:
        if not self.paired:
            return self.tr_key("not_connected_value")
        diagnostics = self.client.diagnostics()
        if diagnostics.get("websocketConnected") is True or diagnostics.get("fastPathTransport") == "websocket":
            return self.tr_key("local_websocket_fast_path")
        return self.tr_key("local_http_fallback")

    @Property(str, notify=settingsChanged)
    def musicBackendName(self) -> str:
        return self.cfg.music_backend_name or self.cfg.music_backend

    @Property(str, notify=settingsChanged)
    def musicBackendId(self) -> str:
        return self.cfg.music_backend

    @Property(bool, notify=settingsChanged)
    def musicBackendAvailable(self) -> bool:
        return self.cfg.music_backend_available

    def _about_music_scope(self):
        return (self._private_read_context(), self.cfg.music_backend, self.cfg.music_backend_revision)

    @Property(str, notify=aboutMusicAvailabilityChanged)
    def aboutMusicAvailability(self) -> str:
        observation = self._about_music_availability
        if (self._demo_mode or not self.paired or not self._backend_available or self._version_mismatch_visible
                or observation is None or observation[0] != self._about_music_scope()):
            return "unconfirmed"
        return "available" if observation[1] else "unavailable"

    def _reconcile_about_status(self):
        if self._about_music_availability is not None and self.aboutMusicAvailability == "unconfirmed":
            self._about_music_availability = None
        if self._about_connection_origin is not None and not self.aboutConnectionOrigin:
            self._about_connection_origin = None
            self.aboutConnectionOriginChanged.emit()
        self.aboutMusicAvailabilityChanged.emit()

    @Slot(object, object, float)
    def _apply_about_music_availability(self, scope, available, started):
        if (self._shutting_down or scope != self._about_music_scope() or started < self._about_music_request_time
                or self._demo_mode or not self.paired or not self._backend_available or self._version_mismatch_visible):
            return
        self._about_music_request_time = started
        self._about_music_availability = (scope, available) if type(available) is bool else None
        self.aboutMusicAvailabilityChanged.emit()

    @Property(str, notify=aboutConnectionOriginChanged)
    def aboutConnectionOrigin(self) -> str:
        observation = self._about_connection_origin
        if (self._demo_mode or not self.paired or not self._backend_available or self._version_mismatch_visible
                or observation is None or observation[0] != self._private_read_context()):
            return ""
        return observation[1]

    @Property(str, notify=aboutConnectionOriginChanged)
    def aboutConnectionTransport(self) -> str:
        origin = self.aboutConnectionOrigin
        if not origin:
            return ""
        if self._about_connection_origin[2]:
            return "local_websocket_fast_path"
        return "ha_transport_https" if origin.startswith("https:") else "ha_transport_http"

    @Slot(object, str, float, bool)
    def _apply_about_connection_origin(self, binding, origin: str, started: float, via_websocket: bool = False) -> None:
        if (self._shutting_down or started < self._about_connection_request_time
                or binding != self._private_read_context() or self._demo_mode
                or not self.paired or not self._backend_available or self._version_mismatch_visible):
            return
        self._about_connection_request_time = started
        self._about_connection_origin = (binding, origin, via_websocket) if origin else None
        self.aboutConnectionOriginChanged.emit()

    @Property(str, notify=settingsChanged)
    def musicTargetPlayerName(self) -> str:
        target = self.cfg.music_target_player or {}
        return str(target.get("name") or target.get("id") or "")

    @Property(str, notify=settingsChanged)
    def musicTargetPlayerId(self) -> str:
        target = self.cfg.music_target_player or {}
        return str(target.get("id") or "")

    @Property(str, notify=settingsChanged)
    def musicBackendCapabilitiesLabel(self) -> str:
        capabilities = self.cfg.music_backend_capabilities or {}
        return ", ".join(key.replace("supports_", "") for key, value in sorted(capabilities.items()) if value)

    @Property(str, notify=settingsChanged)
    def musicBackendError(self) -> str:
        return self.cfg.music_backend_error

    def observeLocalAudioDevices(self) -> None:
        """Enumerate OS devices without opening an input or output stream."""
        try:
            from PySide6.QtMultimedia import QMediaDevices

            self._audio_devices = QMediaDevices(self)
            self._audio_devices.audioInputsChanged.connect(self.localAudioDevicesChanged.emit)
            self._audio_devices.audioOutputsChanged.connect(self.localAudioDevicesChanged.emit)
        except (ImportError, OSError, RuntimeError):
            self._audio_devices = None
        self.localAudioDevicesChanged.emit()

    @Property(bool, notify=localAudioDevicesChanged)
    def localMicrophoneAvailable(self) -> bool:
        return self._audio_devices is not None and bool(self._audio_devices.audioInputs())

    @Property(bool, notify=localAudioDevicesChanged)
    def localAudioOutputAvailable(self) -> bool:
        return self._audio_devices is not None and bool(self._audio_devices.audioOutputs())

    @Property(bool, notify=sharedSettingsChanged)
    def sessionVoiceAvailable(self) -> bool:
        state = self._shared_settings_state
        return (state.get("available") is True and state.get("selection_required") is not True
                and state.get("voice_available") is True and state.get("speaker_available") is True
                and (state.get("values") or {}).get("announcement_output") in ("ha_speaker", "both"))

    @Property(str, notify=djAnnouncementChanged)
    def djAnnouncementOutput(self) -> str:
        return normalize_dj_announcement_output(self.cfg.dj_announcement_output, self.cfg.music_backend_capabilities)

    @Property(bool, notify=djAnnouncementChanged)
    def djAnnouncementSpeakerAvailable(self) -> bool:
        return normalize_dj_announcement_output("ha_speaker", self.cfg.music_backend_capabilities) == "ha_speaker"

    @Property(str, notify=djAnnouncementChanged)
    def djAnnouncementSpeakerName(self) -> str:
        announcement = self.cfg.music_backend_capabilities.get("dj_announcement") if isinstance(self.cfg.music_backend_capabilities, dict) else None
        if not isinstance(announcement, dict):
            return ""
        target = announcement.get("target") if isinstance(announcement.get("target"), dict) else {}
        return str(announcement.get("speaker_name") or target.get("name") or announcement.get("speaker_entity_id") or target.get("entity_id") or "")

    @Slot(QImage, result=str)
    def sampleArtworkTint(self, pixels: QImage) -> str:
        return "" if self._shutting_down else displayed_artwork_tint(pixels)

    @Property(int, notify=moodChanged)
    def moodValue(self) -> int:
        if self.sharedSettingsManaged:
            if self._shared_settings_state.get("selection_required"):
                return -1
            return {"chill": 0, "groove": 35, "energy": 70, "party": 100}.get(
                self._shared_settings_state.get("values", {}).get("mood"), -1)
        if self.cfg.mood is None:
            return -1
        return max(0, min(100, int(self.cfg.mood)))

    @Property(str, notify=settingsChanged)
    def screenshotFile(self) -> str:
        return self.cfg.screenshot_file

    @Property(int, notify=settingsChanged)
    def sessionRefreshSeconds(self) -> int:
        return effective_session_refresh_seconds(self.cfg)

    @Property(int, notify=screenTimeoutChanged)
    def screenTimeoutSeconds(self) -> int:
        return self.cfg.screen_timeout_seconds

    @Property(int, notify=returnToNowChanged)
    def returnToNowSeconds(self) -> int:
        return self.cfg.return_to_now_seconds

    @Property(int, notify=screenBrightnessChanged)
    def screenBrightnessPercent(self) -> int:
        return self.cfg.screen_brightness_percent

    @Property(str, notify=settingsChanged)
    def releaseProfile(self) -> str:
        return self.cfg.release_profile

    @Property(bool, notify=glassChanged)
    def glassEnabled(self) -> bool:
        return effective_glass_enabled(self.cfg)

    @Slot(bool, result=bool)
    def setGlassEnabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            return False
        if self.cfg.glass_enabled is enabled:
            return True
        previous = self.cfg.glass_enabled
        self.cfg.glass_enabled = enabled
        try:
            save_config(self.config_path, self.cfg)
        except OSError:
            self.cfg.glass_enabled = previous
            self.glassChanged.emit()
            self.showToastForContext(self.tr_key("save_failed"), "settings")
            return False
        self.glassChanged.emit()
        return True

    @Property(str, notify=updateChannelChanged)
    def updateChannel(self) -> str:
        return self.cfg.update_channel

    @Property(str, notify=logFileChanged)
    def logFile(self) -> str:
        return self.cfg.log_file

    @Property(str, notify=logLevelChanged)
    def logLevel(self) -> str:
        return self.cfg.log_level

    @Property(bool, notify=settingsChanged)
    def websocketFastPathEnabled(self) -> bool:
        return bool(self.cfg.websocket_fast_path_enabled)

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self.cfg.language

    @Property(str, notify=translationsChanged)
    def languageName(self) -> str:
        return LANGUAGES.get(self.cfg.language, LANGUAGES["nl"])

    @Property(int, notify=translationsChanged)
    def translationVersion(self) -> int:
        return self._translation_version

    @Property(str, notify=djResponseChanged)
    def djResponseText(self) -> str:
        return self._dj_response_text

    @Property(bool, notify=djResponseChanged)
    def djResponseVisible(self) -> bool:
        return self._dj_response_visible

    @Property(str, notify=toastChanged)
    def toastText(self) -> str:
        return self._toast_text

    @Property(str, notify=toastChanged)
    def toastIcon(self) -> str:
        return self._toast_icon

    @Property(bool, notify=toastChanged)
    def toastVisible(self) -> bool:
        return self._toast_visible

    @Property(bool, notify=versionMismatchChanged)
    def versionMismatchVisible(self) -> bool:
        return self._version_mismatch_visible

    @Property(str, notify=versionMismatchChanged)
    def versionMismatchText(self) -> str:
        return self._version_mismatch_text

    @Property(str, notify=logsChanged)
    def logsText(self) -> str:
        return self._logs_text

    @Property(str, notify=logsChanged)
    def logsReadError(self) -> str:
        return self._logs_read_error

    @Property(bool, notify=logsChanged)
    def logsVisible(self) -> bool:
        return self._logs_visible

    @Slot(result=bool)
    @Slot(str, result=bool)
    def openFeedbackIssue(self, surface: str = "") -> bool:
        # User click opens a draft only; no private app state or automatic submit.
        title_key = "ask_dj_feedback_title" if surface == "ask_dj" else "feedback_issue_title"
        query = urlencode({"title": translate(self.cfg.language, title_key),
                           "body": translate(self.cfg.language, "feedback_issue_template")})
        return QDesktopServices.openUrl(QUrl("https://github.com/djconnect-music-dj/djconnect-pi/issues/new?" + query))

    @Slot(result=str)
    def bundledFontLicense(self) -> str:
        try:
            return files("djconnect_pi").joinpath("fonts", "LICENSE.txt").read_text(encoding="utf-8")
        except OSError:
            return ""

    @Slot(str)
    @Slot(str, str)
    @Slot(str, str, str)
    def copyAskDjFeedbackAnswer(self, message_id: str, note: str = "", edited_answer: str | None = None) -> None:
        if (not message_id or not self._application_foreground
                or (not self._demo_mode and (not self.paired or not self.backendAvailable or self.versionMismatchVisible))):
            return
        for message in self._ask_dj_messages:
            if (message.get("id") == message_id and message.get("role") == "assistant"
                    and message.get("messageKind") not in {"system", "status"} and message.get("text")):
                note = note.strip()[:1200]
                answer = message["text"] if edited_answer is None else edited_answer.strip()
                if not answer and not note:
                    return
                details = f"{self.tr_key('ask_dj_feedback_note')}:\n{note}" if note else ""
                QGuiApplication.clipboard().setText("\n\n".join(part for part in (details, answer) if part))
                self.showToastForContext(self.tr_key("copied"), "connectivity")
                return

    @Slot()
    def openPrivacyStatement(self) -> None:
        language = normalize_language(self.cfg.language)
        path = "privacy" if language == "nl" else f"{language}/privacy"
        if not QDesktopServices.openUrl(QUrl(f"https://djconnect.dev/{path}")):
            self.showToastForContext(self.tr_key("privacy_open_failed"), "connectivity")

    @Slot(str, result=str)
    def t(self, key: str) -> str:
        return self.tr_key(key)

    def tr_key(self, key: str, **values: object) -> str:
        return translate(self.cfg.language, key, **values)

    @Slot(str)
    def setHaUrl(self, value: str) -> None:
        self.cfg.ha_url = value.strip()
        self._invalidate_shared_settings_if_context_changed()
        save_config(self.config_path, self.cfg)
        self.settingsChanged.emit()
        _LOGGER.info("User saved Home Assistant URL setting")
        self.showToastForContext(self.tr_key("ha_url_saved"), "connectivity")
        self._set_status_text(self.tr_key("ha_url_saved"))

    @Slot(int)
    def setSessionRefreshSeconds(self, seconds: int) -> None:
        if seconds not in SESSION_REFRESH_CHOICES or self.cfg.session_refresh_seconds == seconds:
            return
        self.cfg.session_refresh_seconds = seconds
        save_config(self.config_path, self.cfg)
        self.settingsChanged.emit()

    @Slot(int)
    def setScreenTimeoutSeconds(self, value: int) -> None:
        value = max(0, int(value))
        if self.cfg.screen_timeout_seconds == value:
            return
        self.cfg.screen_timeout_seconds = value
        save_config(self.config_path, self.cfg)
        self.screenTimeoutChanged.emit()
        self.settingsChanged.emit()
        _LOGGER.info("User set screen timeout to %s seconds", value)
        self.showToastForContext(self.tr_key("screen_timeout_saved"), "settings")
        self._set_status_text(self.tr_key("screen_timeout_saved"))

    @Slot(int)
    def setReturnToNowSeconds(self, value: int) -> None:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            seconds = 60
        if seconds not in {0, 30, 60, 120}:
            seconds = 60
        if self.cfg.return_to_now_seconds == seconds:
            return
        self.cfg.return_to_now_seconds = seconds
        save_config(self.config_path, self.cfg)
        self.returnToNowChanged.emit()
        self.settingsChanged.emit()
        _LOGGER.info("User set return-to-now timeout to %s seconds", seconds)
        self.showToastForContext(self.tr_key("return_to_now_saved"), "settings")
        self._set_status_text(self.tr_key("return_to_now_saved"))

    @Slot(int)
    def setScreenBrightnessPercent(self, value: int) -> None:
        value = max(10, min(100, int(value)))
        if self.cfg.screen_brightness_percent == value:
            return
        self.cfg.screen_brightness_percent = value
        save_config(self.config_path, self.cfg)
        self.screenBrightnessChanged.emit()
        self.settingsChanged.emit()
        _LOGGER.info("User set app brightness to %s%%", value)
        self.showToastForContext(self.tr_key("brightness_saved"), "settings")
        self._set_status_text(self.tr_key("brightness_saved"))

    @Slot(str)
    def setLanguage(self, value: str) -> None:
        language = normalize_language(value)
        if self.cfg.language == language:
            return
        self.cfg.language = language
        save_config(self.config_path, self.cfg)
        self._invalidate_track_insight_language()
        self._translation_version += 1
        self.languageChanged.emit()
        self.translationsChanged.emit()
        self.settingsChanged.emit()
        self.titleChanged.emit()
        _LOGGER.info("User changed language to %s", language)
        self.showToastForContext(self.tr_key("language_saved"), "settings")
        self._set_status_text(self.tr_key("language_saved"))

    @Slot(str)
    def setUpdateChannel(self, value: str) -> None:
        channel = value.strip().lower()
        if channel not in {"stable", "beta"}:
            channel = "stable"
        if self.cfg.update_channel == channel:
            return
        self.cfg.update_channel = channel
        save_config(self.config_path, self.cfg)
        self.updateChannelChanged.emit()
        self.settingsChanged.emit()
        _LOGGER.info("User changed update channel to %s", channel)
        self.showToastForContext(self.tr_key("update_channel", channel=channel), "updates")
        self._set_status_text(self.tr_key("update_channel", channel=channel))

    @Slot(str)
    def setLogLevel(self, value: str) -> None:
        level = normalize_log_level(value)
        if self.cfg.log_level == level:
            return
        self.cfg.log_level = level
        save_config(self.config_path, self.cfg)
        applied = self._apply_log_level()
        self.logLevelChanged.emit()
        self.settingsChanged.emit()
        if applied:
            _LOGGER.info("User changed log level to %s", level)
            label = self.tr_key("off") if level == DEFAULT_LOG_LEVEL else level
            self.showToastForContext(self.tr_key("log_level_saved", level=label), "diagnostics")

    def _apply_log_level(self) -> bool:
        disabled = self.cfg.log_level == DEFAULT_LOG_LEVEL
        if disabled:
            self._logs_text = ""
            self.hideLogs()
        try:
            path = setup_logging(self.cfg.log_file, self.cfg.log_level)
            if disabled:
                clear_log_files(path)
        except OSError as exc:
            self.showToastForContext(self.tr_key("logs_failed", error=exc), "diagnostics")
            return False
        return True

    @Slot(bool)
    def setWebSocketFastPathEnabled(self, enabled: bool) -> None:
        value = bool(enabled)
        if self.cfg.websocket_fast_path_enabled == value:
            return
        self.cfg.websocket_fast_path_enabled = value
        save_config(self.config_path, self.cfg)
        self.client.cfg = self.cfg
        self.client.fast_path.update_config(self.cfg)
        self.settingsChanged.emit()
        _LOGGER.info("User changed WebSocket fast path setting to %s", value)
        self.showToastForContext(self.tr_key("settings_saved"), "connectivity")

    @Slot(int)
    def setMoodValue(self, value: int) -> None:
        if self.sharedSettingsManaged:
            mood = "chill" if value < 25 else "groove" if value < 60 else "energy" if value < 85 else "party"
            self.saveSharedSettings({"mood": mood})
            return
        mood = max(0, min(100, int(value)))
        if self.cfg.mood == mood:
            return
        self.cfg.mood = mood
        save_config(self.config_path, self.cfg)
        self.client.cfg = self.cfg
        self.moodChanged.emit()
        _LOGGER.info("User changed mood selector to %s", mood)
        self.showToastForContext(self.tr_key("mood_saved"), "musicdna")

    @Slot(str)
    def setDjAnnouncementOutput(self, value: str) -> None:
        if self.sharedSettingsManaged:
            self.saveSharedSettings({"announcement_output": value})
            return
        output = normalize_dj_announcement_output(value, self.cfg.music_backend_capabilities)
        if self.cfg.dj_announcement_output == output:
            return
        self.cfg.dj_announcement_output = output
        self.cfg.dj_announcement_output_user_set = True
        save_config(self.config_path, self.cfg)
        self.client.cfg = self.cfg
        self.djAnnouncementChanged.emit()
        self.settingsChanged.emit()
        _LOGGER.info("User changed DJ announcement output to %s", output)
        self.showToastForContext(self.tr_key("dj_announcement_output_saved"), "askdj")

    @Slot(str)
    def pair(self, pair_code: str) -> None:
        if self._demo_mode:
            self.exitDemoMode()
        code = pair_code.strip()
        if not code:
            self._set_status_text(self.tr_key("enter_pairing_code"))
            self.showToastForContext(self.tr_key("enter_pairing_code"), "connectivity")
            return
        _LOGGER.info("User started pairing from touch UI")
        self.showToastForContext(self.tr_key("pairing"), "connectivity")
        self._run(self.tr_key("pairing"), lambda: self._pair_worker(code))

    @Slot()
    def refresh(self) -> None:
        if self._shutting_down:
            return
        if self._demo_mode:
            return
        if self._busy or self._refresh_pending:
            _LOGGER.debug("Refresh skipped because another task is active")
            return
        if not self.paired:
            latest = load_config(self.config_path)
            if latest.paired and latest.device_token:
                self.cfg = latest
                self.client.cfg = self.cfg
                self._pairing_success_visible = True
                self.pairedChanged.emit()
                self.settingsChanged.emit()
                self.pairingSuccessChanged.emit()
                self._set_status_text(self.tr_key("paired"))
                self._run(self.tr_key("refreshing"), self._refresh_worker)
                return
        if not self.paired and not self.cfg.ha_url:
            self._set_status_text(self.tr_key("ready_to_pair"))
            return
        self._refresh_pending = True
        self._run(self.tr_key("refreshing"), self._refresh_worker, done=self._finish_refresh, interactive=False)

    def _finish_refresh(self) -> None:
        self._refresh_pending = False

    def _sync_config_from_disk(self) -> None:
        latest = load_config(self.config_path)
        if (
            latest.device_token == self.cfg.device_token
            and latest.paired == self.cfg.paired
            and latest.ha_url == self.cfg.ha_url
            and latest.device_name == self.cfg.device_name
            and latest.language == self.cfg.language
            and latest.log_level == self.cfg.log_level
            and latest.screen_brightness_percent == self.cfg.screen_brightness_percent
            and latest.session_refresh_seconds == self.cfg.session_refresh_seconds
            and latest.screen_timeout_seconds == self.cfg.screen_timeout_seconds
            and latest.return_to_now_seconds == self.cfg.return_to_now_seconds
            and latest.update_channel == self.cfg.update_channel
            and latest.glass_enabled is self.cfg.glass_enabled
            and latest.active_profile_id == self.cfg.active_profile_id
            and latest.active_profile_type == self.cfg.active_profile_type
            and latest.active_profile_privacy_mode == self.cfg.active_profile_privacy_mode
            and latest.explicit_profile_id == self.cfg.explicit_profile_id
            and latest.private_session == self.cfg.private_session
            and latest.dj_announcement_output == self.cfg.dj_announcement_output
        ):
            return
        language_changed = latest.language != self.cfg.language
        glass_changed = effective_glass_enabled(latest) != self.glassEnabled
        log_level_changed = latest.log_level != self.cfg.log_level
        self.cfg = latest
        self.client.cfg = self.cfg
        if log_level_changed:
            self._apply_log_level()
        self._invalidate_shared_settings_if_context_changed()
        self.pairedChanged.emit()
        self.settingsChanged.emit()
        self.logLevelChanged.emit()
        self.screenBrightnessChanged.emit()
        self.screenTimeoutChanged.emit()
        self.returnToNowChanged.emit()
        self.updateChannelChanged.emit()
        self.profileChanged.emit()
        self.djAnnouncementChanged.emit()
        if glass_changed:
            self.glassChanged.emit()
        if language_changed:
            self._invalidate_track_insight_language()
            self._translation_version += 1
            self.languageChanged.emit()
            self.translationsChanged.emit()

    @Slot()
    def startAfterPairing(self) -> None:
        self._pairing_success_visible = False
        self.pairingSuccessChanged.emit()
        self.refresh()

    @Slot()
    def togglePlay(self) -> None:
        if self._demo_mode:
            self.playback.is_playing = not self.playback.is_playing
            self.playingChanged.emit()
            self.djSession.demo_playback_changed()
            _LOGGER.info("User toggled demo playback to playing=%s", self.playback.is_playing)
            self.showToastForContext(self.tr_key("pause") if self.playback.is_playing else self.tr_key("play"), "playback")
            return
        _LOGGER.info("User requested playback toggle")
        self.showToastForContext(self.tr_key("pause") if self.playback.is_playing else self.tr_key("play"), "playback")
        self.command("pause" if self.playback.is_playing else "play")

    @Slot()
    def wakeDisplay(self) -> None:
        now = time.monotonic()
        if now - self._last_display_wake_at < DISPLAY_WAKE_DEBOUNCE_SECONDS:
            return
        self._last_display_wake_at = now
        for command in (
            ["xset", "s", "reset"],
            ["xset", "dpms", "force", "on"],
        ):
            try:
                subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                _LOGGER.debug("Could not run display wake command %s: %s", command, exc)

    @Slot(QObject, result=bool)
    def saveWindowScreenshot(self, window: QObject) -> bool:
        # ApplicationWindow.contentItem is a native Qt item without a QML
        # engine in Qt 6.11. Capture the actual window, including its overlays.
        if not isinstance(window, QQuickWindow):
            return False
        try:
            if not window.isVisible():
                return False
            image = window.grabWindow()
            if image.isNull():
                return False
            target = QSaveFile(self.cfg.screenshot_file)
            if not target.open(QIODevice.OpenModeFlag.WriteOnly):
                return False
            if not image.save(target, "PNG"):
                target.cancelWriting()
            return target.commit()
        except RuntimeError as exc:
            _LOGGER.warning("Could not capture debug screenshot: %s", exc)
            return False

    @Slot()
    def previous(self) -> None:
        self.wakeScreenRequested.emit()
        if self._demo_mode:
            self._move_demo_track(-1)
            _LOGGER.info("User selected previous demo track")
            self.showToastForContext(self.tr_key("previous"), "playback")
            return
        _LOGGER.info("User requested previous track")
        self.showToastForContext(self.tr_key("previous"), "playback")
        self.command("previous")

    @Slot()
    def next(self) -> None:
        self.wakeScreenRequested.emit()
        if self._demo_mode:
            self._move_demo_track(1)
            _LOGGER.info("User selected next demo track")
            self.showToastForContext(self.tr_key("next"), "playback")
            return
        _LOGGER.info("User requested next track")
        self.showToastForContext(self.tr_key("next"), "playback")
        self.command("next")

    @Slot()
    def saveCurrentTrack(self) -> None:
        if self._demo_mode:
            self._set_demo_favorite(True)
            return
        self._sync_config_from_disk()
        if self._demo_mode or not self.favoriteAvailable or self._favorite_busy:
            return
        _LOGGER.info("User requested saving the current track")
        self._set_favorite_busy(True)
        self._run("save_current_track", self._save_current_track_worker, done=lambda: self._set_favorite_busy(False))

    def _favorite_removal_context(self) -> tuple | None:
        if (not self._application_foreground or not self.favoriteRemovalAvailable
                or self._demo_mode or self._shutting_down):
            return None
        return self._playback_mode_context(), self.playback.uri.strip()

    @Slot()
    def removeCurrentTrackFavorite(self) -> None:
        if self._demo_mode:
            self._set_demo_favorite(False)
            return
        self._sync_config_from_disk()
        context = self._favorite_removal_context()
        if context is None or not self.playback.is_favorite or self._favorite_busy:
            return
        self._set_favorite_busy(True)
        result = ""

        def worker() -> None:
            nonlocal result
            result = self._remove_current_favorite_worker(context)

        self._run("set_current_track_favorite", worker, done=lambda: self._favoriteRemovalFinished.emit(context, result))

    def _remove_current_favorite_worker(self, context: tuple) -> str:
        if context != self._favorite_removal_context():
            return ""
        expected_uri = context[1]
        try:
            status = self.client.command("status")
            current = status.get("playback")
            if (status.get("success") is not True or not isinstance(current, dict)
                    or current.get("uri") != expected_uri or status.get("music_backend") != "spotify_direct"):
                return "unavailable"
            liked = favorite_observation(status, "spotify_direct")
            if context != self._favorite_removal_context():
                return ""
            if not liked:
                return "already_removed"
            response = self.client.command("set_current_track_favorite", value=False)
        except SessionError:
            return "unavailable"
        except requests.RequestException:
            return "uncertain"
        if response.get("success") is not True:
            return "unavailable"
        observed = response.get("playback")
        if (not isinstance(observed, dict) or observed.get("uri") != expected_uri
                or observed.get("favorite_status") is not False or observed.get("is_liked", False) is not False):
            return "uncertain"
        return "removed"

    @Slot(object, str)
    def _finish_favorite_removal(self, context: tuple, result: str) -> None:
        self._set_favorite_busy(False)
        if not result or context != self._favorite_removal_context():
            return
        if result in {"removed", "already_removed"}:
            if self.playback.is_favorite:
                self.playback.is_favorite = False
                self.favoriteChanged.emit()
            if result == "removed":
                self._show_toast(self.tr_key("favorite_removed"), 2200, "playback")
        else:
            key = "session_uncertain" if result == "uncertain" else "session_favorite_unavailable"
            self._show_toast(self.tr_key(key), 3500, "connectivity")

    def setApplicationForeground(self, foreground: bool) -> None:
        if foreground != self._application_foreground:
            self._application_foreground = foreground
            if not foreground:
                self.cancelPlaybackSeek()
                self.closeAskDjHelp()
                self._track_insight_revision += 1
                self._clear_track_insight()
        self._session_controller.setForeground(foreground)
        if foreground:
            self.controlVolumeChanged.emit()
        else:
            self.cancelControlVolume()

    @Slot()
    @Slot(bool)
    def openTrackInsight(self, explicit: bool = False) -> None:
        if not self._demo_mode and not self.playback.is_playing and not (explicit and self.hasTrackTitle):
            return
        self._request_track_insight(manual=False)

    @Slot()
    def refreshTrackInsight(self) -> None:
        self._request_track_insight(manual=True)

    def _request_track_insight(self, *, manual: bool) -> None:
        if self._demo_mode:
            self._show_demo_track_insight()
            return
        self._sync_config_from_disk()
        if self._demo_mode or not self.paired or self._ask_dj_busy or not self._application_foreground:
            return
        _LOGGER.info("User requested Track Insight for current track")
        scope = self._track_insight_scope()
        if self._track_insight_result_scope == scope and not manual:
            return
        if self._track_insight_result_scope is not None and self._track_insight_result_scope != scope:
            self._clear_track_insight()
        playback = deepcopy(self.playback)
        if manual:
            self.showToastForContext(self.tr_key("refreshing"), "trackinsight")
        self._track_insight_loading = True
        self._set_ask_dj_busy(True)
        self._run(self.tr_key("track_insight"), lambda: self._track_insight_worker(playback, scope, force_refresh=manual),
                  done=self._finish_track_insight_request)

    def _finish_track_insight_request(self) -> None:
        self._track_insight_loading = False
        self._set_ask_dj_busy(False)

    def _playback_seek_context(self, playback: Playback) -> tuple | None:
        if (not self.paired or not session_profile_id(self.cfg) or not self.musicBackendAvailable
                or self._pending_output_choice
                or not self.musicBackendSupportsSeek or not playback.uri or playback.duration_seconds <= 0
                or not output_id(playback.output_device_id)):
            return None
        return (self._private_read_context(), self.cfg.music_backend, self.cfg.music_backend_revision,
                playback.uri, playback.output_device_id)

    @Slot()
    def cancelPlaybackSeek(self) -> None:
        had_gesture = self._playback_seek_gesture is not None
        self._playback_seek_epoch += 1
        self._playback_seek_gesture = None
        if had_gesture:
            self.playbackSeekCancelled.emit()

    @Slot()
    def _invalidate_playback_seek(self) -> None:
        if self._playback_seek_gesture and self._playback_seek_gesture[1] != self._seek_gesture_context():
            self.cancelPlaybackSeek()

    def _seek_gesture_context(self):
        if self._demo_mode:
            return ("demo", self.playback.uri, self.playback.output_device) if self.playback.duration_seconds > 0 else None
        return self._playback_seek_context(self.playback)

    @Slot(result=int)
    def beginPlaybackSeek(self) -> int:
        context = self._seek_gesture_context()
        if self.busy or not self._session_controller._foreground or context is None:
            return 0
        self.cancelPlaybackSeek()
        self._playback_seek_gesture = (self._playback_seek_epoch, context)
        return self._playback_seek_epoch

    @Slot(int, int)
    def commitPlaybackSeek(self, lease: int, target_seconds: int) -> None:
        if not lease or self._playback_seek_gesture != (lease, self._seek_gesture_context()):
            return
        self.cancelPlaybackSeek()
        self._seek_playback(target_seconds=target_seconds)

    @Slot(int)
    def seekPlayback(self, offset_ms: int) -> None:
        if offset_ms in (-15000, 15000):
            self._seek_playback(offset_ms=offset_ms)

    def _seek_playback(self, *, offset_ms: int = 0, target_seconds: int | None = None) -> None:
        if self.playback.duration_seconds <= 0:
            return
        if self._demo_mode:
            position = self.playback.position_seconds + offset_ms // 1000 if target_seconds is None else target_seconds
            self.playback.position_seconds = max(0, min(self.playback.duration_seconds - 1, position))
            self.progressChanged.emit()
            self.djSession.demo_playback_changed()
            return
        context = self._playback_seek_context(self.playback)
        if self.busy or not self._session_controller._foreground or context is None:
            return
        transport = self._session_controller.transport
        try:
            binding = transport.binding()
        except SessionError:
            return
        self.cancelPlaybackSeek()
        epoch = self._playback_seek_epoch

        def current() -> bool:
            return (self._session_controller._foreground and epoch == self._playback_seek_epoch
                    and context == self._playback_seek_context(self.playback))

        def worker() -> None:
            if not current():
                return
            _, backend, revision, uri, output = context
            try:
                status = transport.playback_status(binding)
                observed = status.get("playback")
                device = observed.get("device") if isinstance(observed, dict) else None
                capabilities = status.get("music_backend_capabilities")
                if (not current() or status.get("music_backend") != backend
                        or type(status.get("music_backend_revision")) is not int or status["music_backend_revision"] != revision
                        or status.get("backend_available") is not True or status.get("music_backend_available") is not True
                        or not isinstance(capabilities, dict) or capabilities.get("supports_seek") is not True
                        or not isinstance(observed, dict) or observed.get("has_playback") is not True or observed.get("uri") != uri
                        or not isinstance(device, dict) or device.get("active") is not True
                        or device.get("is_restricted") is not False or device.get("id") != output):
                    return
                if target_seconds is None:
                    transport.playback_adjustment(binding, "seek_relative", offset_ms)
                else:
                    progress = observed.get("progress_ms")
                    duration = observed.get("duration_ms")
                    if type(progress) is not int or type(duration) is not int or not 0 <= progress <= duration or duration <= 0:
                        return
                    delta = max(0, min(duration, target_seconds * 1000)) - progress
                    if abs(delta) < 500:
                        return
                    transport.playback_seek_offset(binding, delta)
            except SessionError:
                return
            if current():
                self._refresh_worker()

        label = "session_playback_position" if target_seconds is not None else "seek_back_15" if offset_ms < 0 else "seek_forward_15"
        self._run(self.tr_key(label), worker)

    def _control_volume_context(self, playback: Playback) -> tuple | None:
        if self._demo_mode:
            return ("demo",)
        if (not self.paired or not session_profile_id(self.cfg)
                or self._pending_output_choice
                or (self._pending_output_device and time.monotonic() < self._pending_output_until)
                or self.cfg.music_backend not in {"spotify_direct", "music_assistant"}
                or self.cfg.music_backend_capabilities.get("supports_volume") is not True
                or not playback.uri or not output_id(playback.volume_output_id)):
            return None
        return (
            self._private_read_context(), self.cfg.music_backend, self.cfg.music_backend_revision,
            self.cfg.music_target_player.get("id", ""), playback.uri, playback.volume_output_id,
        )

    @Property(bool, notify=controlVolumeChanged)
    def controlVolumeKnown(self) -> bool:
        return self._control_volume_context(self.playback) is not None

    @Property(bool, notify=controlVolumeChanged)
    def controlVolumeAvailable(self) -> bool:
        return (self._session_controller._foreground and not self._control_volume_pending
                and self.controlVolumeKnown)

    @Slot(result=int)
    def beginControlVolume(self) -> int:
        context = self._control_volume_context(self.playback)
        if not self.controlVolumeAvailable or context is None:
            return 0
        self._control_volume_serial += 1
        self._control_volume_intent = (self._control_volume_serial, context)
        return self._control_volume_serial

    @Slot()
    def cancelControlVolume(self) -> None:
        self._control_volume_serial += 1
        self._control_volume_intent = None
        self.controlVolumeChanged.emit()

    @Slot(int, int)
    def commitControlVolume(self, lease: int, value: int) -> None:
        intent = self._control_volume_intent
        if intent is None or lease != intent[0]:
            return
        self._control_volume_intent = None
        context = intent[1]
        value = max(0, min(60, value))
        if (not self.controlVolumeAvailable or context != self._control_volume_context(self.playback)
                or value == self.playback.volume):
            return
        if self._demo_mode:
            self.setVolume(value)
            return
        transport = self._session_controller.transport
        try:
            binding = transport.binding()
        except SessionError:
            return
        self._control_volume_pending = True
        self.controlVolumeChanged.emit()

        def current() -> bool:
            return (self._session_controller._foreground and lease == self._control_volume_serial
                    and context == self._control_volume_context(self.playback))

        def worker() -> None:
            if not current():
                return
            _, backend, revision, player, uri, output = context
            try:
                status = transport.playback_status(binding)
                observed = status.get("playback")
                target = status.get("music_target_player")
                if (not current() or status.get("music_backend") != backend
                        or type(status.get("music_backend_revision")) is not int
                        or status["music_backend_revision"] != revision
                        or status.get("backend_available") is not True
                        or status.get("music_backend_available") is not True
                        or not isinstance(observed, dict) or observed.get("has_playback") is not True
                        or observed.get("uri") != uri
                        or (target.get("id", "") if isinstance(target, dict) else "") != player
                        or volume_observation(status, backend)["output_id"] != output):
                    return
                if current():
                    transport.playback_volume(binding, value, backend)
            except SessionError:
                return  # Never repeat a volume command with an uncertain outcome.
            if current():
                self._refresh_worker()

        self._run(self.tr_key("vol"), worker, done=self._controlVolumeFinished.emit)

    @Slot()
    def _finish_control_volume(self) -> None:
        self._control_volume_pending = False
        self.controlVolumeChanged.emit()

    @Slot(int)
    def setVolume(self, value: int) -> None:
        value = max(0, min(60, int(value)))
        if value == self.playback.volume:
            return
        self.playback.volume = value
        self.volumeChanged.emit()
        _LOGGER.info("User set volume to %s", value)
        self.showToastForContext(f"{self.tr_key('vol')} {value}", "playback")
        if self._demo_mode:
            return
        self.command("set_volume", value=value)

    @Slot(int)
    def adjustVolume(self, delta: int) -> None:
        self.setVolume(self.playback.volume + int(delta))

    @Slot()
    def toggleShuffle(self) -> None:
        self._request_playback_mode("shuffle", not self.playback.shuffle)

    @Slot()
    def cycleRepeat(self) -> None:
        self.setPlaybackRepeatMode(REPEAT_NEXT.get(self.playback.repeat, "off"))

    @Slot(str)
    def setPlaybackRepeatMode(self, value: str) -> None:
        if value not in REPEAT_NEXT:
            return
        self._request_playback_mode("repeat", value)

    def _playback_mode_context(self) -> tuple:
        return (self._private_read_context(), self.cfg.music_backend, self.cfg.music_backend_revision,
                self.cfg.music_target_player.get("id", ""), self.playback.output_device_id,
                self.playback.output_device, self._demo_mode)

    def _request_playback_mode(self, mode: str, value: bool | str) -> None:
        context = self._playback_mode_context()
        if self._demo_mode:
            self._apply_playback_mode(mode, value)
            return
        if self.busy or self.playbackModePending or not self.paired:
            return

        request = (context, mode, value)
        self._set_pending_playback_mode(request)
        outcome = None

        def worker() -> None:
            nonlocal outcome
            outcome = self._playback_mode_worker(request)

        self._run(self.tr_key(mode), worker, done=lambda: self._playbackModeFinished.emit(request, outcome))

    def _set_pending_playback_mode(self, request: tuple[tuple, str, bool | str] | None) -> None:
        self._pending_playback_mode = request
        self.playbackModePendingChanged.emit()
        self.shuffleChanged.emit()
        self.repeatChanged.emit()

    @Slot()
    def _invalidate_playback_mode(self) -> None:
        if self._pending_playback_mode and self._pending_playback_mode[0] != self._playback_mode_context():
            self._set_pending_playback_mode(None)

    def _playback_mode_worker(self, request: tuple[tuple, str, bool | str]) -> str | None:
        context, mode, value = request
        if self._pending_playback_mode is not request or context != self._playback_mode_context():
            return None
        data = self.client.command(f"set_{mode}", value=value)
        self._persist_backend_summary()
        if context != self._playback_mode_context():
            return None
        if data.get("success") is False or data.get("backend_available") is False:
            return "rejected"
        if not self._playback_mode_matches(data, mode, value):
            # A provider can acknowledge the command before its playback state propagates.
            time.sleep(0.35)
            if self._pending_playback_mode is not request or context != self._playback_mode_context():
                return None
            data = self.client.command("status")
            self._persist_backend_summary()
        if (data.get("success") is not False and data.get("backend_available") is not False
                and self._playback_mode_matches(data, mode, value)):
            return "confirmed"
        return None

    @staticmethod
    def _playback_mode_matches(data: dict, mode: str, value: bool | str) -> bool:
        playback = data.get("playback")
        snapshot = playback if isinstance(playback, dict) else data
        observed = snapshot.get("repeat_state", snapshot.get(mode)) if mode == "repeat" else snapshot.get(mode)
        return type(observed) is type(value) and observed == value

    @Slot(object, object)
    def _finish_playback_mode(self, request: tuple[tuple, str, bool | str], outcome: str | None) -> None:
        if self._shutting_down or self._pending_playback_mode is not request:
            return
        context, mode, value = request
        if context == self._playback_mode_context():
            if outcome == "confirmed":
                self._apply_playback_mode(mode, value)
            elif outcome == "rejected":
                self.showToastForContext(self.tr_key("command_failed"), "connectivity")
        self._set_pending_playback_mode(None)

    def _apply_playback_mode(self, mode: str, value: bool | str) -> None:
        if mode == "shuffle" and isinstance(value, bool):
            self.playback.shuffle = value
            self.shuffleChanged.emit()
        elif mode == "repeat" and isinstance(value, str):
            self.playback.repeat = value
            self.repeatChanged.emit()
        self.showToastForContext(self.tr_key(mode), "playback")

    @Slot(str)
    def setOutputDevice(self, value: str) -> None:
        value = value.strip()
        if not value:
            self.playback.output_device = ""
            self.cancelPlaybackSeek()
            self.outputDeviceChanged.emit()
            _LOGGER.info("User cleared output device selection from touch UI")
            self.showToastForContext(self.tr_key("none"), "playback")
            return
        self._select_output(value)

    @Slot()
    @Slot(bool)
    def refreshControlOutputs(self, manual: bool = True) -> None:
        if self._demo_mode:
            if manual:
                self.showToastForContext(self.tr_key("session_output_updated"), "playback")
            return
        if (not self.paired or not self.backendAvailable or self._pending_output_choice
                or self._control_outputs_request is not None):
            return
        context = self._private_read_context()
        self._control_outputs_request = context
        self.outputDeviceChanged.emit()
        observed = None

        def worker() -> None:
            nonlocal observed
            if self._control_outputs_request is not context or context != self._private_read_context():
                return
            result = self.client.command("devices")
            if result.get("success") is False:
                return
            playback = result.get("playback")
            containers = (result, playback) if isinstance(playback, dict) else (result,)
            rows = next((container[key] for container in containers
                         for key in ("output_devices", "devices", "available_devices", "outputs")
                         if isinstance(container.get(key), (list, dict))), None)
            if rows is not None:
                observed = self.client.playback_from_status({"output_devices": rows})

        self._run(self.tr_key("session_output_refresh"), worker,
                  done=lambda: self._controlOutputsFinished.emit(context, observed, manual), interactive=False)

    @Slot(object, object, bool)
    def _finish_control_outputs(self, context, observed: Playback | None, manual: bool) -> None:
        if self._control_outputs_request is not context:
            return
        self._control_outputs_request = None
        if context == self._private_read_context():
            self._control_outputs_status = "ready" if observed is not None else "unavailable"
            if observed is not None:
                self.playback.output_devices = observed.output_devices
                self.playback.output_device_details = observed.output_device_details
            if manual:
                key = "session_output_updated" if observed is not None else "session_output_unavailable"
                self.showToastForContext(self.tr_key(key), "playback")
        self.outputDeviceChanged.emit()

    @Slot(str)
    def setOutputChoice(self, output_value: str) -> None:
        matches = [choice for choice in self.outputDeviceChoices if choice.get("value") == output_value]
        if not output_id(output_value) or len(matches) != 1 or not matches[0].get("name"):
            return
        name = matches[0]["name"]
        if self._demo_mode:
            self._select_output(name)
            return
        if (not self.paired or not self.backendAvailable or not self._session_controller._foreground
                or self._pending_output_choice or self.controlOutputsRefreshing
                or output_value == self.playback.output_device_id):
            return
        context = self._private_read_context()
        request = (output_value, name, context)
        self._pending_output_choice = request
        self._unconfirmed_output_choice = ""
        confirmed = False
        self.cancelControlVolume()
        self.cancelPlaybackSeek()
        self.outputDeviceChanged.emit()

        def worker() -> None:
            nonlocal confirmed
            if (not self._session_controller._foreground or self._pending_output_choice is not request
                    or context != self._private_read_context()):
                return
            result = self.client.command("set_output", value=output_value)
            if result.get("success") is False:
                return
            if self._pending_output_choice is request and context == self._private_read_context():
                confirmed = self._refresh_worker(ticket=context, expected_output=output_value) is True

        self._run(self.tr_key("output_device"), worker, done=lambda: self._outputChoiceFinished.emit(request, confirmed))

    @Slot(object, bool)
    def _finish_output_choice(self, request, confirmed: bool) -> None:
        if self._pending_output_choice is request:
            self._pending_output_choice = None
            if not confirmed and request[2] == self._private_read_context():
                self._unconfirmed_output_choice = request[0]
            self.controlVolumeChanged.emit()
            self.outputDeviceChanged.emit()

    def _select_output(self, value: str) -> None:
        previous = self.playback.output_device
        self.playback.output_device = value
        self._pending_output_device = value
        self._pending_output_until = time.monotonic() + 20
        self.cancelControlVolume()
        self.cancelPlaybackSeek()
        self.outputDeviceChanged.emit()
        _LOGGER.info("User selected output device: %s", value)
        self.showToastForContext(value, "playback")
        if self._demo_mode:
            return
        self._run("set_output", lambda: self._set_output_worker(value, previous))

    @Slot()
    def enterDemoMode(self) -> None:
        if self.paired or self._demo_mode:
            return
        self._demo_mode = True
        self._queue_items = demo_queue_items()
        self._playlist_items = demo_playlist_items()
        self.playback.output_device = ""
        self.playback.output_devices = ("Keuken", "Woonkamer")
        self.demoModeChanged.emit()
        self.mediaListsChanged.emit()
        self.outputDeviceChanged.emit()
        self._apply_demo_track(self._queue_items[0])
        self._demo_progress.start()
        self._seed_demo_content()
        _LOGGER.info("User entered local demo mode")
        self.showToastForContext(self.tr_key("demo_active"), "playback")
        self._set_status_text(self.tr_key("demo_active"))

    @Slot()
    def exitDemoMode(self) -> None:
        if not self._demo_mode:
            return
        self._demo_mode = False
        self._demo_favorite_uris.clear()
        self._demo_progress.stop()
        self._clear_profile_scoped_caches(preserve_session=True)
        self._queue_items = []
        self._playlist_items = []
        self.demoModeChanged.emit()
        self.mediaListsChanged.emit()
        self.playback = Playback()
        self.favoriteChanged.emit()
        self.titleChanged.emit()
        self.artistChanged.emit()
        self.albumChanged.emit()
        self.imageUrlChanged.emit()
        self.playingChanged.emit()
        self.volumeChanged.emit()
        self.shuffleChanged.emit()
        self.repeatChanged.emit()
        self.progressChanged.emit()
        self.outputDeviceChanged.emit()
        _LOGGER.info("User exited local demo mode")
        self.showToastForContext(self.tr_key("exit_demo"), "connectivity")
        self._set_status_text(self.tr_key("ready_to_pair"))

    @Slot()
    def quitApp(self) -> None:
        _LOGGER.info("Quit requested from touch UI")
        app = QCoreApplication.instance()
        if app is not None:
            app.quit()

    @Slot()
    def clearDjResponse(self) -> None:
        if not self._dj_response_visible and not self._dj_response_text:
            return
        self._dj_response_text = ""
        self._dj_response_visible = False
        self.djResponseChanged.emit()

    @Slot()
    def manualRefresh(self) -> None:
        _LOGGER.info("User requested manual refresh")
        self.showToastForContext(self.tr_key("refreshing"), "playback")
        self._pending_output_device = ""
        self._pending_output_until = 0.0
        self.refresh()

    @Slot()
    def loadAskDjHistory(self) -> None:
        self._load_ask_dj_history(show_toast=False, action="opened")

    @Slot()
    def refreshAskDjHistory(self) -> None:
        self._load_ask_dj_history(show_toast=True, action="refreshed")

    def _ask_dj_help_context(self, surface):
        cfg = self.cfg
        if not self._application_foreground or self._shutting_down:
            return None
        if self._demo_mode:
            session_id = ""
            if surface == "active_session":
                header = self._session_controller.runtimeHeader
                if header["runtime_state"] != "active" or not header["session_id"]:
                    return None
                session_id = header["session_id"]
            return (self._private_read_context(), "", frozenset(), session_id, True)
        if (not self.paired or cfg.private_session or not self._backend_available
                or self._version_mismatch_visible or cfg.music_backend_available is not True):
            return None
        subject = self._session_controller.ask_dj_help_subject() if surface == "active_session" else None
        if surface == "active_session" and subject is None:
            return None
        return (self._private_read_context(), cfg.music_backend,
                frozenset(key for key, value in (cfg.music_backend_capabilities or {}).items() if value is True),
                subject, False)

    def _ask_dj_help_is_current(self, request):
        return (request is not None and self._ask_dj_help_request is request
                and elapsed_time() < request[1] and request[0] == self._ask_dj_help_context(request[2]))

    def _invalidate_ask_dj_help_if_changed(self):
        if self._ask_dj_help_request is not None and not self._ask_dj_help_is_current(self._ask_dj_help_request):
            self.closeAskDjHelp()

    @Slot()
    def openAskDjHelp(self) -> None:
        self._read_ask_dj_help("ask_dj")

    @Slot()
    def openSessionAskDjHelp(self) -> None:
        self._read_ask_dj_help("active_session")

    @Slot(str, int)
    def selectAskDjHelp(self, entry_id, edit_revision):
        if self._ask_dj_help_busy or self._ask_dj_busy:
            return
        for section in self.askDjHelpSections:
            for entry in section["examples"]:
                if entry["id"] == entry_id:
                    context, _, surface = self._ask_dj_help_request
                    self._read_ask_dj_help(surface, (edit_revision, entry, ()), context)
                    return

    @Slot(str, int)
    def selectAskDjSuggestion(self, entry_id, edit_revision):
        if self._ask_dj_help_busy or self._ask_dj_busy:
            return
        original = tuple(self.askDjSuggestions)
        if len(original) != 3:
            return
        for entry in original:
            if entry["id"] == entry_id:
                context, _, surface = self._ask_dj_help_request
                self._read_ask_dj_help(surface, (edit_revision, entry, original), context)
                return

    def _read_ask_dj_help(self, surface, selection=None, expected_context=None):
        self.closeAskDjHelp()
        if not self._demo_mode:
            self._sync_config_from_disk()
        context = self._ask_dj_help_context(surface)
        if context is None or (expected_context is not None and context != expected_context):
            return
        _, music_backend, supported_features, subject, demo = context
        if demo:
            request = (context, float("inf"), surface)
            self._ask_dj_help_request = request
            try:
                projection = demo_help(self.cfg.language, surface)
            except (OSError, ValueError):
                projection = ([], [])
            self._apply_ask_dj_help(request, projection, selection)
            return
        transport = self._session_controller.transport
        try:
            binding = transport.binding()
        except SessionError:
            return
        if not binding.resolved_profile_id:
            return
        request = (context, elapsed_time() + 15, surface)
        self._ask_dj_help_request = request
        self._ask_dj_help_busy = True
        self._ask_dj_help_timer.start(15_000)
        self.askDjHelpChanged.emit()
        session_id, _, track, session_features = subject or (None, None, (), frozenset())

        def read():
            projection = ([], [])
            try:
                if self._ask_dj_help_is_current(request):
                    capabilities = transport.capabilities(binding, isolated=True)
                    if compatible_catalogue(capabilities) and self._ask_dj_help_is_current(request):
                        observation = transport.ask_dj_help(binding, surface, session_id)
                        if observation.available and (observation.track_id is None or (track and track[0] == observation.track_id)):
                            features = session_features if observation.track_id is not None else (
                                session_features - {"current_track", "current_album", "forecast_ready"})
                            artist = track[3] if observation.track_id is not None else ""
                            projection = project_help(binding.language, surface, observation.eligible_ids,
                                                      music_backend, supported_features, features,
                                                      capabilities["contract_versions"], artist)
            except (SessionError, OSError, ValueError):
                pass
            self._askDjHelpReady.emit(request, projection, selection)

        self._executor.submit(read)

    @Slot(object, object, object)
    def _apply_ask_dj_help(self, request, projection, selection):
        if not self._ask_dj_help_is_current(request):
            return
        sections, candidates = projection
        self._ask_dj_help_busy = False
        self._ask_dj_help_sections = sections
        if selection is None:
            self._ask_dj_suggestions = candidates if request[0][-1] else select_suggestions(candidates, uuid4().hex)
        self.askDjHelpChanged.emit()
        if selection is not None and not self._ask_dj_busy and self._ask_dj_help_is_current(request):
            edit_revision, entry, original = selection
            if original and all(suggestion in candidates for suggestion in original):
                self.askDjSuggestionSelected.emit(entry["text"], edit_revision)
            elif not original and any(entry in section["examples"] for section in sections):
                self.askDjHelpSelected.emit(entry["text"], entry["placeholder"], edit_revision)

    @Slot()
    def closeAskDjHelp(self) -> None:
        if self._ask_dj_help_request is not None:
            self._ask_dj_help_timer.stop()
            self._ask_dj_help_request = None
            self._ask_dj_help_busy = False
            self._ask_dj_help_sections = []
            self._ask_dj_suggestions = []
            self.askDjHelpChanged.emit()

    @Slot(QUrl, int)
    def exportAskDjHistory(self, destination: QUrl, revision: int) -> None:
        self._export_profile_file(destination, revision, music_dna=False)

    @Slot(QUrl, int)
    def exportMusicDna(self, destination: QUrl, revision: int) -> None:
        self._export_profile_file(destination, revision, music_dna=True)

    def _export_profile_file(self, destination: QUrl, revision: int, *, music_dna: bool) -> None:
        ticket = self._private_read_context()
        self._sync_config_from_disk()
        if (revision != self._music_dna_context_revision or ticket != self._private_read_context()
                or not destination.isLocalFile() or not destination.toLocalFile()
                or not self.paired or self._demo_mode or not self.personalProfileExportAllowed
                or not self._backend_available or self._version_mismatch_visible
                or not self._application_foreground or self._shutting_down):
            return
        dna_key = self.cfg.music_dna_key
        if music_dna:
            if self._music_dna_busy or not self._music_dna_loaded or not self._music_dna_enabled or not dna_key:
                return
            read = self.client.music_dna_export
            set_busy = self._set_music_dna_busy
            prefix, context = "music_dna_export", "musicdna"
        else:
            if self._ask_dj_busy:
                return
            read = self.client.ask_dj_history_export
            set_busy = self._set_ask_dj_busy
            prefix, context = "ask_dj_export", "askdj"

        def current() -> bool:
            return (self._application_foreground and not self._shutting_down and not self._demo_mode
                    and ticket == self._private_read_context()
                    and (not music_dna or (self._music_dna_enabled and self.cfg.music_dna_key == dna_key)))

        outcome = "failed"

        def export() -> None:
            nonlocal outcome
            if not current():
                return
            try:
                data = read()
                if music_dna:
                    profile = data.get("profile")
                    if (data.get("success") is not True or data.get("format") != "djconnect.music_dna.export"
                            or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                            or not isinstance(profile, dict) or profile.get("enabled") is not True
                            or profile.get("music_dna_key") != dna_key or not isinstance(profile.get("profile"), dict)):
                        raise DJConnectError("invalid_music_dna_export")
            except (AuthenticationError, ProtocolVersionMismatch):
                if current():
                    raise
                return
            except (DJConnectError, requests.RequestException):
                if current():
                    outcome = "read_failed"
                    self._toastReady.emit(self.tr_key("profile_export_read_failed"), 3500, context)
                return
            if not current():
                return
            contents = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            target = QSaveFile(destination.toLocalFile())
            if target.open(QIODevice.OpenModeFlag.WriteOnly):
                if (target.setPermissions(QSaveFile.Permission.ReadOwner | QSaveFile.Permission.WriteOwner)
                        and target.write(contents) == len(contents) and current()):
                    if target.commit():
                        outcome = "saved"
                else:
                    target.cancelWriting()
            if current():
                self._toastReady.emit(self.tr_key(prefix + "_" + outcome), 3500, context)

        def finish() -> None:
            set_busy(False)
            self.profileExportProgress.emit(revision, music_dna, outcome if current() else "")

        set_busy(True)
        self.profileExportProgress.emit(revision, music_dna, "saving")
        self._run(self.tr_key(prefix), export, done=finish, interactive=False)

    @Slot()
    @Slot(int)
    def clearAskDjHistory(self, revision: int | None = None) -> None:
        if revision is not None and revision != self._music_dna_context_revision:
            return
        if self._demo_mode:
            self._clear_ask_dj_cache()
            return
        ticket = self._private_read_context()
        self._sync_config_from_disk()
        if (self._demo_mode or not self.paired or self._ask_dj_busy
                or not self._backend_available or self._version_mismatch_visible
                or ticket != self._private_read_context()
                or (revision is not None and revision != self._music_dna_context_revision)):
            return
        _LOGGER.info("User requested Ask DJ chat history clear")
        self._set_ask_dj_busy(True)
        self._run(self.tr_key("ask_dj_clear_chat"), lambda: self._clear_ask_dj_history_worker(ticket=ticket),
                  done=lambda: self._set_ask_dj_busy(False))

    def _load_ask_dj_history(self, *, show_toast: bool, action: str) -> None:
        self._sync_config_from_disk()
        if self._demo_mode or not self.paired or self._ask_dj_busy:
            return
        _LOGGER.info("User %s Ask DJ history", action)
        if show_toast:
            self.showToastForContext(self.tr_key("refreshing"), "askdj")
        ticket = self._private_read_context()
        request = self._ask_dj_context_request = object()
        self._ask_dj_destination_loading = True
        self._set_ask_dj_busy(True)

        def load_history():
            reading_destination = False
            try:
                reading_destination = self._load_ask_dj_history_worker(
                    ticket=ticket, show_toast=show_toast, context_request=request)
            finally:
                if not reading_destination:
                    self._askDjContextReady.emit(ticket, request, ("", [], ""))

        self._run(self.tr_key("ask_dj"), load_history, done=lambda: self._set_ask_dj_busy(False))

    @Slot(str, result=bool)
    @Slot(str, str, str, str, result=bool)
    def sendAskDjQuestion(self, text: str, reply_id="", session_id="", entry_id="") -> bool:
        return self._send_ask_dj_question(text, reply_id, session_id, entry_id, restore_draft=True)

    def _send_ask_dj_question(self, text, reply_id, session_id, entry_id, *, restore_draft, from_archive=False):
        text = text.strip()
        if (not text or len(text) > 4000 or self._ask_dj_busy or self._ask_dj_destination_loading
                or self.askDjDestinationUnavailable
                or not self._application_foreground or self._shutting_down):
            return False
        selected = {"session_id": session_id, "entry_id": entry_id} if reply_id or from_archive else None
        message_id = uuid4().hex
        if self._demo_mode:
            if selected is not None:
                return False
            self._merge_ask_dj_messages(parse_ask_dj_messages({"messages": [
                {"id": message_id, "role": "user", "text": text},
                {"id": message_id + "-demo", "role": "assistant", "text": self.tr_key("demo_dj_answer")},
            ]}))
            return True
        self._sync_config_from_disk()
        if not self.paired or self.cfg.private_session or not self._backend_available or self._version_mismatch_visible:
            return False
        def reference_current():
            if from_archive:
                return (self.canAskAboutSavedEntry(session_id, entry_id)
                        and (restore_draft or text == self._session_controller.saved_exploration_question(selected)))
            return selected is None or self.askDjContextEntry(reply_id) == selected

        if not reference_current():
            self.showToastForContext(self.tr_key("session_question_reference_unavailable"), "askdj")
            return False
        transport = self._session_controller.transport
        try:
            binding = transport.binding()
        except SessionError:
            return False
        ticket = self._private_read_context()
        page = self._ask_dj_context_page
        destination = page[1] if page and page[0] == ticket else None
        if destination is None:
            destination = self._session_controller.ask_dj_session_destination()
        question_context = {}
        if destination and not from_archive:
            question_context["active_session_id"] = destination
        if selected is not None:
            question_context["selected_entry"] = selected

        def current():
            return (not self._shutting_down and self._application_foreground and not self._demo_mode
                    and ticket == self._private_read_context() and binding == transport.binding())

        def restore_unsent():
            if restore_draft:
                self._askDjUnsentDraftReady.emit(ticket, text)

        def send():
            accepted = False
            try:
                if not current():
                    return
                owner = transport.private_scope_owner(binding)
                if not current():
                    return
                if not owner:
                    restore_unsent()
                    self._toastReady.emit(self.tr_key("session_profile_missing"), 5000, "askdj")
                    return
                if from_archive:
                    try:
                        active = active_session(transport.active(binding, isolated=True))
                    except SessionError:
                        restore_unsent()
                        self._askDjDeliveryReady.emit(ticket, "ask_dj_destination_unavailable")
                        return
                    if active:
                        restore_unsent()
                        self._askDjDeliveryReady.emit(ticket, "ask_dj_destination_changed")
                        return
                if not current():
                    return
                if not reference_current():
                    restore_unsent()
                    self._toastReady.emit(self.tr_key("session_question_reference_unavailable"), 5000, "askdj")
                    return
                transport.standalone_question(binding, text, message_id, owner, **question_context)
                accepted = True
                if not current():
                    return
                history = transport.standalone_history(binding, owner)
                if current():
                    self._askDjReady.emit(self._profile_read_result(history, ticket))
                    self._askDjDeliveryReady.emit(ticket, "")
            except SessionError as exc:
                if ticket == self._private_read_context() and self._application_foreground:
                    if not accepted and selected is not None and exc.category == "reference_unavailable":
                        restore_unsent()
                        self._askDjContextWithdrawn.emit(ticket, (selected["session_id"], selected["entry_id"]))
                        return
                    if not accepted and destination and exc.category == "session_changed":
                        restore_unsent()
                        self._askDjDeliveryReady.emit(ticket, "ask_dj_destination_changed")
                        return
                    key = "session_question_accepted_unavailable" if accepted else "session_question_uncertain"
                    self._askDjDeliveryReady.emit(ticket, key)

        def execute():
            try:
                send()
            finally:
                self._set_ask_dj_busy(False)

        self._set_ask_dj_busy(True)
        self._question_executor.submit(execute)
        return True

    @Slot(object, str)
    def _restore_unsent_ask_dj_draft(self, ticket, text: str) -> None:
        if (self._application_foreground and not self._shutting_down and not self._demo_mode
                and ticket == self._private_read_context()):
            self.askDjUnsentDraft.emit(text)

    @Slot(object, object)
    def _apply_ask_dj_context_withdrawal(self, ticket, reference):
        if (not self._application_foreground or self._shutting_down or self._demo_mode
                or ticket != self._private_read_context()):
            return
        self._ask_dj_withdrawn_entries.add(reference)
        self._apply_ask_dj_delivery(ticket, "session_question_reference_unavailable")

    @Property(str, notify=askDjChanged)
    def askDjDeliveryKey(self) -> str:
        return self._ask_dj_delivery_key

    @Slot(object, str)
    def _apply_ask_dj_delivery(self, ticket, key: str) -> None:
        if (not self._application_foreground or self._shutting_down or self._demo_mode
                or ticket != self._private_read_context()):
            return
        self._ask_dj_delivery_key = key
        self.askDjChanged.emit()
        if key:
            self._show_toast(self.tr_key(key), 5000, "askdj")

    @Slot(str)
    def sendAskDjAction(self, action_payload: str) -> None:
        self._sync_config_from_disk()
        if self._demo_mode or not self.paired or self._ask_dj_busy:
            return
        try:
            action = json.loads(action_payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Ignoring malformed Ask DJ action payload")
            return
        if not isinstance(action, dict):
            _LOGGER.warning("Ignoring non-object Ask DJ action payload")
            return
        _LOGGER.info("User triggered Ask DJ structured action kind=%s", action.get("kind") or action.get("action_style") or action.get("type") or "")
        self._set_ask_dj_busy(True)
        self._run(self.tr_key("ask_dj"), lambda: self._send_ask_dj_action_worker(action), done=lambda: self._set_ask_dj_busy(False))

    @Slot()
    def loadMusicDna(self) -> None:
        self._music_dna_request("profile", show_toast=False)

    @Slot()
    def refreshMusicDna(self) -> None:
        self._music_dna_request("profile", show_toast=True)

    def _music_dna_import_current(self, request: dict) -> bool:
        return (self._music_dna_import is request and self._application_foreground and not self._shutting_down
                and not self._demo_mode and self._music_dna_enabled
                and request["ticket"] == self._private_read_context() and request["key"] == self.cfg.music_dna_key)

    @Slot(QUrl, int)
    def prepareMusicDnaImport(self, source: QUrl, revision: int) -> None:
        ticket = self._private_read_context()
        self._sync_config_from_disk()
        if (revision != self._music_dna_context_revision or ticket != self._private_read_context()
                or not source.isLocalFile() or not source.toLocalFile()
                or not self.paired or self._demo_mode or not self.personalProfileExportAllowed
                or not self.musicDnaLoaded or not self.musicDnaEnabled or not self.cfg.music_dna_key
                or not self._backend_available or self._version_mismatch_visible or self._music_dna_busy
                or not self._application_foreground or self._shutting_down):
            return
        request = {"ticket": ticket, "key": self.cfg.music_dna_key, "preview": {}}
        self._music_dna_import = request
        self._set_music_dna_busy(True)

        def prepare() -> None:
            if not self._music_dna_import_current(request):
                return
            try:
                payload, preview = _read_music_dna_import(Path(source.toLocalFile()))
            except (OSError, ValueError, RecursionError):
                payload, preview = None, {}
            self._musicDnaImportPrepared.emit(request, payload, preview)

        self._run(self.tr_key("music_dna_import"), prepare,
                  done=lambda: self._set_music_dna_busy(False), interactive=False)

    @Slot(object, object, object)
    def _apply_music_dna_import(self, request: dict, payload: dict | None, preview: dict) -> None:
        if not self._music_dna_import_current(request):
            return
        if payload is None:
            self.cancelMusicDnaImport()
            self._show_toast(self.tr_key("music_dna_import_invalid"), 3500, "musicdna")
            return
        request["payload"] = payload
        request["preview"] = preview
        self.musicDnaChanged.emit()

    @Slot()
    def cancelMusicDnaImport(self) -> None:
        if self._music_dna_import is not None:
            self._music_dna_import = None
            self.musicDnaChanged.emit()

    @Slot()
    def confirmMusicDnaImport(self) -> None:
        self._sync_config_from_disk()
        request = self._music_dna_import
        if (not request or not self._music_dna_import_current(request) or self._music_dna_busy
                or not self.personalProfileExportAllowed or not self._backend_available or self._version_mismatch_visible
                or "payload" not in request):
            return
        payload = request.pop("payload")
        request["uploading"] = True
        config = deepcopy(self.cfg)
        config.explicit_profile_id = session_profile_id(config)
        client = HAClient(config)
        self._set_music_dna_busy(True)

        def upload() -> None:
            data = None
            notice = "music_dna_import_uncertain"
            try:
                if not self._music_dna_import_current(request):
                    return
                response = client.music_dna_import(payload)
                if (response.get("success") is True and response.get("enabled") is True
                        and response.get("music_dna_key") == request["key"] and isinstance(response.get("profile"), dict)):
                    data = response
                elif response.get("success") is False:
                    notice = "music_dna_import_failed"
            except (AuthenticationError, ProfileContextError):
                notice = "music_dna_import_failed"
            except (requests.RequestException, DJConnectError):
                pass
            finally:
                self._musicDnaImportFinished.emit(request, data, notice)

        self._run(self.tr_key("music_dna_import"), upload,
                  done=lambda: self._set_music_dna_busy(False), interactive=False)

    @Slot(object, object, str)
    def _finish_music_dna_import(self, request: dict, data: dict | None, notice: str) -> None:
        if not self._music_dna_import_current(request):
            return
        request["uploading"] = False
        if data is not None:
            request["saved"] = True
            self._apply_music_dna_data(self._profile_read_result(data, request["ticket"]))
            return
        request["failure"] = notice
        self.musicDnaChanged.emit()

    @Slot(bool)
    @Slot(bool, int)
    def setMusicDnaEnabled(self, enabled: bool, revision: int | None = None) -> None:
        self._music_dna_request("settings", revision=revision, enabled=bool(enabled))

    @Slot()
    @Slot(int)
    def clearMusicDna(self, revision: int | None = None) -> None:
        self._music_dna_request("clear", revision=revision)

    def _music_dna_request(
        self, action: str, show_toast: bool = False, revision: int | None = None, **payload: object,
    ) -> None:
        if revision is not None and revision != self._music_dna_context_revision:
            return
        if self._demo_mode:
            if action == "settings":
                self._set_demo_music_dna(bool(payload["enabled"]))
            elif action == "clear":
                self._set_demo_music_dna(False)
            return
        confirmation_context = self._private_read_context() if revision is not None else None
        self._sync_config_from_disk()
        if self._demo_mode or not self.paired or self._music_dna_busy:
            return
        ticket = self._private_read_context()
        if revision is not None and (
            revision != self._music_dna_context_revision or confirmation_context != ticket
        ):
            return
        self._set_music_dna_busy(True)
        if show_toast:
            self.showToastForContext(self.tr_key("refreshing"), "musicdna")
        if action == "settings":
            def worker() -> None:
                self._music_dna_settings_worker(ticket=ticket, **payload)
        elif action == "clear":
            def worker() -> None:
                self._music_dna_clear_worker(ticket=ticket)
        else:
            def worker() -> None:
                self._music_dna_profile_worker(ticket=ticket, manual=show_toast)
        _LOGGER.info("User requested Music DNA %s", action)
        self._run(self.tr_key("music_dna"), worker, done=lambda: self._set_music_dna_busy(False))

    @Slot()
    def loadMusicDiscovery(self) -> None:
        self._music_discovery_request("feed")

    @Slot()
    def openMusicDiscovery(self) -> None:
        self._music_discovery_request("open")

    @Slot()
    def refreshMusicDiscovery(self) -> None:
        if not self._demo_mode and self._music_dna_blocks_discover():
            self.loadMusicDna()
            return
        self._music_discovery_request("refresh")

    @Slot()
    @Slot(int)
    def acceptMusicDiscoveryConsent(self, revision: int | None = None) -> None:
        self._music_discovery_request("accept", revision=revision)

    @Slot()
    def rejectMusicDiscoveryConsent(self) -> None:
        self._music_discovery_consent_rejected = True
        self._music_discovery_error = ""
        self._music_discovery_empty_text = self.tr_key("music_discovery_requires_music_dna")
        self.musicDiscoveryChanged.emit()

    @Slot(str)
    def playMusicDiscoveryItem(self, payload: str) -> None:
        ticket = self._private_read_context()
        if not self._demo_mode:
            self._sync_config_from_disk()
            if ticket != self._private_read_context():
                return
        if ((not self._demo_mode and (not self.paired or self._music_dna_blocks_discover()))
                or self._music_discovery_busy or self._music_discovery_playing):
            return
        try:
            item = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Ignoring malformed Music Discovery play payload")
            return
        if not isinstance(item, dict):
            return
        if not str(item.get("discovery_item_id") or item.get("id") or "").strip():
            _LOGGER.info("Ignoring Music Discovery item without backend id")
            return
        item_id = str(item.get("discovery_item_id") or item.get("id") or "").strip()
        section_id = str(item.get("section_id") or "").strip()
        if self._demo_mode:
            self._play_demo_uri(item_id)
            self.musicDiscoveryPlayed.emit(section_id, item_id)
            return
        self._music_discovery_error = ""
        playing = (section_id, item_id)
        consent_epoch = self._music_discovery_consent_epoch
        self._music_discovery_playing = playing
        self.musicDiscoveryChanged.emit()
        self._run(
            self.tr_key("music_discovery"),
            lambda: self._music_discovery_play_worker(item, ticket=ticket, consent_epoch=consent_epoch),
            done=lambda: self._clear_music_discovery_playing(playing),
        )

    @Slot(str, str)
    def sendMusicDiscoveryFeedback(self, payload: str, feedback: str) -> None:
        ticket = self._private_read_context()
        self._sync_config_from_disk()
        if (self._demo_mode or not self.paired or self._music_discovery_busy or self._music_dna_blocks_discover()
                or not self._music_discovery_feedback_supported or ticket != self._private_read_context()):
            return
        feedback_value = str(feedback or "").strip()
        if feedback_value not in {"not_for_me", "less_like_this", "hide_artist"}:
            return
        try:
            item = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Ignoring malformed Music Discovery feedback payload")
            return
        if not isinstance(item, dict):
            return
        if not str(item.get("discovery_item_id") or item.get("id") or "").strip():
            return
        self._music_discovery_error = ""
        consent_epoch = self._music_discovery_consent_epoch
        self._set_music_discovery_busy(True)
        self._run(
            self.tr_key("music_discovery"),
            lambda: self._music_discovery_feedback_worker(item, feedback_value, ticket=ticket, consent_epoch=consent_epoch),
            done=lambda: self._set_music_discovery_busy(False),
        )

    def _music_discovery_request(self, action: str, *, revision: int | None = None) -> None:
        if revision is not None and revision != self._music_dna_context_revision:
            return
        if self._demo_mode:
            if action == "accept":
                self._set_demo_music_dna(True)
            return
        confirmation_context = self._private_read_context() if revision is not None else None
        self._sync_config_from_disk()
        if (self._demo_mode or not self.paired or self._music_discovery_busy
                or (action != "accept" and self._music_dna_blocks_discover())):
            return
        if action == "open" and self._music_discovery_feed_deadline is not None:
            now = self._session_controller._clock.now()
            if (self._backend_available and not self._music_discovery_error and self._music_discovery_items
                    and now < self._music_discovery_feed_deadline
                    and all(not item.get("imageExpiresAt") or item["imageExpiresAt"] > now * 1000
                            for item in self._music_discovery_items)):
                return
        ticket = self._private_read_context()
        consent_epoch = self._music_discovery_consent_epoch
        if revision is not None and (
            revision != self._music_dna_context_revision or confirmation_context != ticket
        ):
            return
        if action == "accept":
            self._music_discovery_consent_rejected = False
        self._set_music_discovery_busy(True)
        if action == "refresh":
            self.showToastForContext(self.tr_key("refreshing"), "discover")
        if action == "accept":
            def worker() -> None:
                self._music_discovery_accept_worker(ticket=ticket, consent_epoch=consent_epoch)
        else:
            manual = action == "refresh"

            def worker() -> None:
                self._music_discovery_feed_worker(ticket=ticket, manual=manual, consent_epoch=consent_epoch)
        _LOGGER.info("User requested Music Discovery %s", action)
        self._run(self.tr_key("music_discovery"), worker, done=lambda: self._set_music_discovery_busy(False))

    @Slot()
    def resetPairing(self) -> None:
        _LOGGER.info("User requested pairing reset from touch UI")
        self._forget_pairing()
        self.showToastForContext(self.tr_key("ready_to_pair"), "connectivity")
        self._set_status_text(self.tr_key("ready_to_pair"))

    @Property(bool, constant=True)
    def localTerminalAvailable(self) -> bool:
        return LOCAL_TERMINAL_UNIT.is_file()

    @Slot()
    def openLocalTerminal(self) -> None:
        if not self.localTerminalAvailable:
            return

        def start() -> None:
            try:
                subprocess.run(
                    ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", LOCAL_TERMINAL_UNIT.name],
                    check=True, timeout=10, capture_output=True, text=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                _LOGGER.warning("Could not start local terminal: %s", exc)
                self._toastReady.emit(self.tr_key("local_terminal_failed"), 5000, "diagnostics")

        self._run(self.tr_key("local_terminal"), start)

    @Slot()
    def rebootDevice(self) -> None:
        _LOGGER.info("User requested device reboot from touch UI")
        self._run_power_command(
            action="reboot",
            status_key="rebooting",
            failure_key="reboot_failed",
            commands=(
                ["sudo", "-n", "/usr/bin/systemctl", "reboot"],
                ["sudo", "-n", "/bin/systemctl", "reboot"],
                ["sudo", "-n", "systemctl", "reboot"],
                ["/usr/bin/systemctl", "reboot"],
                ["/bin/systemctl", "reboot"],
            ),
        )

    @Slot()
    def shutdownDevice(self) -> None:
        _LOGGER.info("User requested device shutdown from touch UI")
        self._run_power_command(
            action="shutdown",
            status_key="shutting_down",
            failure_key="shutdown_failed",
            commands=(
                ["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
                ["sudo", "-n", "/bin/systemctl", "poweroff"],
                ["sudo", "-n", "systemctl", "poweroff"],
                ["/usr/bin/systemctl", "poweroff"],
                ["/bin/systemctl", "poweroff"],
            ),
        )

    @Slot()
    def checkForUpdates(self) -> None:
        if self._update_check_running or self._shutting_down:
            return
        self._update_check_running = True
        self._update_check_status = ""
        self.updateCheckChanged.emit()
        _LOGGER.info("User requested update check from touch UI")
        self._set_status_text(self.tr_key("checking_updates"))
        self.showToastForContext(self.tr_key("checking_updates"), "updates")
        self._executor.submit(self._check_for_updates_worker)

    @Property(bool, notify=updateCheckChanged)
    def updateCheckRunning(self) -> bool:
        return self._update_check_running

    @Property(str, notify=updateCheckChanged)
    def updateCheckStatus(self) -> str:
        return self._update_check_status

    def _check_for_updates_worker(self) -> None:
        commands = (
            ["sudo", "-n", "/usr/bin/systemctl", "start", "djconnect-updater.service"],
            ["sudo", "-n", "/bin/systemctl", "start", "djconnect-updater.service"],
        )
        last_error = "unknown error"
        for command in commands:
            if self._shutting_down:
                return
            try:
                _LOGGER.info("Starting update check command: %s", " ".join(command))
                subprocess.run(command, check=True, timeout=8, capture_output=True, text=True)
                self._updateCheckFinished.emit("")
                return
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                last_error = detail or str(exc)
                _LOGGER.warning("Update check command failed: %s: %s", " ".join(command), last_error)
            except Exception as exc:  # noqa: BLE001 - native command boundary must surface every local failure to the UI
                last_error = str(exc)
                _LOGGER.warning("Update check command failed: %s: %s", " ".join(command), exc)
        self._updateCheckFinished.emit(last_error or "unknown error")

    @Slot(str)
    def _finish_update_check(self, error) -> None:
        if self._shutting_down:
            return
        self._update_check_running = False
        self._update_check_status = "update_check_start_failed" if error else "update_check_started"
        self.updateCheckChanged.emit()
        message = self.tr_key("update_check_failed", error=error) if error else self.tr_key("update_check_started")
        self._set_status_text(message)
        if error:
            self._show_toast(message, 5000, "updates")
        else:
            self.showToastForContext(message, "updates")

    def _run_power_command(self, *, action: str, status_key: str, failure_key: str, commands: tuple[list[str], ...]) -> None:
        self._set_status_text(self.tr_key(status_key))
        self.showToastForContext(self.tr_key(status_key), "updates")
        last_error: str = "unknown error"
        for command in commands:
            try:
                _LOGGER.info("Starting %s command: %s", action, " ".join(command))
                subprocess.run(command, check=True, timeout=5, capture_output=True, text=True)
                return
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                last_error = detail or str(exc)
                _LOGGER.warning("%s command failed: %s: %s", action.capitalize(), " ".join(command), last_error)
            except Exception as exc:  # noqa: BLE001 - native command boundary must surface every local failure to the UI
                last_error = str(exc)
                _LOGGER.warning("%s command failed: %s: %s", action.capitalize(), " ".join(command), exc)
        message = self.tr_key(failure_key, error=last_error)
        self._set_status_text(message)
        self._show_toast(message, 5000, "updates")

    @Slot(str, str)
    def playMediaItem(self, command: str, item: str) -> None:
        if self._media_playback_busy:
            return
        command = command.strip()
        payload = media_item_payload(command, item)
        if not command or not payload:
            return
        if command not in {"play_context_at", "start_playlist"}:
            _LOGGER.warning("Ignoring unsupported media item command from touch UI: %s", command)
            return
        starting_key = "media_playlist_starting" if command == "start_playlist" else "media_track_starting"
        self.showToastForContext(self.tr_key(starting_key), "media_start")
        if self._demo_mode:
            uri = payload["uri"] if command == "start_playlist" else payload["value"]["uri"]
            self._play_demo_uri(uri)
            return
        _LOGGER.info("User requested %s from touch UI", command)
        scope = self._media_playback_scope()
        self._media_playback_busy = True
        self.mediaActivityChanged.emit()
        self._run(command, lambda: self._play_media_item_worker(command, payload, scope=scope),
                  done=self._mediaPlaybackFinished.emit)

    def _media_playback_scope(self):
        return (self._private_read_context(), self.cfg.music_backend, self.cfg.music_backend_revision,
                self.cfg.music_target_player.get("id", ""))

    def _media_playback_current(self, scope):
        return (not self._demo_mode and not self._shutting_down and self._application_foreground
                and not self._version_mismatch_visible and (scope is None or scope == self._media_playback_scope()))

    @Slot(object, object, bool)
    def _apply_media_playback_observation(self, scope, playback, suppress_navigation):
        if self._media_playback_current(scope):
            self._suppress_next_playback_navigation = suppress_navigation
            if playback is not None:
                self._apply_playback(playback)

    @Slot()
    def _finish_media_playback(self) -> None:
        self._media_playback_busy = False
        self.mediaActivityChanged.emit()

    @Slot()
    def showLogs(self) -> None:
        if self.cfg.log_level == DEFAULT_LOG_LEVEL:
            return
        _LOGGER.info("User opened logs view")
        self._logs_visible = True
        self._read_logs()

    @Slot()
    def refreshLogs(self) -> None:
        if not self._logs_visible:
            return
        if self._read_logs():
            self.showToastForContext(self.tr_key("logs_refreshed"), "diagnostics")
        elif self._toast_icon == _toast_icon_for_context("diagnostics"):
            self.hideToast()

    def _read_logs(self) -> bool:
        path = Path(self.cfg.log_file)
        self._logs_read_error = ""
        read = False
        try:
            data = _read_tail_text(path, LOG_DISPLAY_MAX_BYTES)
            self._logs_text = _format_logs_for_display(data)
            read = True
        except FileNotFoundError:
            self._logs_text = ""
        except Exception as exc:  # noqa: BLE001 - log rendering must remain available when local diagnostics are unreadable
            self._logs_text = ""
            self._logs_read_error = str(exc)
        self.logsChanged.emit()
        return read

    @Slot()
    def copyLogs(self) -> None:
        _LOGGER.info("User copied logs from touch UI")
        QGuiApplication.clipboard().setText(self._logs_text)
        self.showToastForContext(self.tr_key("logs_copied"), "diagnostics")

    @Slot()
    def copyDeviceId(self) -> None:
        QGuiApplication.clipboard().setText(self.deviceId)
        self.showToastForContext(self.tr_key("device_id_copied"), "connectivity")

    @Slot()
    def copyPairingCode(self) -> None:
        if self.paired or self._demo_mode or not self.pairingCode:
            return
        QGuiApplication.clipboard().setText(self.pairingCode)
        self.showToastForContext(self.tr_key("copied"), "connectivity")

    @Slot()
    def copyActiveHaAddress(self) -> None:
        origin = self.aboutConnectionOrigin
        if not origin:
            return
        QGuiApplication.clipboard().setText(origin)
        self.showToastForContext(self.tr_key("copied"), "connectivity")

    @Slot()
    def clearLogs(self) -> None:
        _LOGGER.info("User cleared logs from touch UI")
        path = Path(self.cfg.log_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            self._logs_text = ""
            self._logs_read_error = ""
            self.showToastForContext(self.tr_key("logs_cleared"), "diagnostics")
        except Exception as exc:  # noqa: BLE001 - log clearing must report all local filesystem failures to the UI
            _LOGGER.warning("Could not clear the local log file: %s", exc)
            self.showToastForContext(self.tr_key("logs_clear_failed"), "diagnostics")
        self.logsChanged.emit()

    @Slot()
    def hideLogs(self) -> None:
        self._logs_visible = False
        self._logs_text = ""
        self._logs_read_error = ""
        self.logsChanged.emit()

    @Slot(str)
    def showToast(self, text: str) -> None:
        self._show_toast(text, 2000)

    @Slot(str, str)
    def showToastForContext(self, text: str, context: str) -> None:
        self._show_toast(text, 2000, context)

    @Property("QVariantMap", notify=gameHighScoresChanged)
    def gameHighScores(self) -> dict[str, int]:
        return self._game_high_scores.copy()

    @Slot(str, int)
    def recordGameHighScore(self, game: str, score: int) -> None:
        previous = self._game_high_scores.get(game)
        if previous is None or score <= previous:
            return
        self._game_high_scores[game] = score
        self._game_settings.setValue(f"highScores/{game}", score)
        self.gameHighScoresChanged.emit()

    @Slot(str)
    def playGameSound(self, kind: str) -> None:
        if not self.localAudioOutputAvailable:
            return
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except Exception as exc:  # noqa: BLE001 - optional multimedia support must never prevent the renderer from starting
            _LOGGER.debug("Game sound unavailable: %s", exc)
            return
        tones = {
            "start": (220, 90),
            "move": (180, 45),
            "fire": (760, 65),
            "pellet": (520, 45),
            "power": (300, 110),
            "ghost": (880, 90),
            "death": (90, 140),
            "hit": (430, 55),
            "wall": (260, 45),
            "explode": (120, 120),
            "crash": (70, 140),
            "gameover": (100, 120),
        }
        frequency, duration_ms = tones.get(kind.strip(), (240, 55))
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                return
            audio_format = QAudioFormat()
            audio_format.setSampleRate(GAME_SOUND_SAMPLE_RATE)
            audio_format.setChannelCount(1)
            audio_format.setSampleFormat(QAudioFormat.Int16)
            if not device.isFormatSupported(audio_format):
                audio_format = device.preferredFormat()
            data = _square_wave_pcm(frequency, duration_ms, audio_format.sampleRate())
            if not data:
                return
            buffer = QBuffer(self)
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.ReadOnly)
            sink = QAudioSink(device, audio_format, self)
            sink.setVolume(0.04)
            sink.start(buffer)
            self._game_sound_objects.append((sink, buffer))
            QTimer.singleShot(duration_ms + 250, lambda: self._cleanup_game_sound(sink, buffer))
        except Exception as exc:  # noqa: BLE001 - optional game audio must not affect the renderer lifecycle
            _LOGGER.debug("Game sound skipped: %s", exc)

    @Slot()
    def stopGameSounds(self) -> None:
        for sink, buffer in tuple(self._game_sound_objects):
            self._cleanup_game_sound(sink, buffer)

    def _cleanup_game_sound(self, sink: object, buffer: QBuffer) -> None:
        if (sink, buffer) not in self._game_sound_objects:
            return
        try:
            if hasattr(sink, "stop"):
                sink.stop()
            buffer.close()
        finally:
            self._game_sound_objects = [(s, b) for s, b in self._game_sound_objects if s is not sink and b is not buffer]

    def _show_toast(self, text: str, duration_ms: int = 2000, context: str = "") -> None:
        text = text.strip()
        if not text:
            return
        self._toast_text = text
        self._toast_icon = _toast_icon_for_context(context)
        self._toast_visible = True
        self.toastChanged.emit()
        self._toast_timer.setInterval(duration_ms)
        self._toast_timer.start()

    @Slot()
    def hideToast(self) -> None:
        if not self._toast_visible and not self._toast_text:
            return
        self._toast_visible = False
        self._toast_text = ""
        self._toast_icon = TOAST_ICON_FALLBACK
        self.toastChanged.emit()

    def command(self, command: str, **payload: object) -> None:
        if self._demo_mode:
            return
        self._run(command, lambda: self._command_worker(command, **payload))

    @Slot(str)
    def playUri(self, uri: str) -> None:
        uri = uri.strip()
        if not uri:
            return
        if self._demo_mode:
            self._play_demo_uri(uri)
            return
        _LOGGER.info("User requested play_uri from touch UI")
        self.showToastForContext(self.tr_key("play"), "playback")
        self.command("play_uri", uri=uri)

    @Slot()
    @Slot(bool)
    def loadQueue(self, manual: bool = False) -> None:
        if not self._demo_mode:
            self._start_media_list_load("queue", self._load_queue_worker, manual=manual)

    @Slot()
    @Slot(bool)
    def loadPlaylists(self, manual: bool = False) -> None:
        if self._demo_mode:
            self._playlist_items = demo_playlist_items()
            self.mediaListsChanged.emit()
            return
        self._start_media_list_load("playlists", self._load_playlists_worker, manual=manual)

    def _start_media_list_load(self, kind: str, worker: Callable[..., None], *, manual: bool = False) -> None:
        self._sync_config_from_disk()
        if not self.paired or self._version_mismatch_visible or kind in self._media_loads_in_flight:
            return
        self._media_loads_in_flight.add(kind)
        self._media_list_load_failures.discard(kind)
        self.mediaActivityChanged.emit()
        _LOGGER.info("User requested %s from touch UI", kind)
        context = self._private_read_context()
        failed = True

        def read() -> None:
            nonlocal failed
            if context != self._private_read_context():
                return
            key = f"{kind}_updated"
            try:
                worker(ticket=context)
                failed = False
            except (AuthenticationError, ProtocolVersionMismatch):
                if context == self._private_read_context():
                    raise
                return
            except (DJConnectError, requests.RequestException) as exc:
                if context != self._private_read_context():
                    return
                if not manual:
                    raise
                _LOGGER.debug("%s refresh failed: %s", kind, type(exc).__name__)
                key = f"{kind}_update_failed"
            if manual and context == self._private_read_context():
                self._toastReady.emit(self.tr_key(key), 2200, kind)

        self._run(self.tr_key(kind), read,
                  done=lambda: self._mediaListFinished.emit(kind, context, failed), interactive=False)

    @Slot(str, object, bool)
    def _finish_media_list_load(self, kind: str, ticket, failed: bool) -> None:
        self._media_loads_in_flight.discard(kind)
        if failed and not self._demo_mode and ticket == self._private_read_context():
            self._media_list_load_failures.add(kind)
        self.mediaActivityChanged.emit()

    @Slot(str, result=str)
    def cachedImageUrl(self, url: str) -> str:
        return cached_image_url(url)

    def _pair_worker(self, code: str) -> None:
        _LOGGER.info("Pairing DJConnect Pi client")
        self.client.pair(code)
        save_config(self.config_path, self.cfg)
        self._pairingReady.emit(True, self.tr_key("paired"))

    def _refresh_worker(self, *, ticket=None, expected_output: str = "") -> bool | None:
        if ticket is not None and ticket != self._private_read_context():
            return
        started = time.monotonic()
        about_started = elapsed_time()
        about_binding = self._private_read_context() if self.paired else None
        _LOGGER.debug("Refreshing playback status")
        data = self.client.command("status") if self.paired else self.client.status()
        if ticket is not None and ticket != self._private_read_context():
            return
        about_scope = self._about_music_scope()
        active_origin = ""
        status_via_websocket = isinstance(data, StatusObservation) and data.via_websocket
        music_available = data.get("music_backend_available") if data.get("success") is not False else None
        self._persist_backend_summary()
        playback = self.client.playback_from_status(data)
        if self._pending_output_device and time.monotonic() < self._pending_output_until:
            if not playback.output_devices or self._pending_output_device in playback.output_devices:
                playback.output_device = self._pending_output_device
        elif self._pending_output_device:
            self._pending_output_device = ""
            self._pending_output_until = 0.0
        if self.playback.output_device and not playback.output_device:
            playback.output_device = self.playback.output_device
        if self.paired and (not playback.output_devices or not playback.output_device):
            try:
                devices_data = self.client.command("devices")
                self._persist_backend_summary()
                devices_playback = self.client.playback_from_status(devices_data)
                if devices_playback.output_devices:
                    playback.output_devices = devices_playback.output_devices
                    playback.output_device_details = devices_playback.output_device_details
                    _LOGGER.debug("Loaded %s output devices from Home Assistant devices command", len(playback.output_devices))
                if devices_playback.output_device and not playback.output_device:
                    playback.output_device = devices_playback.output_device
                    _LOGGER.debug("Loaded active output device from Home Assistant devices command: %s", playback.output_device)
            except DJConnectError as exc:
                _LOGGER.warning("Output devices refresh failed: %s", exc)
        if self.paired:
            if ticket is not None and ticket != self._private_read_context():
                return
            status = self.client.status(playback, queue_items=self._queue_items)
            if isinstance(status, StatusObservation):
                active_origin = status.authenticated_origin
            self._persist_backend_summary()
        if playback.image_url:
            playback.image_url = cached_image_url(playback.image_url, timeout_seconds=3)
        if ticket is not None and ticket != self._private_read_context():
            return
        self._playbackReady.emit(playback)
        if about_binding is not None and about_binding == about_scope[0]:
            self._aboutConnectionReady.emit(about_binding, active_origin, about_started, status_via_websocket)
            self._aboutMusicAvailabilityReady.emit(about_scope, music_available, about_started)
        _LOGGER.info("Refresh completed in %.0fms", _elapsed_ms(started))
        if expected_output:
            return (data.get("success") is not False and data.get("backend_available") is not False
                    and playback.output_device_active and playback.output_device_id == expected_output)
        return None

    def _command_worker(self, command: str, **payload: object) -> None:
        started = time.monotonic()
        _LOGGER.info("Sending playback command: %s", command)
        data = self.client.command(command, **payload)
        self._persist_backend_summary()
        self._playbackReady.emit(self.client.playback_from_status(data))
        _LOGGER.info("Playback command %s completed in %.0fms", command, _elapsed_ms(started))

    def _save_current_track_worker(self) -> None:
        started = time.monotonic()
        try:
            data = self.client.command("save_current_track")
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch, BackendUnavailable):
            raise
        except DJConnectError as exc:
            raise SaveCurrentTrackError(str(exc)) from exc
        if data.get("success") is False:
            error = str(data.get("error") or data.get("message") or "save_current_track_failed")
            raise SaveCurrentTrackError(error)
        self._toastReady.emit(self.tr_key("favorite_saved"), 2200, "playback")
        if _contains_playback_payload(data):
            self._playbackReady.emit(self.client.playback_from_status(data))
        _LOGGER.info("Save current track completed in %.0fms", _elapsed_ms(started))

    def _play_media_item_worker(self, command: str, payload: dict[str, object], *, scope=None) -> None:
        if not self._media_playback_current(scope):
            return
        started = time.monotonic()
        _LOGGER.info("Sending media item command: %s", command)
        try:
            data = self.client.command(command, **payload)
        except DJConnectError:
            if self._media_playback_current(scope):
                raise
            return
        except requests.RequestException as exc:
            if not self._media_playback_current(scope):
                return
            _LOGGER.warning("Media item command %s was not confirmed: %s", command, type(exc).__name__)
            self._backendAvailableReady.emit(False)
            self._statusReady.emit(self.tr_key("backend_unavailable"))
            self._toastReady.emit(self.tr_key("session_uncertain"), 5000, "connectivity")
            return
        if not self._media_playback_current(scope):
            return
        success = data.get("success")
        if success is False:
            self._persist_backend_summary()
            failure_key = "media_playlist_start_failed" if command == "start_playlist" else "media_track_start_failed"
            self._toastReady.emit(self.tr_key(failure_key), 3500, "connectivity")
            _LOGGER.warning("Media item command %s was rejected", command)
            return
        has_playback = _contains_playback_payload(data)
        if success is not True and ("success" in data or not has_playback):
            self._toastReady.emit(self.tr_key("session_uncertain"), 5000, "connectivity")
            _LOGGER.warning("Media item command %s returned no confirmation", command)
            return
        self._persist_backend_summary()
        self._mediaPlaybackObserved.emit(scope, self.client.playback_from_status(data) if has_playback else None,
                                         command == "play_context_at")
        for read in (self._load_queue_worker, self._refresh_worker):
            if not self._media_playback_current(scope):
                return
            try:
                if scope is None:
                    read()
                else:
                    read(ticket=scope[0])
            except (AuthenticationError, ProtocolVersionMismatch):
                if self._media_playback_current(scope):
                    raise
                return
            except (DJConnectError, requests.RequestException) as exc:
                _LOGGER.debug("Media item readback failed: %s", type(exc).__name__)
        _LOGGER.info("Media item command %s completed in %.0fms", command, _elapsed_ms(started))

    def _set_output_worker(self, value: str, previous: str) -> None:
        started = time.monotonic()
        try:
            data = self.client.command("set_output", value=value)
            self._persist_backend_summary()
            playback = self.client.playback_from_status(data)
            if playback.output_devices and value not in playback.output_devices:
                raise DJConnectError(f"Output device not available: {value}")
            if playback.output_devices and value in playback.output_devices:
                playback.output_device = value
            elif playback.output_device and playback.output_device != value:
                raise DJConnectError(f"Output device not accepted: {value}")
            if not playback.output_device:
                playback.output_device = value
            if not playback.output_devices:
                playback.output_devices = self.playback.output_devices or ((value,) if value else ())
                playback.output_device_details = self.playback.output_device_details or (({"name": value, "value": value},) if value else ())
            self._pending_output_device = value
            self._pending_output_until = time.monotonic() + 20
            self._playbackReady.emit(playback)
            _LOGGER.info("Output device %s validated in %.0fms", value, _elapsed_ms(started))
        except Exception:
            self._outputDeviceRejected.emit(previous, value)
            if self._is_cached_output_device(value):
                self._toastReady.emit(self.tr_key("spotify_cached_output_unavailable"), 7000, "playback")
            raise

    def _load_queue_worker(self, *, ticket=None) -> None:
        if ticket is None:
            ticket = self._private_read_context()
        started = time.monotonic()
        _LOGGER.info("Loading queue from Home Assistant")
        # An earlier manual read must publish before the post-selection queue.
        with self._queue_read_lock:
            if ticket != self._private_read_context():
                return
            data = self.client.command("queue", limit=100)
            if ticket != self._private_read_context():
                return
            items = _parse_queue_snapshot(data) if data.get("success") is not False else None
            if items is None:
                raise DJConnectError(self.tr_key("queue_update_failed"))
            self._persist_backend_summary()
            _LOGGER.info("Loaded %s queue items from Home Assistant", len(items))
            self._mediaListReady.emit("queue", items, ticket)
        _LOGGER.info("Queue list displayed in %.0fms", _elapsed_ms(started))
        self._cache_media_artwork_async("queue", items)

    def _load_playlists_worker(self, *, ticket=None) -> None:
        if ticket is None:
            ticket = self._private_read_context()
        if ticket != self._private_read_context():
            return
        started = time.monotonic()
        _LOGGER.info("Loading playlists from Home Assistant")
        data = self.client.command("playlists", limit=100)
        if ticket != self._private_read_context():
            return
        items = _parse_playlist_snapshot(data) if data.get("success") is not False else None
        if items is None:
            raise DJConnectError(self.tr_key("playlists_update_failed"))
        self._persist_backend_summary()
        _LOGGER.info("Loaded %s playlists from Home Assistant", len(items))
        self._mediaListReady.emit("playlists", items, ticket)
        _LOGGER.info("Playlist list displayed in %.0fms", _elapsed_ms(started))
        self._cache_media_artwork_async("playlists", items)

    def _load_ask_dj_history_worker(self, *, ticket, show_toast: bool, context_request) -> bool:
        if ticket != self._private_read_context():
            return False
        try:
            data = self.client.ask_dj_history(self._ask_dj_history_revision)
            if ticket != self._private_read_context():
                return False
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            if ticket == self._private_read_context():
                raise
            return False
        except (DJConnectError, requests.RequestException) as exc:
            if ticket != self._private_read_context():
                return False
            if not show_toast:
                raise BackendUnavailable("Ask DJ unavailable") from exc
            data = {"success": False, "_transport_unavailable": True}
        ready = self._askDjRefreshReady if show_toast else self._askDjReady
        ready.emit(self._profile_read_result(data, ticket))
        owner = data.get("owner_profile_id")
        if data.get("history_scope") == "profile" and owner == self.cfg.active_profile_id and owner:
            self._session_executor.submit(self._read_ask_dj_context_rows, ticket, context_request, owner)
            return True
        return False

    def _read_ask_dj_context_rows(self, ticket, request, owner):
        def current():
            return (request is self._ask_dj_context_request and ticket == self._private_read_context()
                    and not self._shutting_down and self._application_foreground and self.personalProfileExportAllowed
                    and self.cfg.active_profile_privacy_mode == "private")

        session = self._session_controller
        transport = session.transport
        rows = []
        destination = None
        question = ""
        try:
            if not current():
                return
            binding = transport.binding()
            if binding.profile_id != owner:
                return
            caps = transport.capabilities(binding, isolated=True)
            if not current():
                return
            if session._supports_history(caps) and session._supports_contextual_dialogue(caps):
                active = active_session(transport.active(binding, isolated=True))
                if not active:
                    destination = ""
                    question = contribution_question(caps, binding.language)
                if (current() and active.get("owner_profile_id") == owner
                        and active.get("runtime_state") == "active"):
                    destination = active["session_id"]
                    question = contribution_question(caps, binding.language)
                    self._askDjContextReady.emit(ticket, request, (destination, [], question))
                    page = history_page(transport.timeline(binding, destination, isolated=True), session_id=destination,
                                        now=session._clock.now())
                    keys = ("session_id", "entry_id", "kind", "role", "origin_message_id", "retained_until", "state")
                    dialogue = conversation_entries(page)
                    rows = [{**{key: row[key] for key in keys if key in row},
                             "preview": retained_contribution_preview(row)}
                            for row in presented_history({"entries": dialogue + [
                                entry for entry in page["entries"] if entry.get("state") == "source_withdrawn"
                            ]})["entries"]
                            if row["kind"] == "conversation_dj"]
            else:
                destination = ""
        except SessionError:
            pass
        finally:
            self._askDjContextReady.emit(ticket, request, (destination, rows, question) if current() else (None, [], ""))

    @Slot(object, object, object)
    def _apply_ask_dj_context_rows(self, ticket, request, result):
        if request is not self._ask_dj_context_request:
            return
        self._ask_dj_destination_loading = False
        if ticket == self._private_read_context():
            self._ask_dj_context_page = (ticket, *result)
        self.askDjChanged.emit()

    @Slot(object)
    def _apply_ask_dj_refresh_result(self, data: object) -> None:
        if self._demo_mode or not self._profile_result_current(data):
            return
        if isinstance(data, dict) and data.get("success") is False and data.get("_transport_unavailable") is True:
            self._set_backend_available(False)
        refreshed = self._apply_ask_dj_data(data)
        if refreshed and self._ask_dj_delivery_key:
            self._apply_ask_dj_delivery(self._private_read_context(), "")
        key = "ask_dj_history_updated" if refreshed else "ask_dj_history_refresh_failed"
        self._show_toast(self.tr_key(key), 2500, "askdj")

    def _send_ask_dj_action_worker(self, action: dict[str, object]) -> None:
        ticket = self._private_read_context()
        action = dict(action)
        if not self.sharedSettingsManaged:
            action["dj_announcement_output"] = normalize_dj_announcement_output(
                self.cfg.dj_announcement_output,
                self.cfg.music_backend_capabilities,
            )
        try:
            data = self.client.ask_dj_action(action)
            if ticket != self._private_read_context():
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except (UnsupportedBackendCapability, StaleBackendAction):
            raise
        except (DJConnectError, requests.RequestException) as exc:
            raise BackendUnavailable("Ask DJ unavailable") from exc
        self._persist_backend_summary()
        self._askDjReady.emit(self._profile_read_result(data, ticket))

    def _clear_ask_dj_history_worker(self, *, ticket=None) -> None:
        current = self._private_read_context()
        if ticket is not None and ticket != current:
            return
        ticket = current
        try:
            data = self.client.ask_dj_history_clear()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except (DJConnectError, requests.RequestException) as exc:
            raise BackendUnavailable("Ask DJ clear unavailable") from exc
        if ticket != self._private_read_context():
            return
        self._persist_backend_summary()
        self._askDjClearReady.emit(self._profile_read_result(data, ticket))

    @Slot(object)
    def _apply_ask_dj_clear_result(self, data: object) -> None:
        if self._demo_mode or not isinstance(data, dict) or not self._profile_result_current(data):
            return
        cleared = data.get("cleared")
        revision = data.get("clear_revision")
        legacy_receipt = (
            cleared is None and type(revision) is int and revision > self._ask_dj_clear_revision
            and data.get("messages") == []
        )
        if data.get("success") is not True or not (cleared is True or legacy_receipt):
            self._show_toast(self.tr_key("ask_dj_clear_unconfirmed"), 5000, "askdj")
            return
        if self._apply_ask_dj_data(data) and self._profile_result_current(data):
            self._show_toast(self.tr_key("ask_dj_cleared"), 2500, "askdj")

    def _track_insight_worker(self, playback: Playback, scope: tuple, *, force_refresh: bool) -> None:
        if not self._application_foreground or scope != self._track_insight_scope():
            return
        proof = None
        try:
            current_mode = self._uses_current_track_insight()
            if self._needs_track_insight_capabilities():
                owner = self._session_controller
                transport = owner.transport
                snapshot = transport.track_insight_capabilities(transport.binding())
                if not self._application_foreground or scope != self._track_insight_scope():
                    return
                current_mode = owner.supports_current_track_insight(snapshot)
            if current_mode:
                if not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", playback.uri):
                    data = {"success": False, "error": "current_track_unavailable"}
                else:
                    clock = self._session_controller._clock
                    requested_at = clock.now()
                    data = self.client.current_track_insight(force_refresh=force_refresh)
                    if data.get("success") is False:
                        data = {"success": False, "error": data.get("error") or "current_track_unavailable"}
                    else:
                        proof = _current_track_insight_proof(data.get("subject"), playback.uri, requested_at, clock.now())
                        if proof is None:
                            data = {"success": False, "error": "track_insight_context_changed"}
            else:
                data = self.client.track_insight(playback, force_refresh=force_refresh)
            if scope != self._track_insight_scope():
                return
            self._apply_profile_context(data)
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except (DJConnectError, SessionError, requests.RequestException) as exc:
            _LOGGER.warning("Track Insight unavailable; showing retry state: %s", exc.__class__.__name__)
            data = {"success": False, "error": "transient"}
        self._trackInsightReady.emit(_TrackInsightResult(data, scope, proof, manual=force_refresh))

    def _music_dna_profile_worker(self, *, ticket=None, manual: bool = False) -> None:
        current = self._private_read_context()
        if ticket is not None and ticket != current:
            return
        ticket = current
        retryable = False
        try:
            data = self.client.music_dna_profile()
            if ticket != self._private_read_context():
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except ProfileContextError as exc:
            data = {"success": False, "error": exc.code}
        except (BackendUnavailable, requests.RequestException):
            data = {"success": False, "error": "music_dna_unavailable"}
            retryable = True
        except DJConnectError:
            data = {"success": False, "error": "music_dna_unavailable"}
        self._musicDnaReady.emit(_MusicDnaProfileResult(data, ticket, manual=manual, retryable=retryable))

    def _music_dna_settings_worker(self, *, ticket=None, **settings: object) -> None:
        current = self._private_read_context()
        if ticket is not None and ticket != current:
            return
        ticket = current
        try:
            data = self.client.music_dna_settings(**settings)
            if ticket != self._private_read_context():
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except ProfileContextError as exc:
            self._musicDnaReady.emit(self._profile_read_result({"success": False, "error": exc.code}, ticket))
            return
        except (DJConnectError, requests.RequestException):
            self._musicDnaReady.emit(self._profile_read_result({"success": False, "error": "music_dna_unavailable"}, ticket))
            return
        self._musicDnaReady.emit(self._profile_read_result(data, ticket))

    def _music_dna_clear_worker(self, *, ticket=None) -> None:
        current = self._private_read_context()
        if ticket is not None and ticket != current:
            return
        ticket = current
        try:
            data = self.client.music_dna_clear()
            if ticket != self._private_read_context():
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            raise
        except ProfileContextError as exc:
            self._musicDnaReady.emit(self._profile_read_result({"success": False, "error": exc.code}, ticket))
            return
        except (DJConnectError, requests.RequestException):
            self._musicDnaReady.emit(self._profile_read_result({"success": False, "error": "music_dna_unavailable"}, ticket))
            return
        self._musicDnaReady.emit(self._profile_read_result(data, ticket))

    def _music_discovery_feed_worker(self, *, ticket=None, manual: bool = False, consent_epoch=None) -> None:
        current = self._private_read_context()
        if consent_epoch is None:
            consent_epoch = self._music_discovery_consent_epoch
        if ((ticket is not None and ticket != current) or consent_epoch != self._music_discovery_consent_epoch
                or self._music_dna_blocks_discover()):
            return
        ticket = current
        used_existing_feed = False
        try:
            if manual:
                try:
                    data = self.client.music_discovery_refresh()
                except BackendUnavailable:
                    data = {"success": False, "error": "music_discovery_unavailable"}
                if data.get("success") is False and data.get("error") in ("rate_limited", "music_discovery_unavailable"):
                    if not self._discovery_action_current(ticket, consent_epoch):
                        return
                    data = self.client.music_discovery_feed()
                    used_existing_feed = True
            else:
                data = self.client.music_discovery_feed()
            if not self._discovery_action_current(ticket, consent_epoch):
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            data["_feedback_supported"] = self._supports_music_discovery_feedback()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch):
            if ticket == self._private_read_context():
                raise
            return
        except ProfileContextError as exc:
            data = {"success": False, "error": exc.code}
        except (DJConnectError, requests.RequestException):
            data = {"success": False, "error": "music_discovery_unavailable"}
        result = _DiscoveryReadResult(data, ticket, consent_epoch, manual=manual, used_existing_feed=used_existing_feed)
        self._musicDiscoveryReady.emit(result)

    def _music_discovery_accept_worker(self, *, ticket=None, consent_epoch=None) -> None:
        current = self._private_read_context()
        if consent_epoch is None:
            consent_epoch = self._music_discovery_consent_epoch
        if (ticket is not None and ticket != current) or consent_epoch != self._music_discovery_consent_epoch:
            return
        ticket = current
        try:
            profile = self.client.music_dna_settings(enabled=True)
            if ticket != self._private_read_context() or consent_epoch != self._music_discovery_consent_epoch:
                return
            self._apply_profile_context(profile)
            ticket = self._private_read_context()
            self._persist_backend_summary()
            self._musicDnaReady.emit(self._profile_read_result(profile, ticket))
            data = self.client.music_discovery_feed()
            if ticket != self._private_read_context() or consent_epoch != self._music_discovery_consent_epoch:
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            data["_feedback_supported"] = self._supports_music_discovery_feedback()
            self._persist_backend_summary()
        except (DJConnectError, requests.RequestException) as exc:
            raise BackendUnavailable("Music Discovery unavailable") from exc
        self._musicDiscoveryReady.emit(_DiscoveryReadResult(data, ticket, consent_epoch))

    def _discovery_action_current(self, ticket, consent_epoch):
        return (ticket == self._private_read_context() and consent_epoch == self._music_discovery_consent_epoch
                and not self._shutting_down and not self._demo_mode and not self._music_dna_blocks_discover())

    def _music_discovery_play_worker(self, item: dict[str, object], *, ticket, consent_epoch) -> None:
        if not self._discovery_action_current(ticket, consent_epoch):
            return
        try:
            data = self.client.music_discovery_play(item)
        except (AuthenticationError, ProtocolVersionMismatch):
            if ticket == self._private_read_context():
                raise
            return
        except (DJConnectError, requests.RequestException):
            data = {}
        if not self._discovery_action_current(ticket, consent_epoch):
            return
        item_id = str(item.get("discovery_item_id") or item.get("id") or "").strip()
        receipt_item = data.get("item")
        legacy_receipt = "played" not in data and "item" not in data
        acknowledged = (legacy_receipt or (data.get("played") is True and isinstance(receipt_item, dict)
                                           and receipt_item.get("id") == item_id))
        if data.get("success") is not True or not acknowledged:
            outcome = data.get("action_outcome")
            if data.get("success") is True or (isinstance(outcome, dict) and outcome.get("state") in ("unknown", "dispatched")):
                error_key = "music_discovery_play_unconfirmed"
            else:
                error_key = "music_discovery_play_failed"
            error = {"success": False, "error": error_key}
            self._musicDiscoveryReady.emit(_DiscoveryReadResult(error, ticket, consent_epoch))
            return
        self._apply_profile_context(data)
        if not self._discovery_action_current(ticket, consent_epoch):
            return
        self._persist_backend_summary()
        has_playback = _contains_playback_payload(data)
        self._musicDiscoveryPlayConfirmed.emit(
            (ticket, consent_epoch), str(item.get("section_id") or ""), item_id,
            self.client.playback_from_status(data) if has_playback else None,
        )
        readbacks = (self._load_queue_worker,) if has_playback else (self._load_queue_worker, self._refresh_worker)
        for read in readbacks:
            try:
                read(ticket=ticket)
            except (AuthenticationError, ProtocolVersionMismatch):
                if ticket == self._private_read_context():
                    raise
                return
            except (DJConnectError, requests.RequestException) as exc:
                _LOGGER.debug("Discovery playback readback failed: %s", type(exc).__name__)
        self._music_discovery_feed_worker(ticket=ticket, consent_epoch=consent_epoch)

    @Slot(object, str, str, object)
    def _apply_music_discovery_play(self, action_context, section_id: str, item_id: str, playback: Playback | None) -> None:
        if not self._discovery_action_current(*action_context):
            return
        if playback is not None:
            self._apply_playback(playback)
        self.musicDiscoveryPlayed.emit(section_id, item_id)
        self._show_toast(self.tr_key("play"), 1800, "playback")

    def _music_discovery_feedback_worker(self, item: dict[str, object], feedback: str, *, ticket, consent_epoch) -> None:
        if not self._discovery_action_current(ticket, consent_epoch):
            return
        try:
            result = self.client.music_discovery_feedback(item, feedback)
        except (AuthenticationError, ProtocolVersionMismatch):
            if ticket == self._private_read_context():
                raise
            return
        except (DJConnectError, requests.RequestException):
            result = {}
        if not self._discovery_action_current(ticket, consent_epoch):
            return
        item_id = str(item.get("discovery_item_id") or item.get("id") or "").strip()
        receipt_item = result.get("item")
        legacy_receipt = "feedback" not in result and "item" not in result
        acknowledged = (legacy_receipt or (result.get("feedback") == feedback and isinstance(receipt_item, dict)
                                           and receipt_item.get("id") == item_id))
        if result.get("success") is not True or not acknowledged:
            error = {"success": False, "error": "music_discovery_feedback_failed"}
            self._musicDiscoveryReady.emit(_DiscoveryReadResult(error, ticket, consent_epoch))
            return
        self._persist_backend_summary()
        media_key = None if legacy_receipt else _discovery_media_key(receipt_item.get("uri"))
        self._musicDiscoveryFeedbackAccepted.emit(
            (ticket, consent_epoch), str(item.get("section_id") or ""), item_id, media_key,
        )
        self._music_discovery_feed_worker(ticket=ticket, consent_epoch=consent_epoch)

    @Slot(object, str, str, object)
    def _remove_music_discovery_item(self, action_context, section_id: str, item_id: str, media_key: str | None) -> None:
        if not self._discovery_action_current(*action_context):
            return
        self._set_music_discovery_items([
            item for item in self._music_discovery_items
            if (item.get("sectionId"), item.get("discoveryItemId")) != (section_id, item_id)
            and not (media_key is not None and (item.get("discoveryItemId") == item_id
                     or (media_key and item.get("_media_key") == media_key)))
        ])

    def _supports_music_discovery_feedback(self) -> bool:
        try:
            return self.client.music_discovery_feedback_supported()
        except (DJConnectError, requests.RequestException):
            return False

    @Slot()
    def pollAskDjHistory(self) -> None:
        self._sync_config_from_disk()
        if self._demo_mode or not self.paired or not self.cfg.device_token:
            return
        now = time.monotonic()
        if self._ask_dj_busy or self._ask_dj_poll_in_flight or now < self._ask_dj_unavailable_until:
            return
        self._ask_dj_poll_in_flight = True
        self._executor.submit(self._poll_ask_dj_history_worker)

    def _poll_ask_dj_history_worker(self) -> None:
        ticket = self._private_read_context()
        try:
            data = self.client.ask_dj_history(self._ask_dj_history_revision)
            if ticket != self._private_read_context():
                self._ask_dj_poll_in_flight = False
                return
            self._apply_profile_context(data)
            ticket = self._private_read_context()
            self._persist_backend_summary()
        except (AuthenticationError, ProtocolVersionMismatch, BackendUnavailable, DJConnectError, requests.RequestException) as exc:
            self._ask_dj_poll_error_count = min(self._ask_dj_poll_error_count + 1, 6)
            delay = min(300, 10 * (2 ** (self._ask_dj_poll_error_count - 1)))
            self._ask_dj_unavailable_until = time.monotonic() + delay
            _LOGGER.warning("Ask DJ read-only poll unavailable; retrying later: %s", exc.__class__.__name__)
            self._ask_dj_poll_in_flight = False
            return
        self._ask_dj_poll_error_count = 0
        self._ask_dj_unavailable_until = 0.0
        self._askDjReady.emit(self._profile_read_result(data, ticket))
        self._ask_dj_poll_in_flight = False

    def _cache_media_artwork_async(self, kind: str, items: list[dict[str, object]]) -> None:
        # The compact ListView already loads nearby covers. A second pass would
        # compete with touch commands on the shared playback worker.
        if not items or self.cfg.release_profile == "pi-zero-2w-arm64":
            return
        if kind in self._media_artwork_cache_in_flight:
            _LOGGER.debug("%s artwork cache skipped because a cache worker is already active", kind)
            return
        self._media_artwork_cache_in_flight.add(kind)
        cache_items = [dict(item) for item in items[:MEDIA_ARTWORK_CACHE_LIMIT]]

        def worker() -> None:
            try:
                started = time.monotonic()
                cached = prepare_media_artwork(cache_items)
                _LOGGER.info("Cached %s artwork for %s items in %.0fms", kind, len(cached), _elapsed_ms(started))
                merged = [dict(item) for item in items]
                for index, cached_item in enumerate(cached):
                    if index < len(merged):
                        merged[index] = cached_item
                self._mediaArtworkReady.emit(kind, items, merged)
            finally:
                self._media_artwork_cache_in_flight.discard(kind)

        self._executor.submit(worker)

    def _show_dj_response(self, payload: dict[str, object]) -> dict[str, object]:
        text = str(payload.get("dj_text") or payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return {"success": False, "error": "missing_text"}
        self._dj_response_text = text
        self._dj_response_visible = True
        self.djResponseChanged.emit()
        self.temporaryWakeRequested.emit(20, False)
        return {
            "success": True,
            "displayed": True,
            "audio_played": False,
            "text": text,
        }

    def _forget_pairing(self) -> None:
        if self._demo_mode:
            self.exitDemoMode()
        self._shared_settings_epoch += 1
        self._shared_settings_state = {}
        self.sharedSettingsChanged.emit()
        self.cfg.device_token = ""
        self.cfg.paired = False
        self.cfg.active_profile_id = ""
        self.cfg.active_profile_name = ""
        self.cfg.active_profile_type = "household"
        self.cfg.active_profile_privacy_mode = "shared"
        self.cfg.explicit_profile_id = ""
        self.cfg.music_dna_key = ""
        self._queue_items = []
        self._playlist_items = []
        self._clear_ask_dj_cache()
        self._clear_profile_scoped_caches()
        from .config import generate_pairing_code

        self.cfg.pairing_code = generate_pairing_code()
        save_config(self.config_path, self.cfg)
        self.pairedChanged.emit()
        self.pairingCodeChanged.emit()
        self.settingsChanged.emit()
        self.mediaListsChanged.emit()
        self.profileChanged.emit()

    def _play_demo_uri(self, uri: str) -> None:
        tracks = demo_queue_items()
        for index, playlist in enumerate(demo_playlist_items()):
            if playlist["uri"] == uri:
                self._queue_items = tracks[index:] + tracks[:index]
                self.mediaListsChanged.emit()
                self._apply_demo_track(self._queue_items[0])
                return
        track = next((item for item in tracks if item["uri"] == uri), None)
        if track:
            self._apply_demo_track(track)

    def _move_demo_track(self, step: int) -> None:
        if not self._queue_items:
            return
        index = next((i for i, track in enumerate(self._queue_items) if track["uri"] == self.playback.uri), 0)
        self._apply_demo_track(self._queue_items[(index + step) % len(self._queue_items)])

    def _tick_demo_playback(self) -> None:
        if not self._demo_mode or not self.playback.is_playing:
            return
        if self.playback.position_seconds + 1 >= self.playback.duration_seconds:
            self._move_demo_track(1)
            return
        self.playback.position_seconds += 1
        self.progressChanged.emit()
        self.djSession.demo_playback_changed()

    def _apply_demo_track(self, track: dict[str, object]) -> None:
        refresh_insight = bool(self._track_insight_title)
        previous_uri = self.playback.uri
        self.playback.title = str(track["title"])
        self.playback.artist = str(track["subtitle"])
        self.playback.album = str(track.get("album") or "")
        self.playback.uri = str(track["uri"])
        self.playback.is_favorite = self.playback.uri in self._demo_favorite_uris
        self.playback.position_seconds = 0
        self.playback.duration_seconds = int(track["duration"])
        self.playback.image_url = ""
        self.playback.is_playing = True
        self._clear_track_insight()
        if refresh_insight:
            self._show_demo_track_insight()
        self.titleChanged.emit()
        self.artistChanged.emit()
        self.albumChanged.emit()
        self.imageUrlChanged.emit()
        self.playingChanged.emit()
        self.progressChanged.emit()
        track_changed = previous_uri != self.playback.uri
        self.djSession.demo_playback_changed(track_changed=track_changed)
        if track_changed:
            self.favoriteChanged.emit()
            self.trackInsightPlaybackItemChanged.emit()
            self.playbackTrackChanged.emit()

    def _set_demo_favorite(self, liked: bool) -> None:
        if not self.favoriteAvailable or liked == self.playback.is_favorite:
            return
        if liked:
            self._demo_favorite_uris.add(self.playback.uri)
        else:
            self._demo_favorite_uris.discard(self.playback.uri)
        self.playback.is_favorite = liked
        self.favoriteChanged.emit()
        self.showToastForContext(self.tr_key("favorite_saved" if liked else "favorite_removed"), "playback")

    def _show_demo_track_insight(self) -> None:
        track = next((item for item in demo_queue_items() if item["uri"] == self.playback.uri), None)
        if not track:
            return
        self._track_insight_title = self.playback.title
        self._track_insight_artist = self.playback.artist
        self._track_insight_text = self.tr_key(str(track["insight_key"]))
        self._track_insight_error = ""
        self.trackInsightChanged.emit()

    def _seed_demo_content(self) -> None:
        self._ask_dj_messages = parse_ask_dj_messages({"messages": [
            {"id": "demo-ask", "role": "user", "text": self.tr_key("demo_dj_question")},
            {"id": "demo-reply", "role": "assistant", "text": self.tr_key("demo_dj_answer")},
        ]})
        self.askDjChanged.emit()
        self._set_demo_music_dna(True)

    def _set_demo_music_dna(self, enabled: bool) -> None:
        self._music_dna_enabled = enabled
        self._music_dna_loaded = True
        self._music_dna_updated_at = ""
        self._music_discovery_consent_rejected = False
        self._music_dna_summary = self.tr_key("demo_dna_summary") if enabled else ""
        tracks = demo_queue_items()
        self._music_dna_sections = _music_dna_cards({
            "recent_tracks": [{"title": track["title"], "artist": track["subtitle"]} for track in tracks],
        }, "") if enabled else []
        self._set_music_discovery_items(parse_music_discovery_feed({"sections": [{
            "id": "demo", "title": self.tr_key("demo_mode"), "items": [
                {"id": track["uri"], "uri": track["uri"], "title": track["title"],
                 "subtitle": track["subtitle"], "reason": self.tr_key(str(track["insight_key"]))}
                for track in tracks],
        }]})["items"] if enabled else [])
        self.musicDnaChanged.emit()
        self.musicDiscoveryChanged.emit()

    def _run(self, label: str, worker, done=None, *, interactive=True) -> None:
        if interactive:
            self._set_busy(True)
            self._set_status_text(label)

        def execute() -> None:
            try:
                worker()
            except ProtocolVersionMismatch as exc:
                self._backendAvailableReady.emit(False)
                _LOGGER.warning("%s blocked by version mismatch: %s", label, exc)
                self._versionMismatchReady.emit(exc.client_version, exc.ha_version)
                self._statusReady.emit(str(exc))
            except AuthenticationError as exc:
                self._backendAvailableReady.emit(False)
                message = self.tr_key("ha_auth_failed")
                _LOGGER.warning("%s authentication failed: %s", label, exc)
                if not self.paired:
                    _LOGGER.info("Suppressing authentication toast while DJConnect is waiting for pairing")
                    self._statusReady.emit(self.tr_key("ready_to_pair"))
                    return
                _LOGGER.info("Clearing local pairing after Home Assistant rejected the device token")
                self._forget_pairing()
                message = self.tr_key("ready_to_pair")
                self._statusReady.emit(message)
                self._toastReady.emit(message, 5000, "connectivity")
            except BackendUnavailable as exc:
                self._backendAvailableReady.emit(False)
                _LOGGER.warning("%s backend unavailable: %s", label, exc)
                message = self.tr_key("backend_unavailable")
                if not self.paired:
                    _LOGGER.info("Suppressing backend-unavailable toast while DJConnect is waiting for pairing")
                    self._statusReady.emit(self.tr_key("ready_to_pair"))
                    return
                self._statusReady.emit(message)
                self._toastReady.emit(message, 5000, "connectivity")
            except UnsupportedBackendCapability as exc:
                self._backendAvailableReady.emit(True)
                message = str(exc)
                _LOGGER.warning("%s unsupported backend capability: %s", label, message)
                self._statusReady.emit(message)
                self._toastReady.emit(message, 5000, "connectivity")
            except StaleBackendAction as exc:
                self._backendAvailableReady.emit(True)
                message = str(exc)
                _LOGGER.warning("%s stale backend action: %s", label, message)
                self._statusReady.emit(message)
                self._toastReady.emit(message, 5000, "connectivity")
            except SaveCurrentTrackError as exc:
                _LOGGER.warning("%s failed: %s", label, exc)
                message = self.tr_key("favorite_save_failed")
                self._statusReady.emit(message)
                self._toastReady.emit(message, 3500, "connectivity")
            except DJConnectError as exc:
                self._backendAvailableReady.emit(False)
                _LOGGER.warning("%s failed: %s", label, exc)
                self._statusReady.emit(str(exc))
            except Exception as exc:
                self._backendAvailableReady.emit(False)
                _LOGGER.exception("%s failed unexpectedly", label)
                self._statusReady.emit(self.tr_key("offline", error=exc))
            finally:
                if done is not None:
                    done()
                if interactive:
                    self._busyReady.emit(False)

        self._executor.submit(execute)

    @Slot(object)
    def _apply_playback(self, playback: Playback) -> None:
        if self._demo_mode:
            return
        self._set_backend_available(True)
        if self._version_mismatch_visible:
            self._version_mismatch_visible = False
            self._version_mismatch_text = ""
            self.versionMismatchChanged.emit()
        old = self.playback
        old_volume_context = self._control_volume_context(old)
        if self._playback_seek_context(old) != self._playback_seek_context(playback):
            self.cancelPlaybackSeek()
        self.playback = playback
        if old_volume_context != self._control_volume_context(playback):
            self.cancelControlVolume()
        track_changed = old.title != playback.title or old.artist != playback.artist or old.image_url != playback.image_url or old.uri != playback.uri
        observation_changed = ((self._uses_current_track_insight() or self._needs_track_insight_capabilities())
                               and (old.is_playing != playback.is_playing
                                    or old.position_seconds - playback.position_seconds > 5))
        if (self._track_insight_subject(old) != self._track_insight_subject(playback)
                or self._track_insight_output(old) != self._track_insight_output(playback)
                or observation_changed):
            self._track_insight_revision += 1
            self._clear_track_insight()
            if observation_changed:
                self.trackInsightObservationChanged.emit()
        elif self._track_insight_result_scope is not None and self._track_insight_result_scope != self._track_insight_scope():
            self._clear_track_insight()
            self.trackInsightObservationChanged.emit()
        resumed_playback = not old.is_playing and playback.is_playing
        if old.title != playback.title:
            self.titleChanged.emit()
        if old.artist != playback.artist:
            self.artistChanged.emit()
        if old.album != playback.album:
            self.albumChanged.emit()
        if old.uri != playback.uri:
            self.trackInsightPlaybackItemChanged.emit()
        if old.image_url != playback.image_url:
            self.imageUrlChanged.emit()
        if old.is_playing != playback.is_playing:
            self.playingChanged.emit()
        if old.is_favorite != playback.is_favorite or old.uri != playback.uri:
            self.favoriteChanged.emit()
        if old.volume != playback.volume:
            self.volumeChanged.emit()
        if old.shuffle != playback.shuffle:
            self.shuffleChanged.emit()
        if old.repeat != playback.repeat:
            self.repeatChanged.emit()
        output_confirmed = bool(self._unconfirmed_output_choice and playback.output_device_active
                                and playback.output_device_id == self._unconfirmed_output_choice)
        if output_confirmed:
            self._unconfirmed_output_choice = ""
        if (
            old.output_device != playback.output_device
            or old.output_device_id != playback.output_device_id
            or old.output_devices != playback.output_devices
            or old.output_device_details != playback.output_device_details
            or output_confirmed
        ):
            self.outputDeviceChanged.emit()
        if old.position_seconds != playback.position_seconds or old.duration_seconds != playback.duration_seconds:
            self.progressChanged.emit()
        suppress_navigation = self._suppress_next_playback_navigation
        self._suppress_next_playback_navigation = False
        if (track_changed and (playback.title or playback.artist or playback.image_url)) or resumed_playback:
            navigate_now = not suppress_navigation
            self.temporaryWakeRequested.emit(10, navigate_now)
        if self._playback_track_changed(old, playback):
            self.playbackTrackChanged.emit()
        self._set_status_text(self.tr_key("connected" if self.paired else "ready_to_pair"))

    @Slot(str, object, object)
    def _apply_media_list(self, kind: str, items: object, ticket=None) -> None:
        if self._demo_mode or (ticket is not None and ticket != self._private_read_context()):
            return
        if not isinstance(items, list):
            return
        if kind == "queue":
            self._queue_items = items
        elif kind == "playlists":
            self._playlist_items = items
        else:
            return
        self.mediaListsChanged.emit()

    @Slot(str, object, object)
    def _apply_cached_media_list(self, kind: str, original: object, cached: object) -> None:
        current = self._queue_items if kind == "queue" else self._playlist_items
        if current is original:
            self._apply_media_list(kind, cached)

    @Slot(object, result=bool)
    def _apply_ask_dj_data(self, data: object) -> bool:
        if self._demo_mode:
            return False
        if not self._profile_result_current(data):
            return False
        if not isinstance(data, dict) or data.get("success") is False:
            return False
        self._apply_profile_context(data)
        clear_revision = _int_value(data.get("clear_revision"), self._ask_dj_clear_revision)
        history_revision = _int_value(data.get("history_revision"), self._ask_dj_history_revision)
        if clear_revision < self._ask_dj_clear_revision or history_revision < self._ask_dj_history_revision:
            return False
        if history_revision > self._ask_dj_history_revision:
            self._ask_dj_withdrawn_ids.clear()
        clear_ack = data.get("cleared") is True
        if clear_revision > self._ask_dj_clear_revision or clear_ack:
            if clear_ack:
                self._ask_dj_context_request = None
                self._ask_dj_destination_loading = False
            self._clear_ask_dj_cache(reset_revisions=False)
            self._ask_dj_clear_revision = max(self._ask_dj_clear_revision, clear_revision)
            self._ask_dj_history_revision = max(self._ask_dj_history_revision, history_revision)
            self._set_backend_available(True)
            if clear_ack:
                return True
        trimmed_before = str(data.get("history_trimmed_before") or "").strip()
        if trimmed_before:
            self._ask_dj_messages = [m for m in self._ask_dj_messages if str(m.get("created_at") or m.get("server_time") or "") >= trimmed_before]
        trimmed_count = _int_value(data.get("history_trimmed_count"), 0)
        if trimmed_count > 0:
            self._ask_dj_messages = _sort_ask_dj_messages_newest_first(_sort_ask_dj_messages(self._ask_dj_messages)[trimmed_count:])
        messages = parse_ask_dj_messages(data)
        if (data.get("history_scope") == "profile" and self.cfg.active_profile_id
                and data.get("owner_profile_id") == self.cfg.active_profile_id
                and self.personalProfileExportAllowed and self.cfg.active_profile_privacy_mode == "private"
                and isinstance(data.get("messages"), list)):
            by_id = {
                row["id"]: row
                for row in data["messages"] if isinstance(row, dict) and isinstance(row.get("id"), str)
            }
            withdrawn = {message_id for message_id, row in by_id.items() if row.get("status") == "source_withdrawn"}
            if withdrawn:
                self._ask_dj_withdrawn_ids.update(withdrawn)
                self._ask_dj_messages = [message for message in self._ask_dj_messages if message.get("id") not in withdrawn]
            now = self._session_controller._clock.now()
            for message in messages:
                if message.get("role") == "assistant":
                    row = by_id.get(message.get("id"), {})
                    message["contextEligible"] = (row.get("role") == "assistant"
                        and row.get("message_kind", "assistant") == "assistant")
                    message["historicalMatches"] = [
                        match for match in ask_dj_history_matches(row.get("historical_matches"), now=now)
                        if (match["session_id"], match["entry_id"]) not in self._ask_dj_withdrawn_entries
                    ]
        messages = [message for message in messages if message.get("id") not in self._ask_dj_withdrawn_ids]
        if messages:
            self._merge_ask_dj_messages(messages, limit=_int_value(data.get("history_limit"), 100))
        else:
            self.askDjChanged.emit()
        self._ask_dj_history_revision = max(self._ask_dj_history_revision, history_revision)
        self._set_backend_available(True)
        return True

    def _merge_ask_dj_messages(self, messages: list[dict[str, object]], limit: int = 100) -> None:
        merged = list(self._ask_dj_messages)
        next_arrival_index = max((_int_value(message.get("_arrival_index"), index) for index, message in enumerate(merged)), default=-1) + 1
        index_by_key: dict[str, int] = {}
        for index, message in enumerate(merged):
            for key in _ask_dj_message_keys(message):
                index_by_key[key] = index
        for message in messages:
            match_index = next((index_by_key[key] for key in _ask_dj_message_keys(message) if key in index_by_key), None)
            if match_index is not None:
                if not message.get("created_at"):
                    message["created_at"] = merged[match_index].get("created_at", "")
                if not message.get("displayTime"):
                    message["displayTime"] = merged[match_index].get("displayTime", "")
                merged[match_index] = {**merged[match_index], **message, "pending": False}
                for key in _ask_dj_message_keys(merged[match_index]):
                    index_by_key[key] = match_index
            else:
                message = {**message, "_arrival_index": next_arrival_index}
                next_arrival_index += 1
                merged.append(message)
                for key in _ask_dj_message_keys(message):
                    index_by_key[key] = len(merged) - 1
        latest_messages = _sort_ask_dj_messages(merged)[-max(1, limit):]
        self._ask_dj_messages = _sort_ask_dj_messages_newest_first(latest_messages)
        self.askDjChanged.emit()

    def _clear_ask_dj_cache(self, *, reset_revisions: bool = True) -> None:
        self._ask_dj_messages = []
        self._ask_dj_context_page = None
        if reset_revisions:
            self._ask_dj_context_request = None
            self._ask_dj_destination_loading = False
        self._ask_dj_withdrawn_ids.clear()
        self._ask_dj_withdrawn_entries.clear()
        self._ask_dj_delivery_key = ""
        self._ask_dj_poll_error_count = 0
        self._ask_dj_poll_in_flight = False
        self._ask_dj_unavailable_until = 0.0
        if reset_revisions:
            self._ask_dj_history_revision = 0
            self._ask_dj_clear_revision = 0
        self.askDjChanged.emit()

    def _apply_profile_context(self, data: dict[str, object]) -> None:
        before = self._profile_cache_key()
        self.client.apply_profile_context(data)
        after = self._profile_cache_key()
        if after != before:
            _LOGGER.info(
                "Resolved DJConnect Profile changed type=%s privacy=%s id_present=%s",
                self.cfg.active_profile_type,
                self.cfg.active_profile_privacy_mode,
                bool(self.cfg.active_profile_id),
            )
            self._clear_profile_scoped_caches()
            save_config(self.config_path, self.cfg)
            self.profileChanged.emit()
            self.settingsChanged.emit()

    def _private_read_context(self):
        c = self.cfg
        return (c.ha_url, c.device_id, c.device_token, c.paired, c.active_profile_id,
                c.explicit_profile_id, c.private_session, c.active_profile_type,
                c.active_profile_privacy_mode, c.language,
                getattr(getattr(self, "_session_controller", None), "_owner_epoch", 0))

    def _profile_read_result(self, data, context):
        return _ProfileReadResult(data, context) if isinstance(data, dict) else data

    def _profile_result_current(self, data):
        return (not self._shutting_down and (not isinstance(data, _ProfileReadResult)
                or data.owner_context == self._private_read_context()))

    @Slot()
    def _session_owner_changed(self):
        if self._shutting_down:
            return
        self.closeAskDjHelp()
        self._clear_profile_scoped_caches(preserve_session=True)
        self._invalidate_shared_settings_if_context_changed()
        timer = getattr(self, "_settings_reconcile_timer", None)
        if timer is not None:
            timer.start()

    def _profile_cache_key(self) -> tuple[str, str, str]:
        return (self.cfg.active_profile_id, self.cfg.active_profile_type, self.cfg.active_profile_privacy_mode)

    def _clear_profile_scoped_caches(self, *, preserve_session=False) -> None:
        if self._media_list_load_failures:
            self._media_list_load_failures.clear()
            self.mediaActivityChanged.emit()
        if not preserve_session and hasattr(self, "_session_controller"):
            self._session_controller.invalidate()
        if self._queue_items or self._playlist_items:
            self._queue_items = []
            self._playlist_items = []
            self.mediaListsChanged.emit()
        if (self._pending_output_choice or self._unconfirmed_output_choice
                or self._control_outputs_request is not None or self._control_outputs_status):
            self._pending_output_choice = None
            self._unconfirmed_output_choice = ""
            self._control_outputs_request = None
            self._control_outputs_status = ""
            self.controlVolumeChanged.emit()
            self.outputDeviceChanged.emit()
        self._shared_settings_epoch += 1
        self._shared_settings_state = {}
        self.sharedSettingsChanged.emit()
        self._clear_ask_dj_cache()
        self._music_dna_enabled = False
        self._music_dna_loaded = False
        self._music_dna_context_revision += 1
        self._music_dna_updated_at = ""
        self._music_dna_summary = ""
        self._music_dna_error = ""
        self._music_dna_needs_recovery = False
        self._music_dna_import = None
        self._music_dna_sections = []
        self._set_music_discovery_items([])
        self._music_discovery_empty_text = ""
        self._music_discovery_error = ""
        self._music_discovery_dna_disabled = False
        self._music_discovery_loaded = False
        self._music_discovery_feed_deadline = None
        self._music_discovery_playing = None
        self._music_discovery_feedback_supported = False
        self._music_discovery_consent_rejected = False
        self._track_insight_revision += 1
        self._clear_track_insight()
        self.musicDnaChanged.emit()
        self.musicDiscoveryChanged.emit()

    def _set_ask_dj_busy(self, value: bool) -> None:
        if self._ask_dj_busy == value:
            return
        self._ask_dj_busy = value
        self.askDjChanged.emit()

    def _set_music_dna_busy(self, value: bool) -> None:
        if self._music_dna_busy == value:
            return
        self._music_dna_busy = value
        self.musicDnaChanged.emit()

    def _set_music_discovery_busy(self, value: bool) -> None:
        if self._music_discovery_busy == value:
            return
        self._music_discovery_busy = value
        self.musicDiscoveryChanged.emit()

    def _set_favorite_busy(self, value: bool) -> None:
        if self._favorite_busy == value:
            return
        self._favorite_busy = value
        self.favoriteBusyChanged.emit()

    def _set_music_discovery_items(self, items: list[dict[str, object]]) -> None:
        if items != self._music_discovery_items:
            self._music_discovery_items = items
            self.musicDiscoveryItemsChanged.emit()

    def _clear_music_discovery_playing(self, expected: tuple[str, str]) -> None:
        if self._music_discovery_playing is not expected:
            return
        self._music_discovery_playing = None
        self.musicDiscoveryChanged.emit()

    @Slot(object)
    def _apply_music_dna_data(self, data: object) -> None:
        if self._demo_mode:
            return
        if not self._profile_result_current(data):
            return
        if not isinstance(data, dict):
            return
        profile_read = isinstance(data, _MusicDnaProfileResult)
        self._music_dna_needs_recovery = profile_read and data.retryable
        if data.get("success") is False:
            code = data.get("error")
            key = "session_profile_missing" if isinstance(code, str) and code in PROFILE_ERROR_CODES | {"music_dna_context_changed"} else "backend_unavailable"
            self._music_dna_error = self.tr_key("music_dna_recovering" if self._music_dna_needs_recovery else key)
            if not self._music_dna_needs_recovery:
                self._music_dna_summary = ""
                self._music_dna_sections = []
                self._music_dna_updated_at = ""
            self.musicDnaChanged.emit()
            if profile_read and data.manual:
                notice_key = key if key == "session_profile_missing" else "music_dna_profile_unavailable"
                self._show_toast(self.tr_key(notice_key), 2200, "musicdna")
            return
        self._music_dna_error = ""
        self._apply_profile_context(data)
        was_disabled = self._music_dna_blocks_discover()
        parsed = parse_music_dna_profile(data, ha_url=self.cfg.ha_url)
        self._music_dna_enabled = bool(parsed["enabled"])
        self._music_dna_loaded = isinstance(data.get("enabled"), bool)
        profile = data.get("profile")
        updated_at = profile.get("updated_at") if isinstance(profile, dict) else None
        if timestamp(updated_at) is None:
            updated_at = data.get("updated_at")
        self._music_dna_updated_at = updated_at if (self._music_dna_loaded and self._music_dna_enabled
            and isinstance(updated_at, str) and timestamp(updated_at) is not None) else ""
        self._music_dna_summary = str(parsed["summary"])
        self._music_dna_sections = list(parsed["sections"])
        if not was_disabled and self._music_dna_blocks_discover():
            self._music_discovery_consent_epoch += 1
            self._set_music_discovery_items([])
            self._music_discovery_loaded = False
            self._music_discovery_feed_deadline = None
            self._music_discovery_feedback_supported = False
            self._music_discovery_error = ""
            self._music_discovery_empty_text = ""
            self.musicDiscoveryChanged.emit()
        self._set_backend_available(True)
        self.musicDnaChanged.emit()
        if profile_read and data.manual:
            notice_key = "music_dna_updated" if self._music_dna_loaded else "music_dna_profile_unavailable"
            self._show_toast(self.tr_key(notice_key), 2200, "musicdna")

    @Slot(object)
    def _apply_music_discovery_data(self, data: object) -> None:
        if self._demo_mode:
            return
        if not self._profile_result_current(data):
            return
        if not isinstance(data, dict):
            return
        if self._music_dna_blocks_discover() or (
                isinstance(data, _DiscoveryReadResult) and data.consent_epoch != self._music_discovery_consent_epoch):
            return
        manual = isinstance(data, _DiscoveryReadResult) and data.manual
        error = str(data.get("error") or "")
        if data.get("success") is False and error in {
            "music_discovery_play_failed", "music_discovery_play_unconfirmed", "music_discovery_feedback_failed",
        }:
            self._music_discovery_error = self.tr_key(error)
            self.musicDiscoveryChanged.emit()
            return
        if data.get("success") is False and error == "music_discovery_unavailable":
            self._music_discovery_error = self.tr_key("music_discovery_load_failed")
            self._set_backend_available(False)
            self.musicDiscoveryChanged.emit()
            if manual:
                self._show_toast(self._music_discovery_error, 2200, "discover")
            return
        if data.get("success") is False and error != "music_dna_disabled":
            # Rejected feeds are not content or Profile authority. Never render
            # their machine error/message as user-facing recommendation text.
            self._set_music_discovery_items([])
            self._music_discovery_feedback_supported = False
            self._music_discovery_empty_text = ""
            self._music_discovery_dna_disabled = False
            self._music_discovery_loaded = False
            self._music_discovery_feed_deadline = None
            key = "session_profile_missing" if error in PROFILE_ERROR_CODES | {"music_dna_context_changed"} else "backend_unavailable"
            self._music_discovery_error = self.tr_key(key)
            self.musicDiscoveryChanged.emit()
            if manual:
                self._show_toast(self._music_discovery_error, 2200, "discover")
            return
        self._apply_profile_context(data)
        self._music_discovery_consent_rejected = False
        self._music_discovery_loaded = True
        self._music_discovery_dna_disabled = data.get("enabled") is False or error == "music_dna_disabled"
        if self._music_discovery_dna_disabled:
            self._music_discovery_feed_deadline = None
            self._set_music_discovery_items([])
            self._music_discovery_feedback_supported = False
            self._music_discovery_error = ""
            self._music_discovery_empty_text = str(data.get("reason") or data.get("empty_text") or data.get("message") or self.tr_key("music_discovery_requires_music_dna"))
            self.musicDiscoveryChanged.emit()
            if manual:
                self._show_toast(self.tr_key("music_discovery_requires_music_dna"), 2200, "discover")
            return
        parsed = parse_music_discovery_feed(data, ha_url=self.cfg.ha_url)
        generated = timestamp(data.get("generated_at"))
        ttl = data.get("ttl_seconds")
        self._music_discovery_feed_deadline = (generated + ttl
            if data.get("enabled") is True and generated is not None
            and type(ttl) is int and 0 < ttl <= 2_147_483_647 else None)
        self._set_music_discovery_items(parsed["items"])
        self._music_discovery_empty_text = parsed["empty_text"]
        self._music_discovery_feedback_supported = bool(data.get("_feedback_supported") or data.get("feedback_supported") or data.get("supports_feedback"))
        self._music_discovery_error = ""
        self._set_backend_available(True)
        self.musicDiscoveryChanged.emit()
        if manual:
            if data.used_existing_feed:
                notice_key = "music_discovery_existing_feed"
            else:
                notice_key = "music_discovery_rate_limited" if data.get("rate_limited") is True else "music_discovery_updated"
            self._show_toast(self.tr_key(notice_key), 2200, "discover")

    def _music_dna_blocks_discover(self):
        return self._music_dna_loaded and not self._music_dna_enabled

    @Slot(object)
    def _apply_track_insight_data(self, data: object) -> None:
        if self._demo_mode:
            return
        if not self._application_foreground or not self._profile_result_current(data):
            return
        scope = self._track_insight_scope()
        if isinstance(data, _TrackInsightResult) and data.scope != scope:
            return
        if not isinstance(data, dict):
            return
        manual = isinstance(data, _TrackInsightResult) and data.manual
        error = str(data.get("error") or "").strip()
        if error in ("no_track_playing", "current_track_unavailable"):
            self._clear_track_insight(error=self.tr_key("track_insight_no_track"),
                                      retryable=error == "current_track_unavailable", notify=manual)
            return
        if error == "rate_limited":
            self._clear_track_insight(error=self.tr_key("track_insight_rate_limited"), retryable=True, notify=manual)
            return
        if error:
            self._clear_track_insight(error=self.tr_key("track_insight_retry"),
                                      retryable=error in {"transient", "track_insight_context_changed"}, notify=manual)
            return
        proof = data.proof if isinstance(data, _TrackInsightResult) else None
        deadline = None
        if proof is not None:
            observation, deadline = proof
            previous = self._track_insight_last_proof
            if previous and previous[:2] == (scope, observation):
                deadline = min(deadline, previous[2])
            if deadline <= self._session_controller._clock.now():
                self._expire_track_insight()
                return
            self._track_insight_last_proof = (scope, observation, deadline)
        messages = parse_ask_dj_messages(data)
        message = next((item for item in messages if item.get("trackInsight")), None)
        if not isinstance(message, dict):
            self._clear_track_insight(error=self.tr_key("track_insight_empty"), notify=manual)
            return
        track = message.get("trackInsightData")
        track_data = track.get("track") if isinstance(track, dict) and isinstance(track.get("track"), dict) else {}
        analysis = message.get("analysis") if isinstance(message.get("analysis"), dict) else {}
        image_url = _image_url_from(track_data) if isinstance(track_data, dict) else ""
        self._track_insight_title = str(track_data.get("title") or self.playback.title or "").strip() if isinstance(track_data, dict) else self.playback.title
        self._track_insight_artist = str(track_data.get("artist") or self.playback.artist or "").strip() if isinstance(track_data, dict) else self.playback.artist
        self._track_insight_album = str(track_data.get("album") or "").strip() if isinstance(track_data, dict) else ""
        self._track_insight_image_url = cached_image_url(image_url) if image_url else self.playback.image_url
        self._track_insight_text = str(message.get("text") or "").strip()
        self._track_insight_error = ""
        self._track_insight_items = list(message.get("items")) if isinstance(message.get("items"), list) else []
        self._track_insight_sections = list(analysis.get("sections")) if isinstance(analysis.get("sections"), list) else []
        raw_analysis = track.get("analysis", {}) if isinstance(track, dict) else {}
        self._track_insight_audio_notice = (_track_insight_unmeasured(raw_analysis)
                                            and raw_analysis.get("basis") != "unmeasured_metadata_only_v1")
        self._track_insight_verified_credit = track.get("verifiedCredit", {}) if isinstance(track, dict) else {}
        self._track_insight_visualizer = _track_insight_visualizer(track, self._track_insight_items, self._track_insight_sections) if isinstance(track, dict) else {}
        self._track_insight_result_scope = scope
        self._track_insight_deadline = deadline
        self._track_insight_retryable = False
        self._schedule_track_insight_expiry()
        self._set_backend_available(True)
        self.trackInsightChanged.emit()
        if manual:
            self._show_toast(self.tr_key("track_insight_updated"), 2200, "trackinsight")

    @staticmethod
    def _playback_track_changed(previous: Playback, current: Playback) -> bool:
        if not current.title.strip() or (not previous.title.strip() and not current.is_playing):
            return False
        if previous.uri.strip() and current.uri.strip():
            return previous.uri.strip() != current.uri.strip()
        # A late URI or new artwork does not identify a different track.
        return (previous.title.strip().casefold(), previous.artist.strip().casefold()) != (
            current.title.strip().casefold(), current.artist.strip().casefold(),
        )

    @staticmethod
    def _track_insight_subject(playback: Playback) -> tuple[str, ...]:
        if playback.uri.strip():
            return ("uri", playback.uri.strip())
        return ("metadata", *(value.strip().casefold() for value in (playback.title, playback.artist, playback.album)))

    @staticmethod
    def _track_insight_output(playback: Playback) -> tuple[str, str]:
        if playback.output_device_id:
            return ("id", playback.output_device_id)
        return ("label", playback.output_device)

    def _track_insight_scope(self) -> tuple:
        return (
            self._private_read_context(),
            self._track_insight_revision,
            self._track_insight_subject(self.playback),
            self.cfg.music_backend,
            self.cfg.music_backend_revision,
            self.cfg.music_target_player.get("id", ""),
            self._track_insight_output(self.playback),
            self._current_track_insight_style(),
        )

    def _current_track_insight_style(self) -> tuple:
        if not (self._uses_current_track_insight() or self._needs_track_insight_capabilities()):
            return ()
        return (self._session_controller.track_insight_session_style(),
                self._shared_settings_state.get("track_insight_style", ""))

    @Slot()
    def _observe_track_insight_style(self) -> None:
        style = self._current_track_insight_style()
        if style == self._track_insight_style:
            return
        self._track_insight_style = style
        if self._demo_mode:
            return
        self._track_insight_revision += 1
        self._clear_track_insight()
        self.trackInsightObservationChanged.emit()

    def _uses_current_track_insight(self) -> bool:
        return (self.cfg.music_backend not in {"music_assistant", "later_manual"}
                and self._session_controller.supports_current_track_insight())

    def _needs_track_insight_capabilities(self) -> bool:
        return bool(self.cfg.ha_url and self.paired
                    and self.cfg.music_backend not in {"music_assistant", "later_manual"}
                    and not self._session_controller.has_capability_snapshot())

    def _schedule_track_insight_expiry(self) -> None:
        self._track_insight_expiry_timer.stop()
        if self._track_insight_deadline is not None:
            remaining_ms = 1000 * (self._track_insight_deadline - self._session_controller._clock.now())
            self._track_insight_expiry_timer.start(max(1, math.ceil(remaining_ms)))

    def _expire_track_insight(self) -> None:
        if self._track_insight_deadline is None:
            return
        if self._track_insight_deadline <= self._session_controller._clock.now():
            self._clear_track_insight(retryable=True)
            self.trackInsightObservationChanged.emit()
        else:
            self._schedule_track_insight_expiry()

    def _invalidate_track_insight_language(self) -> None:
        self._track_insight_revision += 1
        if self._demo_mode:
            if self._track_insight_title:
                self._show_demo_track_insight()
        else:
            self._clear_track_insight()

    def _clear_track_insight(self, *, error: str = "", retryable: bool = False, notify: bool = False) -> None:
        changed = any(
            (
                self._track_insight_title,
                self._track_insight_artist,
                self._track_insight_album,
                self._track_insight_image_url,
                self._track_insight_text,
                self._track_insight_error,
                self._track_insight_audio_notice,
                self._track_insight_verified_credit,
                self._track_insight_items,
                self._track_insight_sections,
                self._track_insight_visualizer,
                self._track_insight_result_scope,
            )
        )
        self._track_insight_title = ""
        self._track_insight_artist = ""
        self._track_insight_album = ""
        self._track_insight_image_url = ""
        self._track_insight_text = ""
        self._track_insight_error = error
        self._track_insight_audio_notice = False
        self._track_insight_verified_credit = {}
        self._track_insight_items = []
        self._track_insight_sections = []
        self._track_insight_visualizer = {}
        self._track_insight_result_scope = None
        self._track_insight_deadline = None
        self._track_insight_retryable = retryable
        self._track_insight_expiry_timer.stop()
        if changed or error:
            self.trackInsightChanged.emit()
        if notify:
            self._show_toast(error, 2200, "trackinsight")

    @Slot(str, str)
    def _apply_output_device_rejection(self, previous: str, attempted: str) -> None:
        self.playback.output_device = previous
        self.outputDeviceChanged.emit()
        _LOGGER.warning("Output device selection rejected: %s", attempted)

    def _is_cached_output_device(self, value: str) -> bool:
        normalized = value.strip().casefold()
        if not normalized:
            return False
        for item in self.playback.output_device_details:
            names = {
                str(item.get("name") or "").strip().casefold(),
                str(item.get("value") or "").strip().casefold(),
            }
            if normalized in names and item.get("cached") is True:
                return True
        return False

    @Slot(bool, str)
    def _apply_pairing(self, paired: bool, message: str) -> None:
        if paired:
            self._set_backend_available(True)
        if self._version_mismatch_visible:
            self._version_mismatch_visible = False
            self._version_mismatch_text = ""
            self.versionMismatchChanged.emit()
        if paired:
            self._pairing_success_visible = True
            self.pairedChanged.emit()
            self.settingsChanged.emit()
            self.pairingSuccessChanged.emit()
        self._set_status_text(message)

    @Slot(str)
    def _set_status_text(self, value: str) -> None:
        if self._status_text == value:
            return
        self._status_text = value
        self.statusTextChanged.emit()

    def _set_backend_available(self, value: bool) -> None:
        if self._backend_available == value:
            return
        if not value:
            self._about_music_request_time = elapsed_time()
            self._about_connection_request_time = self._about_music_request_time
        self._backend_available = value
        self.backendAvailableChanged.emit()

    def _persist_backend_summary(self) -> None:
        self._invalidate_shared_settings_if_context_changed()
        try:
            if save_config(self.config_path, self.cfg, skip_unchanged=True):
                self.settingsChanged.emit()
                self.favoriteChanged.emit()
                self.djAnnouncementChanged.emit()
        except Exception as exc:  # noqa: BLE001 - config persistence failures are reported without interrupting renderer state
            _LOGGER.warning("Could not persist music backend summary: %s", exc)

    def _set_busy(self, value: bool) -> None:
        if self._busy == value:
            return
        self._busy = value
        self.busyChanged.emit()

    @Slot(str, str)
    def _apply_version_mismatch(self, client_version: str, ha_version: str) -> None:
        self._version_mismatch_text = self.tr_key(
            "version_mismatch_message",
            client=client_version,
            ha=ha_version,
            required=f"{client_version.rsplit('.', 1)[0]}.x",
        )
        self._version_mismatch_visible = True
        self.versionMismatchChanged.emit()
        self.showToastForContext(self.tr_key("version_mismatch_title"), "updates")
        self._trigger_update_service()

    def _trigger_update_service(self) -> None:
        if self._update_service_triggered:
            return
        self._update_service_triggered = True
        try:
            _LOGGER.info("Triggering djconnect-updater.service after version mismatch")
            subprocess.Popen(["systemctl", "start", "djconnect-updater.service"])
        except Exception as exc:  # noqa: BLE001 - updater launch is best-effort after a version mismatch
            _LOGGER.warning("Could not trigger updater service after version mismatch: %s", exc)

    @Slot()
    def shutdown(self) -> None:
        self._shutting_down = True
        self.cancelTrackInsightClip()
        self._game_settings.sync()
        self.closeAskDjHelp()
        self._edge_back_gesture.cancel()
        _LOGGER.info("Stopping DJConnect Pi client backend")
        self._poll_timer.stop()
        self._event_timer.stop()
        self._shared_settings_timer.stop()
        self._startup_settings_timer.stop()
        self._startup_refresh_timer.stop()
        self._settings_reconcile_timer.stop()
        self._track_insight_expiry_timer.stop()
        self._session_controller.setForeground(False)
        self._session_controller.shutdownImages()
        self._session_executor.shutdown(wait=False, cancel_futures=True)
        self._question_executor.shutdown(wait=False, cancel_futures=True)
        self._executor.shutdown(wait=False, cancel_futures=True)

    @Slot()
    def _poll_local_events(self) -> None:
        self._poll_dj_response_event()
        self._poll_command_event()
        self._poll_screenshot_event()

    def _poll_dj_response_event(self) -> None:
        path = Path(self.cfg.dj_response_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Failed to read local DJ response event file: %s", exc)
            return
        if isinstance(data, dict):
            _LOGGER.info("Displaying DJ response from local API daemon event")
            self._show_dj_response(data)

    def _poll_command_event(self) -> None:
        path = Path(self.cfg.command_event_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Failed to read local command event file: %s", exc)
            return
        raw_events = data.get("events") if isinstance(data.get("events"), list) else [data]
        self._sync_config_from_disk()
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            command = str(event.get("command") or "").strip()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if not command:
                continue
            if command == "debug_show_screen":
                screen = str(payload.get("screen") or "").strip()
                if screen:
                    _LOGGER.info("Executing local debug screen request: %s", screen)
                    self.debugScreenRequested.emit(screen)
                continue
            if command == "settings":
                _LOGGER.info("Executing local web portal settings update")
                self._sync_config_from_disk()
                self.languageChanged.emit()
                self.logLevelChanged.emit()
                self.screenBrightnessChanged.emit()
                self.screenTimeoutChanged.emit()
                self.settingsChanged.emit()
                self.translationsChanged.emit()
                continue
            if command == "forget_pairing":
                _LOGGER.info("Executing local web portal pairing reset")
                self.resetPairing()
                continue
            if command == "reboot":
                _LOGGER.info("Executing local web portal reboot request")
                self.rebootDevice()
                continue
            if command == "shutdown":
                _LOGGER.info("Executing local web portal shutdown request")
                self.shutdownDevice()
                continue
            if command == "check_updates":
                _LOGGER.info("Executing local web portal update check request")
                self.checkForUpdates()
                continue
            if command in {"play_context_at", "start_playlist"}:
                item_payload = media_item_payload(command, payload)
                if not item_payload:
                    _LOGGER.info("Ignoring local media item request without usable URI: %s", command)
                    continue
                _LOGGER.info("Executing local web portal media item request: %s", command)
                self.playMediaItem(command, item_payload)
                continue
            _LOGGER.info("Executing Client API command event from Home Assistant: %s", command)
            if command in {"previous", "next"}:
                self.wakeScreenRequested.emit()
            self.command(command, **{k: v for k, v in payload.items() if k not in {"command", "device_id", "client_type", "version", "firmware", "local_url", "capabilities"}})

    def _poll_screenshot_event(self) -> None:
        path = Path(self.cfg.screenshot_event_file)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Failed to read local screenshot event file: %s", exc)
            return
        target = Path(str(data.get("target") or self.cfg.screenshot_file)) if isinstance(data, dict) else Path(self.cfg.screenshot_file)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.warning("Failed to prepare screenshot directory: %s", exc)
            return
        _LOGGER.info("Capturing debug screenshot to %s", target)
        self.cfg.screenshot_file = str(target)
        self.wakeScreenRequested.emit()
        self.screenshotRequested.emit()



def configure_graphics(cfg: Config, environment: dict[str, str]) -> None:
    """Request RHI/OpenGL on Pi5 only; never rewrite an explicit override."""
    if cfg.release_profile == "pi5-arm64":
        environment.setdefault("QT_QUICK_BACKEND", "rhi")
        environment.setdefault("QSG_RHI_BACKEND", "opengl")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--ha-url", default="")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--exit-after-ms", type=int, default=0)
    parser.add_argument("--log-file", default="")
    parser.add_argument("--log-level", default="")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.ha_url:
        cfg.ha_url = args.ha_url
        save_config(args.config, cfg)
    if args.log_file:
        cfg.log_file = args.log_file
        save_config(args.config, cfg)
    if args.log_level:
        cfg.log_level = args.log_level.upper()
        save_config(args.config, cfg)

    setup_logging(cfg.log_file, cfg.log_level)
    _LOGGER.info("Starting DJConnect Pi client")
    if _LOGGER.isEnabledFor(logging.INFO):
        log_raspberry_pi_system_info()

    # Select the Pi5 GPU path once at startup so its local toggle can switch
    # material without restarting. Explicit operator backend settings win.
    configure_graphics(cfg, os.environ)
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    apply_palette(app)
    app_icon = QIcon()
    for size in (16, 20, 24, 32, 40, 64, 128):
        app_icon.addFile(str(files("djconnect_pi.qml").joinpath(f"brand-mark-{size}.png")))
    app_icon.addFile(str(files("djconnect_pi.qml").joinpath("app-icon.png")))
    app.setWindowIcon(app_icon)
    QPixmapCache.setCacheLimit(QT_PIXMAP_CACHE_LIMIT_KB)
    engine = QQmlApplicationEngine()
    backend = DJConnectBackend(args.config)
    backend.observeLocalAudioDevices()
    backend.setApplicationForeground(app.applicationState() == Qt.ApplicationState.ApplicationActive)
    app.applicationStateChanged.connect(lambda state: backend.setApplicationForeground(state == Qt.ApplicationState.ApplicationActive))
    engine.rootContext().setContextProperty("djconnect", backend)
    engine.rootContext().setContextProperty("startWindowed", args.windowed)
    qml_path = files("djconnect_pi.qml").joinpath("Main.qml")
    engine.load(str(qml_path))
    if not engine.rootObjects():
        raise SystemExit(1)
    app.aboutToQuit.connect(lambda: [obj.deleteLater() for obj in engine.rootObjects()])
    app.aboutToQuit.connect(backend.shutdown)
    if args.exit_after_ms > 0:
        QTimer.singleShot(args.exit_after_ms, app.quit)
    raise SystemExit(app.exec())


def _format_logs_for_display(data: str, max_chars: int = 12000) -> str:
    lines = data.splitlines()[-220:]
    formatted: list[str] = []
    for line in reversed(lines):
        match = LOG_LINE_RE.match(line)
        if not match:
            formatted.append(line)
            continue
        level = LOG_LEVEL_SHORT.get(match.group("level"), match.group("level")[:3])
        formatted.append(f"{match.group('time')} {level} {match.group('rest')}")
    result = "\n".join(formatted)
    return result[-max_chars:] if len(result) > max_chars else result


def _read_tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(-max_bytes, 2)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _square_wave_pcm(frequency: int, duration_ms: int, sample_rate: int) -> bytes:
    if sample_rate <= 0:
        return b""
    sample_count = max(1, int(sample_rate * duration_ms / 1000))
    period = max(1, int(sample_rate / max(1, frequency)))
    amplitude = 1400
    data = bytearray()
    for index in range(sample_count):
        envelope = 1.0 - (index / sample_count)
        value = int((amplitude if (index % period) < period / 2 else -amplitude) * envelope)
        data.extend(value.to_bytes(2, "little", signed=True))
    return bytes(data)


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def cached_image_url(url: str, ttl_seconds: int = 24 * 60 * 60, timeout_seconds: float = 8) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url

    try:
        cache_dir = DEFAULT_LOG_PATH.parent.parent / "cache" / "album-art"
        cache_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".img"
        target = cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}{suffix}"
        if target.exists() and time.time() - target.stat().st_mtime < ttl_seconds:
            return target.as_uri()
        response = requests.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        target.write_bytes(response.content)
        return target.as_uri()
    except Exception as exc:  # noqa: BLE001 - remote artwork is an optional cache enhancement
        _LOGGER.debug("Album art cache fallback for %s: %s", url, exc)
        return url


def prepare_media_artwork(items: list[dict[str, object]]) -> list[dict[str, object]]:
    for item in items:
        image_url = _image_url_from(item)
        if image_url:
            item["imageUrl"] = cached_image_url(image_url)
    return items


def demo_queue_items() -> list[dict[str, object]]:
    return [
        {
            "title": "Midnight City",
            "subtitle": "M83",
            "uri": "demo:track:0",
            "duration": 244,
            "insight_key": "demo_insight_0",
            "imageUrl": "",
            "tint": "#d946ef",
        },
        {
            "title": "Sweet Disposition",
            "subtitle": "The Temper Trap",
            "uri": "demo:track:1",
            "duration": 232,
            "insight_key": "demo_insight_1",
            "imageUrl": "",
            "tint": "#a78bfa",
        },
        {
            "title": "Electric Feel",
            "subtitle": "MGMT",
            "uri": "demo:track:2",
            "duration": 228,
            "insight_key": "demo_insight_2",
            "imageUrl": "",
            "tint": "#38bdf8",
        },
    ]


def demo_playlist_items() -> list[dict[str, object]]:
    return [
        {"title": "Friday Night", "subtitle": "", "uri": "demo:playlist:night", "imageUrl": "", "tint": "#d946ef"},
        {"title": "Dinner Vibes", "subtitle": "", "uri": "demo:playlist:dinner", "imageUrl": "", "tint": "#64748b"},
        {"title": "DJConnect", "subtitle": "", "uri": "demo:playlist:djconnect", "imageUrl": "", "tint": "#8b5cf6"},
    ]


def parse_queue_items(data: dict[str, object]) -> list[dict[str, object]]:
    return _parse_queue_snapshot(data) or []


def _parse_queue_snapshot(data: dict[str, object]) -> list[dict[str, object]] | None:
    raw_queue = _first_present(data, ("queue",))
    context_uri = str(_first_present(data, ("context_uri", "contextUri")) or "")
    if isinstance(raw_queue, dict):
        raw_items = raw_queue.get("items")
        context_uri = str(raw_queue.get("context_uri") or raw_queue.get("contextUri") or raw_queue.get("queue_context") or raw_queue.get("queueContext") or context_uri)
    elif isinstance(raw_queue, list):
        raw_items = raw_queue
    else:
        raw_items = None
    if not isinstance(raw_items, list):
        return None
    return compact_adjacent_tracks([
        {**parsed, "sourceIndex": source_index}
        for source_index, item in enumerate(raw_items[:100])
        if isinstance(item, dict) and (parsed := _queue_item(item, context_uri))
    ])


def parse_playlist_items(data: dict[str, object]) -> list[dict[str, object]]:
    return _parse_playlist_snapshot(data) or []


def _parse_playlist_snapshot(data: dict[str, object]) -> list[dict[str, object]] | None:
    raw_items = _first_present(data, ("playlists", "items"))
    if not isinstance(raw_items, list):
        return None
    return [parsed for item in raw_items[:100] if isinstance(item, dict) and (parsed := _playlist_item(item))]


def _playlist_item(item: dict[str, object]) -> dict[str, object] | None:
    title = str(item.get("name") or item.get("title") or item.get("display_title") or item.get("track_name") or "")
    uri = playlist_command_target(item)
    if not title or not uri:
        return None
    subtitle = str(item.get("owner") or item.get("owner_name") or item.get("description")
                   or item.get("artist") or item.get("artist_name") or item.get("artists")
                   or item.get("subtitle") or item.get("album") or "")
    return {
        "title": title,
        "subtitle": subtitle,
        "uri": uri,
        "imageUrl": _image_url_from(item),
        "tint": "#8b5cf6",
    }


def _queue_item(item: dict[str, object], queue_context_uri: str = "") -> dict[str, object] | None:
    title = str(item.get("title") or item.get("name") or item.get("display_title") or item.get("track_name") or "")
    artist, album = queue_artist_and_album(item)
    uri = str(item.get("uri") or item.get("id") or item.get("track_uri") or "")
    context_uri = str(item.get("context_uri") or item.get("contextUri") or item.get("queue_context") or item.get("queueContext") or queue_context_uri)
    index = item.get("index")
    image_url = _queue_image_url_from(item)
    return {
        "title": title,
        "subtitle": artist,
        "artist": artist,
        "album": album,
        "uri": uri,
        "imageUrl": image_url,
        "tint": "#38bdf8",
        "contextUri": context_uri,
        "index": index if type(index) is int and index >= 0 else None,
    }


def media_item_payload(command: str, item: object) -> dict[str, object]:
    if isinstance(item, str):
        text = item.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {}
            if not isinstance(parsed, dict):
                return {}
            item_data = parsed
            uri = str(item_data.get("uri") or item_data.get("value") or "").strip()
        else:
            uri = text
            item_data = {"uri": uri}
    elif isinstance(item, dict):
        item_data = item
        uri = str(item_data.get("uri") or item_data.get("value") or "").strip()
    else:
        return {}
    if not uri:
        return {}
    if command == "start_playlist":
        return {"value": uri, "uri": uri, "context_uri": uri}

    value: dict[str, object] = {"uri": uri}
    title = str(item_data.get("title") or "").strip()
    artist = str(item_data.get("artist") or item_data.get("subtitle") or "").strip()
    context_uri = str(
        item_data.get("context_uri")
        or item_data.get("contextUri")
        or item_data.get("queue_context")
        or item_data.get("queueContext")
        or ""
    ).strip()
    index = item_data.get("index")
    if type(index) is not int or index < 0:
        index = item_data.get("sourceIndex")
    if title:
        value["title"] = title
    if artist:
        value["artist"] = artist
    if type(index) is int and index >= 0:
        value["index"] = index
    if context_uri:
        value["context_uri"] = context_uri
        if _context_supports_offset(context_uri):
            value["offset_uri"] = uri
    return {"value": value, "play": True}


def parse_ask_dj_messages(data: dict[str, object]) -> list[dict[str, object]]:
    raw_messages = _first_present(data, ("messages", "history"))
    parsed: list[dict[str, object]] = []
    if isinstance(raw_messages, list):
        for index, item in enumerate(raw_messages):
            if isinstance(item, dict):
                message = _ask_dj_message(item)
                if message:
                    message["_source_index"] = index
                    parsed.append(message)
        if parsed:
            return parsed
    parsed.extend(_ask_dj_legacy_response_messages(data))
    return parsed


def _contains_playback_payload(data: dict[str, object]) -> bool:
    playback = data.get("playback")
    if isinstance(playback, dict) and bool(playback):
        return True
    return any(
        data.get(key) not in (None, "")
        for key in (
            "title",
            "track",
            "track_name",
            "last_track",
            "artist",
            "artists",
            "image_url",
            "album_image_url",
            "is_playing",
            "playing",
            "duration",
            "duration_seconds",
            "duration_ms",
        )
    )


def _ask_dj_message(item: dict[str, object]) -> dict[str, object] | None:
    if item.get("status") == "source_withdrawn":
        return None
    assistant_payload, text, user_text = _ask_dj_message_content(item)
    role = str(item.get("role") or item.get("sender") or "").strip().lower()
    kind = str(item.get("message_kind") or item.get("kind") or "").strip().lower()
    recent_history = _is_recently_played_history(item)
    track_insight = _is_track_insight(item)
    if not text and track_insight:
        text = _track_insight_text(item)
    if _ask_dj_message_is_empty(item, text, user_text, track_insight):
        return None
    role, text = _ask_dj_message_role_and_text(role, kind, text, user_text)
    actions = _ask_dj_message_actions(item, track_insight)
    text = _ask_dj_display_text(text, actions)
    track_insight_data = _track_insight_data(item) if track_insight else {}
    announcement = _ask_dj_announcement(item)
    message = _ask_dj_message_metadata(item)
    message.update(
        {
        "role": role,
        "messageKind": kind or role,
        "origin": str(item.get("origin") or ""),
        "text": text,
        "created_at": str(item.get("created_at") or item.get("timestamp") or item.get("server_time") or ""),
        "images": _ask_dj_images(item.get("images")),
        "items": _ask_dj_message_items(item, assistant_payload, track_insight_data, track_insight, recent_history),
        "links": _ask_dj_links(item.get("links"), item.get("sources")),
        "actions": actions,
        "announcementDelivery": str(announcement.get("delivery") or ""),
        "announcementOutput": str(announcement.get("output") or ""),
        "announcementTargetName": str(announcement.get("target_name") or ""),
        "announcementWarnings": announcement.get("warnings") or [],
        "audioUrl": "",
        "audioType": str(item.get("audio_type") or item.get("audioType") or ""),
        "trackInsight": track_insight,
        "trackInsightData": track_insight_data,
        "musicDnaMatch": _track_insight_music_dna_match(track_insight_data) if track_insight else "",
        "analysis": _track_insight_analysis(track_insight_data) if track_insight else {},
        }
    )
    message["displayTime"] = _ask_dj_display_time(message["created_at"])
    return message


def _ask_dj_message_metadata(item: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(item.get("id") or item.get("message_id") or item.get("server_id") or ""),
        "contextEligible": False,
        "client_message_id": str(item.get("client_message_id") or ""),
        "exchange_id": str(item.get("exchange_id") or ""),
        "exchange_order": _optional_int(item.get("exchange_order")),
        "history_revision": _optional_int(item.get("history_revision") or item.get("revision")),
        "server_order": _optional_int(item.get("server_order") or item.get("order") or item.get("sequence")),
    }


def _ask_dj_message_content(item: dict[str, object]) -> tuple[dict[str, object], str, str]:
    assistant_message = item.get("assistant_message")
    assistant_payload = assistant_message if isinstance(assistant_message, dict) else {}
    assistant_text = _first_present(assistant_payload, ("text", "dj_text", "message"))
    text = str(
        _first_present(item, ("text", "dj_text", "message"))
        or assistant_text
        or (assistant_message if isinstance(assistant_message, str) else "")
        or item.get("content")
        or ""
    ).strip()
    return assistant_payload, text, str(item.get("user_message") or "").strip()


def _ask_dj_message_is_empty(item: dict[str, object], text: str, user_text: str, track_insight: bool) -> bool:
    if text or user_text or track_insight:
        return False
    return not any(isinstance(item.get(key), list) and item.get(key) for key in ("images", "items", "links", "sources", "playback_actions", "confirmation_actions"))


def _ask_dj_message_role_and_text(role: str, kind: str, text: str, user_text: str) -> tuple[str, str]:
    if role not in {"user", "assistant", "system", "status"}:
        role = "user" if user_text and not text else "status" if kind == "status" else "system" if kind == "system" else "assistant"
    if user_text and not text:
        text = user_text
    return ("system" if kind == "system" else role), text


def _ask_dj_message_actions(item: dict[str, object], track_insight: bool) -> list[dict[str, object]]:
    playback_actions = item.get("playback_actions")
    if track_insight:
        return _ask_dj_actions(playback_actions, None) if isinstance(playback_actions, list) else []
    return _ask_dj_actions(playback_actions, item.get("confirmation_actions"))


def _ask_dj_message_items(
    item: dict[str, object],
    assistant_payload: dict[str, object],
    track_insight_data: dict[str, object],
    track_insight: bool,
    recent_history: bool,
) -> list[dict[str, object]]:
    if track_insight:
        return _track_insight_items(track_insight_data)
    items = _first_present(item, ("items",))
    if recent_history:
        return _ask_dj_recently_played_items(items, assistant_payload.get("items"))
    return _ask_dj_items(items, assistant_payload.get("items"))


def _ask_dj_announcement(item: dict[str, object]) -> dict[str, object]:
    raw = item.get("announcement")
    if not isinstance(raw, dict):
        return {}
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    warnings = [str(value).strip() for value in raw.get("warnings", []) if str(value).strip()] if isinstance(raw.get("warnings"), list) else []
    return {
        "output": str(raw.get("output") or ""),
        "delivery": str(raw.get("delivery") or ""),
        "target_name": str(target.get("name") or target.get("entity_id") or raw.get("speaker_name") or ""),
        "warnings": warnings,
    }


def _is_recently_played_history(item: dict[str, object]) -> bool:
    intent = item.get("intent")
    if isinstance(intent, dict):
        return str(intent.get("intent") or "").strip() == "recently_played_history"
    return str(intent or item.get("action") or "").strip() == "recently_played_history"


def _is_track_insight(item: dict[str, object]) -> bool:
    intent = item.get("intent")
    intent_name = ""
    intent_action = ""
    if isinstance(intent, dict):
        intent_name = str(intent.get("intent") or "").strip()
        intent_action = str(intent.get("action") or "").strip()
    elif isinstance(intent, str):
        intent_name = intent.strip()
    action = str(item.get("action") or intent_action or "").strip()
    response_type = str(item.get("type") or "").strip()
    open_screen = str(item.get("open_screen") or "").strip()
    return (
        intent_name == "track_insight"
        or action == "track_insight"
        or response_type == "track_insight"
        or open_screen == "track_insight"
        or isinstance(item.get("track_insight"), dict)
        or (isinstance(item.get("track"), dict) and isinstance(item.get("analysis"), dict))
    )


def _current_track_insight_proof(subject: object, recording: str, requested_at: float, now: float) -> tuple[str, float] | None:
    if (not isinstance(subject, dict) or type(subject.get("schema_version")) is not int
            or subject["schema_version"] != 1 or subject.get("mode") != "current"
            or not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", recording)
            or subject.get("media_identity") != recording or now < requested_at):
        return None
    observation = subject.get("observation_id")
    expires_in, expires_at = subject.get("expires_in"), subject.get("expires_at")
    if (not isinstance(observation, str) or not observation or len(observation) > 512
            or type(expires_in) not in (int, float) or not 0 < expires_in <= 30
            or type(expires_at) not in (int, float)):
        return None
    try:
        if len(observation.encode("utf-8")) > 512 or not math.isfinite(expires_at):
            return None
    except (UnicodeEncodeError, OverflowError):
        return None
    deadline = min(requested_at + expires_in, expires_at)
    return (observation, deadline) if deadline > now else None


def _track_insight_payload(item: dict[str, object]) -> dict[str, object]:
    payload = item.get("track_insight")
    return payload if isinstance(payload, dict) else item


def _track_insight_text(item: dict[str, object]) -> str:
    payload = _track_insight_payload(item)
    error = str(payload.get("error") or item.get("error") or "").strip()
    if error == "no_track_playing":
        return "Er speelt nu geen track."
    if error == "rate_limited":
        return "Track Insight is even beperkt. Probeer het zo opnieuw."
    analysis = payload.get("analysis")
    if isinstance(analysis, dict) and _track_insight_unmeasured(analysis):
        explanation = analysis.get("full_text") or analysis.get("summary")
        if isinstance(explanation, str) and explanation.strip():
            return explanation.strip()
    track = payload.get("track")
    if isinstance(track, dict):
        title = str(track.get("title") or track.get("name") or "").strip()
        artist = str(track.get("artist") or track.get("artists") or "").strip()
        if title and artist:
            return f"Track Insight: {artist} - {title}"
        if title:
            return f"Track Insight: {title}"
    return "Track Insight"


def _track_insight_data(item: dict[str, object]) -> dict[str, object]:
    payload = _track_insight_payload(item)
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    visualizer = _first_dict(
        payload.get("visualisation"),
        payload.get("visualization"),
        payload.get("visualizer"),
        analysis.get("visualisation"),
        analysis.get("visualization"),
        analysis.get("visualizer"),
    )
    return {
        "track": payload.get("track") if isinstance(payload.get("track"), dict) else {},
        "analysis": _track_insight_allowed_analysis(analysis),
        "verifiedCredit": track_insight_verified_credit(analysis),
        "music_dna": payload.get("music_dna") if isinstance(payload.get("music_dna"), dict) else {},
        "visual_profile": payload.get("visual_profile") if isinstance(payload.get("visual_profile"), dict) else {},
        "visualizer": visualizer,
        "colors": _first_present(payload, ("colors", "palette", "gradient")),
        "mood_context": payload.get("mood_context") if isinstance(payload.get("mood_context"), dict) else {},
        "language": str(payload.get("language") or item.get("language") or "").strip(),
        "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
        "error": str(payload.get("error") or item.get("error") or "").strip(),
    }


def _track_insight_allowed_analysis(analysis: dict[str, object]) -> dict[str, object]:
    allowed = {
        "summary",
        "full_text",
        "fullText",
        "genre",
        "subgenre",
        "mood",
        "vibe",
        "texture",
        "emotional_tone",
        "energy",
        "danceability",
        "intensity",
        "confidence",
        "basis",
        "measured_audio",
        "production_notes",
        "instrumentation",
        "arrangement_notes",
        "listening_cues",
        "similar_tracks",
        "why_it_fits",
        "whyItFits",
        "reasons",
        "sections",
    }
    return {key: value for key, value in analysis.items() if key in allowed}


def _track_insight_unmeasured(analysis: dict[str, object]) -> bool:
    return (analysis.get("measured_audio") is False or analysis.get("basis") in (
        "unmeasured_metadata_only_v1", "qualified_credit_and_interpretation_v1",
    ))


def _track_insight_music_dna_match(data: dict[str, object]) -> str:
    music_dna = data.get("music_dna")
    if not isinstance(music_dna, dict):
        return ""
    raw = music_dna.get("match_percent")
    if isinstance(raw, (int, float)):
        return f"{max(0, min(100, int(raw)))}%"
    return str(raw or "").strip()


def _track_insight_visualizer(data: dict[str, object], items: list[dict[str, object]], sections: list[dict[str, object]]) -> dict[str, object]:
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
    if _track_insight_unmeasured(analysis):
        return {}
    track = data.get("track") if isinstance(data.get("track"), dict) else {}
    visualizer = data.get("visualizer") if isinstance(data.get("visualizer"), dict) else {}
    colors = _track_visualizer_colors(visualizer, data)
    bars = _track_visualizer_bars(visualizer)
    source = "backend" if bars else "fallback"
    if not bars:
        bars = _track_visualizer_fallback_bars(data, items, sections)
    if not bars:
        return {}
    return {
        "available": True,
        "source": source,
        "bars": bars[:48],
        "colors": colors[:4],
        "title": str(track.get("title") or "").strip(),
        "artist": str(track.get("artist") or "").strip(),
        "mood": str(analysis.get("mood") or analysis.get("vibe") or "").strip(),
        "genre": str(analysis.get("genre") or analysis.get("subgenre") or "").strip(),
        "texture": str(analysis.get("texture") or "").strip(),
    }


def _track_visualizer_bars(visualizer: dict[str, object]) -> list[float]:
    value = _first_present(visualizer, ("bars", "levels", "waveform", "spectrum", "values"))
    if isinstance(value, dict):
        value = value.get("values") or value.get("bars") or value.get("levels")
    if not isinstance(value, list):
        return []
    bars: list[float] = []
    for item in value:
        raw = item
        if isinstance(item, dict):
            raw = _first_present(item, ("value", "height", "level", "amplitude"))
        number = _visualizer_number(raw)
        if number is not None:
            bars.append(number)
    return _normalize_visualizer_bars(bars)


def _track_visualizer_fallback_bars(data: dict[str, object], items: list[dict[str, object]], sections: list[dict[str, object]]) -> list[float]:
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
    metrics: list[float] = []
    for key in ("energy", "danceability", "intensity", "confidence"):
        number = _visualizer_number(analysis.get(key))
        if number is not None:
            metrics.append(number)
    for item in items:
        if isinstance(item, dict):
            number = _visualizer_number(item.get("value"))
            if number is not None:
                metrics.append(number)
    if not metrics:
        seed_text = " ".join(
            str(value)
            for value in (
                analysis.get("mood"),
                analysis.get("vibe"),
                analysis.get("genre"),
                analysis.get("texture"),
                *(section.get("title", "") for section in sections if isinstance(section, dict)),
            )
            if value
        )
        if seed_text:
            metrics = [((ord(char) % 13) + 5) / 18 for char in seed_text[:16]]
    if not metrics:
        metrics = [0.45, 0.58, 0.52, 0.66]
    bars: list[float] = []
    for index in range(36):
        base = metrics[index % len(metrics)]
        wave = 0.5 + 0.5 * abs(((index % 12) - 6) / 6)
        bars.append(max(0.18, min(1.0, base * 0.72 + wave * 0.28)))
    return bars


def _track_visualizer_colors(visualizer: dict[str, object], data: dict[str, object]) -> list[str]:
    visual_profile = data.get("visual_profile") if isinstance(data.get("visual_profile"), dict) else {}
    colors: list[str] = []
    for source in (
        visualizer.get("colors"),
        visualizer.get("palette"),
        visualizer.get("gradient"),
        data.get("colors"),
        visual_profile.get("palette"),
        visual_profile.get("colors"),
    ):
        colors.extend(_color_list(source))
    deduped: list[str] = []
    for color in colors:
        if color not in deduped:
            deduped.append(color)
    return deduped


def _color_list(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key in ("start", "mid", "middle", "end", "primary", "secondary", "accent"):
            color = _safe_hex_color(value.get(key))
            if color:
                result.append(color)
        return result
    if isinstance(value, list):
        return [color for color in (_safe_hex_color(item) for item in value) if color]
    color = _safe_hex_color(value)
    return [color] if color else []


def _safe_hex_color(value: object) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else ""


def _visualizer_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
        if not match:
            return None
        number = float(match.group(0))
    if number > 1:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def _normalize_visualizer_bars(values: list[float]) -> list[float]:
    if not values:
        return []
    peak = max(values)
    if peak > 0:
        values = [value / peak for value in values]
    return [max(0.08, min(1.0, value)) for value in values]


def _track_insight_analysis(data: dict[str, object]) -> dict[str, object]:
    analysis = data.get("analysis")
    music_dna = data.get("music_dna")
    sections: list[dict[str, object]] = []
    if isinstance(analysis, dict) and analysis.get("basis") != "unmeasured_metadata_only_v1":
        sections.extend(_track_insight_analysis_sections(analysis, data.get("track")))
    sections.extend(_track_insight_context_sections(data.get("mood_context"), music_dna))
    return {
        "sections": sections[:12],
        "timeline": [],
        "djTips": [],
        "limitations": [],
        "providers": [],
        "metadata": {},
    }


def _track_insight_analysis_sections(analysis: dict[str, object], track: object) -> list[dict[str, object]]:
    sections = _track_insight_primary_sections(analysis, track)
    for key, title in (
        ("production_notes", "Production"),
        ("instrumentation", "Instrumentation"),
        ("arrangement_notes", "Arrangement"),
        ("listening_cues", "Listening cues"),
        ("similar_tracks", "Similar tracks"),
    ):
        section = _track_insight_detail_section(analysis, key, title)
        if section:
            sections.append(section)
    why = _string_list(analysis.get("why_it_fits") or analysis.get("whyItFits") or analysis.get("reasons"))
    if why:
        sections.append(_track_insight_section("why_it_fits", "music_dna", "Why it fits you", details=why))
    return [*sections, *_ask_dj_analysis_sections(analysis.get("sections"))]


def _track_insight_primary_sections(analysis: dict[str, object], track: object) -> list[dict[str, object]]:
    summary = str(analysis.get("summary") or analysis.get("full_text") or analysis.get("fullText") or "").strip()
    genre = str(analysis.get("genre") or "").strip()
    subgenre = str(analysis.get("subgenre") or "").strip()
    track_genres = _string_list(track.get("genres")) if isinstance(track, dict) else []
    genre_details = [value for value in [subgenre, *track_genres] if value and value != genre]
    vibe = str(analysis.get("vibe") or analysis.get("mood") or analysis.get("texture") or analysis.get("emotional_tone") or "").strip()
    sections: list[dict[str, object]] = []
    if summary:
        sections.append(_track_insight_section("summary", "summary", "Summary", body=summary))
    if genre or genre_details:
        sections.append(_track_insight_section("genre", "genre", "Genre", body=genre, details=genre_details))
    if vibe:
        sections.append(_track_insight_section("vibe", "vibe", "Vibe", body=vibe))
    return sections


def _track_insight_detail_section(analysis: dict[str, object], key: str, title: str) -> dict[str, object] | None:
    details = _string_list(analysis.get(key))
    body = str(analysis.get(key) or "").strip() if not details else ""
    if not body and not details:
        return None
    return _track_insight_section(key, key, title, body=body, details=details)


def _track_insight_context_sections(mood_context: object, music_dna: object) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    if isinstance(mood_context, dict):
        zone = str(mood_context.get("zone") or mood_context.get("mood_zone") or mood_context.get("label") or "").strip()
        if zone:
            sections.append(_track_insight_section("mood_context", "mood_context", "Mood context", body=zone, metadata_context=True))
    if isinstance(music_dna, dict):
        summary = str(music_dna.get("summary") or "").strip()
        if summary:
            sections.append(_track_insight_section("music_dna", "music_dna", "This expands your Music DNA.", body=summary))
    return sections


def _track_insight_section(
    section_id: str,
    kind: str,
    title: str,
    *,
    body: str = "",
    details: list[str] | None = None,
    metadata_context: bool = False,
) -> dict[str, object]:
    return {
        "id": section_id,
        "kind": kind,
        "title": title,
        "body": body,
        "source": "",
        "confidence": "",
        "details": details or [],
        "metadataContext": metadata_context,
    }


def _ask_dj_legacy_response_messages(data: dict[str, object]) -> list[dict[str, object]]:
    user_text = str(data.get("user_message") or "").strip()
    assistant_message = data.get("assistant_message")
    assistant_payload = assistant_message if isinstance(assistant_message, dict) else {}
    assistant_text = str(
        assistant_payload.get("text")
        or assistant_payload.get("dj_text")
        or assistant_payload.get("message")
        or (assistant_message if isinstance(assistant_message, str) else "")
        or data.get("dj_text")
        or data.get("text")
        or data.get("message")
        or data.get("content")
        or ""
    ).strip()
    messages: list[dict[str, object]] = []
    if user_text:
        user = _ask_dj_message({**data, "role": "user", "text": user_text, "assistant_message": "", "message": "", "content": ""})
        if user:
            user["exchange_order"] = user.get("exchange_order") if user.get("exchange_order") is not None else 0
            user["_source_index"] = 0
            messages.append(user)
    if assistant_text:
        assistant = _ask_dj_message({**data, "role": "assistant", "text": assistant_text, "user_message": ""})
        if assistant:
            assistant["exchange_order"] = assistant.get("exchange_order") if assistant.get("exchange_order") is not None else 1
            assistant["_source_index"] = 1
            messages.append(assistant)
    if messages:
        return messages
    response_message = _ask_dj_message(data)
    if response_message:
        response_message["_source_index"] = 0
        return [response_message]
    return []


def _ask_dj_images(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    images: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _image_url_from(item)
        if url:
            images.append({"url": cached_image_url(url), "title": str(item.get("title") or item.get("alt") or "")})
    return images[:6]


def _ask_dj_links(*values: object) -> list[dict[str, object]]:
    links: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                links.append({"title": item, "url": item, "kind": "link"})
            elif isinstance(item, dict):
                url = str(item.get("url") or item.get("href") or "").strip()
                source = str(item.get("source") or "").strip()
                title = str(item.get("title") or item.get("label") or item.get("name") or source or item.get("kind") or url).strip()
                if url or title:
                    links.append({"title": title, "url": url, "kind": str(item.get("kind") or source or "source"), "source": source})
    return links[:8]


def _ask_dj_items(*values: object) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("track") or item.get("album") or item.get("artist") or "").strip()
            item_kind = str(item.get("kind") or item.get("type") or "").strip()
            subtitle = str(item.get("subtitle") or item.get("artist") or item.get("album") or item.get("description") or "").strip()
            when = str(item.get("played_at") or item.get("time") or item.get("timestamp") or item.get("created_at") or "").strip()
            image_url = _image_url_from(item)
            if title or subtitle or image_url:
                items.append(
                    {
                        "title": title,
                        "subtitle": subtitle,
                        "value": "",
                        "time": when,
                        "kind": item_kind,
                        "source": str(item.get("source") or "").strip(),
                        "confidence": "",
                        "imageUrl": cached_image_url(image_url) if image_url else "",
                        "trackInsightMetric": False,
                        "musicDna": False,
                    }
                )
    return items[:20]


def _ask_dj_recently_played_items(*values: object) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            subtitle = str(item.get("subtitle") or item.get("artist") or "").strip()
            if not title and not subtitle:
                continue
            items.append(
                {
                    "title": title,
                    "subtitle": subtitle,
                    "value": str(item.get("value") or ""),
                    "time": str(item.get("played_at") or item.get("time") or item.get("timestamp") or "").strip(),
                    "kind": str(item.get("kind") or item.get("type") or "").strip(),
                    "source": str(item.get("source") or "").strip(),
                    "confidence": str(item.get("confidence") or ""),
                    "imageUrl": str(item.get("image_url") or item.get("imageUrl") or ""),
                    "trackInsightMetric": False,
                    "musicDna": False,
                }
            )
    return items[:20]


def _track_insight_items(data: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    music_dna = data.get("music_dna")
    analysis = data.get("analysis")
    match_percent = _track_insight_music_dna_match(data)
    if match_percent:
        items.append(
            {
                "title": "Music DNA Match",
                "subtitle": "",
                "value": match_percent,
                "time": "",
                "kind": "music_dna_match",
                "source": "",
                "confidence": "",
                "imageUrl": "",
                "trackInsightMetric": True,
                "musicDna": True,
            }
        )
    if isinstance(analysis, dict) and not _track_insight_unmeasured(analysis):
        for key, title in (("energy", "Energy"), ("danceability", "Danceability"), ("intensity", "Intensity"), ("confidence", "Confidence")):
            raw = analysis.get(key)
            if raw not in (None, ""):
                value = _metric_percent(raw)
                items.append(
                    {
                        "title": title,
                        "subtitle": "",
                        "value": value,
                        "time": "",
                        "kind": "track_insight_metric",
                        "source": "",
                        "confidence": "",
                        "imageUrl": "",
                        "trackInsightMetric": True,
                        "musicDna": False,
                    }
                )
                if (key != "confidence" and not bounded_text(analysis.get("basis"), 128).strip()
                        and not data.get("verifiedCredit") and type(raw) in (int, float) and 0 <= raw <= 1):
                    items[-1]["shareFraction"] = float(raw)
    if isinstance(music_dna, dict):
        traits = _string_list(music_dna.get("traits") or music_dna.get("signals"))
        for trait in traits[:4]:
            items.append(
                {
                    "title": trait,
                    "subtitle": "",
                    "value": "",
                    "time": "",
                    "kind": "music_dna_trait",
                    "source": "",
                    "confidence": "",
                    "imageUrl": "",
                    "trackInsightMetric": False,
                    "musicDna": True,
                }
            )
    return items[:20]


def _ask_dj_analysis_sections(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    sections: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("id") or item.get("section_id") or item.get("kind") or item.get("title") or "").strip()
        title = str(item.get("title") or item.get("label") or section_id.replace("_", " ").title()).strip()
        if section_id == "metadata_context" and not (item.get("title") or item.get("label")):
            title = "Context"
        body = str(item.get("body") or item.get("text") or item.get("description") or item.get("value") or "").strip()
        source = str(item.get("source") or item.get("provider") or "").strip()
        confidence = str(item.get("confidence") or "").strip()
        details = _string_list(item.get("details") or item.get("items") or item.get("bullets"))
        if title or body or details:
            sections.append(
                {
                    "id": section_id,
                    "kind": str(item.get("kind") or "").strip(),
                    "title": title,
                    "body": body,
                    "source": source,
                    "confidence": confidence,
                    "details": details,
                    "metadataContext": section_id == "metadata_context" or source == "metabrainz_metadata",
                }
            )
    return sections[:12]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item.get("text") if isinstance(item, dict) else item).strip()
        if text:
            result.append(text)
    return result[:8]


def _metric_percent(value: object) -> str:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 0.0 <= numeric <= 1.0:
            return f"{round(numeric * 100)}%"
        if 1.0 < numeric <= 100.0:
            return f"{round(numeric)}%"
    return str(value).strip()


def _ask_dj_actions(playback_actions: object, confirmation_actions: object) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for value in (playback_actions, confirmation_actions):
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            actions.append(_ask_dj_action(item))
    return actions[:8]


def _ask_dj_action(item: dict[str, object]) -> dict[str, object]:
    kind = str(item.get("kind") or "").strip()
    action_style = str(item.get("action_style") or "")
    is_confirmation = kind == "confirmation" or action_style == "confirmation"
    is_output = _is_output_action(item)
    is_recommendation = kind in {"track", "album", "artist", "playlist", "track_mix"} or action_style == "play_now"
    image_url = _image_url_from(item)
    return {
        "title": _ask_dj_action_title(item, kind, is_confirmation),
        "subtitle": str(item.get("subtitle") or item.get("artist") or item.get("description") or "").strip(),
        "kind": kind,
        "isOutput": is_output,
        "isMedia": is_recommendation and not is_output and not is_confirmation,
        "imageUrl": cached_image_url(image_url) if image_url and is_recommendation else "",
        "payload": json.dumps(item, ensure_ascii=True),
    }


def _ask_dj_action_title(item: dict[str, object], kind: str, is_confirmation: bool) -> str:
    title = str(item.get("button_label") or item.get("label") or item.get("title") or item.get("name") or "").strip()
    if title:
        return title
    if is_confirmation:
        return translate("en", "yes" if str(item.get("response_value") or "").lower() == "yes" else "no")
    if kind == "control" and str(item.get("command") or "").strip() == "save_current_track":
        return translate("en", "favorite_action")
    return translate("en", "play_now")


def _image_url_from(item: dict[str, object]) -> str:
    for key in (
        "image_url",
        "imageUrl",
        "album_image_url",
        "albumImageUrl",
        "album_art_url",
        "albumArtUrl",
        "art_url",
        "artUrl",
        "artwork_url",
        "artworkUrl",
        "media_image_url",
        "mediaImageUrl",
        "entity_picture",
        "thumbnail_url",
        "thumbnailUrl",
        "thumbnail",
        "cover_url",
        "coverUrl",
        "cover",
        "image",
        "picture",
        "images",
        "artworks",
        "thumbnails",
        "url",
    ):
        value = item.get(key)
        url = _image_url_value(value)
        if url:
            return url
    for key in ("album", "track", "playlist", "show", "episode", "item", "metadata"):
        nested = item.get(key)
        if isinstance(nested, dict):
            url = _image_url_from(nested)
            if url:
                return url
    return ""


def _queue_image_url_from(item: dict[str, object]) -> str:
    for key in ("album_image_url", "image_url", "thumbnail_url", "imageUrl", "thumbnailUrl"):
        value = item.get(key)
        if value:
            return str(value)
    return _image_url_from(item)


def _image_url_value(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("http://", "https://", "file://")):
            return text
        return ""
    if isinstance(value, dict):
        return _image_url_from(value)
    if isinstance(value, list):
        candidates: list[dict[str, object]] = [item for item in value if isinstance(item, dict)]
        if candidates:
            candidates.sort(key=lambda item: int(item.get("width") or item.get("height") or 0), reverse=True)
            for candidate in candidates:
                url = _image_url_from(candidate)
                if url:
                    return url
        for item in value:
            url = _image_url_value(item)
            if url:
                return url
    return ""


def _is_output_action(item: dict[str, object]) -> bool:
    values = {
        str(item.get("kind") or "").strip().lower(),
        str(item.get("type") or "").strip().lower(),
        str(item.get("command") or "").strip().lower(),
        str(item.get("action") or "").strip().lower(),
    }
    return bool(values & {"speaker", "speakers", "output", "outputs", "output_device", "set_output", "select_output"})


def _ask_dj_display_text(text: str, actions: list[dict[str, object]]) -> str:
    output_titles = {
        _normalized_action_title(str(action.get("title") or ""))
        for action in actions
        if action.get("isOutput")
    }
    if not text or not output_titles:
        return text
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            title = _normalized_action_title(stripped[2:])
            if title in output_titles:
                continue
        lines.append(line)
    return "\n".join(lines).strip()


def _normalized_action_title(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _ask_dj_message_keys(message: dict[str, object]) -> list[str]:
    keys: list[str] = []
    message_id = str(message.get("id") or "").strip()
    if message_id:
        keys.append(f"id:{message_id}")
    client_message_id = str(message.get("client_message_id") or "").strip()
    role = str(message.get("role") or "").strip()
    if client_message_id and role:
        keys.append(f"client_message_id:{client_message_id}:role:{role}")
    return keys


def _ask_dj_exchange_order(message: dict[str, object]) -> int:
    value = message.get("exchange_order")
    if isinstance(value, int):
        return value
    role = str(message.get("role") or "").strip().lower()
    if role == "user":
        return 0
    if role == "assistant":
        return 1
    return 2


def _ask_dj_order_value(message: dict[str, object]) -> tuple[int, object]:
    for priority, key in enumerate(("history_revision", "server_order", "created_at", "_arrival_index", "_source_index")):
        value = message.get(key)
        if value is None or value == "":
            continue
        return priority, value
    return 99, ""


def _sort_ask_dj_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    exchange_anchor: dict[str, tuple[int, object]] = {}
    for message in messages:
        exchange_id = str(message.get("exchange_id") or "").strip()
        if not exchange_id:
            continue
        order_value = _ask_dj_order_value(message)
        if exchange_id not in exchange_anchor or order_value < exchange_anchor[exchange_id]:
            exchange_anchor[exchange_id] = order_value

    def sort_key(message: dict[str, object]) -> tuple[object, ...]:
        exchange_id = str(message.get("exchange_id") or "").strip()
        order_value = exchange_anchor.get(exchange_id, _ask_dj_order_value(message))
        client_message_id = str(message.get("client_message_id") or "").strip()
        return (
            order_value,
            exchange_id or client_message_id,
            _ask_dj_exchange_order(message),
            _ask_dj_order_value(message),
        )

    return sorted(messages, key=sort_key)


def _sort_ask_dj_messages_newest_first(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return list(reversed(_sort_ask_dj_messages(messages)))


def _ask_dj_display_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        match = re.search(r"\b(\d{2}:\d{2})(?::\d{2})?\b", raw)
        return match.group(1) if match else ""
    return parsed.astimezone().strftime("%H:%M")


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _context_supports_offset(context_uri: str) -> bool:
    return context_uri.startswith(("spotify:playlist:", "spotify:album:", "spotify:show:"))


def _toast_icon_for_context(context: str) -> str:
    return TOAST_CONTEXT_ICONS.get(context.strip().lower(), TOAST_ICON_FALLBACK)


def _first_present(data: dict[str, object], keys: tuple[str, ...]) -> object:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    for container_key in ("data", "result"):
        container = data.get(container_key)
        if isinstance(container, dict):
            value = _first_present(container, keys)
            if value is not None:
                return value
    return None


def _first_dict(*values: object) -> dict[str, object]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _read_music_dna_import(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("invalid_music_dna_import")
        contents = source.read(MUSIC_DNA_IMPORT_MAX_BYTES + 1)
    if len(contents) > MUSIC_DNA_IMPORT_MAX_BYTES:
        raise ValueError("invalid_music_dna_import")
    document = json.loads(contents)
    if not isinstance(document, dict):
        raise ValueError("invalid_music_dna_import")
    envelope = document.get("format") == "djconnect.music_dna.export"
    if "format" in document and not envelope:
        raise ValueError("invalid_music_dna_import")
    if envelope and (document.get("success") is not True or type(document.get("schema_version")) is not int
                     or document["schema_version"] != 1):
        raise ValueError("invalid_music_dna_import")
    response = document.get("profile") if envelope else document
    if (not isinstance(response, dict) or response.get("success") is not True
            or type(response.get("enabled")) is not bool or not isinstance(response.get("profile"), dict)):
        raise ValueError("invalid_music_dna_import")
    profile = response["profile"]
    json.dumps(profile, allow_nan=False)
    preview = {"filename": path.name}
    counts = []
    for field, label in (("track_count", "music_dna_card_tracks"), ("artist_count", "music_dna_card_artists"),
                         ("genre_count", "music_dna_card_genres")):
        value = profile.get(field)
        if type(value) is int and 0 <= value <= 2**53 - 1:
            counts.append({"label": label, "value": value})
    if counts:
        preview["counts"] = counts
    exported = document.get("exported_at") if envelope else None
    if isinstance(exported, str) and timestamp(exported) is not None:
        preview["exportedAt"] = exported
    client_type = document.get("exported_by_client_type") if envelope else None
    if isinstance(client_type, str):
        client_label = {
            "ios": "music_dna_origin_ios",
            "macos": "music_dna_origin_macos",
            "watchos": "music_dna_origin_watchos",
            "raspberry_pi": "music_dna_origin_raspberry_pi",
            "esp32": "music_dna_origin_esp32",
        }.get(client_type)
        if client_label:
            preview["exportClientLabel"] = client_label
    for updated in (response.get("updated_at"), profile.get("updated_at"), preview.get("exportedAt")):
        if isinstance(updated, str) and timestamp(updated) is not None:
            preview["updatedAt"] = updated
            break
    generation = response.get("generation")
    if type(generation) is int and 0 <= generation <= 2**53 - 1:
        preview["generation"] = generation
    return {"success": True, "enabled": response["enabled"], "profile": profile}, preview


def parse_music_dna_profile(data: dict[str, object], *, ha_url: str = "") -> dict[str, object]:
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    enabled = bool(data.get("enabled"))
    summary = str(profile.get("summary") or data.get("summary") or "").strip() if enabled else ""
    return {"enabled": enabled, "summary": summary,
            "sections": _music_dna_cards(profile, ha_url) if enabled else []}


def _music_dna_cards(profile: dict[str, object], ha_url: str) -> list[dict[str, object]]:
    cards = []

    def block(key):
        value = profile.get(key)
        return value if isinstance(value, dict) and value.get("eligible") is not False else {}

    def rows(items, *, artwork=False, limit=5, title_fields=("title", "name", "track_name")):
        result = []
        for item in items[:limit] if isinstance(items, list) else []:
            if isinstance(item, str) and item.strip():
                result.append({"title": item.strip()})
                continue
            if not isinstance(item, dict):
                continue
            title = next((item[key].strip() for key in title_fields
                          if isinstance(item.get(key), str) and item[key].strip()), "")
            if not title:
                continue
            subtitle = " · ".join(value for value in (item.get("artist") or item.get("artist_name"),
                                                       item.get("album") or item.get("album_name"))
                                  if isinstance(value, str) and value.strip())
            count = item.get("play_count", item.get("count"))
            row = {"title": title, "subtitle": subtitle, "value": item.get("formatted") or "",
                   "count": count if type(count) is int and count >= 0 else None}
            if artwork and item.get("image_subject_uri") == item.get("uri") and item.get("image_subject_uri"):
                row.update(_discovery_artwork(item, ha_url))
            result.append(row)
        return result

    def card(key, icon, *, entries=None, groups=None, value="", value_key="", metric=None, average=None, count=None):
        if entries or groups or value or metric is not None or average is not None:
            cards.append({"key": "music_dna_card_" + key, "icon": icon, "rows": entries or [],
                          "groups": groups or [], "value": value, "valueKey": value_key, "metric": metric,
                          "average": average, "count": count})

    def grouped(title, entries, *, shelf=False):
        return [{"key": "music_dna_card_" + title, "rows": entries, "shelf": shelf}] if entries else []

    def playtime_ranking(items):
        entries = rows(items, limit=3)
        for rank, entry in enumerate(entries, 1):
            entry["rank"] = rank
            entry["value"] = bounded_text(entry.get("value"), 64).strip() or "–"
        return entries

    def signal_rows(items):
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            titles = (bounded_text(item.get(key), 256).strip() for key in ("title", "name", "value"))
            primary = next(filter(None, titles), "")
            artist = bounded_text(item.get("artist"), 256).strip() or bounded_text(item.get("artist_name"), 256).strip()
            album = bounded_text(item.get("album"), 256).strip()
            genres = item.get("genres")
            styles = ""
            if isinstance(genres, list):
                styles = ", ".join(filter(None, (bounded_text(genre, 128).strip() for genre in genres[:2])))
            labels = [label for label in (primary, artist, album, styles) if label]
            if labels:
                result.append({"title": labels[0], "subtitle": " · ".join(labels[1:])})
                if len(result) == 4:
                    break
        return result

    def percentages(items, field, labels):
        return [{"labelKey": labels.get(str(item[field]).lower(), ""), "title": str(item[field]),
                 "percent": item["percent"]}
                for item in items[:7] if isinstance(item, dict) and isinstance(item.get(field), str)
                and type(item.get("percent")) in (int, float) and 0 <= item["percent"] <= 100] if isinstance(items, list) else []

    zones = {zone: "mood_" + zone for zone in ("chill", "groove", "energy", "party")}
    dayparts = {value: "music_dna_card_" + key for key, value in
                (("morning", "ochtend"), ("afternoon", "middag"), ("evening", "avond"), ("night", "nacht"))}
    dayparts.update({key.removeprefix("music_dna_card_"): key for key in tuple(dayparts.values())})
    weekdays = {day: "music_dna_card_" + day for day in
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    weekdays.update({nl: weekdays[en] for nl, en in zip(
        ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
        tuple(weekdays), strict=True)})

    playtime = block("playtime")
    formatted_duration = playtime.get("formatted_total")
    duration = formatted_duration.strip() if isinstance(formatted_duration, str) else ""
    if not duration:
        seconds = playtime.get("total_seconds")
        duration = f"{seconds}s" if type(seconds) is int and seconds > 0 else ""
    genres = ", ".join(entry["title"] for entry in rows(profile.get("favorite_genres"), limit=10))
    tracks = profile.get("recent_tracks")
    artists = profile.get("favorite_artists")
    track_count = profile.get("track_count")
    artist_count = profile.get("artist_count")
    tracks_total = type(track_count) is int and track_count >= 0
    if type(artist_count) is not int or artist_count < 0:
        artist_count = len(artists) if isinstance(artists, list) else None
    mood = block("mood")
    mood_average = mood.get("average")
    has_mood_average = type(mood_average) in (int, float) and 0 <= mood_average <= 100
    mood_percent = mood_average if has_mood_average else mood.get("value")
    zone = (mood.get("average_zone") if has_mood_average else None) or mood.get("zone") or mood.get("average_zone")
    mood_zone_key = zones.get(zone.lower(), "") if isinstance(zone, str) else ""
    summary = [{"labelKey": "music_dna_card_" + key, "title": str(value)}
               for key, value in (("playtime", duration),
                                  ("tracks", track_count if tracks_total else None),
                                  ("recent_tracks", len(tracks) if not tracks_total and isinstance(tracks, list) else None),
                                  ("artists", artist_count),
                                  ("favorite_genres", genres)) if value is not None and value != ""]
    if mood_zone_key:
        summary.append({"labelKey": "music_dna_card_profile_mood", "valueKey": mood_zone_key})
    card("summary", "waveform", entries=summary)
    card("favorite_genres", "music", value=genres)
    card("favorite_artists", "session_persona", groups=grouped("", rows(artists, artwork=True, limit=12), shelf=True))
    if type(mood_percent) in (int, float) and 0 <= mood_percent <= 100:
        sample_count = mood.get("sample_count")
        card("profile_mood", mood_zone_key or "waveform", metric=mood_percent,
             count=sample_count if type(sample_count) is int and sample_count >= 0 else None,
             entries=[{"valueKey": mood_zone_key}] if mood_zone_key else [])
    energy = block("energy_profile")
    percent = energy.get("energy_percent")
    sample_count = energy.get("sample_count")
    energy_zone = energy.get("zone")
    if type(sample_count) is int and sample_count > 0 and type(percent) in (int, float) and 0 <= percent <= 100:
        energy_rows = [{"valueKey": zones[energy_zone.lower()]}] if (
            isinstance(energy_zone, str) and energy_zone.lower() in zones) else []
        for name in ("danceability", "intensity"):
            secondary = energy.get(name + "_percent")
            if type(secondary) in (int, float) and 0 <= secondary <= 100:
                energy_rows.append({"labelKey": "music_dna_card_" + name, "percent": secondary})
        card("energy", "mood_energy", metric=percent, entries=energy_rows, count=sample_count)
    rhythm = block("listening_rhythm")
    top_daypart = bounded_text(rhythm.get("top_daypart"), 64).strip()
    top_weekday = bounded_text(rhythm.get("top_weekday"), 64).strip()
    rhythm_entries = percentages(rhythm.get("dayparts"), "daypart", dayparts)
    if top_daypart or top_weekday:
        title, title_labels = (top_daypart, dayparts) if top_daypart else (top_weekday, weekdays)
        value_key = title_labels.get(title.casefold(), "")
        rhythm_entries.insert(0, {
            "title": "" if value_key else title,
            "valueKey": value_key,
            "subtitle": top_weekday if top_daypart else "",
            "subtitleKey": weekdays.get(top_weekday.casefold(), "") if top_daypart else "",
        })
    card("rhythm", "history", entries=rhythm_entries,
         groups=grouped("weekdays", percentages(rhythm.get("weekdays"), "weekday", weekdays)))
    mix = block("mood_mix")
    average = mix.get("average")
    top_zone = bounded_text(mix.get("top_zone"), 64).strip()
    top_zone_key = zones.get(top_zone.casefold(), "")
    card("mood_mix", top_zone_key or "sparkles", value=top_zone, value_key=top_zone_key,
         entries=percentages(mix.get("zones"), "zone", zones),
         average=average if type(average) in (int, float) and 0 <= average <= 100 else None)
    card("playtime", "history", value=duration,
         groups=grouped("artists_by_time", playtime_ranking(playtime.get("top_artists")))
         + grouped("albums_by_time", playtime_ranking(playtime.get("top_albums"))))
    card("repeat", "repeat", entries=rows(block("repeat_magnets").get("items")))
    positives = block("explicit_positives")
    card("positives", "heart", groups=grouped("recent_favorites", rows(positives.get("favorite_tracks")))
         + grouped("accepted", rows(positives.get("accepted_recommendations"))),
         entries=rows(positives.get("items")))
    card("anchors", "sparkles", entries=rows(block("taste_anchors").get("items")))
    card("recent_tracks", "history", entries=rows(tracks, artwork=True, limit=3))
    card("recent_favorites", "heart", entries=rows(profile.get("recent_favorite_tracks"), artwork=True, limit=3))
    top_tracks = block("top_tracks_by_range")
    track_groups = [group for term in ("short_term", "medium_term", "long_term")
                    for group in grouped(term, rows(top_tracks.get(term), artwork=True, limit=3))]
    card("top_tracks", "music", groups=track_groups)
    top_artists = block("top_artists_by_range")
    artist_groups = [
        {"key": "music_dna_card_" + term, "rows": rows(top_artists.get(term), artwork=True, limit=12), "shelf": True}
        for term in ("long_term", "medium_term", "short_term")
    ]
    if any(group["rows"] for group in artist_groups):
        card("top_artists", "session_persona", groups=artist_groups)
    feedback = block("discovery_feedback")
    accepted = feedback.get("accepted_recommendations") or feedback.get("accepted_items") or feedback.get("accepted")
    groups = grouped("accepted", rows(accepted, title_fields=("title", "name", "track_name", "subtitle"), limit=3))
    less_like_this = signal_rows(feedback.get("negative_signals"))
    if less_like_this:
        groups.append({"key": "music_discovery_less_like_this", "rows": less_like_this, "shelf": False})
    hidden = rows(feedback.get("hidden_artists") or feedback.get("blocked_artists")) + rows(feedback.get("blocked_items"))
    groups += grouped("feedback_hidden", hidden)
    counts = feedback.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    totals = [
        ("music_dna_card_feedback_accepted_count", feedback.get("accepted_count")),
        ("music_dna_card_feedback_negative_count", feedback.get("negative_count")),
        *((f"music_discovery_{action}", counts.get(action)) for action in ("not_for_me", "less_like_this", "hide_artist")),
    ]
    card("feedback", "sparkles", groups=groups, entries=[
        {"labelKey": label, "title": str(value)} for label, value in totals if type(value) is int and value >= 0])
    card("basis", "waveform", entries=signal_rows(profile.get("based_on"))
         or signal_rows(energy.get("recent_signals")) or signal_rows(profile.get("recommendation_signals")))
    return cards


def parse_music_discovery_feed(data: dict[str, object], *, ha_url: str = "") -> dict[str, object]:
    items: list[dict[str, object]] = []
    sections_source = data.get("sections")
    if isinstance(sections_source, list):
        for section in sections_source:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("id") or section.get("section_id") or "").strip()
            section_title = str(section.get("title") or section.get("name") or "").strip()
            raw_items = section.get("items")
            if not isinstance(raw_items, list):
                continue
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                item = _music_discovery_item(raw, section_id=section_id, section_title=section_title, ha_url=ha_url)
                if not item:
                    continue
                items.append(item)
    empty_text = str(data.get("empty_text") or data.get("empty_message") or data.get("message") or "").strip()
    return {"items": items, "empty_text": empty_text}


def _discovery_artwork(raw: dict[str, object], ha_url: str) -> dict[str, object]:
    empty = {"imageUrl": "", "imageExpiresAt": 0, "imageSourceUrl": ""}
    source = raw.get("image_url")
    if not isinstance(source, str) or not source or any(c.isspace() or ord(c) < 32 for c in source):
        return empty
    try:
        url = urlsplit(source)
        proxy_prefix = f"{DJCONNECT_API_PREFIX}/image_proxy/"
        legacy_proxy_prefix = f"{DJCONNECT_API_PREFIX.removesuffix('/v1')}/image_proxy/"
        if url.scheme:
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                return empty
            if url.path.startswith((proxy_prefix + "disc-", legacy_proxy_prefix + "disc-")):
                # Scoped grants are issued as relative paths on the configured HA.
                return empty
            return {**empty, "imageUrl": source}
        if not source.startswith(proxy_prefix) or not re.fullmatch(
            r"disc-[A-Za-z0-9_-]{32}|(?!disc-)[A-Za-z0-9_-]{1,128}", source[len(proxy_prefix):]
        ):
            return empty
        if ha_url:
            origin = urlsplit(ha_url)
            if (origin.scheme not in {"http", "https"} or not origin.hostname or origin.username
                    or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}):
                return empty
            source = f"{origin.scheme}://{origin.netloc}{source}"
    except ValueError:
        return empty
    if not url.path.rsplit("/", 1)[-1].startswith("disc-"):
        return {**empty, "imageUrl": source}

    if not ha_url:
        return empty
    expires = raw.get("image_expires_at")
    now = time.time()
    attribution = raw.get("image_attribution")
    if (type(expires) not in (int, float) or not now < expires <= now + 900
            or not isinstance(attribution, dict)
            or attribution.get("provider") != "spotify"):
        return empty
    media = attribution.get("media_uri")
    if not isinstance(media, str) or not re.fullmatch(r"spotify:(?:track|album|artist):[A-Za-z0-9]{22}", media):
        return empty
    spotify_url = "https://open.spotify.com/" + media.removeprefix("spotify:").replace(":", "/")
    if attribution.get("source_url") != spotify_url:
        return empty
    return {"imageUrl": source, "imageExpiresAt": expires * 1000, "imageSourceUrl": spotify_url}


def _discovery_media_key(uri: object) -> str:
    if not isinstance(uri, str) or not re.fullmatch(r"spotify:(?:track|album|artist|playlist):[A-Za-z0-9]{22}", uri):
        return ""
    return hashlib.sha256(uri.encode()).hexdigest()


def _music_discovery_item(raw: dict[str, object], *, section_id: str, section_title: str, ha_url: str) -> dict[str, object] | None:
    kind = str(raw.get("kind") or "").strip().lower()
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    subtitle = str(raw.get("subtitle") or "").strip()
    item_id = str(raw.get("id") or "").strip()
    discovery_item_id = str(raw.get("discovery_item_id") or raw.get("id") or "").strip()
    uri = str(raw.get("uri") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    reason_sources = _string_list(raw.get("reason_sources"))
    quality_score = raw.get("quality_score")
    quality_band = str(raw.get("quality_band") or "").strip()
    quality_factors = raw.get("quality_factors") if isinstance(raw.get("quality_factors"), (dict, list)) else {}
    playable = bool(uri)
    payload = {
        "section_id": section_id,
        "discovery_item_id": discovery_item_id,
    }
    return {
        "id": item_id,
        "sectionId": section_id,
        "sectionTitle": section_title,
        "discoveryItemId": discovery_item_id,
        "title": title,
        "subtitle": subtitle,
        "kind": kind,
        "kindLabel": kind.title(),
        **_discovery_artwork(raw, ha_url),
        "qualityScore": quality_score,
        "qualityBand": quality_band,
        "qualityFactors": quality_factors,
        "reason": reason,
        "reasonSources": reason_sources,
        "hasReason": bool(reason),
        "playable": playable,
        "_media_key": _discovery_media_key(uri),
        "payload": json.dumps(payload, ensure_ascii=True),
    }


if __name__ == "__main__":
    main()
