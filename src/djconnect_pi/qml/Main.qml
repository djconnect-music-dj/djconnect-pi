import QtQuick
import "BrandTheme.js" as BrandTheme
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtQuick.Window
import "MoodTheme.js" as MoodTheme

BrandWindow {
    id: root
    width: wallProfile ? Screen.width : 720
    height: wallProfile ? Screen.height : 720
    visible: true
    color: BrandTheme.colors.background
    title: "DJConnect"
    visibility: startWindowed ? Window.Windowed : Window.FullScreen

    readonly property var sessionHeader: djconnect.djSession.runtimeHeader
    onSessionHeaderChanged: syncVibeScape()
    readonly property string vibeScapeCurrentOwner: root.sessionHeader.owner_profile_id || djconnect.activeProfileId
    readonly property int vibeScapeCurrentEpoch: root.sessionHeader.owner_epoch || 0
    property int vibeScapeVisitEpoch: 0
    onVibeScapeCurrentOwnerChanged: syncVibeScape()
    onVibeScapeCurrentEpochChanged: syncVibeScape()
    property bool vibeScapeRequested: false
    property string vibeScapeOwner: ""
    property string vibeScapeSession: ""
    property real vibeScapePickerTop: 64
    readonly property bool vibeScapeAllowed: wallProfile && visible && active && activeScreen === "session" &&
        djconnect.paired && !djconnect.demoMode && vibeScapeCurrentOwner.length > 0 &&
        !root.sessionHeader.profile_confirmation_required && !root.sessionHeader.owner_read_only && root.sessionHeader.runtime_state === "active" &&
        root.sessionHeader.session_id.length > 0
    onVibeScapeAllowedChanged: if (!vibeScapeAllowed) closeVibeScape()
    function closeVibeScape() { vibeScapeLoader.active = false }
    function syncVibeScape() {
        if (vibeScapeLoader.active && (!vibeScapeAllowed || !djconnect.paired ||
                vibeScapeOwner !== vibeScapeCurrentOwner || vibeScapeVisitEpoch !== vibeScapeCurrentEpoch ||
                vibeScapeSession !== root.sessionHeader.session_id)) closeVibeScape()
    }
    Connections {
        target: djconnect.djSession
        function onNewCurrentContribution() {
            if (!root.screenBlanked || !root.active || root.splashVisible || !djconnect.paired)
                return
            djconnect.wakeDisplay()
            root.temporaryWake(20, false)
        }
    }
    Connections {
        target: djconnect
        function onProfileChanged() { root.trackArtworkOpen = false; root.syncVibeScape(); musicDnaOptIn.close(); root.closeMusicDnaConfirmation(); root.closeDiscoveryDetail() }
        function onPairedChanged() { root.trackArtworkOpen = false; root.syncVibeScape(); musicDnaOptIn.close(); root.closeMusicDnaConfirmation(); root.closeDiscoveryDetail() }
        function onDemoModeChanged() { musicDnaOptIn.close(); root.closeMusicDnaConfirmation() }
        function onMusicDnaChanged() {
            if (root.musicDnaConfirmationRevision !== djconnect.musicDnaContextRevision)
                root.closeMusicDnaConfirmation()
            if (musicDnaOptIn.visible && !musicDnaOptIn.currentProfile)
                musicDnaOptIn.close()
        }
    }
    function openVibeScape(pickerTop) {
        if (!vibeScapeAllowed) return
        vibeScapeOwner = vibeScapeCurrentOwner
        vibeScapeVisitEpoch = vibeScapeCurrentEpoch
        vibeScapeSession = root.sessionHeader.session_id
        vibeScapePickerTop = Number.isFinite(pickerTop) ? vibeScapeLoader.mapFromItem(null, 0, pickerTop).y : 64
        vibeScapeRequested = true
        vibeScapeLoader.active = true
    }
    Loader {
        id: vibeScapeLoader
        objectName: "vibeScapeLoader"
        anchors.fill: parent; z: 35
        // Explicit visit latch: invalidation cannot re-arm a returned context.
        active: false
        focus: active
        onLoaded: item.forceActiveFocus()
        onActiveChanged: {
            if (!active) {
                root.vibeScapeRequested = false
                root.vibeScapeOwner = ""
                root.vibeScapeSession = ""
                root.vibeScapePickerTop = 64
                root.restartIdleTimer()
            }
        }
        sourceComponent: VibeScape {
            backend: djconnect; admitted: root.vibeScapeAllowed
            mood: root.appearanceMood
            pickerTop: root.vibeScapePickerTop
            forecastAvailable: root.sessionHeader.forecast_supported
            onCloseRequested: root.closeVibeScape()
            onFlowRequested: { root.closeVibeScape(); sharedSessionScreen.forecastMode = false }
            onForecastRequested: { root.closeVibeScape(); sharedSessionScreen.forecastMode = true }
        }
    }
    property bool wallProfile: djconnect.releaseProfile === "pi5-arm64"
    readonly property bool lowMemoryProfile: djconnect.releaseProfile === "pi-zero-2w-arm64"
    property string trackInsightVisualMood: "groove"
    readonly property bool trackInsightHasLiveSession: {
        const session = root.sessionHeader
        return !!session.session_id && ["opening", "active", "ending"].includes(session.runtime_state)
    }
    readonly property bool liquidGlassEnabled: djconnect.glassEnabled
    readonly property string appearanceMood: {
        if (root.activeScreen === "games") return gamesPanel.gameMood
        if (root.activeScreen === "trackinsight" && !root.trackInsightHasLiveSession)
            return root.trackInsightVisualMood
        var session = root.sessionHeader
        if (session.session_id && ["chill", "groove", "energy", "party"].indexOf(session.mood) >= 0) return session.mood
        var settings = djconnect.sharedSettings
        var confirmed = settings && settings.available && !settings.selection_required ? (settings.values || {}).mood : ""
        return ["chill", "groove", "energy", "party"].indexOf(confirmed) >= 0 ? confirmed : ""
    }
    readonly property string visibleArtworkTint: activeScreen === "now" && nowLoader.item ? nowLoader.item.artworkTint :
        activeScreen === "trackinsight" && trackInsightLoader.item ? trackInsightLoader.item.artworkTint :
        activeScreen === "session" ? sharedSessionScreen.artworkTint : ""
    Binding { target: GlassStyle; property: "mood"; value: root.appearanceMood }
    Binding { target: GlassStyle; property: "contextArtworkTint"; value: root.visibleArtworkTint }
    Binding { target: GlassStyle; property: "moodAccent"; value: MoodTheme.moodColor(root.appearanceMood, "gradientStart") }
    Binding { target: GlassStyle; property: "surfaceTint"; value: MoodTheme.material(root.appearanceMood,root.visibleArtworkTint,GlassStyle.moodTintStrength,GlassStyle.artworkTintStrength) }
    Binding { target: GlassStyle; property: "heroStart"; value: MoodTheme.mix("#142652",MoodTheme.moodColor(root.appearanceMood,"gradientStart"),GlassStyle.heroMoodStrength) }
    Binding { target: GlassStyle; property: "heroMid"; value: MoodTheme.mix("#211745",MoodTheme.moodColor(root.appearanceMood,"gradientMid"),GlassStyle.heroMoodStrength) }
    Binding { target: GlassStyle; property: "heroEnd"; value: MoodTheme.mix("#2B1452",MoodTheme.moodColor(root.appearanceMood,"gradientEnd"),GlassStyle.heroMoodStrength) }
    Binding { target: GlassStyle; property: "enabled"; value: root.liquidGlassEnabled }
    Binding { target: GlassStyle; property: "hardware"; value: root.wallProfile && root.GraphicsInfo.api === GraphicsInfo.OpenGL }
    Binding { target: GlassStyle; property: "awake"; value: root.visible && !root.screenBlanked }
    readonly property bool usesPageBackdrop: ["session", "queue", "playlists", "trackinsight", "discover",
        "more", "settings", "musicdna", "askdj", "legal", "privacy", "feedback"].includes(activeScreen)
    readonly property bool showsSessionBackdrop: activeScreen === "session" && sessionHeader.runtime_state === "active"

    // Keep the backdrop outside the page loaders so a slow page never exposes a different surface.
    Item {
        id: pageBackdrop
        objectName: "persistentPageBackdrop"
        anchors.fill: parent
        z: -999
        visible: root.usesPageBackdrop
        SessionMoodCanvas {
            anchors.fill: parent
            mood: root.appearanceMood
            activeSession: root.sessionHeader.runtime_state === "active"
        }
        SessionMoodCanvas {
            anchors.fill: parent
            visible: !root.showsSessionBackdrop
            mood: "chill"
            activeSession: false
        }
    }
    Binding { target: GlassStyle; property: "backdrop"; value: root.usesPageBackdrop ? pageBackdrop : glassUnderlay }
    Binding { target: GlassStyle; property: "texture"; value: sharedGlassCapture.item ? sharedGlassCapture.item.texture : null }
    // Never captures private content or the material itself; no recursive feedback.
    AppBackground {
        id: glassUnderlay
        visible: false
        sessionCanvas: root.activeScreen === "session" || root.activeScreen === "games" || root.activeScreen === "discover"
    }
    Loader {
        id: sharedGlassCapture
        objectName: "sharedGlassCapture"
        active: GlassStyle.enabled && GlassStyle.hardware && GlassStyle.awake
        anchors.fill: parent; visible: false
        sourceComponent: GlassBackdrop { sourceItem: root.usesPageBackdrop ? pageBackdrop : glassUnderlay }
    }
    FrameAnimation {
        objectName: "glassGeometryAnimation"
        running: GlassStyle.active
        onTriggered: GlassStyle.geometryFrame = (GlassStyle.geometryFrame + 1) % 1000000
    }
    property real edge: wallProfile ? Math.max(56, width * 0.07) : 28
    property real wallTypeScale: wallProfile ? Math.min(width / 900, height / 1440) : 1
    property int motionFast: wallProfile ? 180 : 140
    property int motionStandard: wallProfile ? 360 : 240
    property int motionAmbient: wallProfile ? 4200 : 0
    property bool splashVisible: true
    property string activeScreen: "session"
    property bool settingsOpen: activeScreen === "settings"
    property bool gamesOpen: activeScreen === "games"
    property bool aboutOpen: false
    property bool resetPairingConfirmOpen: false
    property bool rebootConfirmOpen: false
    property bool shutdownConfirmOpen: false
    property bool clearLogsConfirmOpen: false
    property bool askDjClearConfirmOpen: false
    property int askDjClearConfirmationRevision: -1
    property string askDjDraft: ""
    readonly property int askDjDraftContextRevision: djconnect.musicDnaContextRevision
    onAskDjDraftContextRevisionChanged: askDjDraft = ""
    Connections {
        target: djconnect
        function onProfileChanged() { root.askDjDraft = "" }
        function onPairedChanged() { root.askDjDraft = "" }
        function onDemoModeChanged() { root.askDjDraft = "" }
    }
    property bool musicDnaDisableConfirmOpen: false
    property bool musicDnaClearConfirmOpen: false
    property int musicDnaConfirmationRevision: -1
    property bool forceScreenAwake: false
    property bool forceBrightnessFull: false
    property bool suppressNextNowPanelTap: false
    property bool trackArtworkOpen: false
    property bool trackArtworkControlsOpen: false
    property Item trackArtworkOpeningControl: null
    property string trackArtworkOpeningScope: ""
    readonly property bool trackArtworkCanControl: trackArtworkOpen && sessionTrackArtwork
        && trackArtworkViewOpen && !!sharedSessionScreen.state.playback_control.available
    onTrackArtworkCanControlChanged: if (!trackArtworkCanControl) trackArtworkControlsOpen = false
    onTrackArtworkControlsOpenChanged: {
        if (!trackArtworkControlsOpen && sharedSessionScreen.owner)
            sharedSessionScreen.owner.cancelPlaybackIntent()
    }
    property bool sessionTrackArtwork: false
    readonly property bool trackArtworkViewOpen: sharedSessionScreen.liveViewOpen && !sharedSessionScreen.showForecast
    onTrackArtworkViewOpenChanged: if (!trackArtworkViewOpen) trackArtworkOpen = false
    readonly property bool overlayTrackAvailable: sessionTrackArtwork ? sharedSessionScreen.trackArtworkAvailable : djconnect.hasTrackTitle
    onOverlayTrackAvailableChanged: if (!overlayTrackAvailable) trackArtworkOpen = false
    onTrackArtworkOpenChanged: {
        if (trackArtworkOpen) return
        trackArtworkDismissTimer.stop()
        const source = trackArtworkOpeningControl
        const scope = trackArtworkOpeningScope
        trackArtworkOpeningControl = null
        trackArtworkOpeningScope = ""
        if (source) Qt.callLater(function() {
            if (source && !root.trackArtworkOpen && root.trackArtworkViewOpen
                    && scope === sharedSessionScreen.playbackMenuScope && source.visible && source.enabled)
                source.forceActiveFocus(Qt.PopupFocusReason)
        })
    }
    onScreenBlankedChanged: if (screenBlanked) trackArtworkOpen = false
    function showTrackArtwork(fromSession, automatic) {
        if (!trackArtworkViewOpen || (automatic && trackArtworkOpen && !trackArtworkDismissTimer.running))
            return
        sessionTrackArtwork = fromSession
        trackArtworkOpen = true
        trackArtworkControlsOpen = trackArtworkCanControl
        if (automatic && !trackArtworkControlsOpen) trackArtworkDismissTimer.restart()
        else trackArtworkDismissTimer.stop()
    }
    function toggleTrackArtworkControls() {
        if (!trackArtworkCanControl)
            return
        trackArtworkDismissTimer.stop()
        trackArtworkControlsOpen = !trackArtworkControlsOpen
    }
    function openTrackArtwork(source) {
        if (!trackArtworkViewOpen || !sharedSessionScreen.trackArtworkAvailable) return
        trackArtworkOpeningControl = source
        trackArtworkOpeningScope = sharedSessionScreen.playbackMenuScope
        showTrackArtwork(true)
    }
    property double lastWakeDisplayAt: 0
    property int wakeDisplayDebounceMs: 800
    property int standardButtonRadius: 24
    property bool screenBlanked: djconnect.screenTimeoutSeconds > 0 && !idleTimer.running && !root.forceScreenAwake && !vibeScapeLoader.active
    property real brightnessOverlayOpacity: root.screenBlanked || root.forceBrightnessFull ? 0 : 1 - (djconnect.screenBrightnessPercent / 100.0)
    property int trVersion: djconnect.translationVersion

    readonly property string edgeBackRoute: {
        if (!root.active || root.screenBlanked || root.splashVisible || (!djconnect.paired && !djconnect.demoMode)
                || root.trackArtworkOpen || vibeScapeLoader.active || musicDnaOptIn.visible || root.resetPairingConfirmOpen || root.rebootConfirmOpen
                || root.shutdownConfirmOpen || root.clearLogsConfirmOpen || root.askDjClearConfirmOpen
                || root.musicDnaDisableConfirmOpen || root.musicDnaClearConfirmOpen || (musicDiscoveryLoader.item && musicDiscoveryLoader.item.detailVisible))
            return ""
        if (djconnect.logsVisible) return "logs"
        if (root.aboutOpen) return "about"
        if (root.activeScreen === "history" || root.activeScreen === "session-history") {
            if (sharedSessionScreen.historyNavigationBlocked) return ""
            var state = sharedSessionScreen.state
            return JSON.stringify([root.activeScreen, state.owner_profile_id || "", state.owner_epoch || 0,
                state.session_id || "", (state.timeline.session || {}).session_id || ""])
        }
        if (["askdj", "musicdna", "playlists", "settings", "legal", "privacy", "feedback", "games"].indexOf(root.activeScreen) >= 0)
            return root.activeScreen
        return ""
    }
    function navigateEdgeBack(route) {
        if (!route || route !== edgeBackRoute) return
        if (route === "logs") djconnect.hideLogs()
        else if (route === "about") root.aboutOpen = false
        else if (root.activeScreen === "history" || root.activeScreen === "session-history")
            sharedSessionScreen.navigateBackFromHistory()
        else root.activeScreen = "more"
    }

    function openMusicDnaPrompt(discoveryConsent) {
        if (musicDnaOptIn.visible || djconnect.musicDnaBusy || djconnect.musicDiscoveryBusy) return
        musicDnaOptIn.discoveryConsent = discoveryConsent
        musicDnaOptIn.profileRevision = djconnect.musicDnaContextRevision
        musicDnaOptIn.open()
    }

    function closeMusicDnaConfirmation() {
        root.musicDnaDisableConfirmOpen = false
        root.musicDnaClearConfirmOpen = false
    }

    function tr(key) {
        root.trVersion
        return djconnect.t(key)
    }

    function trackInsightLabel(value) {
        var key = String(value || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
        var labels = {
            "summary": "track_insight_summary",
            "genre": "track_insight_genre",
            "vibe": "track_insight_vibe",
            "production": "track_insight_production",
            "production_notes": "track_insight_production",
            "instrumentation": "track_insight_instrumentation",
            "arrangement": "track_insight_arrangement",
            "arrangement_notes": "track_insight_arrangement",
            "listening_cues": "track_insight_listening_cues",
            "similar_tracks": "track_insight_similar_tracks",
            "why_it_fits_you": "track_insight_why_it_fits_you",
            "mood_context": "track_insight_mood_context",
            "this_expands_your_music_dna": "track_insight_music_dna_expands",
            "music_dna_match": "music_dna_match",
            "energy": "track_insight_energy",
            "danceability": "track_insight_danceability",
            "intensity": "track_insight_intensity",
            "confidence": "track_insight_confidence",
            "measured": "track_insight_measured",
            "inferred": "track_insight_inferred",
            "context": "analysis_context"
        }
        return labels[key] ? root.tr(labels[key]) : (value || "")
    }

    function moodColor(token) {
        return MoodTheme.color(root.appearanceMood, token)
    }

    function moodDisabledColor(position) {
        return MoodTheme.disabled(position)
    }

    function recordActivity() {
        var wasBlanked = root.screenBlanked
        root.restoreSessionFromSleep()
        root.forceBrightnessFull = false
        root.restartIdleTimer()
        if (root.forceScreenAwake && forcedWakeTimer.running) {
            forcedWakeTimer.restart()
        }
        if (wasBlanked) {
            root.hideTransientUi()
            root.suppressNextNowPanelTap = true
            djconnect.refresh()
        }
    }

    function restartIdleTimer() {
        if (djconnect.screenTimeoutSeconds > 0) {
            idleTimer.restart()
        }
    }

    function wakeDisplay() {
        var now = Date.now()
        if (now - root.lastWakeDisplayAt < root.wakeDisplayDebounceMs) {
            root.recordActivity()
            return
        }
        root.lastWakeDisplayAt = now
        djconnect.wakeDisplay()
        root.recordActivity()
    }

    onActiveScreenChanged: {
        root.trackArtworkOpen = false
        if (root.wallProfile && (root.activeScreen === "now" || root.activeScreen === "control")) {
            root.closeVibeScape()
            root.activeScreen = "session"
            return
        }
        root.restartIdleTimer()
        if (root.activeScreen === "askdj") {
            root.scrollAskDjToTop()
        }
    }

    function scrollAskDjToTop() {
        Qt.callLater(function() {
            if (root.activeScreen === "askdj" && askDjLoader.item)
                askDjLoader.item.scrollToTop()
        })
    }

    function hideTransientUi() {
        root.resetPairingConfirmOpen = false
        root.rebootConfirmOpen = false
        root.shutdownConfirmOpen = false
        root.clearLogsConfirmOpen = false
        root.askDjClearConfirmOpen = false
        if (askDjLoader.item) askDjLoader.item.closeSearch()
        root.closeMusicDnaConfirmation()
        root.closeDiscoveryDetail()
        musicDnaOptIn.close()
    }

    function restoreSessionFromSleep() {
        if (root.screenBlanked && (djconnect.paired || djconnect.demoMode))
            root.activeScreen = "session"
    }

    function temporaryWake(seconds, fullBrightness = true) {
        root.restoreSessionFromSleep()
        root.forceScreenAwake = true
        root.forceBrightnessFull = fullBrightness
        forcedWakeTimer.interval = Math.max(1000, seconds * 1000)
        forcedWakeTimer.restart()
    }

    component PurpleButton: Button {
        id: control
        property bool primary: false
        font.pixelSize: 22
        font.bold: true
        contentItem: Text {
            text: control.text
            font: control.font
            color: control.enabled ? BrandTheme.colors.text : BrandTheme.colors.disabledText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: GlassSurface {
            radius: control.primary ? height / 2 : root.standardButtonRadius
            interactive: true
            primary: control.primary
            pressed: control.down
            focused: control.activeFocus
            opacity: control.enabled ? 1 : 0.45
            scale: control.down ? 0.98 : 1
            Behavior on scale { NumberAnimation { duration: root.motionFast; easing.type: Easing.OutCubic } }
        }
    }

    component AskDjGradientButton: Button {
        id: askDjButton
        font.pixelSize: 18
        font.bold: true
        contentItem: Text {
            text: askDjButton.text
            font: askDjButton.font
            color: askDjButton.enabled ? BrandTheme.colors.text : BrandTheme.colors.muted
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            maximumLineCount: 1
        }
        background: GlassSurface {
            radius: height / 2
            interactive: true
            primary: true
            pressed: askDjButton.down
            focused: askDjButton.activeFocus
            opacity: askDjButton.enabled ? 1 : 0.45
        }
    }

    component MenuIcon: FeatureIcon {}

    component NavButton: PurpleButton {
        id: navControl
        property string iconName: ""

        font.pixelSize: 15
        contentItem: ColumnLayout {
            spacing: 3

            MenuIcon {
                objectName: "navIcon-" + navControl.iconName
                iconName: navControl.iconName
                iconColor: !navControl.enabled ? BrandTheme.colors.disabledText : navControl.iconName === "session" ?
                    BrandTheme.colors.white : (navControl.checked ? BrandTheme.colors.actionAccent : BrandTheme.colors.muted)
                strokeWidth: navControl.checked ? 2.8 : 2.4
                Layout.preferredWidth: navControl.checked ? 30 : 28
                Layout.preferredHeight: navControl.checked ? 30 : 28
                Layout.alignment: Qt.AlignHCenter
                Layout.fillWidth: true
            }

            Text {
                text: navControl.text
                color: navControl.enabled ? (navControl.checked ? BrandTheme.colors.actionAccent : BrandTheme.colors.muted) : BrandTheme.colors.disabledText
                font: navControl.font
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                maximumLineCount: 1
                Layout.fillWidth: true
            }
        }
        background: Rectangle {
            radius: height / 2
            color: navControl.checked ? "#700e0b2a" : (navControl.down ? "#25ffffff" : "transparent")
            border.width: navControl.activeFocus ? 2 : 0
            border.color: BrandTheme.colors.focus
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    component RefreshIconButton: PurpleButton {
        id: refreshControl
        property bool loading: false

        text: root.tr("refresh")
        ToolTip.visible: hovered
        ToolTip.text: text
        Accessible.name: text
        Layout.preferredWidth: 48
        Layout.preferredHeight: 48
        background: GlassSurface { tertiary: true; interactive: true; pressed: refreshControl.down; focused: refreshControl.activeFocus }
        contentItem: Item {
            MenuIcon {
                iconName: "refresh"
                iconColor: refreshControl.enabled ? BrandTheme.colors.text : BrandTheme.colors.disabledText
                strokeWidth: 2.8
                anchors.centerIn: parent
                width: 28
                height: 28
                visible: !refreshControl.loading
            }
            BusyIndicator {
                anchors.centerIn: parent
                width: 28
                height: width
                visible: refreshControl.loading
                running: visible
            }
        }
    }

    component MoreMenuButton: Button {
        id: moreControl
        property string iconName: ""

        font.pixelSize: root.wallProfile ? 24 : 21
        font.weight: Font.DemiBold
        padding: root.wallProfile ? 20 : 15
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: Math.max(root.wallProfile ? 108 : 80, contentItem.implicitHeight + 2 * padding)
        Accessible.name: text
        contentItem: RowLayout {
            spacing: root.wallProfile ? 19 : 14

            Rectangle {
                Layout.preferredWidth: root.wallProfile ? 60 : 44
                Layout.preferredHeight: Layout.preferredWidth
                radius: root.wallProfile ? 19 : 14
                color: BrandTheme.alpha(BrandTheme.colors.actionAccent, "24")
                border.color: "#23FFFFFF"
                FeatureIcon {
                    anchors.centerIn: parent
                    width: parent.width * 0.63
                    height: width
                    iconName: moreControl.iconName
                    iconColor: BrandTheme.colors.actionAccent
                    Accessible.ignored: true
                }
            }
            Text {
                text: moreControl.text
                textFormat: Text.PlainText
                color: moreControl.enabled ? BrandTheme.colors.text : BrandTheme.colors.disabledText
                font: moreControl.font
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            FeatureIcon {
                iconName: "back"
                rotation: 180
                Layout.preferredWidth: 24
                Layout.preferredHeight: 24
                iconColor: BrandTheme.colors.muted
                Accessible.ignored: true
            }
        }
        background: GlassSurface {
            introduction: true
            radius: root.wallProfile ? 34 : 24
            pressed: moreControl.down
            focused: moreControl.activeFocus
        }
    }

    component LoadingSpinner: Item {
        id: spinner
        property bool running: true
        property color dotColor: BrandTheme.colors.muted
        property bool spokes: false

        implicitWidth: 42
        implicitHeight: 42

        Repeater {
            model: 12

            Rectangle {
                required property int index
                width: spinner.spokes ? Math.max(2, spinner.width * 0.08) : Math.max(4, spinner.width * 0.13)
                height: spinner.spokes ? spinner.height * 0.23 : width
                rotation: spinner.spokes ? index * 30 + 90 : 0
                radius: width / 2
                color: spinner.dotColor
                opacity: 0.18 + (index / 11) * 0.72
                x: spinner.width / 2 - width / 2 + Math.cos((index / 12) * Math.PI * 2) * spinner.width * 0.32
                y: spinner.height / 2 - height / 2 + Math.sin((index / 12) * Math.PI * 2) * spinner.height * 0.32
            }
        }

        RotationAnimation on rotation {
            running: spinner.running && spinner.visible
            loops: Animation.Infinite
            from: 0
            to: 360
            duration: spinner.spokes ? 900 : root.wallProfile ? 1200 : 900
            easing.type: spinner.spokes ? Easing.Linear : Easing.InOutSine
        }

        SequentialAnimation on scale {
            running: spinner.running && spinner.visible && root.wallProfile && !spinner.spokes
            loops: Animation.Infinite
            NumberAnimation { to: 1.12; duration: 760; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0.94; duration: 760; easing.type: Easing.InOutSine }
        }
    }

    component DangerButton: Button {
        id: control

        font.pixelSize: 24
        font.bold: true
        contentItem: Text {
            text: control.text
            font: control.font
            color: BrandTheme.colors.actionDanger
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: GlassSurface {
            radius: root.standardButtonRadius
            interactive: true; destructive: true; pressed: control.down; focused: control.activeFocus
            opacity: control.enabled ? 1 : 0.4
        }
    }

    component WarningButton: Button {
        id: control

        font.pixelSize: 24
        font.bold: true
        contentItem: Text {
            text: control.text
            font: control.font
            color: BrandTheme.colors.warning
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: root.standardButtonRadius
            color: Qt.rgba(1, 0.82, 0.35, control.down ? 0.22 : 0.12)
            border.color: "#1FFFFFFF"
            border.width: 1
            opacity: control.down ? 0.82 : 1.0
        }
    }

    component ModalBlocker: MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        preventStealing: true
        propagateComposedEvents: false
        onClicked: function(mouse) { root.recordActivity(); mouse.accepted = true }
        onPressed: function(mouse) { root.recordActivity(); mouse.accepted = true }
        onReleased: function(mouse) { mouse.accepted = true }
        onWheel: function(wheel) { wheel.accepted = true }
    }

    component CompactPageLoader: Loader {
        anchors.fill: parent
        active: !root.lowMemoryProfile || visible
        asynchronous: root.lowMemoryProfile
    }

    component AppBackground: Item {
        id: appCanvas
        property bool sessionCanvas: false
        anchors.fill: parent

        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: root.moodColor("backgroundStart") }
                GradientStop { position: 0.48; color: root.moodColor("backgroundMid") }
                GradientStop { position: 1.0; color: root.moodColor("backgroundEnd") }
            }
        }

        Rectangle {
            property real driftX: 0
            property real driftY: 0
            width: parent.width * 0.94
            height: width
            radius: width / 2
            x: -width * 0.30 + driftX
            y: parent.height * 0.08 + driftY
            color: root.moodColor("overlayStart")
            opacity: root.wallProfile ? 0.04 : 0
            scale: 0.94

            SequentialAnimation on scale {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: 1.08; duration: 7200; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.94; duration: 7200; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on driftX {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: parent.width * 0.035; duration: 18000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0; duration: 18000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on driftY {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: parent.height * 0.018; duration: 21000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0; duration: 21000; easing.type: Easing.InOutSine }
            }
        }

        Rectangle {
            property real driftX: 0
            property real driftY: 0
            width: parent.width * 0.82
            height: width
            radius: width / 2
            x: parent.width - width * 0.58 + driftX
            y: parent.height * 0.48 + driftY
            color: root.moodColor("overlayEnd")
            opacity: root.wallProfile ? 0.04 : 0
            scale: 1.05

            SequentialAnimation on scale {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: 0.92; duration: 8200; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.05; duration: 8200; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on driftX {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: -parent.width * 0.028; duration: 20000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0; duration: 20000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on driftY {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: -parent.height * 0.014; duration: 17000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0; duration: 17000; easing.type: Easing.InOutSine }
            }
        }

        Rectangle {
            anchors.fill: parent
            opacity: root.wallProfile ? 0.48 : 0.36
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: root.moodColor("overlayStart") }
                GradientStop { position: 0.48; color: "#00000000" }
                GradientStop { position: 1.0; color: root.moodColor("overlayEnd") }
            }
            SequentialAnimation on opacity {
                running: root.wallProfile && !root.screenBlanked && !appCanvas.sessionCanvas
                loops: Animation.Infinite
                NumberAnimation { to: 0.62; duration: 5200; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.42; duration: 5200; easing.type: Easing.InOutSine }
            }
        }
        Loader {
            anchors.fill: parent
            active: appCanvas.sessionCanvas && (!root.lowMemoryProfile || (appCanvas.visible && !root.screenBlanked))
            sourceComponent: SessionMoodCanvas {
                mood: root.appearanceMood
                activeSession: root.activeScreen === "games" || (root.activeScreen === "session" && root.sessionHeader.runtime_state === "active")
            }
        }
    }

    component IconButton: Button {
        id: control
        property string iconName: "play"
        property bool primary: false
        property bool active: false

        Layout.fillWidth: true
        Layout.fillHeight: true
        contentItem: Canvas {
            id: iconCanvas
            anchors.fill: parent
            antialiasing: true
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var w = width
                var h = height
                var cx = w / 2
                var cy = h / 2
                var s = Math.min(w, h)
                ctx.strokeStyle = BrandTheme.colors.text
                ctx.fillStyle = BrandTheme.colors.text
                ctx.lineWidth = Math.max(4, s * 0.08)
                ctx.lineCap = "round"
                ctx.lineJoin = "round"

                function triangle(dir, x) {
                    ctx.beginPath()
                    if (dir > 0) {
                        ctx.moveTo(x - s * 0.12, cy - s * 0.20)
                        ctx.lineTo(x - s * 0.12, cy + s * 0.20)
                        ctx.lineTo(x + s * 0.18, cy)
                    } else {
                        ctx.moveTo(x + s * 0.12, cy - s * 0.20)
                        ctx.lineTo(x + s * 0.12, cy + s * 0.20)
                        ctx.lineTo(x - s * 0.18, cy)
                    }
                    ctx.closePath()
                    ctx.fill()
                }

                function arrowHead(x, y, dir) {
                    var l = s * 0.11
                    var o = s * 0.08
                    ctx.beginPath()
                    ctx.moveTo(x, y)
                    ctx.lineTo(x - dir * l, y - o)
                    ctx.moveTo(x, y)
                    ctx.lineTo(x - dir * l, y + o)
                    ctx.stroke()
                }

                if (control.iconName === "pause") {
                    ctx.fillRect(cx - s * 0.18, cy - s * 0.22, s * 0.11, s * 0.44)
                    ctx.fillRect(cx + s * 0.07, cy - s * 0.22, s * 0.11, s * 0.44)
                } else if (control.iconName === "previous") {
                    triangle(-1, cx - s * 0.05)
                    triangle(-1, cx + s * 0.17)
                    ctx.fillRect(cx - s * 0.30, cy - s * 0.24, s * 0.07, s * 0.48)
                } else if (control.iconName === "next") {
                    triangle(1, cx + s * 0.05)
                    triangle(1, cx - s * 0.17)
                    ctx.fillRect(cx + s * 0.23, cy - s * 0.24, s * 0.07, s * 0.48)
                } else if (control.iconName === "shuffle") {
                    ctx.lineWidth = Math.max(5, s * 0.09)
                    ctx.beginPath()
                    ctx.moveTo(cx - s * 0.34, cy - s * 0.19)
                    ctx.bezierCurveTo(cx - s * 0.10, cy - s * 0.19, cx + s * 0.02, cy + s * 0.19, cx + s * 0.32, cy + s * 0.19)
                    ctx.stroke()
                    arrowHead(cx + s * 0.34, cy + s * 0.19, 1)
                    ctx.beginPath()
                    ctx.moveTo(cx - s * 0.34, cy + s * 0.19)
                    ctx.bezierCurveTo(cx - s * 0.10, cy + s * 0.19, cx + s * 0.02, cy - s * 0.19, cx + s * 0.32, cy - s * 0.19)
                    ctx.stroke()
                    arrowHead(cx + s * 0.34, cy - s * 0.19, 1)
                } else if (control.iconName === "heart" || control.iconName === "heartFilled") {
                    ctx.lineWidth = Math.max(4, s * 0.075)
                    ctx.beginPath()
                    ctx.moveTo(cx, cy + s * 0.28)
                    ctx.bezierCurveTo(cx - s * 0.40, cy + s * 0.02, cx - s * 0.34, cy - s * 0.33, cx - s * 0.10, cy - s * 0.28)
                    ctx.bezierCurveTo(cx - s * 0.01, cy - s * 0.26, cx, cy - s * 0.16, cx, cy - s * 0.16)
                    ctx.bezierCurveTo(cx, cy - s * 0.16, cx + s * 0.01, cy - s * 0.26, cx + s * 0.10, cy - s * 0.28)
                    ctx.bezierCurveTo(cx + s * 0.34, cy - s * 0.33, cx + s * 0.40, cy + s * 0.02, cx, cy + s * 0.28)
                    ctx.closePath()
                    if (control.iconName === "heartFilled") {
                        ctx.fill()
                    } else {
                        ctx.stroke()
                    }
                } else if (control.iconName === "repeat" || control.iconName === "repeatOne" || control.iconName === "repeatOff") {
                    ctx.lineWidth = Math.max(5, s * 0.09)
                    ctx.beginPath()
                    ctx.moveTo(cx - s * 0.26, cy - s * 0.18)
                    ctx.lineTo(cx + s * 0.25, cy - s * 0.18)
                    ctx.stroke()
                    arrowHead(cx + s * 0.31, cy - s * 0.18, 1)
                    ctx.beginPath()
                    ctx.moveTo(cx + s * 0.26, cy + s * 0.18)
                    ctx.lineTo(cx - s * 0.25, cy + s * 0.18)
                    ctx.stroke()
                    arrowHead(cx - s * 0.31, cy + s * 0.18, -1)
                    if (control.iconName === "repeatOne") {
                        ctx.font = "bold " + Math.max(14, s * 0.24) + "px sans-serif"
                        ctx.textAlign = "center"
                        ctx.textBaseline = "middle"
                        ctx.fillText("1", cx, cy)
                    }
                } else {
                    triangle(1, cx)
                }
            }
            Component.onCompleted: requestPaint()
            Connections {
                target: control
                function onIconNameChanged() { iconCanvas.requestPaint() }
                function onActiveChanged() { iconCanvas.requestPaint() }
                function onDownChanged() { iconCanvas.requestPaint() }
            }
        }
        background: GlassSurface {
            radius: control.primary ? height / 2 : root.standardButtonRadius
            interactive: true
            primary: control.primary
            selected: control.active
            pressed: control.down
            focused: control.activeFocus
            opacity: control.enabled ? 1 : 0.4
            scale: control.down ? 0.98 : 1
            Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
    }

    component MediaOverflowText: Item {
        id: overflowLabel
        property alias text: label.text
        property alias color: label.color
        property alias font: label.font
        property int stationaryLines: 1
        property bool queueLabel: false
        property bool inViewport: false
        property bool reduceMotion: GlassStyle.reduceMotion
        property real shift: 0
        readonly property real overflow: Math.max(0, metrics.advanceWidth - width)
        readonly property bool scrolling: queueLabel && root.active && GlassStyle.awake && !reduceMotion && overflow > 1
        implicitHeight: label.implicitHeight
        clip: scrolling

        TextMetrics { id: metrics; text: label.text; font: label.font }
        Text {
            id: label
            objectName: "mediaOverflowLabel"
            x: -overflowLabel.shift
            width: overflowLabel.scrolling ? Math.ceil(metrics.advanceWidth) : overflowLabel.width
            textFormat: Text.PlainText
            wrapMode: overflowLabel.scrolling ? Text.NoWrap : Text.Wrap
            maximumLineCount: overflowLabel.queueLabel && overflowLabel.reduceMotion ? 2147483647
                : overflowLabel.scrolling ? 1 : overflowLabel.stationaryLines
            elide: overflowLabel.scrolling || (overflowLabel.queueLabel && overflowLabel.reduceMotion)
                ? Text.ElideNone : Text.ElideRight
        }
        SequentialAnimation {
            running: overflowLabel.scrolling && overflowLabel.inViewport && overflowLabel.visible
            loops: Animation.Infinite
            onRunningChanged: if (!running) overflowLabel.shift = 0
            PauseAnimation { duration: 1250 }
            NumberAnimation {
                target: overflowLabel; property: "shift"
                from: 0; to: overflowLabel.overflow
                duration: Math.round(overflowLabel.overflow * 1000 / 28)
            }
            PauseAnimation { duration: 1250 }
            NumberAnimation {
                target: overflowLabel; property: "shift"
                from: overflowLabel.overflow; to: 0
                duration: Math.round(overflowLabel.overflow * 1000 / 28)
            }
        }
    }

    component PageFindShortcut: Shortcut {
        required property bool pageReady
        sequences: [StandardKey.Find]
        enabled: pageReady && root.active && !root.screenBlanked && !root.splashVisible
            && !root.trackArtworkOpen && !djconnect.logsVisible
    }

    component MediaListPanel: Rectangle {
        id: panel
        property string heading: ""
        property string emptyText: ""
        property string searchLabel: ""
        property string refreshLabel: root.tr("refresh")
        property string iconName: "queue"
        property string playCommand: "play_context_at"
        property bool loading: false
        property var items: []
        property bool searching: false
        property bool keyboardOpen: false
        property var pendingMediaItem: null
        property var refreshReading: null
        property int selectedMatchIndex: 0
        readonly property bool compact: width < 900
        readonly property bool playbackAvailable: djconnect.demoMode || (djconnect.paired && djconnect.backendAvailable
            && djconnect.musicBackendAvailable && !djconnect.versionMismatchVisible)
        readonly property bool refreshAvailable: playbackAvailable && !loading
        readonly property bool trackQueue: playCommand === "play_context_at"
        readonly property string query: searchInput.text.trim()
        readonly property var matches: filterItems(items, query)
        signal refreshRequested()

        function queueSubtitle(item) {
            var artist = (item.artist || item.subtitle || "").trim()
            var album = (item.album || "").trim()
            if (!artist) return album
            return album && artist.toLowerCase() !== album.toLowerCase() ? artist + " • " + album : artist
        }

        function filterItems(source, query) {
            var term = searchText(query.trim())
            if (!term) return source
            return source.filter(function(item) {
                var values = [item.title, item.subtitle, item.artist, item.album, item.uri]
                if (panel.trackQueue) {
                    var artist = (item.artist || item.subtitle || "").trim()
                    if (item.title && artist) values.push(item.title + " - " + artist)
                    values.push(panel.queueSubtitle(item))
                }
                return values.some(function(value) {
                    return searchText(value || "").indexOf(term) >= 0
                })
            })
        }
        function searchText(value) {
            return String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
        }
        function moveMatch(delta) {
            if (!matches.length || !query) return false
            selectedMatchIndex = (selectedMatchIndex + delta + matches.length) % matches.length
            centerMatch()
            return true
        }
        function centerMatch() {
            if (!visible || !query || !matches.length) return
            mediaRows.positionViewAtIndex(selectedMatchIndex, ListView.Center)
        }
        function refreshList() {
            if (!refreshAvailable) return
            if (!query && mediaRows.count) {
                const y = mediaRows.contentY
                const first = mediaRows.indexAt(1, y)
                const index = first >= 0 ? first : mediaRows.indexAt(1, y + mediaRows.spacing)
                const row = mediaRows.itemAtIndex(index)
                if (row) refreshReading = {index: index, item: row.modelData, offset: row.y - y}
            }
            refreshRequested()
            if (!loading) refreshReading = null
        }
        function restoreMediaReading(anchor) {
            if (!visible || query || !matches.length) return
            const match = matches.findIndex(item => item.uri && item.uri === anchor.item.uri
                && (playCommand !== "play_context_at" || item.index === anchor.item.index))
            const index = match < 0 ? Math.min(anchor.index, matches.length - 1) : match
            mediaRows.positionViewAtIndex(index, ListView.Beginning)
            mediaRows.forceLayout()
            const row = mediaRows.itemAtIndex(index)
            if (row) mediaRows.contentY = Math.max(mediaRows.originY, Math.min(row.y - anchor.offset,
                mediaRows.originY + Math.max(0, mediaRows.contentHeight - mediaRows.height)))
        }
        function openSearch(showKeyboard) {
            if (!searchToggle.enabled) return
            searching = true
            keyboardOpen = showKeyboard
            searchInput.forceActiveFocus()
        }
        function closeSearch(resetPending) {
            const restoreFocus = !resetPending && searching && visible
            if (resetPending) { pendingMediaItem = null; refreshReading = null }
            keyboardOpen = false
            searching = false
            searchInput.clear()
            if (restoreFocus && searchToggle.enabled) searchToggle.forceActiveFocus()
        }

        function isPendingMediaItem(item) {
            const pending = pendingMediaItem
            if (!pending) return false
            if (pending === item) return true
            if (playCommand === "start_playlist") return !!pending.uri && pending.uri === item.uri
            return playCommand === "play_context_at" && Number.isInteger(pending.index)
                && pending.index === item.index && pending.uri === item.uri && pending.contextUri === item.contextUri
        }

        function itemPayload(item) {
            return JSON.stringify({
                title: item.title || "",
                subtitle: item.subtitle || "",
                artist: item.artist || "",
                album: item.album || "",
                uri: item.uri || "",
                value: item.value || "",
                contextUri: item.contextUri || "",
                context_uri: item.context_uri || "",
                queueContext: item.queueContext || "",
                queue_context: item.queue_context || "",
                index: typeof item.index === "number" ? item.index : null
            })
        }

        anchors.fill: parent
        color: "transparent"
        z: 16
        onVisibleChanged: {
            if (visible) mediaRows.positionViewAtBeginning()
            else closeSearch(true)
        }
        onMatchesChanged: {
            selectedMatchIndex = Math.max(0, Math.min(selectedMatchIndex, matches.length - 1))
            if (query) Qt.callLater(centerMatch)
            else if (refreshReading) {
                Qt.callLater(restoreMediaReading, refreshReading)
                refreshReading = null
            }
            else mediaRows.positionViewAtBeginning()
        }
        onLoadingChanged: if (!loading) refreshReading = null
        onQueryChanged: {
            selectedMatchIndex = 0
            if (query) Qt.callLater(centerMatch)
        }
        Connections {
            target: djconnect
            function onProfileChanged() { panel.closeSearch(true) }
            function onPairedChanged() { panel.closeSearch(true) }
            function onMediaActivityChanged() {
                if (!djconnect.mediaPlaybackBusy) panel.pendingMediaItem = null
            }
        }
        Connections {
            target: root
            function onActiveChanged() { if (!root.active) panel.closeSearch(true) }
            function onScreenBlankedChanged() { if (root.screenBlanked) panel.closeSearch(true) }
        }
        PageFindShortcut {
            pageReady: panel.visible && searchToggle.enabled
            onActivated: panel.openSearch(false)
        }

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: panel.compact ? 16 : 24
            anchors.bottomMargin: panel.keyboardOpen ? 8 : 132
            spacing: panel.compact ? 10 : 16

            PageToolbar {
                title: panel.heading
                showLeading: panel.playCommand === "start_playlist"
                leading: [ GlassButton {
                    iconName: "back"; tertiary: true; implicitWidth: 52
                    Accessible.name: root.tr("more")
                    onClicked: root.activeScreen = "more"
                } ]
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType

                GlassButton {
                    id: searchToggle
                    objectName: "mediaSearchOpen"
                    readonly property string actionLabel: panel.searching ? root.tr("close_search") : panel.searchLabel
                    iconName: "search"
                    tertiary: true
                    implicitWidth: 52
                    foregroundColor: panel.searching ? BrandTheme.colors.actionAccent : BrandTheme.colors.text
                    Accessible.name: actionLabel
                    ToolTip.visible: hovered
                    ToolTip.text: actionLabel
                    enabled: panel.searching || panel.items.length > 0
                    onClicked: {
                        if (panel.searching) panel.closeSearch()
                        else panel.openSearch(true)
                    }
                }
                RefreshIconButton {
                    objectName: "mediaRefresh"
                    text: panel.refreshLabel
                    loading: panel.loading
                    enabled: panel.refreshAvailable
                    onClicked: panel.refreshList()
                }
            }
            Control {
                visible: panel.searching
                Layout.fillWidth: true
                padding: 5
                background: GlassSurface { radius: 24; interactive: true; focused: searchInput.activeFocus }
                contentItem: RowLayout {
                    spacing: 4
                    FeatureIcon { iconName: "search"; Layout.preferredWidth: 27; Layout.preferredHeight: 27 }
                    TextField {
                        id: searchInput
                        objectName: "mediaSearchInput"
                        Layout.fillWidth: true
                        Layout.minimumHeight: 48
                        background: Item {}
                        placeholderText: panel.searchLabel
                        Accessible.name: placeholderText
                        font.pixelSize: panel.compact ? 18 : 22
                        maximumLength: 120
                        selectByMouse: true
                        TapHandler { onTapped: panel.keyboardOpen = true }
                        onAccepted: {
                            if (panel.keyboardOpen) panel.keyboardOpen = false
                            else panel.moveMatch(1)
                        }
                        Keys.onUpPressed: function(event) { event.accepted = panel.moveMatch(-1) }
                        Keys.onDownPressed: function(event) { event.accepted = panel.moveMatch(1) }
                        Keys.onEscapePressed: if (panel.keyboardOpen) panel.keyboardOpen = false; else panel.closeSearch()
                    }
                    Text {
                        objectName: "mediaSearchMatchCount"
                        visible: panel.query.length > 0 && panel.matches.length > 0
                        text: (panel.selectedMatchIndex + 1) + " / " + panel.matches.length
                        color: BrandTheme.colors.muted
                        font.pixelSize: 16
                    }
                    GlassButton {
                        objectName: "mediaSearchPreviousMatch"
                        iconName: "arrow_up"; tertiary: true
                        Accessible.name: root.tr("logs_previous_match")
                        enabled: panel.matches.length > 0 && searchInput.text.trim().length > 0
                        onClicked: panel.moveMatch(-1)
                    }
                    GlassButton {
                        objectName: "mediaSearchNextMatch"
                        iconName: "arrow_down"; tertiary: true
                        Accessible.name: root.tr("logs_next_match")
                        enabled: panel.matches.length > 0 && searchInput.text.trim().length > 0
                        onClicked: panel.moveMatch(1)
                    }
                    GlassButton {
                        objectName: "mediaSearchClose"
                        iconName: "close"; tertiary: true; Accessible.name: root.tr("close_search")
                        onClicked: panel.closeSearch()
                    }
                }
            }
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 0
                ListView {
                    id: mediaRows
                    objectName: "mediaRows"
                    anchors.fill: parent
                    model: panel.matches
                    clip: true
                    flickableDirection: Flickable.VerticalFlick
                    spacing: panel.compact ? 10 : 14
                    ScrollBar.vertical: ScrollBar {}

                    PullToRefresh {
                        objectName: "mediaPullToRefresh"
                        flickable: mediaRows
                        canRefresh: panel.visible && root.active && !root.screenBlanked
                            && panel.refreshAvailable && !djconnect.demoMode
                        onRefreshRequested: if (canRefresh) panel.refreshList()
                    }

                    delegate: Button {
                        id: mediaRow
                        required property var modelData
                        required property int index
                        objectName: "mediaItemRow-" + index
                        readonly property bool starting: djconnect.mediaPlaybackBusy && panel.isPendingMediaItem(modelData)
                        readonly property bool hasPlayableUri: String(modelData.uri || "").trim().length > 0
                        width: mediaRows.width
                        implicitHeight: Math.max(panel.compact ? 86 : 100, contentItem.implicitHeight + 2 * verticalPadding)
                        topPadding: panel.compact ? 9 : 13
                        bottomPadding: topPadding
                        leftPadding: panel.compact ? 12 : 16
                        rightPadding: leftPadding
                        text: modelData.title || ""
                        Accessible.name: text
                        Accessible.description: starting ? root.tr("loading") : ""
                        enabled: hasPlayableUri && panel.playbackAvailable && !panel.loading && !djconnect.mediaPlaybackBusy
                        opacity: enabled || starting ? 1 : 0.58
                        onClicked: {
                            panel.pendingMediaItem = modelData
                            djconnect.playMediaItem(panel.playCommand, panel.itemPayload(modelData))
                            if (!djconnect.mediaPlaybackBusy) panel.pendingMediaItem = null
                        }
                        background: GlassSurface {
                            introduction: true
                            radius: panel.compact ? 24 : 34
                            pressed: mediaRow.down
                            focused: mediaRow.activeFocus || (panel.query.length > 0
                                && panel.selectedMatchIndex === mediaRow.index)
                        }
                        contentItem: RowLayout {
                            spacing: panel.compact ? 12 : 17
                            Rectangle {
                                id: mediaArt
                                Layout.preferredWidth: panel.compact ? 60 : 74
                                Layout.preferredHeight: Layout.preferredWidth
                                radius: panel.compact ? 14 : 19
                                color: "#22FFFFFF"
                                clip: true
                                FeatureIcon {
                                    anchors.centerIn: parent
                                    width: parent.width * 0.52; height: width
                                    visible: cover.status !== Image.Ready
                                    iconName: panel.iconName
                                    iconColor: BrandTheme.colors.muted
                                }
                                Image {
                                    id: cover
                                    anchors.fill: parent
                                    source: mediaRow.modelData.imageUrl || ""
                                    sourceSize.width: mediaArt.width
                                    sourceSize.height: mediaArt.height
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                    cache: false
                                    visible: status === Image.Ready
                                }
                            }
                            ColumnLayout {
                                id: mediaText
                                readonly property bool onScreen: mediaRow.y + mediaRow.height > mediaRows.contentY
                                    && mediaRow.y < mediaRows.contentY + mediaRows.height
                                Layout.fillWidth: true
                                spacing: 3
                                MediaOverflowText {
                                    objectName: "mediaTrackTitle"
                                    text: mediaRow.text
                                    color: BrandTheme.colors.text
                                    font.pixelSize: panel.compact ? 20 : 24
                                    font.weight: Font.DemiBold
                                    stationaryLines: 2
                                    queueLabel: panel.trackQueue
                                    inViewport: mediaText.onScreen
                                    Layout.fillWidth: true
                                }
                                MediaOverflowText {
                                    objectName: "mediaTrackSubtitle"
                                    text: panel.trackQueue ? panel.queueSubtitle(mediaRow.modelData) : ""
                                    visible: text.length > 0
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: panel.compact ? 16 : 19
                                    queueLabel: panel.trackQueue
                                    inViewport: mediaText.onScreen
                                    Layout.fillWidth: true
                                }
                            }
                            Item {
                                visible: mediaRow.hasPlayableUri
                                Layout.preferredWidth: panel.compact ? 28 : 34
                                Layout.preferredHeight: Layout.preferredWidth
                                Accessible.ignored: true
                                FeatureIcon {
                                    objectName: "mediaPlayIndicator"
                                    anchors.fill: parent
                                    visible: mediaRow.enabled
                                    iconName: "play"
                                }
                                BusyIndicator {
                                    objectName: "mediaPlaySpinner"
                                    anchors.fill: parent
                                    visible: mediaRow.starting
                                    running: visible
                                }
                            }
                        }
                    }
                }
                ColumnLayout {
                    objectName: "mediaEmptyState"
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: Math.max(0, (Math.min(parent.height, 360) - height) / 2)
                    width: parent.width
                    visible: panel.matches.length === 0
                    spacing: 14
                    FeatureIcon {
                        iconName: panel.items.length > 0 ? "search" : panel.iconName
                        iconColor: BrandTheme.colors.muted
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: panel.compact ? 40 : 62
                        Layout.preferredHeight: Layout.preferredWidth
                    }
                    Text {
                        objectName: "mediaEmptyStateMessage"
                        text: panel.items.length > 0 ? root.tr("media_no_matches")
                            : panel.loading ? root.tr("loading") : panel.emptyText
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.muted
                        font.pixelSize: panel.compact ? 20 : 24
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
            SearchKeyboardDock {
                visible: panel.searching && panel.keyboardOpen
                Layout.fillWidth: true
                backend: djconnect
                target: searchInput
                keyboardName: "mediaSearchKeyboard"
                onSubmitted: panel.keyboardOpen = false
                onDismissed: panel.keyboardOpen = false
            }
        }
    }

    Timer {
        id: idleTimer
        interval: Math.max(1000, djconnect.screenTimeoutSeconds * 1000)
        running: djconnect.screenTimeoutSeconds > 0
        repeat: false
        onTriggered: root.hideTransientUi()
    }

    Timer {
        id: forcedWakeTimer
        interval: 10000
        repeat: false
        onTriggered: {
            root.forceScreenAwake = false
            root.forceBrightnessFull = false
        }
    }

    Timer {
        id: djResponseTimer
        interval: 20000
        repeat: false
        onTriggered: djconnect.clearDjResponse()
    }

    Timer {
        id: askDjPollTimer
        interval: 7000
        running: root.activeScreen === "askdj" && djconnect.paired && !djconnect.demoMode
        repeat: true
        triggeredOnStart: true
        onTriggered: djconnect.pollAskDjHistory()
    }

    Connections {
        target: djconnect
        function onWakeScreenRequested() {
            root.temporaryWake(10, false)
            root.restartIdleTimer()
        }
        function onTemporaryWakeRequested(seconds) {
            root.temporaryWake(seconds)
        }
        function onPlaybackTrackChanged() {
            if (root.splashVisible || (!djconnect.paired && !djconnect.demoMode) || !djconnect.hasTrackTitle)
                return
            djconnect.wakeDisplay()
            root.temporaryWake(10)
            root.showTrackArtwork(false, true)
        }
        function onDjResponseChanged() {
            if (djconnect.djResponseVisible) {
                djResponseTimer.restart()
            } else {
                djResponseTimer.stop()
            }
        }
        function onAskDjChanged() {
            const panel = askDjLoader.item
            if (panel && djconnect.askDjMessages.length === 0 && panel.searchOpen)
                panel.closeSearch()
            if (root.askDjClearConfirmOpen && djconnect.askDjMessages.length === 0) {
                root.askDjClearConfirmOpen = false
            }
        }
        function onScreenshotRequested() {
            root.temporaryWake(10, false)
            root.restartIdleTimer()
            root.wakeDisplay()
            Qt.callLater(function() {
                djconnect.saveWindowScreenshot(root)
            })
        }
        function onDebugScreenRequested(screen) {
            root.splashVisible = false
            root.aboutOpen = false
            root.resetPairingConfirmOpen = false
            root.rebootConfirmOpen = false
            root.shutdownConfirmOpen = false
            root.askDjClearConfirmOpen = false
            root.closeMusicDnaConfirmation()
            if (screen === "logs") {
                djconnect.showLogs()
                return
            }
            djconnect.hideLogs()
            if (screen === "about") {
                root.activeScreen = "settings"
                root.aboutOpen = true
                return
            }
            if (screen === "queue") {
                root.activeScreen = "queue"
                djconnect.loadQueue()
                return
            }
            if (screen === "playlists") {
                root.activeScreen = "playlists"
                djconnect.loadPlaylists()
                return
            }
            if (screen === "games" || screen === "settings" || screen === "now" || screen === "control" || screen === "askdj") {
                root.activeScreen = screen
            }
        }
    }

    Timer {
        id: splashTimer
        interval: 1400
        running: true
        repeat: false
        onTriggered: root.splashVisible = false
    }

    Rectangle {
        anchors.fill: parent
        color: root.color
        z: -1000
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        propagateComposedEvents: true
        onPressed: function(mouse) { root.recordActivity(); mouse.accepted = false }
    }

    CompactPageLoader {
        id: nowLoader
        visible: root.activeScreen === "now"
        z: 5
        onVisibleChanged: if (!visible) djconnect.cancelPlaybackSeek()
        sourceComponent: NowPlayingPage {}
    }

    component NowPlayingPage: Item {
        id: nowPanel
        objectName: "nowPanel"
        readonly property alias artworkTint: nowArtworkPalette.tint
        readonly property bool playbackActionsAvailable: djconnect.demoMode || (djconnect.paired && djconnect.backendAvailable)
        readonly property bool seekActionsAvailable: playbackActionsAvailable && djconnect.durationSeconds > 0 && !djconnect.busy
            && (djconnect.demoMode || (djconnect.musicBackendAvailable && djconnect.playbackSeekOutputKnown))

        AppBackground {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.topMargin: 16
            anchors.rightMargin: 16
            anchors.bottomMargin: root.edge + 96
            spacing: 10

            PageToolbar {
                title: root.tr("now_playing")
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                BusyIndicator {
                    running: djconnect.busy
                    visible: djconnect.busy
                    implicitWidth: 28
                    implicitHeight: 28
                }

                GlassButton {
                    iconName: "control"
                    Accessible.name: root.tr("control")
                    onClicked: root.activeScreen = "control"
                }

                RefreshIconButton {
                    id: nowRefreshButton
                    Layout.rightMargin: 0
                    onClicked: djconnect.manualRefresh()
                }

            }

            Item {
                id: artShell
                objectName: "nowTrackArtwork"
                Layout.fillWidth: true
                Layout.fillHeight: true

                Rectangle {
                    id: artFrame
                    anchors.centerIn: parent
                    width: Math.min(parent.width, parent.height)
                    height: width
                    radius: 8
                    color: BrandTheme.colors.background
                    border.color: BrandTheme.colors.surfaceRaised
                    border.width: 1
                    scale: djconnect.playing ? 1.0 : 0.96
                    clip: true

                    Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }

                    ArtworkPalette {
                        id: nowArtworkPalette
                        backend: djconnect; imageItem: albumArt
                        active: !root.screenBlanked
                        leaseKey: djconnect.activeProfileId + "|" + djconnect.title + "|" + djconnect.artist
                    }
                    VinylPlaceholder {
                        objectName: "nowArtworkPlaceholder"
                        anchors.fill: parent
                        radius: artFrame.radius
                        visible: albumArt.opacity < 1
                    }
                    Image {
                        id: albumArt
                        objectName: "nowArtworkImage"
                        anchors.fill: parent
                        source: djconnect.imageUrl
                        sourceSize.width: width
                        sourceSize.height: height
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                        opacity: status === Image.Ready ? 1 : 0

                        Behavior on opacity { NumberAnimation { duration: 240 } }
                    }

                    Rectangle {
                        objectName: "nowPlaybackTextBackdrop"
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        z: 2
                        height: Math.min(parent.height, Math.max(112, parent.height * 0.34, nowPlaybackText.implicitHeight + 48))
                        gradient: Gradient {
                            orientation: Gradient.Vertical
                            GradientStop { position: 0.0; color: "#00000000" }
                            GradientStop { position: 0.44; color: "#b0000000" }
                            GradientStop { position: 1.0; color: "#e8000000" }
                        }
                    }

                    ColumnLayout {
                        id: nowPlaybackText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 18
                        z: 3
                        spacing: 4

                        Text {
                            objectName: "nowPlaybackTitle"
                            text: djconnect.title
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.pixelSize: 34
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            maximumLineCount: 2
                            wrapMode: Text.Wrap
                            style: Text.Raised
                            styleColor: "#aa000000"
                            Layout.fillWidth: true
                        }

                        Text {
                            objectName: "nowPlaybackArtist"
                            text: djconnect.artist
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.muted
                            font.pixelSize: 24
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            maximumLineCount: 2
                            wrapMode: Text.Wrap
                            style: Text.Raised
                            styleColor: "#aa000000"
                            Layout.fillWidth: true
                        }
                    }

                }

                TapHandler {
                    onTapped: {
                        if (root.suppressNextNowPanelTap) {
                            root.suppressNextNowPanelTap = false
                            return
                        }
                        root.activeScreen = "control"
                    }
                }
            }

            RowLayout {
                objectName: "nowPlaybackPosition"
                visible: djconnect.hasTrackTitle && djconnect.durationSeconds > 0
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(parent.width, 416)
                spacing: 10
                Slider {
                    id: nowPositionSlider
                    objectName: "nowPlaybackProgress"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    from: 0
                    to: djconnect.durationSeconds
                    stepSize: 1
                    enabled: nowPanel.seekActionsAvailable && (djconnect.demoMode || djconnect.musicBackendSupportsSeek)
                    Accessible.name: root.tr("session_playback_position")
                    onMoved: if (!seekPointer.pressed) {
                        const lease = djconnect.beginPlaybackSeek()
                        if (lease) djconnect.commitPlaybackSeek(lease, Math.round(value))
                    }
                    Binding {
                        target: nowPositionSlider; property: "value"
                        value: seekPointer.lease > 0 ? seekPointer.draft : djconnect.positionSeconds
                        restoreMode: Binding.RestoreNone
                    }
                    background: Rectangle {
                        x: nowPositionSlider.leftPadding
                        y: (nowPositionSlider.height - height) / 2
                        width: nowPositionSlider.availableWidth
                        height: 6; radius: 3
                        color: BrandTheme.alpha(BrandTheme.colors.text, "30")
                        Rectangle {
                            width: parent.width * nowPositionSlider.visualPosition
                            height: parent.height
                            radius: 3
                            color: BrandTheme.colors.text
                        }
                    }
                    handle: Rectangle {
                        width: 16; height: 16; radius: 8
                        visible: nowPositionSlider.enabled
                        x: nowPositionSlider.leftPadding + nowPositionSlider.visualPosition * (nowPositionSlider.availableWidth - width)
                        y: (nowPositionSlider.height - height) / 2
                        color: BrandTheme.colors.text
                    }
                    MouseArea {
                        id: seekPointer
                        anchors.fill: parent
                        property int lease: 0
                        property int draft: 0
                        function targetAt(x) {
                            const inset = nowPositionSlider.leftPadding + nowPositionSlider.handle.width / 2
                            const travel = Math.max(1, nowPositionSlider.availableWidth - nowPositionSlider.handle.width)
                            return Math.round(nowPositionSlider.valueAt(Math.max(0, Math.min(1, (x - inset) / travel))))
                        }
                        onPressed: function(mouse) {
                            lease = djconnect.beginPlaybackSeek()
                            draft = targetAt(mouse.x)
                        }
                        onPositionChanged: function(mouse) { if (lease) draft = targetAt(mouse.x) }
                        onReleased: function(mouse) {
                            const ticket = lease
                            lease = 0
                            if (ticket) djconnect.commitPlaybackSeek(ticket, targetAt(mouse.x))
                        }
                        onCanceled: {
                            if (lease) djconnect.cancelPlaybackSeek()
                            lease = 0
                        }
                    }
                }
                Text {
                    objectName: "nowPlaybackTime"
                    text: seekPointer.lease > 0
                        ? Math.floor(seekPointer.draft / 60) + ":" + ("0" + seekPointer.draft % 60).slice(-2)
                            + " / " + djconnect.progressLabel.split("/")[1]
                        : djconnect.progressLabel.replace("/", " / ")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 18
                }
            }

            GlassSurface {
                objectName: "nowPlaybackControls"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(parent.width, 416)
                Layout.preferredHeight: 76
                radius: height / 2
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10
                    GlassButton {
                        objectName: "nowSeekBack"
                        Layout.fillWidth: true
                        iconName: "seek_back_15"
                        Accessible.name: root.tr("seek_back_15")
                        visible: djconnect.demoMode || djconnect.musicBackendSupportsSeek
                        enabled: nowPanel.seekActionsAvailable
                        onClicked: djconnect.seekPlayback(-15000)
                    }
                    GlassButton {
                        objectName: "nowPreviousTrack"
                        Layout.fillWidth: true
                        iconName: "previous"
                        Accessible.name: root.tr("session_playback_previous_track")
                        enabled: nowPanel.playbackActionsAvailable
                        onClicked: djconnect.previous()
                    }
                    GlassButton {
                        objectName: "nowTogglePlayback"
                        Layout.fillWidth: true
                        iconName: djconnect.playing ? "pause" : "play"
                        Accessible.name: root.tr(djconnect.playing ? "pause" : "play")
                        primary: true
                        enabled: nowPanel.playbackActionsAvailable
                        onClicked: djconnect.togglePlay()
                    }
                    GlassButton {
                        objectName: "nowNextTrack"
                        Layout.fillWidth: true
                        iconName: "next"
                        Accessible.name: root.tr("session_playback_next_track")
                        enabled: nowPanel.playbackActionsAvailable
                        onClicked: djconnect.next()
                    }
                    GlassButton {
                        objectName: "nowSeekForward"
                        Layout.fillWidth: true
                        iconName: "seek_forward_15"
                        Accessible.name: root.tr("seek_forward_15")
                        visible: djconnect.demoMode || djconnect.musicBackendSupportsSeek
                        enabled: nowPanel.seekActionsAvailable
                        onClicked: djconnect.seekPlayback(15000)
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                visible: djconnect.demoMode || djconnect.musicBackendSupportsShuffle || djconnect.musicBackendSupportsRepeat
                GlassButton {
                    objectName: "nowShuffle"
                    readonly property string modeLabel: root.tr(djconnect.shuffle ? "shuffle_on" : "shuffle_off")
                    iconName: "shuffle"
                    visible: djconnect.demoMode || djconnect.musicBackendSupportsShuffle
                    checked: djconnect.shuffle
                    enabled: nowPanel.playbackActionsAvailable && !djconnect.busy && !djconnect.playbackModePending
                        && (djconnect.demoMode || djconnect.musicBackendAvailable)
                    Accessible.name: root.tr("shuffle")
                    Accessible.description: modeLabel
                    ToolTip.text: modeLabel
                    ToolTip.visible: hovered
                    onClicked: djconnect.toggleShuffle()
                }
                GlassButton {
                    objectName: "nowRepeat"
                    readonly property string modeLabel: root.tr(djconnect.repeat === "track" ? "repeat_one"
                        : djconnect.repeat === "context" ? "repeat_context" : "repeat_off")
                    iconName: djconnect.repeat === "track" ? "repeat_one" : "repeat"
                    visible: djconnect.demoMode || djconnect.musicBackendSupportsRepeat
                    checked: djconnect.repeat !== "off"
                    enabled: nowPanel.playbackActionsAvailable && !djconnect.busy && !djconnect.playbackModePending
                        && (djconnect.demoMode || djconnect.musicBackendAvailable)
                    Accessible.name: root.tr("repeat")
                    Accessible.description: modeLabel
                    ToolTip.text: modeLabel
                    ToolTip.visible: hovered
                    onClicked: djconnect.setPlaybackRepeatMode(djconnect.repeat === "off" ? "track"
                        : djconnect.repeat === "track" ? "context" : "off")
                }
            }

            GlassButton {
                objectName: "nowPlaybackOutput"
                readonly property string reportedOutput: djconnect.outputDevice.trim()
                visible: reportedOutput.length > 0 || djconnect.outputDeviceChoices.length > 0
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(parent.width, 416)
                Layout.preferredHeight: 52
                iconName: "speaker"
                text: reportedOutput || root.tr("output_device")
                Accessible.name: reportedOutput ? root.tr("output_device") + ": " + reportedOutput : text
                onClicked: root.activeScreen = "control"
            }

            Text {
                objectName: "nowOutputSwitching"
                visible: djconnect.pendingOutputChoiceName.length > 0 || djconnect.outputChoiceUnconfirmed
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: parent.width
                text: djconnect.outputChoiceUnconfirmed ? root.tr("session_output_switch_unconfirmed")
                    : root.tr("session_output_switching").replace("%1", () => djconnect.pendingOutputChoiceName)
                textFormat: Text.PlainText
                color: BrandTheme.colors.muted
                font.pixelSize: 17
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
            }

        }

    }

    CompactPageLoader {
        visible: root.activeScreen === "control"
        z: 10
        onVisibleChanged: {
            if (visible && !root.wallProfile) djconnect.refreshControlOutputs(false)
            else djconnect.cancelControlVolume()
        }
        sourceComponent: PlaybackControlPage {}
    }

    component PlaybackControlPage: Rectangle {
        id: controlPanel
        objectName: "controlPanel"
        function adjustVolume(delta) {
            var lease = djconnect.beginControlVolume()
            if (lease > 0)
                djconnect.commitControlVolume(lease, Math.round(djconnect.volume + delta))
        }
        color: BrandTheme.colors.background

        AppBackground {}
        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.topMargin: 16
            anchors.rightMargin: 16
            anchors.bottomMargin: root.edge + 112
            spacing: 10

            PageToolbar {
                title: root.tr("control")
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                BusyIndicator {
                    running: djconnect.busy
                    visible: djconnect.busy
                    implicitWidth: 30
                    implicitHeight: 30
                }

                RefreshIconButton {
                    onClicked: djconnect.manualRefresh()
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                objectName: "controlTrackMetadata"
                Layout.preferredHeight: 72
                spacing: 2

                Text {
                    text: djconnect.title
                    color: BrandTheme.colors.text
                    font.pixelSize: 30
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Layout.fillWidth: true
                }

                Text {
                    text: djconnect.artist
                    color: BrandTheme.colors.muted
                    font.pixelSize: 28
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Layout.fillWidth: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 178
                spacing: 18

                IconButton {
                    iconName: "previous"
                    onClicked: djconnect.previous()
                }

                IconButton {
                    iconName: djconnect.playing ? "pause" : "play"
                    primary: true
                    onClicked: djconnect.togglePlay()
                }

                IconButton {
                    iconName: "next"
                    onClicked: djconnect.next()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 54
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 18
                    Layout.alignment: Qt.AlignVCenter
                    radius: 8
                    color: BrandTheme.alpha(BrandTheme.colors.surface, "55")
                    border.color: BrandTheme.colors.surfaceRaised
                    border.width: 1

                    Rectangle {
                        width: parent.width * djconnect.trackProgress
                        height: parent.height
                        radius: parent.radius
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: root.moodColor("sliderStart") }
                            GradientStop { position: 1.0; color: root.moodColor("sliderEnd") }
                        }
                    }
                }

                Text {
                    text: djconnect.progressLabel
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 128
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 74
                spacing: 14

                Text {
                    text: root.tr("vol")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 24
                    Layout.preferredWidth: 46
                }

                PurpleButton {
                    text: "-"
                    font.pixelSize: 30
                    Layout.preferredWidth: 54
                    Layout.preferredHeight: 54
                    enabled: djconnect.controlVolumeAvailable
                    onClicked: controlPanel.adjustVolume(-10)
                }

                Slider {
                    id: controlVolumeSlider
                    objectName: "controlVolumeSlider"
                    from: 0
                    to: 60
                    enabled: djconnect.controlVolumeAvailable
                    stepSize: 1
                    Layout.fillWidth: true
                    Accessible.name: root.tr("vol")
                    onMoved: {
                        if (!controlVolumePointer.pressed) {
                            var lease = djconnect.beginControlVolume()
                            if (lease > 0) djconnect.commitControlVolume(lease, Math.round(value))
                        }
                    }
                    MouseArea {
                        id: controlVolumePointer
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton
                        property int lease: 0
                        property int draft: 0

                        function valueAt(x) {
                            var slider = controlVolumeSlider
                            var halfHandle = slider.handle.width / 2
                            var travel = Math.max(1, slider.availableWidth - 2 * halfHandle)
                            var position = Math.max(0, Math.min(1, (x - slider.leftPadding - halfHandle) / travel))
                            return Math.round(slider.valueAt(slider.mirrored ? 1 - position : position))
                        }

                        onPressed: function(mouse) {
                            controlVolumeSlider.forceActiveFocus(Qt.MouseFocusReason)
                            lease = djconnect.beginControlVolume()
                            draft = valueAt(mouse.x)
                        }
                        onPositionChanged: function(mouse) {
                            if (pressed && lease > 0) draft = valueAt(mouse.x)
                        }
                        onReleased: function(mouse) {
                            var captured = lease
                            lease = 0
                            if (captured > 0) djconnect.commitControlVolume(captured, valueAt(mouse.x))
                        }
                        onCanceled: {
                            if (lease > 0) djconnect.cancelControlVolume()
                            lease = 0
                        }
                    }
                    Binding {
                        target: controlVolumeSlider; property: "value"
                        value: controlVolumePointer.lease > 0 ? controlVolumePointer.draft : Math.min(60, djconnect.volume)
                        restoreMode: Binding.RestoreNone
                    }
                    background: Rectangle {
                        x: controlVolumeSlider.leftPadding
                        y: controlVolumeSlider.topPadding + controlVolumeSlider.availableHeight / 2 - height / 2
                        width: controlVolumeSlider.availableWidth
                        height: 10
                        radius: 5
                        color: root.moodColor("chip")

                        Rectangle {
                            width: controlVolumeSlider.visualPosition * parent.width
                            height: parent.height
                            radius: parent.radius
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: root.moodColor("sliderStart") }
                                GradientStop { position: 1.0; color: root.moodColor("sliderEnd") }
                            }
                        }
                    }
                    handle: Rectangle {
                        x: controlVolumeSlider.leftPadding + controlVolumeSlider.visualPosition * (controlVolumeSlider.availableWidth - width)
                        y: controlVolumeSlider.topPadding + controlVolumeSlider.availableHeight / 2 - height / 2
                        width: 34
                        height: 34
                        radius: 17
                        color: BrandTheme.colors.text
                        border.color: BrandTheme.colors.muted
                        border.width: 1
                    }
                }

                PurpleButton {
                    text: "+"
                    font.pixelSize: 30
                    Layout.preferredWidth: 54
                    Layout.preferredHeight: 54
                    enabled: djconnect.controlVolumeAvailable
                    onClicked: controlPanel.adjustVolume(10)
                }

                Text {
                    text: Math.round(Math.min(60, djconnect.volume) / 60 * 100) + "%"
                    color: BrandTheme.colors.text
                    font.pixelSize: 26
                    font.bold: true
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 54
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                spacing: 12

                BrandComboBox {
                    id: controlOutputDeviceCombo
                    objectName: "controlOutputDeviceCombo"
                    property string noOutputDeviceLabel: root.tr("none")
                    property var deviceChoices: [{ "name": noOutputDeviceLabel, "value": "" }].concat(djconnect.outputDeviceChoices.length > 0 ? djconnect.outputDeviceChoices : (djconnect.outputDevice.length > 0 ? [{ "name": djconnect.outputDevice, "value": djconnect.outputDevice }] : []))
                    function choiceName(choice) {
                        if (typeof choice === "string") return choice
                        return ((choice && choice.name) || (choice && choice.value) || "").toString()
                    }
                    function choiceValue(choice) {
                        if (typeof choice === "string") return choice
                        return ((choice && choice.value) || (choice && choice.name) || "").toString()
                    }
                    function displayName(index) {
                        const name = choiceName(deviceChoices[index])
                        if (index <= 0) return name
                        let count = 0
                        let ordinal = 0
                        for (let i = 1; i < deviceChoices.length; i++) {
                            if (choiceName(deviceChoices[i]) !== name) continue
                            count++
                            if (i === index) ordinal = count
                        }
                        return count > 1 ? name + " (" + ordinal + ")" : name
                    }
                    function selectedIndex() {
                        const selectedId = djconnect.outputDeviceId
                        var selected = (djconnect.outputDevice || "").trim()
                        if (selected.length === 0) return 0
                        if (selectedId) {
                            for (var index = 1; index < deviceChoices.length; index++) {
                                if (choiceValue(deviceChoices[index]) === selectedId) return index
                            }
                        }
                        let match = 0
                        for (var i = 0; i < deviceChoices.length; i++) {
                            if (choiceName(deviceChoices[i]).trim() !== selected) continue
                            if (match > 0) return 0
                            match = i
                        }
                        return match
                    }
                    model: deviceChoices
                    textRole: "name"
                    displayText: displayName(currentIndex)
                    enabled: djconnect.pendingOutputChoiceName.length === 0 && !djconnect.controlOutputsRefreshing
                    visible: djconnect.outputDeviceChoices.length > 0
                    currentIndex: selectedIndex()
                    font.pixelSize: 26
                    delegate: ItemDelegate {
                        width: controlOutputDeviceCombo.width
                        text: controlOutputDeviceCombo.displayName(index)
                        font.pixelSize: 28
                    }
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onActivated: function(index) {
                        if (index === 0) djconnect.setOutputDevice("")
                        else djconnect.setOutputChoice(controlOutputDeviceCombo.choiceValue(controlOutputDeviceCombo.deviceChoices[index]))
                    }
                }

                Text {
                    objectName: "controlOutputInventoryState"
                    text: djconnect.controlOutputsRefreshing ? root.tr("loading")
                        : djconnect.controlOutputsStatus === "unavailable" ? root.tr("session_output_unavailable")
                        : djconnect.controlOutputsStatus === "ready" ? root.tr(djconnect.outputDevice.length > 0
                            ? "session_output_empty" : "playback_outputs_none_found")
                        : djconnect.outputDevice || root.tr("output_device")
                    visible: !controlOutputDeviceCombo.visible
                    color: BrandTheme.colors.muted
                    font.pixelSize: 24
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                }

                GlassButton {
                    id: controlRefreshOutputs
                    objectName: "controlRefreshOutputs"
                    iconName: djconnect.controlOutputsRefreshing ? "" : "refresh"
                    Accessible.name: root.tr("session_output_refresh")
                    enabled: (djconnect.demoMode || (djconnect.paired && djconnect.backendAvailable))
                        && !djconnect.controlOutputsRefreshing && djconnect.pendingOutputChoiceName.length === 0
                    opacity: enabled || djconnect.controlOutputsRefreshing ? 1 : 0.4
                    onClicked: djconnect.refreshControlOutputs()
                    BusyIndicator {
                        anchors.centerIn: parent
                        width: 26; height: 26
                        visible: djconnect.controlOutputsRefreshing
                        running: visible
                    }
                }
            }

            Text {
                objectName: "controlOutputSwitching"
                visible: djconnect.pendingOutputChoiceName.length > 0 || djconnect.outputChoiceUnconfirmed
                Layout.fillWidth: true
                text: djconnect.outputChoiceUnconfirmed ? root.tr("session_output_switch_unconfirmed")
                    : root.tr("session_output_switching").replace("%1", () => djconnect.pendingOutputChoiceName)
                textFormat: Text.PlainText
                color: BrandTheme.colors.muted
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                font.pixelSize: 17
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 126
                spacing: 18

                IconButton {
                    iconName: "shuffle"
                    active: djconnect.shuffle
                    enabled: !djconnect.busy && !djconnect.playbackModePending
                    onClicked: djconnect.toggleShuffle()
                }

                IconButton {
                    iconName: djconnect.currentTrackFavorite ? "heartFilled" : "heart"
                    active: djconnect.currentTrackFavorite
                    enabled: djconnect.favoriteAvailable && !djconnect.favoriteBusy
                    opacity: djconnect.favoriteAvailable ? (djconnect.favoriteBusy ? 0.55 : 1.0) : 0.0
                    onClicked: djconnect.saveCurrentTrack()
                }

                IconButton {
                    iconName: djconnect.repeat === "track" ? "repeatOne" : djconnect.repeat === "context" ? "repeat" : "repeatOff"
                    active: djconnect.repeat !== "off"
                    enabled: !djconnect.busy && !djconnect.playbackModePending
                    onClicked: djconnect.cycleRepeat()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 24
                spacing: 9

                Rectangle {
                    width: 14
                    height: 14
                    radius: 7
                    color: djconnect.paired ? (djconnect.backendAvailable ? BrandTheme.colors.success : BrandTheme.colors.danger) : BrandTheme.colors.warning
                    Layout.alignment: Qt.AlignVCenter
                }

                Text {
                    text: djconnect.statusText
                    color: BrandTheme.colors.muted
                    font.pixelSize: 16
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    }
                }
            }

        }

    CompactPageLoader {
        visible: root.settingsOpen && (djconnect.paired || djconnect.demoMode)
        z: 10
        sourceComponent: SettingsPage {}
    }

    component SettingsPage: Rectangle {
        id: settingsPanel
        objectName: "settingsPanel"
        color: "transparent"
        readonly property bool canChangeMusicDna: djconnect.musicDnaLoaded && !djconnect.musicDnaBusy
            && (djconnect.paired || djconnect.demoMode)
        readonly property bool canTransferMusicDna: canChangeMusicDna && djconnect.musicDnaEnabled
            && djconnect.paired && djconnect.personalProfileExportAllowed && djconnect.backendAvailable
            && !djconnect.demoMode && !djconnect.versionMismatchVisible

        FileDialog {
            id: profileExportDialog
            objectName: "profileExportDialog"
            property int profileRevision: 0
            property bool musicDna: false
            title: root.tr(musicDna ? "music_dna_export" : "ask_dj_export")
            fileMode: FileDialog.SaveFile
            options: FileDialog.DontUseNativeDialog
            defaultSuffix: "json"
            nameFilters: ["JSON (*.json)"]
            onAccepted: {
                if (musicDna) djconnect.exportMusicDna(selectedFile, profileRevision)
                else djconnect.exportAskDjHistory(selectedFile, profileRevision)
            }
        }

        FileDialog {
            id: musicDnaImportDialog
            objectName: "musicDnaImportDialog"
            property int profileRevision: 0
            title: root.tr("music_dna_import")
            options: FileDialog.DontUseNativeDialog
            nameFilters: ["JSON (*.json)"]
            onAccepted: djconnect.prepareMusicDnaImport(selectedFile, profileRevision)
        }

        GlassPopup {
            objectName: "musicDnaImportPreview"
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 560)
            padding: 24
            modal: true
            closePolicy: Popup.NoAutoClose
            visible: settingsPanel.visible && !!djconnect.musicDnaImportPreview.filename
            contentItem: ColumnLayout {
                spacing: 16
                Text {
                    text: djconnect.musicDnaImportPreview.filename || ""
                    textFormat: Text.PlainText
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Text {
                    objectName: "musicDnaImportUpdatedAt"
                    readonly property string updatedTime: {
                        root.trVersion
                        return djconnect.djSession.formatTimestamp(djconnect.musicDnaImportPreview.updatedAt || "", true)
                    }
                    visible: updatedTime.length > 0
                    text: root.tr("music_dna_updated_at").replace("%1", updatedTime)
                    textFormat: Text.PlainText
                    color: BrandTheme.colors.muted
                    font.pixelSize: 18
                    Layout.fillWidth: true
                }
                Text {
                    objectName: "musicDnaImportGeneration"
                    readonly property var generation: djconnect.musicDnaImportPreview.generation
                    visible: typeof generation === "number"
                    text: root.tr("music_dna_import_generation").replace("%1", generation)
                    textFormat: Text.PlainText
                    color: BrandTheme.colors.muted
                    font.pixelSize: 18
                    Layout.fillWidth: true
                }
                Text {
                    text: root.tr("music_dna_import_confirm")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 20
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                GlassButton {
                    objectName: "musicDnaImportConfirm"
                    text: root.tr("music_dna_import")
                    primary: true
                    enabled: settingsPanel.canTransferMusicDna
                    Layout.fillWidth: true
                    onClicked: djconnect.confirmMusicDnaImport()
                }
                GlassButton {
                    text: root.tr("cancel")
                    enabled: !djconnect.musicDnaBusy
                    Layout.fillWidth: true
                    onClicked: djconnect.cancelMusicDnaImport()
                }
            }
        }

        ProfileReadOnOpen {
            surfaceVisible: settingsPanel.visible
            busy: djconnect.musicDnaBusy
            loaded: djconnect.musicDnaLoaded
            onRequested: djconnect.loadMusicDna()
        }

        onVisibleChanged: {
            if (visible && settingsScroll.contentItem) {
                settingsScroll.contentItem.contentY = 0
            } else if (!visible) djconnect.cancelMusicDnaImport()
        }

        function languageIndex() {
            for (var i = 0; i < languageBox.model.length; i++) {
                if (languageBox.model[i].code === djconnect.language) return i
            }
            return 0
        }


        PageToolbar {
            id: settingsToolbar
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 22
            title: root.tr("setup_title")
            leading: [ GlassButton { iconName: "back"; tertiary: true; implicitWidth: 52; Accessible.name: root.tr("more"); onClicked: root.activeScreen = "more" } ]
        }

        ScrollView {
            id: settingsScroll
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.top: settingsToolbar.bottom
            anchors.margins: 22
            anchors.bottomMargin: 130
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            ColumnLayout {
                width: Math.max(0, settingsScroll.availableWidth)
                spacing: 20

                PurpleButton {
                    objectName: "settingsOpenLocalTerminal"
                    text: root.tr("local_terminal")
                    enabled: djconnect.localTerminalAvailable && !djconnect.busy
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    onClicked: djconnect.openLocalTerminal()
                }

                Text {
                    visible: !djconnect.localTerminalAvailable
                    text: root.tr("local_terminal_disabled")
                    color: BrandTheme.colors.muted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    objectName: "settingsShutdownDevice"
                    text: root.tr("shutdown_device")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    onClicked: root.shutdownConfirmOpen = true
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    BrandSwitch {
                        objectName: "glassAppearanceToggle"
                        text: root.tr("glass_title")
                        font.pixelSize: 24
                        checked: djconnect.glassEnabled
                        onToggled: djconnect.setGlassEnabled(checked)
                        Accessible.name: root.tr("glass_title")
                    }
                    Text {
                        text: root.tr("glass_description")
                        color: BrandTheme.colors.muted; font.pixelSize: 20
                        wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        Layout.fillWidth: true
                    }
                    Text {
                        visible: root.liquidGlassEnabled && !GlassStyle.hardware
                        text: root.tr("glass_fallback")
                        color: BrandTheme.colors.muted; font.pixelSize: 18
                        wrapMode: Text.WordWrap; textFormat: Text.PlainText
                        Layout.fillWidth: true
                    }
                }
                PurpleButton {
                    visible: !djconnect.paired
                    text: djconnect.demoMode ? root.tr("exit_demo") : root.tr("demo_mode")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    onClicked: {
                        if (djconnect.demoMode) {
                            djconnect.exitDemoMode()
                            root.activeScreen = "now"
                        } else {
                            djconnect.enterDemoMode()
                            root.activeScreen = "now"
                        }
                    }
                }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("websocket_fast_path")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                    wrapMode: Text.WordWrap
                }

                BrandSwitch {
                    id: websocketFastPathSwitch
                    checked: djconnect.websocketFastPathEnabled
                    Layout.alignment: Qt.AlignVCenter
                    onToggled: djconnect.setWebSocketFastPathEnabled(checked)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("session_refresh_interval")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                    wrapMode: Text.WordWrap
                }

                BrandComboBox {
                    objectName: "sessionRefreshInterval"
                    readonly property var choices: [1, 5, 10, 15, 30, 60]
                    model: choices.map(function(seconds) {
                        return root.tr("session_refresh_seconds").replace("%1", seconds)
                    })
                    currentIndex: choices.indexOf(djconnect.sessionRefreshSeconds)
                    Accessible.name: root.tr("session_refresh_interval")
                    font.pixelSize: 28
                    Layout.fillWidth: true
                    onActivated: function(index) { djconnect.setSessionRefreshSeconds(choices[index]) }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("screen_off")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                }

                BrandComboBox {
                    id: screenTimeoutBox
                    property var timeoutChoices: [30, 60, 90, 120, 180, 240, 300, 600]
                    model: timeoutChoices
                    currentIndex: Math.max(0, timeoutChoices.indexOf(djconnect.screenTimeoutSeconds))
                    font.pixelSize: 28
                    delegate: ItemDelegate {
                        width: screenTimeoutBox.width
                        text: modelData
                        font.pixelSize: 30
                    }
                    Layout.fillWidth: true
                    onActivated: function(index) { djconnect.setScreenTimeoutSeconds(timeoutChoices[index]) }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("brightness")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                }

                Slider {
                    id: brightnessSlider
                    from: 10
                    to: 100
                    stepSize: 1
                    value: djconnect.screenBrightnessPercent
                    Layout.fillWidth: true
                    onMoved: djconnect.setScreenBrightnessPercent(Math.round(value))
                    background: Rectangle {
                        x: brightnessSlider.leftPadding
                        y: brightnessSlider.topPadding + brightnessSlider.availableHeight / 2 - height / 2
                        width: brightnessSlider.availableWidth
                        height: 10
                        radius: 5
                        color: root.moodColor("chip")

                        Rectangle {
                            width: brightnessSlider.visualPosition * parent.width
                            height: parent.height
                            radius: parent.radius
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: root.moodColor("sliderStart") }
                                GradientStop { position: 1.0; color: root.moodColor("sliderEnd") }
                            }
                        }
                    }
                    handle: Rectangle {
                        x: brightnessSlider.leftPadding + brightnessSlider.visualPosition * (brightnessSlider.availableWidth - width)
                        y: brightnessSlider.topPadding + brightnessSlider.availableHeight / 2 - height / 2
                        width: 30
                        height: 30
                        radius: 15
                        color: BrandTheme.colors.text
                        border.color: BrandTheme.colors.muted
                        border.width: 1
                    }
                }

                Text {
                    text: Math.round(brightnessSlider.value) + "%"
                    color: BrandTheme.colors.text
                    font.pixelSize: 20
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 48
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("updates")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                }

                BrandComboBox {
                    id: updateChannelBox
                    font.pixelSize: 28
                    model: ["stable", "beta"]
                    currentIndex: djconnect.updateChannel === "beta" ? 1 : 0
                    delegate: ItemDelegate {
                        width: updateChannelBox.width
                        text: modelData
                        font.pixelSize: 30
                    }
                    Layout.fillWidth: true
                    onActivated: djconnect.setUpdateChannel(currentText)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("language")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                }

                BrandComboBox {
                    id: languageBox
                    font.pixelSize: 28
                    model: [
                        { code: "en", label: "English" },
                        { code: "nl", label: "Nederlands" },
                        { code: "de", label: "Deutsch" },
                        { code: "fr", label: "Français" },
                        { code: "es", label: "Español" }
                    ]
                    textRole: "label"
                    valueRole: "code"
                    currentIndex: settingsPanel.languageIndex()
                    delegate: ItemDelegate {
                        width: languageBox.width
                        text: modelData.label
                        font.pixelSize: 30
                    }
                    Layout.fillWidth: true
                    onActivated: djconnect.setLanguage(currentValue)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("log_level")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    Layout.preferredWidth: 176
                }

                BrandComboBox {
                    id: logLevelBox
                    font.pixelSize: 28
                    model: ["OFF", "DEBUG", "INFO", "WARNING", "ERROR"]
                    currentIndex: model.indexOf(djconnect.logLevel)
                    displayText: currentText === "OFF" ? root.tr("off") : currentText
                    delegate: ItemDelegate {
                        width: logLevelBox.width
                        text: modelData === "OFF" ? root.tr("off") : modelData
                        font.pixelSize: 30
                    }
                    Layout.fillWidth: true
                    onActivated: djconnect.setLogLevel(model[currentIndex])
                }
            }

            Text {
                text: root.tr("music_dna")
                color: BrandTheme.colors.text
                font.pixelSize: 26
                font.bold: true
                Layout.fillWidth: true
                Layout.topMargin: 8
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("music_dna_settings_status")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    font.bold: true
                    Layout.preferredWidth: 176
                }

                Text {
                    objectName: "musicDnaSettingsStatusText"
                    text: {
                        if (!djconnect.musicDnaLoaded) {
                            return djconnect.musicDnaBusy ? root.tr("music_dna_loading_profile")
                                : djconnect.musicDnaError || root.tr("music_dna_profile_unavailable")
                        }
                        return djconnect.musicDnaEnabled ? root.tr("music_dna_settings_enabled_description")
                            : root.tr("music_dna_settings_disabled_description")
                    }
                    color: BrandTheme.colors.muted
                    font.pixelSize: 22
                    font.bold: true
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignRight
                    Layout.fillWidth: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("music_dna")
                    color: BrandTheme.colors.text
                    font.pixelSize: 22
                    font.bold: true
                    Layout.preferredWidth: 176
                }

                Item { Layout.fillWidth: true }

                PurpleButton {
                    objectName: "musicDnaSettingsToggle"
                    text: djconnect.musicDnaEnabled ? root.tr("music_dna_disable") : root.tr("music_dna_enable")
                    primary: !djconnect.musicDnaEnabled
                    enabled: settingsPanel.canChangeMusicDna
                    font.pixelSize: 22
                    Layout.preferredWidth: 190
                    Layout.preferredHeight: 52
                    onClicked: {
                        if (djconnect.musicDnaEnabled) {
                            root.musicDnaConfirmationRevision = djconnect.musicDnaContextRevision
                            root.musicDnaDisableConfirmOpen = true
                        } else {
                            root.openMusicDnaPrompt(false)
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Text {
                    text: root.tr("music_dna_settings_profile")
                    color: BrandTheme.colors.text
                    font.pixelSize: 22
                    font.bold: true
                    Layout.preferredWidth: 176
                }

                Item { Layout.fillWidth: true }

                DangerButton {
                    objectName: "musicDnaSettingsClear"
                    text: root.tr("music_dna_clear")
                    enabled: djconnect.musicDnaEnabled && settingsPanel.canChangeMusicDna
                    font.pixelSize: 22
                    Layout.preferredWidth: 150
                    Layout.preferredHeight: 52
                    onClicked: {
                        root.musicDnaConfirmationRevision = djconnect.musicDnaContextRevision
                        root.musicDnaClearConfirmOpen = true
                    }
                }
            }

            PurpleButton {
                objectName: "askDjExportButton"
                text: root.tr("ask_dj_export")
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                enabled: djconnect.paired && djconnect.personalProfileExportAllowed && djconnect.backendAvailable
                    && !djconnect.demoMode && !djconnect.versionMismatchVisible && !djconnect.askDjBusy
                onClicked: {
                    profileExportDialog.musicDna = false
                    profileExportDialog.profileRevision = djconnect.musicDnaContextRevision
                    profileExportDialog.open()
                }
            }

            PurpleButton {
                objectName: "musicDnaExportButton"
                text: root.tr("music_dna_export")
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                enabled: settingsPanel.canTransferMusicDna
                onClicked: {
                    profileExportDialog.musicDna = true
                    profileExportDialog.profileRevision = djconnect.musicDnaContextRevision
                    profileExportDialog.open()
                }
            }

            PurpleButton {
                objectName: "musicDnaImportButton"
                text: root.tr("music_dna_import")
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                enabled: settingsPanel.canTransferMusicDna
                onClicked: {
                    musicDnaImportDialog.profileRevision = djconnect.musicDnaContextRevision
                    musicDnaImportDialog.open()
                }
            }

            PurpleButton {
                text: djconnect.updateCheckRunning ? root.tr("checking_updates") : root.tr("check_updates")
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                enabled: !djconnect.updateCheckRunning
                onClicked: djconnect.checkForUpdates()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                DangerButton {
                    text: root.tr("reset_pairing")
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    onClicked: root.resetPairingConfirmOpen = true
                }
            }

            WarningButton {
                text: root.tr("reboot_device")
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                onClicked: root.rebootConfirmOpen = true
            }

            Item { Layout.fillHeight: true }
        }
    }
    }

    CompactPageLoader {
        id: queuePanelLoader
        objectName: "queuePanelLoader"
        visible: root.activeScreen === "queue"
        z: 16
        sourceComponent: MediaListPanel {
            objectName: "queuePanel"
            heading: root.tr("queue")
            emptyText: root.tr("empty_queue")
            searchLabel: root.tr("media_search_queue")
            refreshLabel: root.tr("refresh_queue")
            playCommand: "play_context_at"
            items: djconnect.queueItems
            loading: djconnect.queueLoading
            onRefreshRequested: djconnect.loadQueue(true)
        }
    }

    CompactPageLoader {
        id: playlistsPanelLoader
        objectName: "playlistsPanelLoader"
        visible: root.activeScreen === "playlists"
        z: 16
        sourceComponent: MediaListPanel {
            objectName: "playlistsPanel"
            heading: root.tr("playlists")
            emptyText: root.tr("empty_playlists")
            searchLabel: root.tr("media_search_playlists")
            refreshLabel: root.tr("refresh_playlists")
            iconName: "playlists"
            playCommand: "start_playlist"
            items: djconnect.playlistItems
            loading: djconnect.playlistsLoading
            onRefreshRequested: djconnect.loadPlaylists(true)
        }
    }

    Rectangle {
        id: morePanel
        anchors.fill: parent
        color: "transparent"
        visible: root.activeScreen === "more"
        z: 16

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.topMargin: 24
            anchors.rightMargin: 24
            anchors.bottomMargin: root.edge + 126
            spacing: 16

            PageToolbar { title: root.tr("more") }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                GridLayout {
                    width: parent.width
                    columns: root.width < 900 ? 1 : Math.max(2, Math.floor(width / 420))
                    columnSpacing: root.wallProfile ? 22 : 16
                    rowSpacing: columnSpacing
                    MoreMenuButton {
                        text: root.tr("ask_dj"); iconName: "chat"
                        onClicked: { root.activeScreen = "askdj"; djconnect.loadAskDjHistory() }
                    }
                    MoreMenuButton {
                        text: root.tr("session_history"); iconName: "session"
                        objectName: "openSessionArchive"
                        onClicked: sharedSessionScreen.openArchive()
                    }
                    MoreMenuButton {
                        text: root.tr("music_dna"); iconName: "heart"
                        onClicked: root.activeScreen = "musicdna"
                    }
                    MoreMenuButton {
                        text: root.tr("playlists"); iconName: "playlists"
                        onClicked: { root.activeScreen = "playlists"; djconnect.loadPlaylists() }
                    }
                    MoreMenuButton {
                        text: root.tr("games"); iconName: "gamepad"
                        onClicked: root.activeScreen = "games"
                    }
                    MoreMenuButton {
                        text: root.tr("setup"); iconName: "settings"
                        onClicked: root.activeScreen = "settings"
                    }
                    MoreMenuButton {
                        objectName: "openLogsMenuItem"
                        visible: djconnect.logLevel !== "OFF"
                        text: root.tr("logs"); iconName: "logs"
                        onClicked: djconnect.showLogs()
                    }
                    MoreMenuButton {
                        text: root.tr("about"); iconName: "info"
                        onClicked: root.aboutOpen = true
                    }
                    MoreMenuButton {
                        objectName: "openLegal"
                        text: root.tr("legal"); iconName: "document"
                        onClicked: root.activeScreen = "legal"
                    }
                    MoreMenuButton {
                        objectName: "openPrivacy"
                        text: root.tr("privacy"); iconName: "privacy"
                        onClicked: root.activeScreen = "privacy"
                    }
                    MoreMenuButton {
                        objectName: "openFeedback"
                        text: root.tr("feedback"); iconName: "chat"
                        onClicked: root.activeScreen = "feedback"
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: root.activeScreen === "legal" || root.activeScreen === "privacy" || root.activeScreen === "feedback"
        color: "transparent"
        z: 17
        ModalBlocker {}
        InformationPage {
            objectName: "informationPage"
            anchors.fill: parent
            anchors.bottomMargin: root.edge + 126
            privacy: root.activeScreen === "privacy"
            feedback: root.activeScreen === "feedback"
            backend: djconnect
            onBack: root.activeScreen = "more"
        }
    }

    CompactPageLoader {
        id: trackInsightLoader
        objectName: "trackInsightLoader"
        visible: root.activeScreen === "trackinsight"
        z: 17
        sourceComponent: TrackInsightPage {}
    }

    component TrackInsightPage: Rectangle {
        id: trackInsightPanel
        objectName: "trackInsightPanel"
        readonly property string visualMood: root.trackInsightVisualMood
        readonly property alias artworkTint: trackInsightArtworkFrame.artworkTint
        readonly property bool hasLiveSession: root.trackInsightHasLiveSession
        readonly property bool canRequestAnalysis: visible && root.active && !root.screenBlanked && !root.splashVisible
            && !djconnect.versionMismatchVisible && djconnect.paired && !djconnect.demoMode
            && djconnect.hasTrackTitle && !djconnect.askDjBusy
        readonly property bool canChooseVisualMood: visible && root.active && !root.screenBlanked && !hasLiveSession
        readonly property bool canChooseSessionMood: visible && root.active && !root.screenBlanked
            && !root.splashVisible && !djconnect.logsVisible && !djconnect.versionMismatchVisible
            && root.sessionHeader.runtime_state === "active" && !!root.sessionHeader.session_id
            && (djconnect.demoMode || (djconnect.paired && !root.sessionHeader.profile_confirmation_required
                && !root.sessionHeader.owner_read_only))
        onCanChooseVisualMoodChanged: if (!canChooseVisualMood) trackInsightMoodPopup.close()
        readonly property bool autoInsightSurfaceActive: visible && root.active && !root.screenBlanked && !root.splashVisible
            && (djconnect.paired || djconnect.demoMode)
        readonly property bool canAutoRequestAnalysis: autoInsightSurfaceActive && !djconnect.askDjBusy
            && (djconnect.demoMode || (canRequestAnalysis && djconnect.playing
                && djconnect.backendAvailable && djconnect.musicBackendAvailable))
        readonly property string autoInsightKey: djconnect.hasTrackTitle
            ? JSON.stringify([djconnect.activeProfileId, djconnect.demoMode ? "" : djconnect.language,
                djconnect.trackInsightPlaybackItemId || [djconnect.title, djconnect.artist],
                djconnect.demoMode ? null : (djconnect.outputDeviceId
                    ? ["id", djconnect.outputDeviceId] : ["label", djconnect.outputDevice])]) : ""
        property string requestedInsightKey: ""
        property int renewalDelay: 3000
        readonly property bool shouldRetryRenewal: canAutoRequestAnalysis && djconnect.trackInsightRenewalNeeded
            && requestedInsightKey === autoInsightKey
        color: "transparent"
        Component.onCompleted: Qt.callLater(requestInsightIfVisible)
        onCanAutoRequestAnalysisChanged: if (canAutoRequestAnalysis) Qt.callLater(requestInsightIfVisible)
        onAutoInsightKeyChanged: {
            renewalDelay = 3000
            Qt.callLater(requestInsightIfVisible)
        }
        onAutoInsightSurfaceActiveChanged: if (!autoInsightSurfaceActive) requestedInsightKey = ""
        onShouldRetryRenewalChanged: {
            if (shouldRetryRenewal) trackInsightRenewalRetry.restart()
            else trackInsightRenewalRetry.stop()
        }

        Timer {
            id: trackInsightRenewalRetry
            objectName: "trackInsightRenewalRetry"
            interval: trackInsightPanel.renewalDelay
            onTriggered: {
                if (!trackInsightPanel.shouldRetryRenewal) return
                trackInsightPanel.renewalDelay = Math.min(trackInsightPanel.renewalDelay * 2, 30000)
                djconnect.openTrackInsight()
            }
        }

        Connections {
            target: djconnect
            function onTrackInsightObservationChanged() {
                trackInsightPanel.requestedInsightKey = ""
                trackInsightPanel.renewalDelay = 3000
                Qt.callLater(trackInsightPanel.requestInsightIfVisible)
            }
        }

        function requestInsightIfVisible() {
            if (!canAutoRequestAnalysis || !autoInsightKey || autoInsightKey === requestedInsightKey) return
            requestedInsightKey = autoInsightKey
            djconnect.openTrackInsight()
        }

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.topMargin: 20
            anchors.rightMargin: 20
            anchors.bottomMargin: root.edge + 126
            spacing: 12

            PageToolbar {
                title: root.tr("track_insight")
                showLeading: !trackInsightPanel.hasLiveSession || trackInsightPanel.canChooseSessionMood
                leading: [
                    GlassButton {
                        id: trackInsightMoodButton
                        objectName: trackInsightPanel.hasLiveSession ? "trackInsightSessionMoodToggle" : "trackInsightVisualMoodToggle"
                        iconName: trackInsightPanel.hasLiveSession ? sharedSessionScreen.toolbarIcon("mood")
                            : "mood_" + trackInsightPanel.visualMood
                        tertiary: true
                        implicitWidth: 52
                        enabled: trackInsightPanel.canChooseVisualMood || trackInsightPanel.canChooseSessionMood
                        Accessible.name: trackInsightPanel.hasLiveSession ? sharedSessionScreen.toolbarChoiceLabel("mood")
                            : root.tr("shared_field_mood")
                        Accessible.description: trackInsightPanel.hasLiveSession ? ""
                            : root.tr("shared_mood_" + trackInsightPanel.visualMood)
                        onClicked: {
                            if (trackInsightPanel.canChooseSessionMood) {
                                sharedSessionScreen.openChoice("mood", trackInsightMoodButton, trackInsightPanel)
                                return
                            }
                            if (!trackInsightPanel.canChooseVisualMood)
                                return
                            const anchor = trackInsightMoodButton.mapToItem(trackInsightPanel, 0, trackInsightMoodButton.height)
                            trackInsightMoodPopup.x = Math.max(12, Math.min(anchor.x,
                                trackInsightPanel.width - trackInsightMoodPopup.width - 12))
                            trackInsightMoodPopup.y = anchor.y + 8
                            trackInsightMoodPopup.open()
                        }
                    }
                ]
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                RefreshIconButton {
                    objectName: "trackInsightRefresh"
                    text: root.tr("refresh_track_insight")
                    loading: djconnect.askDjBusy
                    enabled: trackInsightPanel.canRequestAnalysis
                    onClicked: djconnect.refreshTrackInsight()
                }
            }

            ScrollView {
                id: trackInsightScroll
                objectName: "trackInsightScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth
                PullToRefresh {
                    objectName: "trackInsightPullToRefresh"
                    flickable: trackInsightScroll.contentItem
                    canRefresh: trackInsightPanel.canRequestAnalysis
                        && (trackInsightSubject.visible || djconnect.trackInsightText.length > 0
                            || djconnect.trackInsightItems.length > 0 || djconnect.trackInsightSections.length > 0)
                    onRefreshRequested: djconnect.refreshTrackInsight()
                }

                ColumnLayout {
                    width: Math.max(0, trackInsightScroll.availableWidth)
                    spacing: root.wallProfile ? 18 : 10

                    GlassCard {
                        visible: djconnect.trackInsightError.length > 0
                        Layout.fillWidth: true
                        Text {
                            text: djconnect.trackInsightError
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.muted
                            font.pixelSize: 20
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        GlassButton {
                            objectName: "trackInsightRetry"
                            visible: djconnect.hasTrackTitle && djconnect.paired && !djconnect.demoMode
                            enabled: trackInsightPanel.canRequestAnalysis
                            text: root.tr("track_insight_retry_action")
                            iconName: "refresh"
                            Layout.alignment: Qt.AlignLeft
                            onClicked: djconnect.refreshTrackInsight()
                        }
                    }

                    IntroductionCard {
                        objectName: "trackInsightEmptyHero"
                        compact: root.width < 900
                        iconName: "trackInsight"
                        title: root.tr("track_insight")
                        message: root.tr("track_insight_empty")
                        visible: djconnect.trackInsightError.length === 0 && djconnect.trackInsightText.length === 0
                                 && djconnect.trackInsightItems.length === 0 && djconnect.trackInsightSections.length === 0
                                 && !trackInsightSubject.visible && !trackInsightVisualizer.visible && !djconnect.askDjBusy
                    }

                    GlassCard {
                        id: trackInsightSubject
                        objectName: "trackInsightSubjectHero"
                        readonly property bool hasInsightSubject: djconnect.trackInsightTitle.length > 0
                            || djconnect.trackInsightArtist.length > 0 || djconnect.trackInsightImageUrl.length > 0
                        readonly property bool hasPlaybackSubject: djconnect.hasTrackTitle && (djconnect.paired || djconnect.demoMode)
                        readonly property string subjectTitle: hasInsightSubject ? djconnect.trackInsightTitle : djconnect.title
                        readonly property string subjectArtist: hasInsightSubject ? djconnect.trackInsightArtist : djconnect.artist
                        readonly property string subjectAlbum: hasInsightSubject && djconnect.trackInsightAlbum.length > 0
                            ? djconnect.trackInsightAlbum
                            : (hasPlaybackSubject && (!hasInsightSubject || djconnect.trackInsightMatchesPlayback) ? djconnect.album : "")
                        readonly property string artworkUrl: hasInsightSubject ? djconnect.trackInsightImageUrl : djconnect.imageUrl
                        hero: true
                        artworkTint: trackInsightArtworkFrame.artworkTint
                        visible: hasInsightSubject || hasPlaybackSubject
                        Layout.fillWidth: true
                        padding: root.wallProfile ? 32 : 12
                        background: GlassSurface {
                            hero: true
                            artworkTint: trackInsightArtworkFrame.artworkTint
                            border.color: root.moodColor("focus")
                            radius: root.wallProfile ? 44 : 28
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.wallProfile ? 1 : 2
                            rowSpacing: 18
                            columnSpacing: 12

                            GlassArtworkFrame {
                                id: trackInsightArtworkFrame
                                objectName: "trackInsightArtworkFrame"
                                backend: djconnect; imageItem: trackInsightArtworkImage
                                active: !root.screenBlanked
                                leaseKey: [djconnect.activeProfileId, djconnect.trackInsightPlaybackItemId,
                                    trackInsightSubject.subjectTitle, trackInsightSubject.subjectArtist].join("|")
                                Layout.alignment: Qt.AlignHCenter
                                Layout.preferredWidth: root.wallProfile ? 228 : 76
                                Layout.preferredHeight: Layout.preferredWidth
                                radius: root.wallProfile ? 32 : 16
                                FeatureIcon {
                                    objectName: "trackInsightArtworkPlaceholder"
                                    anchors.centerIn: parent
                                    width: Math.min(parent.width, parent.height) * 0.56
                                    height: width
                                    iconName: "trackInsight"
                                    iconColor: BrandTheme.colors.text
                                    visible: trackInsightArtworkImage.status !== Image.Ready
                                    Accessible.ignored: true
                                }
                                Image {
                                    id: trackInsightArtworkImage
                                    objectName: "trackInsightArtworkImage"
                                    sourceSize: root.lowMemoryProfile ? Qt.size(192, 192) : Qt.size(0, 0)
                                    asynchronous: root.lowMemoryProfile
                                    source: trackInsightSubject.visible ? trackInsightSubject.artworkUrl : ""
                                    visible: status === Image.Ready
                                    anchors.fill: parent
                                    fillMode: Image.PreserveAspectCrop
                                    smooth: true
                                }

                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                Text {
                                    objectName: "trackInsightSubjectTitle"
                                    text: trackInsightSubject.subjectTitle
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.text
                                    font.pixelSize: root.wallProfile ? 32 : 22
                                    font.bold: true
                                    horizontalAlignment: root.wallProfile ? Text.AlignHCenter : Text.AlignLeft
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }

                                Text {
                                    objectName: "trackInsightSubjectMetadata"
                                    text: [trackInsightSubject.subjectArtist, trackInsightSubject.subjectAlbum]
                                        .filter(function(v) { return v && v.length > 0 }).join(" · ")
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: root.wallProfile ? 21 : 17
                                    textFormat: Text.PlainText
                                    horizontalAlignment: root.wallProfile ? Text.AlignHCenter : Text.AlignLeft
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }
                                GlassSurface {
                                    objectName: "trackInsightPlaybackTime"
                                    visible: trackInsightSubject.hasPlaybackSubject && djconnect.durationSeconds > 0
                                        && (!trackInsightSubject.hasInsightSubject || djconnect.trackInsightMatchesPlayback)
                                    Layout.alignment: root.wallProfile ? Qt.AlignHCenter : Qt.AlignLeft
                                    implicitWidth: insightTime.implicitWidth + 20
                                    implicitHeight: insightTime.implicitHeight + 10
                                    radius: height / 2
                                    Text {
                                        id: insightTime
                                        objectName: "trackInsightPlaybackTimeLabel"
                                        anchors.centerIn: parent
                                        text: djconnect.progressLabel.replace("/", " / ")
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: root.wallProfile ? 18 : 15
                                        font.bold: true
                                    }
                                }
                            }
                        }
                    }

                    GlassSurface {
                        border.color: root.moodColor("focus")
                        id: trackInsightVisualizer
                        objectName: "trackInsightVisualizer"
                        visible: djconnect.trackInsightVisualizer.available === true
                        Layout.fillWidth: true
                        Layout.preferredHeight: 164
                        radius: root.standardButtonRadius
                        clip: true

                        property var visualizerData: djconnect.trackInsightVisualizer
                        property real phase: 0

                        onVisibleChanged: trackInsightVisualizerCanvas.requestPaint()
                        onVisualizerDataChanged: {
                            phase = 0
                            trackInsightVisualizerCanvas.requestPaint()
                        }

                        Timer {
                            interval: 180
                            repeat: true
                            running: root.wallProfile && trackInsightVisualizer.visible && root.activeScreen === "trackinsight"
                                     && djconnect.playing && root.active && !root.screenBlanked && !GlassStyle.reduceMotion
                            onTriggered: {
                                trackInsightVisualizer.phase = (trackInsightVisualizer.phase + 0.08) % 6.28
                                trackInsightVisualizerCanvas.requestPaint()
                            }
                        }

                        Canvas {
                            id: trackInsightVisualizerCanvas
                            anchors.fill: parent
                            antialiasing: true
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                var data = trackInsightVisualizer.visualizerData || {}
                                var bars = data.bars || []
                                var colors = data.colors || []
                                var c0 = colors.length > 0 ? colors[0] : root.moodColor("gradientStart")
                                var c1 = colors.length > 1 ? colors[1] : root.moodColor("gradientMid")
                                var c2 = colors.length > 2 ? colors[2] : root.moodColor("gradientEnd")
                                var bg = ctx.createLinearGradient(0, 0, width, height)
                                bg.addColorStop(0.0, c0)
                                bg.addColorStop(0.58, c1)
                                bg.addColorStop(1.0, c2)
                                ctx.globalAlpha = 0.28
                                ctx.fillStyle = bg
                                ctx.fillRect(0, 0, width, height)
                                ctx.globalAlpha = 1

                                ctx.fillStyle = "rgba(5, 8, 18, 0.34)"
                                ctx.fillRect(0, 0, width, height)

                                var orbGradient = ctx.createRadialGradient(width * 0.55, height * 0.45, 4, width * 0.55, height * 0.45, Math.max(width, height) * 0.56)
                                orbGradient.addColorStop(0, c1)
                                orbGradient.addColorStop(0.42, "rgba(255,255,255,0.16)")
                                orbGradient.addColorStop(1, "rgba(0,0,0,0)")
                                ctx.globalAlpha = 0.42
                                ctx.fillStyle = orbGradient
                                ctx.fillRect(0, 0, width, height)
                                ctx.globalAlpha = 1

                                ctx.strokeStyle = "rgba(255,255,255,0.16)"
                                ctx.lineWidth = 1
                                for (var line = 0; line < 5; line++) {
                                    var y = height * (0.22 + line * 0.13)
                                    ctx.beginPath()
                                    ctx.moveTo(width * 0.08, y)
                                    ctx.bezierCurveTo(width * 0.28, y - 20, width * 0.52, y + 20, width * 0.92, y - 8)
                                    ctx.stroke()
                                }

                                if (bars.length === 0) {
                                    return
                                }
                                var baseY = height - 18
                                var topY = 28
                                var availableHeight = Math.max(28, baseY - topY)
                                var count = Math.min(48, bars.length)
                                var gap = Math.max(3, width * 0.008)
                                var barWidth = Math.max(4, (width - 34 - gap * (count - 1)) / count)
                                var startX = (width - (barWidth * count + gap * (count - 1))) / 2
                                var barGradient = ctx.createLinearGradient(0, topY, 0, baseY)
                                barGradient.addColorStop(0.0, c2)
                                barGradient.addColorStop(0.52, c1)
                                barGradient.addColorStop(1.0, c0)
                                ctx.fillStyle = barGradient
                                for (var i = 0; i < count; i++) {
                                    var value = Math.max(0.08, Math.min(1, Number(bars[i]) || 0.1))
                                    var pulse = 0.88 + Math.sin(trackInsightVisualizer.phase + i * 0.45) * 0.08
                                    var h = Math.max(10, availableHeight * value * pulse)
                                    var x = startX + i * (barWidth + gap)
                                    var yBar = baseY - h
                                    ctx.fillRect(x, yBar, barWidth, h)
                                }
                                ctx.fillStyle = c2
                                ctx.shadowColor = c2
                                ctx.shadowBlur = 16
                                ctx.beginPath()
                                ctx.arc(width * 0.5, baseY, 8, 0, Math.PI * 2)
                                ctx.fill()
                                ctx.shadowBlur = 0
                            }
                        }
                    }

                    GlassCard {
                        id: verifiedCreditsCard
                        objectName: "trackInsightVerifiedCredits"
                        readonly property var credit: djconnect.trackInsightVerifiedCredit
                        visible: !!credit.context
                        Layout.fillWidth: true
                        Text {
                            text: root.tr("track_insight_verified_credits")
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.bold: true
                            font.pixelSize: 21
                            Layout.fillWidth: true
                            Accessible.role: Accessible.Heading
                        }
                        Text {
                            objectName: "trackInsightVerifiedContext"
                            text: verifiedCreditsCard.credit.context || ""
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.pixelSize: 21
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        SessionAttribution {
                            sources: verifiedCreditsCard.credit.url ? [{
                                provider: verifiedCreditsCard.credit.provider,
                                links: [verifiedCreditsCard.credit.url],
                                license: verifiedCreditsCard.credit.license
                            }] : []
                            Layout.fillWidth: true
                        }
                        Text {
                            visible: djconnect.trackInsightAudioNotice
                                && djconnect.trackInsightText === djconnect.trackInsightVerifiedCredit.context
                            text: root.tr("track_insight_unmeasured_audio")
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.muted
                            font.pixelSize: 17
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    GlassCard {
                        id: trackAnalysisCard
                        objectName: "trackInsightAnalysisCard"
                        readonly property bool accompaniesCredit: !!verifiedCreditsCard.credit.context
                        visible: djconnect.trackInsightText.length > 0
                            && djconnect.trackInsightText !== djconnect.trackInsightVerifiedCredit.context
                        Layout.fillWidth: true

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            FeatureIcon {
                                iconName: trackAnalysisCard.accompaniesCredit ? "chat" : "trackInsight"
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                Accessible.ignored: true
                            }
                            Text {
                                objectName: "trackInsightAnalysisHeading"
                                text: root.tr(trackAnalysisCard.accompaniesCredit
                                    ? "track_insight_interpretation" : "track_insight_analysis_heading")
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.text
                                font.pixelSize: 21
                                font.bold: true
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                                Accessible.role: Accessible.Heading
                            }
                        }
                        Text {
                            objectName: "trackInsightAnalysisText"
                            text: djconnect.trackInsightText
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.pixelSize: 21
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Text {
                            objectName: "trackInsightUnmeasuredNotice"
                            visible: djconnect.trackInsightAudioNotice
                            text: root.tr("track_insight_unmeasured_audio")
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.muted
                            font.pixelSize: 17
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    GridLayout {
                        objectName: "trackInsightMetricsGrid"
                        visible: djconnect.trackInsightItems.length > 0
                        columns: Math.max(1, Math.min(3, djconnect.trackInsightItems.length,
                            Math.floor((trackInsightScroll.availableWidth + columnSpacing) / (172 + columnSpacing))))
                        rowSpacing: 8
                        columnSpacing: 8
                        Layout.fillWidth: true

                        Repeater {
                            model: djconnect.trackInsightItems

                            GlassSurface {
                                required property var modelData
                                required property int index
                                objectName: "trackInsightMetric-" + index
                                border.color: root.moodColor("focus")
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.minimumWidth: 0
                                Layout.preferredHeight: Math.max(64, trackMetricContent.implicitHeight + 16)
                                Layout.alignment: Qt.AlignTop
                                radius: root.standardButtonRadius

                                ColumnLayout {
                                    id: trackMetricContent
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 1

                                    Text {
                                        text: root.trackInsightLabel(modelData.title || modelData.kind || "")
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        color: BrandTheme.colors.text
                                        font.pixelSize: 15
                                        font.bold: true
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                    }

                                    Text {
                                        text: modelData.value || ""
                                        visible: text.length > 0
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 19
                                        font.bold: true
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                    }
                                }
                            }
                        }
                    }

                    Repeater {
                        model: djconnect.trackInsightSections

                        GlassSurface {
                            required property var modelData
                            required property int index
                            objectName: "trackInsightSection-" + index
                            border.color: root.moodColor("focus")
                            Layout.fillWidth: true
                            Layout.preferredHeight: trackSectionContent.implicitHeight + 18
                            radius: root.standardButtonRadius

                            ColumnLayout {
                                id: trackSectionContent
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 5

                                Text {
                                    text: root.trackInsightLabel(modelData.title || modelData.kind || modelData.id || "")
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: 18
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    visible: modelData.body && modelData.body.length > 0
                                    text: modelData.body || ""
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.text
                                    font.pixelSize: 17
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                Repeater {
                                    model: modelData.details || []
                                    Text {
                                        text: "- " + modelData
                                        textFormat: Text.PlainText
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 16
                                        wrapMode: Text.WordWrap
                                        Layout.fillWidth: true
                                    }
                                }
                                Text {
                                    objectName: "trackInsightSectionSource"
                                    readonly property string sourceLabel: modelData.source || ""
                                    visible: sourceLabel.length > 0
                                    text: root.tr("sources") + " · " + sourceLabel
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: 15
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
            }
        }
        GlassPopup {
            id: trackInsightMoodPopup
            objectName: "trackInsightVisualMoodPopup"
            width: Math.min(trackInsightPanel.width - 24, 440)
            padding: 18
            modal: true
            dim: false
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            background: GlassSurface { popover: true; elevated: true }

            contentItem: ColumnLayout {
                spacing: 12
                Text {
                    text: root.tr("shared_field_mood")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    font.bold: true
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 8
                    rowSpacing: 8
                    Repeater {
                        model: ["chill", "groove", "energy", "party"]
                        GlassButton {
                            required property string modelData
                            objectName: "trackInsightVisualMood-" + modelData
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            iconName: "mood_" + modelData
                            text: root.tr("shared_mood_" + modelData)
                            checked: trackInsightPanel.visualMood === modelData
                            enabled: trackInsightPanel.canChooseVisualMood
                            onClicked: {
                                if (!trackInsightPanel.canChooseVisualMood)
                                    return
                                root.trackInsightVisualMood = modelData
                                trackInsightMoodPopup.close()
                            }
                        }
                    }
                }
            }
        }
    }

    component DiscoveryArtwork: GlassArtworkFrame {
        id: artwork
        required property var recommendation
        backend: djconnect
        imageItem: cover
        active: visible && (root.activeScreen === "discover" || root.activeScreen === "musicdna") && !root.screenBlanked
        leaseKey: djconnect.activeProfileId + "|" + (recommendation.payload || "")
        property string displayIcon: djconnect.demoMode ? "music" : "discover"
        property bool playbackPending: false
        readonly property real expiresAt: recommendation.imageExpiresAt || 0
        property bool expired: false
        function checkImageExpiry() {
            expiryTimer.stop()
            expired = expiresAt > 0 && expiresAt <= Date.now()
            if (active && expiresAt > 0 && !expired) {
                expiryTimer.interval = Math.max(1, Math.ceil(expiresAt - Date.now()))
                expiryTimer.start()
            }
        }
        onExpiresAtChanged: checkImageExpiry()
        onActiveChanged: checkImageExpiry()
        Component.onCompleted: checkImageExpiry()
        Timer { id: expiryTimer; onTriggered: artwork.checkImageExpiry() }
        radius: 20
        clip: true

        FeatureIcon {
            objectName: "discoveryArtworkFallback"
            anchors.centerIn: parent
            width: Math.min(42, parent.width * 0.7); height: width
            iconName: artwork.displayIcon
            visible: cover.status === Image.Null || cover.status === Image.Error
        }
        Image {
            id: cover
            objectName: "discoveryArtworkImage"
            anchors.fill: parent
            source: artwork.active && !artwork.expired ? artwork.recommendation.imageUrl || "" : ""
            cache: false
            sourceSize: root.lowMemoryProfile
                ? Qt.size(Math.min(224, Math.ceil(width)), Math.min(224, Math.ceil(height)))
                : Qt.size(Math.ceil(width), Math.ceil(height))
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            visible: status === Image.Ready
        }
        Image {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 4
            width: Math.min(56, parent.width * 0.8); height: width * 0.54
            fillMode: Image.PreserveAspectFit
            source: "spotify-attribution.png"
            visible: cover.visible && !!artwork.recommendation.imageSourceUrl
            Accessible.name: "Spotify"
            TapHandler { onTapped: Qt.openUrlExternally(artwork.recommendation.imageSourceUrl) }
        }
        Rectangle {
            anchors.fill: parent
            radius: Math.max(0, artwork.radius - artwork.contentInset)
            color: "#5c000000"
            visible: artwork.active && artwork.playbackPending
        }
        LoadingSpinner {
            objectName: "discoveryArtworkSpinner"
            anchors.centerIn: parent
            visible: artwork.active && (artwork.playbackPending || cover.status === Image.Loading)
            running: visible
        }
    }

    component DiscoveryFeedbackOption: MenuItem {
        implicitHeight: Math.max(48, implicitBackgroundHeight + topInset + bottomInset,
                                 implicitContentHeight + topPadding + bottomPadding)
    }

    component DiscoveryChipStrip: Flickable {
        id: strip
        property var values: []
        property string accessibleLabel: ""
        visible: values.length > 0
        activeFocusOnTab: visible && contentWidth > width
        Accessible.role: Accessible.Pane
        Accessible.name: accessibleLabel
        implicitHeight: chips.implicitHeight + 8
        contentWidth: chips.implicitWidth
        contentHeight: height
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Keys.onLeftPressed: strip.scrollLabels(-1)
        Keys.onRightPressed: strip.scrollLabels(1)
        ScrollBar.horizontal: ScrollBar {
            policy: strip.activeFocus ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

        function scrollLabels(direction) {
            cancelFlick()
            contentX = Math.max(originX, Math.min(contentX + direction * width / 2,
                                                 originX + Math.max(0, contentWidth - width)))
        }
        function resetLabelScroll() {
            cancelFlick()
            contentX = originX
        }

        RowLayout {
            id: chips
            y: 4
            width: implicitWidth
            height: implicitHeight
            spacing: 6
            Repeater {
                model: strip.values
                delegate: GlassSurface {
                    required property string modelData
                    radius: 14
                    implicitWidth: chipText.implicitWidth + 16
                    implicitHeight: chipText.implicitHeight + 10
                    Text {
                        id: chipText
                        anchors.centerIn: parent
                        text: parent.modelData
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.muted
                        font.pixelSize: 15
                    }
                }
            }
        }
    }

    function closeDiscoveryDetail() {
        if (musicDiscoveryLoader.item) musicDiscoveryLoader.item.closeDetail()
    }

    CompactPageLoader {
        id: musicDiscoveryLoader
        objectName: "musicDiscoveryLoader"
        visible: root.activeScreen === "discover"
        z: 17
        sourceComponent: Component { MusicDiscoveryPage {} }
    }

    component MusicDiscoveryPage: Rectangle {
        id: musicDiscoveryPanel
        objectName: "musicDiscoveryPanel"
        readonly property alias detailVisible: discoveryDetail.visible
        function closeDetail() { discoveryDetail.close() }
        color: "transparent"

        readonly property bool requiresMusicDna: djconnect.musicDnaLoaded ? !djconnect.musicDnaEnabled
            : djconnect.musicDiscoveryDnaDisabled
        readonly property bool showEmptyFeed: sections.length === 0 && !djconnect.musicDiscoveryBusy
        readonly property int profileRevision: djconnect.musicDnaContextRevision
        property var shelfReading: new Map()
        property var feedReading: null
        onProfileRevisionChanged: {
            closeDetail()
            renewedArtworkThrough = 0
            clearDiscoveryReading()
        }
        onRequiresMusicDnaChanged: if (requiresMusicDna) clearDiscoveryReading()
        Connections {
            target: djconnect
            function onDemoModeChanged() { musicDiscoveryPanel.clearDiscoveryReading() }
        }
        readonly property bool canRenewArtwork: visible && root.active && !root.screenBlanked
            && !root.splashVisible && djconnect.paired && !djconnect.demoMode
            && !djconnect.versionMismatchVisible && !djconnect.musicDiscoveryBusy
            && !requiresMusicDna
        property real renewedArtworkThrough: 0
        readonly property var visibleRecommendations: requiresMusicDna ? [] : djconnect.musicDiscoveryItems.filter(function(item) {
            return item.sectionId && item.sectionTitle && item.discoveryItemId && item.title
        })
        readonly property real nextArtworkExpiry: {
            let deadline = 0
            for (const item of visibleRecommendations) {
                if (item.imageUrl && item.imageExpiresAt > renewedArtworkThrough
                        && (deadline === 0 || item.imageExpiresAt < deadline))
                    deadline = item.imageExpiresAt
            }
            return deadline
        }
        onCanRenewArtworkChanged: Qt.callLater(scheduleArtworkRenewal)
        onNextArtworkExpiryChanged: Qt.callLater(scheduleArtworkRenewal)
        Component.onCompleted: Qt.callLater(scheduleArtworkRenewal)

        function scheduleArtworkRenewal() {
            artworkRenewalTimer.stop()
            if (!canRenewArtwork || nextArtworkExpiry === 0) return
            artworkRenewalTimer.interval = Math.max(1000, Math.ceil(nextArtworkExpiry - Date.now() + 200))
            artworkRenewalTimer.start()
        }

        Timer {
            id: artworkRenewalTimer
            onTriggered: {
                if (!musicDiscoveryPanel.canRenewArtwork || musicDiscoveryPanel.nextArtworkExpiry === 0) return
                const now = Date.now()
                if (now < musicDiscoveryPanel.nextArtworkExpiry) {
                    musicDiscoveryPanel.scheduleArtworkRenewal()
                    return
                }
                // A failed read can leave the same leases in the feed.
                musicDiscoveryPanel.renewedArtworkThrough = now
                djconnect.loadMusicDiscovery()
            }
        }

        ProfileReadOnOpen {
            surfaceVisible: musicDiscoveryPanel.visible && !(djconnect.musicDnaLoaded && !djconnect.musicDnaEnabled)
            busy: djconnect.musicDiscoveryBusy
            loaded: djconnect.musicDiscoveryLoaded
            onRequested: djconnect.loadMusicDiscovery()
        }

        readonly property var sections: {
            const groups = []
            for (const item of visibleRecommendations) {
                const last = groups[groups.length - 1]
                if (last && last.id === item.sectionId)
                    last.items.push(item)
                else
                    groups.push({id: item.sectionId, title: item.sectionTitle, items: [item]})
            }
            return groups
        }
        onSectionsChanged: {
            const visibleIds = new Set(sections.map(section => section.id))
            for (const id of shelfReading.keys())
                if (!visibleIds.has(id)) shelfReading.delete(id)
            if (feedReading && feedReadingRestore) feedReadingRestore.restart()
        }

        function clearDiscoveryReading() {
            shelfReading.clear()
            feedReading = null
            if (feedReadingRestore) feedReadingRestore.stop()
            if (discoveryFlick) discoveryFlick.contentY = discoveryFlick.originY
        }

        function rememberFeedReading() {
            if (!visible || !discoveryShelves || !feedReadingRestore || feedReadingRestore.running) return
            const y = discoveryFlick.contentY
            feedReading = null
            if (y <= discoveryFlick.originY + 1) return
            for (let index = 0; index < discoveryShelves.count; ++index) {
                const row = discoveryShelves.itemAt(index)
                if (row && row.y + row.height > y && row.y < y + discoveryFlick.height) {
                    feedReading = {id: row.modelData.id, offset: y - row.y, index: index}
                    return
                }
            }
        }

        Timer {
            id: feedReadingRestore
            interval: 32
            onTriggered: {
                const anchor = musicDiscoveryPanel.feedReading
                if (!anchor || !musicDiscoveryPanel.visible || discoveryFlick.moving || !discoveryShelves.count) return
                discoveryContent.ensurePolished()
                const found = musicDiscoveryPanel.sections.findIndex(section => section.id === anchor.id)
                const index = found >= 0 ? found : Math.min(anchor.index, discoveryShelves.count - 1)
                const row = discoveryShelves.itemAt(index)
                const origin = discoveryFlick.originY
                if (row) discoveryFlick.contentY = Math.max(origin,
                    Math.min(row.y + anchor.offset, origin + Math.max(0, discoveryFlick.contentHeight - discoveryFlick.height)))
            }
        }

        function rememberShelfReading(view, sectionId, userMovement) {
            const x = view.contentX
            if (x <= view.originX + 1) {
                if (userMovement) shelfReading.delete(sectionId)
                return
            }
            let index = view.indexAt(x + 1, 1)
            if (index < 0) index = view.indexAt(x + view.spacing + 1, 1)
            const card = view.itemAtIndex(index)
            if (card) shelfReading.set(sectionId, {id: card.modelData.discoveryItemId, offset: x - card.x, index: index})
        }

        function restoreShelfReading(view, sectionId) {
            const anchor = shelfReading.get(sectionId)
            if (!anchor || !visible || !view || view.moving || !view.count) return
            const found = view.model.findIndex(candidate => candidate.discoveryItemId === anchor.id)
            const index = found >= 0 ? found : Math.min(anchor.index, view.count - 1)
            view.positionViewAtIndex(index, ListView.Beginning)
            view.forceLayout()
            const card = view.itemAtIndex(index)
            if (card) view.contentX = Math.max(view.originX,
                Math.min(card.x + anchor.offset, view.originX + Math.max(0, view.contentWidth - view.width)))
        }

        function kindLabel(kind) {
            switch (kind) {
            case "track": return root.tr("music_discovery_kind_track")
            case "album": return root.tr("music_discovery_kind_album")
            case "artist": return root.tr("music_discovery_kind_artist")
            case "playlist": return root.tr("music_discovery_kind_playlist")
            default: return ""
            }
        }

        onVisibleChanged: if (!visible) discoveryDetail.close()

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.topMargin: root.wallProfile ? 32 : 10
            anchors.rightMargin: 20
            anchors.bottomMargin: root.edge + 126
            spacing: root.wallProfile ? 22 : 10

            PageToolbar {
                title: root.tr("music_discovery")
                Layout.preferredHeight: compact ? implicitHeight : 140
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                RefreshIconButton {
                    id: discoveryRefreshButton
                    objectName: "discoveryRefreshButton"
                    text: root.tr("refresh_discovery")
                    loading: djconnect.musicDiscoveryBusy || (musicDiscoveryPanel.requiresMusicDna && djconnect.musicDnaBusy)
                    enabled: !loading
                    onClicked: djconnect.refreshMusicDiscovery()
                }
            }

            ScrollView {
                id: discoveryScroll
                objectName: "discoveryFeed"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                Flickable {
                    id: discoveryFlick
                    contentWidth: width
                    contentHeight: discoveryContent.implicitHeight
                    boundsBehavior: Flickable.DragOverBounds
                    flickableDirection: Flickable.VerticalFlick
                    onContentYChanged: musicDiscoveryPanel.rememberFeedReading()

                    PullToRefresh {
                        objectName: "discoveryPullToRefresh"
                        flickable: discoveryFlick
                        canRefresh: musicDiscoveryPanel.visible && root.active && !root.screenBlanked
                            && !musicDiscoveryPanel.detailVisible && djconnect.paired && !djconnect.demoMode
                            && !musicDiscoveryPanel.requiresMusicDna
                            && !djconnect.musicDiscoveryBusy && musicDiscoveryPanel.sections.length > 0
                        onRefreshRequested: djconnect.refreshMusicDiscovery()
                    }

                    ColumnLayout {
                        id: discoveryContent
                        width: discoveryFlick.width
                        spacing: root.wallProfile ? 20 : 12

                        IntroductionCard {
                            objectName: "discoveryIntroduction"
                            compact: root.width < 900
                            iconName: "discover"
                            title: root.tr("music_discovery")
                            message: root.tr("music_discovery_introduction")
                        }

                        GlassSurface {
                            objectName: "discoveryMusicDnaGate"
                            hero: true
                            visible: musicDiscoveryPanel.requiresMusicDna && djconnect.musicDiscoveryError.length === 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: discoveryMusicDnaDisabledColumn.implicitHeight + 28
                            radius: root.standardButtonRadius

                            ColumnLayout {
                                id: discoveryMusicDnaDisabledColumn
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 10

                                Text {
                                    text: root.tr("music_dna_disabled")
                                    color: BrandTheme.colors.text
                                    font.pixelSize: 21
                                    font.bold: true
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: root.tr("music_discovery_requires_music_dna")
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: 19
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                PurpleButton {
                                    text: root.tr("music_discovery_enable")
                                    primary: true
                                    enabled: !djconnect.musicDnaBusy && !djconnect.musicDiscoveryBusy
                                    font.pixelSize: 19
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 52
                                    onClicked: root.openMusicDnaPrompt(true)
                                }
                            }
                        }

                        Text {
                            objectName: "discoveryEmptyFeedText"
                            visible: !djconnect.musicDiscoveryBusy && (djconnect.musicDiscoveryError.length > 0
                                || (musicDiscoveryPanel.showEmptyFeed && !musicDiscoveryPanel.requiresMusicDna))
                            textFormat: Text.PlainText
                            text: {
                                if (djconnect.musicDiscoveryError.length > 0) return djconnect.musicDiscoveryError
                                if (!djconnect.musicDiscoveryDnaDisabled && djconnect.musicDiscoveryEmptyText.length > 0)
                                    return djconnect.musicDiscoveryEmptyText
                                return root.tr("music_discovery_empty")
                            }
                            color: BrandTheme.colors.muted
                            font.pixelSize: 20
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            Layout.topMargin: root.wallProfile ? 12 : 4
                            Layout.fillWidth: true
                        }

                        Repeater {
                            id: discoveryShelves
                            model: musicDiscoveryPanel.sections

                            delegate: GlassSurface {
                                id: shelf
                                required property var modelData
                                objectName: "discoveryShelf-" + shelf.modelData.id
                                Layout.fillWidth: true
                                implicitHeight: shelfContent.implicitHeight + 2 * inset
                                radius: root.standardButtonRadius
                                property int inset: root.wallProfile ? 20 : 12

                                ColumnLayout {
                                    id: shelfContent
                                    anchors.fill: parent
                                    anchors.margins: shelf.inset
                                    spacing: 10

                                    Text {
                                        visible: text.length > 0
                                        text: shelf.modelData.title
                                        textFormat: Text.PlainText
                                        color: BrandTheme.colors.text
                                        font.pixelSize: root.wallProfile ? 24 : 20
                                        font.bold: true
                                        wrapMode: Text.WordWrap
                                        Layout.fillWidth: true
                                    }

                                    ListView {
                                        id: shelfItems
                                        objectName: "discoveryShelfItems"
                                        readonly property int tileWidth: root.wallProfile ? 192 : 142
                                        readonly property int textSpacing: 4
                                        property bool readingReady: false
                                        function scheduleReadingRestore() {
                                            readingReady = false
                                            if (shelfReadingRestore) shelfReadingRestore.restart()
                                        }
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: tileWidth
                                            + 2 * Math.ceil(Math.max(titleMetrics.height, titleMetrics.lineSpacing))
                                            + 3 * Math.ceil(Math.max(captionMetrics.height, captionMetrics.lineSpacing))
                                            + Math.ceil(kindMetrics.height) + 4 * textSpacing
                                        orientation: ListView.Horizontal
                                        boundsBehavior: Flickable.StopAtBounds
                                        clip: true
                                        spacing: 12
                                        model: shelf.modelData.items
                                        Component.onCompleted: scheduleReadingRestore()
                                        onModelChanged: scheduleReadingRestore()
                                        onContentXChanged: if (readingReady)
                                            musicDiscoveryPanel.rememberShelfReading(shelfItems, shelf.modelData.id,
                                                                                    moving || shelfScrollBar.pressed)
                                        ScrollBar.horizontal: ScrollBar { id: shelfScrollBar }
                                        Timer {
                                            id: shelfReadingRestore
                                            interval: 32
                                            onTriggered: {
                                                musicDiscoveryPanel.restoreShelfReading(shelfItems, shelf.modelData.id)
                                                shelfItems.readingReady = true
                                            }
                                        }
                                        FontMetrics { id: titleMetrics; font.pixelSize: 18; font.bold: true }
                                        FontMetrics { id: captionMetrics; font.pixelSize: 15 }
                                        FontMetrics {
                                            id: kindMetrics
                                            font.pixelSize: 14
                                            font.bold: true
                                            font.capitalization: Font.AllUppercase
                                        }

                                        delegate: Button {
                                            id: recommendationCard
                                            required property var modelData
                                            objectName: "discoveryRecommendation"
                                            width: shelfItems.tileWidth
                                            height: Math.min(shelfItems.height, recommendationContent.implicitHeight)
                                            padding: 0
                                            Accessible.name: modelData.title
                                            onClicked: discoveryDetail.showRecommendation(modelData)
                                            background: Item {}

                                            contentItem: ColumnLayout {
                                                id: recommendationContent
                                                spacing: shelfItems.textSpacing
                                                DiscoveryArtwork {
                                                    objectName: "discoveryCardArtwork"
                                                    recommendation: recommendationCard.modelData
                                                    Layout.fillWidth: true
                                                    Layout.preferredHeight: shelfItems.tileWidth
                                                    playbackPending: djconnect.musicDiscoveryPlayingId.length > 0
                                                        && djconnect.musicDiscoveryPlayingId === recommendationCard.modelData.discoveryItemId
                                                        && djconnect.musicDiscoveryPlayingSectionId === recommendationCard.modelData.sectionId
                                                }
                                                Text {
                                                    text: recommendationCard.modelData.title
                                                    textFormat: Text.PlainText
                                                    color: BrandTheme.colors.text
                                                    font: titleMetrics.font
                                                    maximumLineCount: 2
                                                    wrapMode: Text.Wrap
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    text: recommendationCard.modelData.subtitle
                                                    visible: text.length > 0 && !(recommendationCard.modelData.kind === "artist"
                                                        && text.trim().toLowerCase() === "artist")
                                                    textFormat: Text.PlainText
                                                    color: BrandTheme.colors.muted
                                                    font: captionMetrics.font
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    text: recommendationCard.modelData.reason
                                                    visible: text.length > 0
                                                    textFormat: Text.PlainText
                                                    color: BrandTheme.colors.muted
                                                    font: captionMetrics.font
                                                    maximumLineCount: 2
                                                    wrapMode: Text.Wrap
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    objectName: "discoveryCardKind"
                                                    text: musicDiscoveryPanel.kindLabel(recommendationCard.modelData.kind)
                                                    visible: text.length > 0 && recommendationCard.modelData.kind !== "artist"
                                                        && !(recommendationCard.modelData.sectionId === "rediscover"
                                                            && recommendationCard.modelData.kind === "track")
                                                    color: BrandTheme.colors.text
                                                    font: kindMetrics.font
                                                    Layout.fillWidth: true
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        GlassPopup {
            id: discoveryDetail
            objectName: "discoveryDetail"
            property var selected: null
            readonly property var item: selected ? musicDiscoveryPanel.visibleRecommendations.find(function(candidate) {
                return candidate.sectionId === selected.sectionId && candidate.discoveryItemId === selected.discoveryItemId
            }) : null
            readonly property bool actionBusy: djconnect.musicDiscoveryBusy || djconnect.musicDiscoveryPlayingId.length > 0
            readonly property var qualityChips: {
                if (!item) return []
                const chips = []
                const score = item.qualityScore
                if (typeof score === "number" && Number.isInteger(score) && score >= 0 && score <= 100)
                    chips.push(score + "/100")
                return chips.concat(labelsForCodes(item.qualityFactors, "music_discovery_quality_"))
            }
            readonly property var sourceChips: item ? labelsForCodes(item.reasonSources, "music_discovery_source_") : []

            function labelsForCodes(codes, prefix) {
                const labels = []
                for (const code of Array.from(codes || [])) {
                    if (typeof code !== "string" || !/^[a-z_]+$/.test(code)) continue
                    const key = prefix + code
                    const label = root.tr(key)
                    if (label !== key) labels.push(label)
                }
                return labels
            }
            anchors.centerIn: Overlay.overlay
            width: Math.min(root.width - 40, 520)
            height: Math.min(root.height - 56, detailContent.implicitHeight + 32)
            modal: true
            focus: true
            padding: 16
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            background: GlassSurface { popover: true; elevated: true }
            onItemChanged: if (!item) Qt.callLater(closeUnavailableRecommendation)
            onOpened: {
                recommendationQualityStrip.resetLabelScroll()
                recommendationSourceStrip.resetLabelScroll()
                closeRecommendationButton.forceActiveFocus(Qt.PopupFocusReason)
            }
            onClosed: selected = null

            function closeUnavailableRecommendation() {
                if (!item) close()
            }

            function showRecommendation(recommendation) {
                selected = {sectionId: recommendation.sectionId, discoveryItemId: recommendation.discoveryItemId}
                if (item) open()
            }

            function revealFocusedControl(control) {
                if (!control.activeFocus) return
                const flick = detailScroll.contentItem
                const top = control.mapToItem(flick.contentItem, 0, 0).y
                const bottom = top + control.height
                if (top < flick.contentY)
                    flick.contentY = Math.max(flick.originY, top)
                else if (bottom > flick.contentY + flick.height)
                    flick.contentY = Math.min(bottom - flick.height,
                                              flick.originY + Math.max(0, flick.contentHeight - flick.height))
            }

            Connections {
                target: djconnect
                function onDemoModeChanged() { discoveryDetail.close() }
                function onMusicDiscoveryPlayed(sectionId, itemId) {
                    const item = discoveryDetail.item
                    if (item && item.sectionId === sectionId && item.discoveryItemId === itemId)
                        discoveryDetail.close()
                }
            }

            contentItem: ScrollView {
                id: detailScroll
                objectName: "discoveryDetailScroll"
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    id: detailContent
                    width: detailScroll.availableWidth
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        DiscoveryArtwork {
                            objectName: "discoveryDetailArtwork"
                            recommendation: discoveryDetail.item || ({})
                            Layout.preferredWidth: 96
                            Layout.preferredHeight: 96
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text {
                                objectName: "discoveryDetailTitle"
                                text: discoveryDetail.item ? discoveryDetail.item.title : ""
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.text
                                font.pixelSize: 24
                                font.bold: true
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Text {
                                text: discoveryDetail.item ? discoveryDetail.item.subtitle : ""
                                visible: text.length > 0
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.muted
                                font.pixelSize: 17
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Text {
                                text: discoveryDetail.item ? musicDiscoveryPanel.kindLabel(discoveryDetail.item.kind) : ""
                                visible: text.length > 0
                                color: BrandTheme.colors.muted
                                font.pixelSize: 16
                                Layout.fillWidth: true
                            }
                        }
                        GlassButton {
                            id: closeRecommendationButton
                            objectName: "closeDiscoveryDetail"
                            iconName: "close"
                            Accessible.name: root.tr("close")
                            Layout.alignment: Qt.AlignTop
                            onActiveFocusChanged: discoveryDetail.revealFocusedControl(closeRecommendationButton)
                            onClicked: discoveryDetail.close()
                        }
                    }

                    Text {
                        text: discoveryDetail.item ? discoveryDetail.item.reason : ""
                        visible: text.length > 0
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 18
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    DiscoveryChipStrip {
                        id: recommendationQualityStrip
                        objectName: "discoveryQualityStrip"
                        values: discoveryDetail.qualityChips
                        accessibleLabel: root.tr("music_discovery_quality_labels")
                        Layout.fillWidth: true
                        onActiveFocusChanged: discoveryDetail.revealFocusedControl(recommendationQualityStrip)
                    }

                    DiscoveryChipStrip {
                        id: recommendationSourceStrip
                        objectName: "discoverySourceStrip"
                        values: discoveryDetail.sourceChips
                        accessibleLabel: root.tr("music_discovery_source_labels")
                        Layout.fillWidth: true
                        onActiveFocusChanged: discoveryDetail.revealFocusedControl(recommendationSourceStrip)
                    }

                    GlassButton {
                        id: playRecommendationButton
                        objectName: "discoveryDetailPlay"
                        text: root.tr("play_now")
                        iconName: "play"
                        primary: true
                        visible: !!discoveryDetail.item && discoveryDetail.item.playable
                        enabled: !discoveryDetail.actionBusy
                        font.pixelSize: 18
                        Layout.fillWidth: true
                        Layout.preferredHeight: 52
                        onActiveFocusChanged: discoveryDetail.revealFocusedControl(playRecommendationButton)
                        onClicked: if (discoveryDetail.item) djconnect.playMusicDiscoveryItem(discoveryDetail.item.payload)
                    }

                    Text {
                        objectName: "discoveryDetailError"
                        visible: djconnect.musicDiscoveryError.length > 0
                        text: djconnect.musicDiscoveryError
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 17
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    PurpleButton {
                        id: feedbackRecommendationButton
                        objectName: "discoveryDetailFeedback"
                        text: root.tr("feedback")
                        visible: djconnect.musicDiscoveryFeedbackSupported
                        enabled: !discoveryDetail.actionBusy
                        font.pixelSize: 18
                        Layout.fillWidth: true
                        Layout.preferredHeight: 48
                        onActiveFocusChanged: discoveryDetail.revealFocusedControl(feedbackRecommendationButton)
                        onClicked: discoveryFeedbackMenu.open()

                        Menu {
                            id: discoveryFeedbackMenu
                            objectName: "discoveryFeedbackMenu"
                            margins: 8
                            onAboutToShow: {
                                const button = feedbackRecommendationButton
                                const bottom = button.mapToItem(root.contentItem, 0, button.height).y
                                y = bottom + implicitHeight + margins <= root.contentItem.height
                                    ? button.height : -implicitHeight
                            }
                            onOpened: currentIndex = 0
                            DiscoveryFeedbackOption {
                                objectName: "discoveryFeedbackFirstOption"
                                text: root.tr("music_discovery_not_for_me")
                                onTriggered: discoveryDetail.sendFeedback("not_for_me")
                            }
                            DiscoveryFeedbackOption {
                                objectName: "discoveryFeedbackMiddleOption"
                                text: root.tr("music_discovery_less_like_this")
                                onTriggered: discoveryDetail.sendFeedback("less_like_this")
                            }
                            DiscoveryFeedbackOption {
                                objectName: "discoveryFeedbackLastOption"
                                text: root.tr("music_discovery_hide_artist")
                                onTriggered: discoveryDetail.sendFeedback("hide_artist")
                            }
                        }
                    }
                }
            }

            function sendFeedback(value) {
                if (item && !actionBusy && djconnect.musicDiscoveryFeedbackSupported)
                    djconnect.sendMusicDiscoveryFeedback(item.payload, value)
            }
        }
    }

    CompactPageLoader {
        id: musicDnaLoader
        objectName: "musicDnaLoader"
        visible: root.activeScreen === "musicdna"
        z: 17
        sourceComponent: MusicDnaPage {}
    }

    component MusicDnaMeter: ColumnLayout {
        id: meter
        required property real value
        property string label: ""
        readonly property real roundedValue: Math.round(value * 10) / 10
        spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: meter.label
                textFormat: Text.PlainText
                color: BrandTheme.colors.muted
                font.pixelSize: 16
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Text {
                text: meter.roundedValue.toLocaleString(Qt.locale(djconnect.language), "f",
                    Number.isInteger(meter.roundedValue) ? 0 : 1) + "%"
                color: BrandTheme.colors.text; font.bold: true; font.pixelSize: 17
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 7; radius: 4
            color: "#30FFFFFF"
            Rectangle { width: parent.width * meter.value / 100; height: parent.height; radius: 4; color: BrandTheme.colors.actionAccent }
        }
    }

    component MusicDnaRow: RowLayout {
        id: dnaRow
        required property var entry
        readonly property string title: entry.labelKey ? root.tr(entry.labelKey) : entry.title || ""
        readonly property string value: entry.valueKey ? root.tr(entry.valueKey)
            : entry.value || (typeof entry.count === "number"
                ? root.tr("music_dna_card_plays").replace("%1", entry.count) : "")
        readonly property bool percentage: typeof entry.percent === "number"
        Layout.fillWidth: true
        spacing: 10
        Text {
            visible: typeof dnaRow.entry.rank === "number"
            text: visible ? dnaRow.entry.rank + "." : ""
            color: BrandTheme.colors.actionAccent
            font.pixelSize: 16; font.bold: true
            Layout.preferredWidth: 20
            Layout.alignment: Qt.AlignTop
        }
        DiscoveryArtwork {
            visible: !!dnaRow.entry.imageUrl
            recommendation: dnaRow.entry
            displayIcon: "music"
            padding: 0; radius: 12
            Layout.preferredWidth: 52; Layout.preferredHeight: 52
        }
        MusicDnaMeter {
            visible: dnaRow.percentage
            Layout.fillWidth: true
            value: dnaRow.percentage ? dnaRow.entry.percent : 0
            label: dnaRow.title
        }
        ColumnLayout {
            visible: !dnaRow.percentage
            Layout.fillWidth: true
            spacing: 3
            Text {
                visible: text.length > 0
                text: dnaRow.title || dnaRow.value
                textFormat: Text.PlainText
                color: BrandTheme.colors.text; font.pixelSize: 17; font.bold: true
                Layout.fillWidth: true; wrapMode: Text.Wrap
            }
            Text {
                visible: text.length > 0
                text: dnaRow.entry.labelKey ? dnaRow.entry.title || dnaRow.value : dnaRow.entry.subtitle || ""
                textFormat: Text.PlainText
                color: BrandTheme.colors.muted; font.pixelSize: 15
                Layout.fillWidth: true; wrapMode: Text.Wrap
            }
        }
        Text {
            visible: !dnaRow.percentage && !dnaRow.entry.labelKey && !!dnaRow.title && !!dnaRow.value
            text: dnaRow.value
            textFormat: Text.PlainText
            color: BrandTheme.colors.muted; font.pixelSize: 15
        }
    }

    component ProfileReadOnOpen: QtObject {
        required property bool surfaceVisible
        required property bool busy
        required property bool loaded
        signal requested()

        readonly property bool profileViewActive: surfaceVisible && root.active && !root.screenBlanked
            && !root.splashVisible && djconnect.paired && !djconnect.demoMode && !djconnect.versionMismatchVisible
        readonly property bool canLoadProfile: profileViewActive && !busy
        readonly property int profileRevision: djconnect.musicDnaContextRevision
        property int requestedProfileRevision: -1
        onProfileViewActiveChanged: if (!profileViewActive) requestedProfileRevision = -1
        onCanLoadProfileChanged: if (canLoadProfile) Qt.callLater(loadProfileIfCurrent)
        onProfileRevisionChanged: Qt.callLater(loadProfileIfCurrent)
        Component.onCompleted: Qt.callLater(loadProfileIfCurrent)

        function loadProfileIfCurrent() {
            if (!canLoadProfile || requestedProfileRevision === profileRevision) return
            const alreadyLoaded = requestedProfileRevision >= 0 && loaded
            requestedProfileRevision = profileRevision
            if (!alreadyLoaded) requested()
        }
    }

    component MusicDnaPage: Rectangle {
        id: musicDnaPanel
        objectName: "musicDnaPanel"
        color: "transparent"
        readonly property bool showProfile: djconnect.musicDnaLoaded && djconnect.musicDnaEnabled
            && (djconnect.musicDnaError.length === 0 || djconnect.musicDnaNeedsRecovery)

        ProfileReadOnOpen {
            id: musicDnaProfileRead
            surfaceVisible: musicDnaPanel.visible
            busy: djconnect.musicDnaBusy
            loaded: djconnect.musicDnaLoaded
            onRequested: djconnect.loadMusicDna()
        }

        Timer {
            objectName: "musicDnaRecoveryTimer"
            interval: 30000
            running: musicDnaProfileRead.canLoadProfile && djconnect.musicDnaNeedsRecovery
            repeat: true
            onTriggered: djconnect.loadMusicDna()
        }

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.topMargin: 22
            anchors.rightMargin: 24
            anchors.bottomMargin: root.edge + 126
            spacing: 14

            PageToolbar {
                title: root.tr("music_dna")
                leading: [ GlassButton { iconName: "back"; tertiary: true; implicitWidth: 52; Accessible.name: root.tr("more"); onClicked: root.activeScreen = "more" } ]
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                RefreshIconButton {
                    id: musicDnaRefreshButton
                    objectName: "musicDnaRefreshButton"
                    text: root.tr("refresh_music_dna")
                    loading: djconnect.musicDnaBusy
                    enabled: !loading && djconnect.paired && !djconnect.demoMode && !djconnect.versionMismatchVisible
                    onClicked: djconnect.refreshMusicDna()
                }
            }

            GlassCard {
                objectName: "musicDnaErrorCard"
                visible: !djconnect.musicDnaBusy && (djconnect.musicDnaError.length > 0 || !djconnect.musicDnaLoaded)
                Layout.fillWidth: true
                Text {
                    objectName: "musicDnaUnavailableText"
                    text: djconnect.musicDnaError || root.tr("music_dna_profile_unavailable")
                    textFormat: Text.PlainText
                    color: BrandTheme.colors.text
                    font.pixelSize: 22
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                GlassButton {
                    objectName: "musicDnaReloadProfile"
                    text: root.tr("music_dna_reload_profile")
                    iconName: "refresh"
                    enabled: musicDnaRefreshButton.enabled
                    Layout.alignment: Qt.AlignLeft
                    onClicked: djconnect.refreshMusicDna()
                }
            }

            GlassCard {
                objectName: "musicDnaLoadingCard"
                visible: !djconnect.musicDnaLoaded && djconnect.musicDnaBusy
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    BusyIndicator {
                        running: visible
                        Layout.preferredWidth: 36
                        Layout.preferredHeight: 36
                        Accessible.ignored: true
                    }
                    Text {
                        objectName: "musicDnaLoadingText"
                        text: root.tr("music_dna_loading_profile")
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 20
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }

            IntroductionCard {
                objectName: "musicDnaOptInHero"
                compact: root.width < 900
                iconName: "heart"
                title: root.tr("music_dna")
                message: root.tr("music_dna_opt_in")
                visible: !djconnect.musicDnaEnabled && djconnect.musicDnaLoaded && djconnect.musicDnaError.length === 0

                PurpleButton {
                    text: root.tr("music_dna_enable")
                    primary: true
                    enabled: !djconnect.musicDnaBusy
                    font.pixelSize: 22
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.openMusicDnaPrompt(false)
                }
            }

            ScrollView {
                id: musicDnaScroll
                objectName: "musicDnaScroll"
                visible: musicDnaPanel.showProfile
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth

                PullToRefresh {
                    objectName: "musicDnaPullToRefresh"
                    flickable: musicDnaScroll.contentItem
                    canRefresh: musicDnaScroll.visible && root.active && !root.screenBlanked
                        && djconnect.paired && !djconnect.demoMode && !djconnect.musicDnaBusy
                        && (djconnect.musicDnaSections.length > 0 || djconnect.musicDnaSummary.length > 0)
                    onRefreshRequested: djconnect.refreshMusicDna()
                }

                GridLayout {
                    id: musicDnaGrid
                    width: Math.max(0, musicDnaScroll.availableWidth)
                    columns: Math.max(1, Math.floor((width + columnSpacing) / (310 + columnSpacing)))
                    columnSpacing: 14
                    rowSpacing: 14

                    GlassCard {
                        objectName: "musicDnaNoProfile"
                        visible: !djconnect.musicDnaBusy && djconnect.musicDnaSections.length === 0
                            && djconnect.musicDnaSummary.length === 0
                        Layout.columnSpan: musicDnaGrid.columns
                        Layout.fillWidth: true
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            FeatureIcon {
                                iconName: "waveform"
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                Accessible.ignored: true
                            }
                            Text {
                                objectName: "musicDnaNoProfileHeading"
                                text: root.tr("music_dna_no_profile_yet")
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.text
                                font.pixelSize: 22
                                font.bold: true
                                wrapMode: Text.WordWrap
                                Accessible.role: Accessible.Heading
                                Layout.fillWidth: true
                            }
                        }
                        Text {
                            objectName: "musicDnaNoProfileDetail"
                            text: root.tr("music_dna_building_profile")
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.muted
                            font.pixelSize: 18
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Repeater {
                        model: djconnect.musicDnaSections.length ? djconnect.musicDnaSections
                            : djconnect.musicDnaSummary ? [{
                                key: "music_dna_card_summary", icon: "waveform", value: djconnect.musicDnaSummary
                            }] : []

                        GlassCard {
                            id: dnaCard
                            required property var modelData
                            objectName: modelData.key
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.alignment: Qt.AlignTop
                            padding: 18
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                FeatureIcon {
                                    iconName: dnaCard.modelData.icon
                                    iconColor: BrandTheme.colors.actionAccent
                                    Layout.preferredWidth: 22; Layout.preferredHeight: 22
                                }
                                Text {
                                    text: root.tr(dnaCard.modelData.key)
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: 18; font.bold: true
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                }
                            }
                            Text {
                                visible: text.length > 0
                                text: dnaCard.modelData.value || ""
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.text
                                font.pixelSize: 23; font.bold: true
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                            }
                            RowLayout {
                                visible: typeof dnaCard.modelData.metric === "number"
                                Layout.fillWidth: true
                                spacing: 14
                                Item {
                                    Layout.preferredWidth: 92
                                    Layout.preferredHeight: 92
                                    Canvas {
                                        id: energyRing
                                        anchors.fill: parent
                                        readonly property real fraction: (dnaCard.modelData.metric || 0) / 100
                                        readonly property color accent: BrandTheme.colors.actionAccent
                                        onFractionChanged: requestPaint()
                                        onAccentChanged: requestPaint()
                                        onPaint: {
                                            const context = getContext("2d")
                                            context.clearRect(0, 0, width, height)
                                            context.lineWidth = 9
                                            context.lineCap = "round"
                                            context.strokeStyle = "#30FFFFFF"
                                            context.beginPath()
                                            context.arc(width / 2, height / 2, width / 2 - 7, 0, 2 * Math.PI)
                                            context.stroke()
                                            if (fraction <= 0) return
                                            context.strokeStyle = accent
                                            context.beginPath()
                                            context.arc(width / 2, height / 2, width / 2 - 7,
                                                        -Math.PI / 2, (fraction * 2 - 0.5) * Math.PI)
                                            context.stroke()
                                        }
                                    }
                                    Text {
                                        anchors.centerIn: parent
                                        text: (dnaCard.modelData.metric || 0) + "%"
                                        color: BrandTheme.colors.text
                                        font.pixelSize: 23; font.bold: true
                                    }
                                }
                                Text {
                                    visible: typeof dnaCard.modelData.count === "number"
                                    text: visible ? root.tr(dnaCard.modelData.key === "music_dna_card_energy"
                                        ? "music_dna_card_analyses" : "music_dna_card_signals").replace("%1", dnaCard.modelData.count) : ""
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.muted
                                    font.pixelSize: 17
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                }
                            }
                            Text {
                                visible: typeof dnaCard.modelData.average === "number"
                                text: visible ? root.tr("music_dna_card_average").replace("%1", dnaCard.modelData.average) : ""
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.muted
                                font.pixelSize: 17
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                            }
                            Repeater {
                                model: dnaCard.modelData.rows || []
                                MusicDnaRow {
                                    required property var modelData
                                    entry: modelData
                                }
                            }
                            Repeater {
                                model: dnaCard.modelData.groups || []
                                ColumnLayout {
                                    id: dnaGroup
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        visible: dnaGroup.modelData.key !== "music_dna_card_"
                                        text: visible ? root.tr(dnaGroup.modelData.key) : ""
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 17; font.bold: true
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                    }
                                    ListView {
                                        visible: dnaGroup.modelData.shelf
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 118
                                        orientation: ListView.Horizontal
                                        clip: true; spacing: 12
                                        model: visible ? dnaGroup.modelData.rows : []
                                        boundsBehavior: Flickable.StopAtBounds
                                        ScrollBar.horizontal: ScrollBar {}
                                        delegate: ColumnLayout {
                                            id: artistTile
                                            required property var modelData
                                            width: 96
                                            DiscoveryArtwork {
                                                recommendation: artistTile.modelData
                                                displayIcon: "session_persona"
                                                padding: 0
                                                radius: 34
                                                Layout.alignment: Qt.AlignHCenter
                                                Layout.preferredWidth: 68; Layout.preferredHeight: 68
                                            }
                                            Text {
                                                text: artistTile.modelData.title
                                                textFormat: Text.PlainText
                                                color: BrandTheme.colors.text
                                                font.pixelSize: 15; font.bold: true
                                                horizontalAlignment: Text.AlignHCenter
                                                elide: Text.ElideRight; Layout.fillWidth: true
                                            }
                                        }
                                    }
                                    Repeater {
                                        model: dnaGroup.modelData.shelf ? [] : dnaGroup.modelData.rows
                                        MusicDnaRow {
                                            required property var modelData
                                            entry: modelData
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Text {
                        objectName: "musicDnaUpdatedFooter"
                        readonly property string updatedTime: {
                            root.trVersion
                            return djconnect.djSession.formatTimestamp(djconnect.musicDnaUpdatedAt, true)
                        }
                        visible: updatedTime.length > 0
                        text: root.tr("music_dna_updated_at").replace("%1", updatedTime)
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.muted
                        horizontalAlignment: Text.AlignHCenter
                        font.pixelSize: 15
                        wrapMode: Text.WordWrap
                        Layout.columnSpan: musicDnaGrid.columns
                        Layout.fillWidth: true
                        Layout.bottomMargin: 8
                    }
                }
            }
            Item {
                visible: !musicDnaPanel.showProfile
                Layout.fillWidth: true; Layout.fillHeight: true
            }
        }
    }

    CompactPageLoader {
        id: askDjLoader
        objectName: "askDjLoader"
        visible: root.activeScreen === "askdj"
        z: 17
        sourceComponent: Component { AskDjPage {} }
    }

    component AskDjPage: Rectangle {
        id: askDjPanel
        objectName: "askDjPanel"
        color: "transparent"
        property bool chatKeyboardOpen: false
        property bool helpOpen: false
        property int draftEditRevision: 0
        property string pendingQuestionText: ""
        readonly property int profileContextRevision: djconnect.musicDnaContextRevision
        onProfileContextRevisionChanged: clearQuestionUi(false)
        readonly property bool historyOffline: !djconnect.demoMode
            && (!djconnect.paired || !djconnect.backendAvailable || djconnect.versionMismatchVisible)

        function clearChatDraft() {
            root.askDjDraft = ""
            chatKeyboardOpen = false
            askDjChatInput.clear()
            askDjChatInput.focus = false
        }

        function sendQuestion() {
            const question = askDjChatInput.text.trim()
            if (askDjSend.enabled && djconnect.sendAskDjQuestion(question)) {
                if (djconnect.askDjBusy) pendingQuestionText = question
                clearChatDraft()
                Qt.callLater(scrollToTop)
            }
        }

        function clearQuestionUi(clearDraft) {
            root.askDjClearConfirmOpen = false
            pendingQuestionText = ""
            readingAnchor = null
            closeHelp()
            if (clearDraft) clearChatDraft()
            else chatKeyboardOpen = false
        }

        function closeHelp() {
            if (!helpOpen) return
            helpOpen = false
            djconnect.closeAskDjHelp()
        }

        Connections {
            target: djconnect
            function onProfileChanged() { askDjPanel.clearQuestionUi(true) }
            function onPairedChanged() { askDjPanel.clearQuestionUi(true) }
            function onDemoModeChanged() { askDjPanel.clearQuestionUi(true) }
            function onAskDjChanged() {
                askDjPanel.updateMessages()
                if (!djconnect.askDjBusy) askDjPanel.pendingQuestionText = ""
            }
            function onAskDjHelpSelected(text, placeholder, revision) {
                if (!askDjPanel.helpOpen || revision !== askDjPanel.draftEditRevision
                        || askDjChatInput.text !== "" || !askDjChatInput.enabled) return
                askDjPanel.closeHelp()
                askDjChatInput.text = text
                if (!placeholder) {
                    askDjPanel.sendQuestion()
                    return
                }
                const start = text.indexOf(placeholder)
                askDjPanel.chatKeyboardOpen = true
                askDjChatInput.forceActiveFocus()
                askDjChatInput.select(start, start + placeholder.length)
            }
            function onAskDjSuggestionSelected(text, revision) {
                if (!askDjPanel.helpOpen || revision !== askDjPanel.draftEditRevision
                        || askDjChatInput.text !== "" || !askDjChatInput.enabled) return
                askDjPanel.closeHelp()
                askDjChatInput.text = text
                askDjChatInput.forceActiveFocus()
                askDjChatInput.cursorPosition = text.length
            }
        }
        Connections {
            target: root
            function onActiveChanged() {
                if (root.active) return
                askDjPanel.pendingQuestionText = ""
                askDjPanel.closeHelp()
            }
            function onScreenBlankedChanged() {
                if (!root.screenBlanked) return
                askDjPanel.pendingQuestionText = ""
                askDjPanel.closeHelp()
            }
        }

        property bool searchOpen: false
        property bool searchKeyboardOpen: false
        property bool returnFocusToChat: false
        property int selectedMatch: -1
        property string selectedMessageId: ""
        property var displayedMessages: []
        property var readingAnchor: null
        Component.onCompleted: updateMessages()
        readonly property var matches: {
            const query = searchText(askDjSearchInput.text.trim())
            const messages = displayedMessages
            const results = []
            if (query) {
                for (let i = 0; i < messages.length; i++) {
                    if (searchText(messages[i].text || "").includes(query))
                        results.push({row: i, messageId: messages[i].id || ""})
                }
            }
            return results
        }
        readonly property int selectedRow: matches[selectedMatch] ? matches[selectedMatch].row : -1

        PageFindShortcut {
            pageReady: askDjPanel.visible && askDjSearchToggle.enabled && !root.askDjClearConfirmOpen
            onActivated: askDjPanel.openSearch(false)
        }

        onVisibleChanged: if (!visible) { closeSearch(); clearQuestionUi(false) }
        onMatchesChanged: {
            if (!searchOpen)
                return
            const previous = selectedMessageId
                ? matches.findIndex(match => match.messageId === selectedMessageId) : -1
            selectMatch(previous >= 0 ? previous : Math.max(0, Math.min(selectedMatch, matches.length - 1)))
            Qt.callLater(centerSelectedMatch)
        }

        function scrollToTop() {
            readingAnchor = null
            searchKeyboardOpen = false
            askDjMessagesView.positionViewAtBeginning()
        }

        function updateMessages() {
            const view = askDjMessagesView
            if (visible && !searchOpen && !readingAnchor && !view.atYBeginning && !view.moving) {
                // The top of the viewport can fall in the spacing between message rows.
                let index = view.indexAt(view.width / 2, view.contentY)
                if (index < 0) index = view.indexAt(view.width / 2, view.contentY + view.spacing + 1)
                const row = view.itemAtIndex(index)
                const message = displayedMessages[index]
                if (row && message && message.id)
                    readingAnchor = {id: message.id, offset: view.contentY - row.y}
            }
            displayedMessages = djconnect.askDjMessages
            if (readingAnchor) Qt.callLater(restoreReadingAnchor)
        }

        function restoreReadingAnchor() {
            const anchor = readingAnchor
            readingAnchor = null
            if (!anchor || !visible || searchOpen || askDjMessagesView.moving) return
            const index = displayedMessages.findIndex(message => message.id === anchor.id)
            if (index < 0) return
            const view = askDjMessagesView
            view.positionViewAtIndex(index, ListView.Beginning)
            view.forceLayout()
            const row = view.itemAtIndex(index)
            if (row) view.contentY = row.y + anchor.offset
            view.returnToBounds()
        }

        function searchText(value) {
            return String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
        }

        function openSearch(showKeyboard) {
            closeHelp()
            if (!searchOpen) returnFocusToChat = !showKeyboard && askDjChatInput.activeFocus
            chatKeyboardOpen = false
            searchOpen = true
            searchKeyboardOpen = showKeyboard
            askDjSearchInput.forceActiveFocus()
        }

        function closeSearch(restoreFocus) {
            searchOpen = false
            searchKeyboardOpen = false
            selectedMatch = -1
            selectedMessageId = ""
            askDjSearchInput.clear()
            if (restoreFocus) {
                if (returnFocusToChat && askDjChatInput.enabled) askDjChatInput.forceActiveFocus()
                else if (askDjSearchToggle.enabled) askDjSearchToggle.forceActiveFocus()
            }
            returnFocusToChat = false
        }

        function selectMatch(position) {
            const match = matches[position]
            selectedMatch = match ? position : -1
            selectedMessageId = match ? match.messageId : ""
        }

        function centerSelectedMatch() {
            if (searchOpen && selectedRow >= 0)
                askDjMessagesView.positionViewAtIndex(selectedRow, ListView.Center)
        }

        function startSearchAtFirstMatch() {
            selectMatch(0)
            Qt.callLater(centerSelectedMatch)
        }

        function dismissSearchKeyboard() {
            searchKeyboardOpen = false
            Qt.callLater(centerSelectedMatch)
        }

        readonly property bool canRefreshHistory: djconnect.paired && !djconnect.demoMode
            && !djconnect.versionMismatchVisible && !djconnect.askDjBusy

        function refreshHistory() {
            if (!canRefreshHistory) return
            chatKeyboardOpen = false
            if (searchKeyboardOpen) dismissSearchKeyboard()
            if (helpOpen) djconnect.openAskDjHelp()
            djconnect.refreshAskDjHistory()
        }

        function moveMatch(offset) {
            if (!matches.length)
                return false
            selectMatch((selectedMatch + offset + matches.length) % matches.length)
            centerSelectedMatch()
            return true
        }

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            anchors.bottomMargin: 130
            spacing: 10

            PageToolbar {
                title: root.tr("ask_dj")
                showConnection: true
                connectionLabel: root.tr("connection_type")
                connectionText: djconnect.connectionType
                Layout.fillWidth: true

                GlassButton {
                    id: askDjSearchToggle
                    objectName: "askDjSearchToggle"
                    readonly property string actionLabel: askDjPanel.searchOpen ? root.tr("close_search") : root.tr("ask_dj_search_history")
                    iconName: "search"
                    tertiary: true
                    checkable: true
                    checked: askDjPanel.searchOpen
                    enabled: askDjPanel.searchOpen || djconnect.askDjMessages.length > 0
                    Accessible.name: actionLabel
                    ToolTip.visible: hovered
                    ToolTip.text: actionLabel
                    onClicked: askDjPanel.searchOpen ? askDjPanel.closeSearch(true) : askDjPanel.openSearch(true)
                }

                RefreshIconButton {
                    id: askDjRefreshButton
                    objectName: "askDjRefreshButton"
                    text: root.tr("refresh_ask_dj")
                    loading: djconnect.askDjBusy
                    enabled: askDjPanel.canRefreshHistory
                    onClicked: askDjPanel.refreshHistory()
                }

                leading: [
                GlassButton { iconName: "back"; tertiary: true; implicitWidth: 52; Accessible.name: root.tr("more"); onClicked: root.activeScreen = "more" },
                Button {
                    id: askDjClearButton
                    objectName: "askDjClearButton"
                    enabled: !askDjPanel.historyOffline && !djconnect.askDjBusy && djconnect.askDjMessages.length > 0
                    ToolTip.visible: hovered
                    ToolTip.text: root.tr("ask_dj_clear_chat")
                    Accessible.name: root.tr("ask_dj_clear_chat")
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    onClicked: {
                        root.askDjClearConfirmationRevision = askDjPanel.profileContextRevision
                        root.askDjClearConfirmOpen = true
                    }
                    contentItem: MenuIcon {
                        iconName: "trash"
                        iconColor: askDjClearButton.enabled ? BrandTheme.colors.text : BrandTheme.colors.disabledText
                        strokeWidth: 2.5
                        anchors.centerIn: parent
                        width: 28
                        height: 28
                    }
                    background: Rectangle {
                        radius: root.standardButtonRadius
                        color: askDjClearButton.down ? BrandTheme.colors.dangerPressed : (askDjClearButton.enabled ? BrandTheme.colors.dangerSurface : root.moodDisabledColor("start"))
                        border.color: askDjClearButton.hovered ? BrandTheme.colors.danger : (askDjClearButton.enabled ? BrandTheme.colors.danger : root.moodDisabledColor("mid"))
                        border.width: 1
                        opacity: askDjClearButton.enabled ? 1.0 : 0.62
                    }
                }
                ]

            }

            Control {
                visible: askDjPanel.searchOpen
                Layout.fillWidth: true
                padding: 5
                background: GlassSurface { radius: 24; interactive: true; focused: askDjSearchInput.activeFocus }
                contentItem: RowLayout {
                    spacing: 4
                    TextField {
                        id: askDjSearchInput
                        objectName: "askDjSearchInput"
                        Layout.fillWidth: true
                        Layout.minimumHeight: 48
                        background: Item {}
                        placeholderText: root.tr("ask_dj_search_history")
                        Accessible.name: placeholderText
                        font.pixelSize: root.width < 900 ? 18 : 22
                        maximumLength: 120
                        selectByMouse: true
                        TapHandler { onTapped: askDjPanel.searchKeyboardOpen = true }
                        onTextEdited: askDjPanel.startSearchAtFirstMatch()
                        onAccepted: {
                            if (askDjPanel.searchKeyboardOpen)
                                askDjPanel.dismissSearchKeyboard()
                            else
                                askDjPanel.moveMatch(1)
                        }
                        Keys.onUpPressed: function(event) { event.accepted = askDjPanel.moveMatch(-1) }
                        Keys.onDownPressed: function(event) { event.accepted = askDjPanel.moveMatch(1) }
                        Keys.onEscapePressed: {
                            if (askDjPanel.searchKeyboardOpen)
                                askDjPanel.dismissSearchKeyboard()
                            else
                                askDjPanel.closeSearch(true)
                        }
                    }
                    Text {
                        objectName: "askDjSearchCount"
                        visible: askDjSearchInput.text.trim().length > 0
                        text: askDjPanel.matches.length === 0 ? "0"
                            : (askDjPanel.selectedMatch + 1) + "/" + askDjPanel.matches.length
                        Accessible.role: Accessible.StaticText
                        Accessible.name: askDjPanel.matches.length === 0 ? root.tr("media_no_matches") : text
                        color: BrandTheme.colors.muted
                        font.pixelSize: root.width < 900 ? 14 : 18
                    }
                    GlassButton {
                        objectName: "askDjPreviousMatch"
                        iconName: "arrow_up"; tertiary: true
                        Accessible.name: root.tr("logs_previous_match")
                        enabled: askDjPanel.matches.length > 0
                        onClicked: askDjPanel.moveMatch(-1)
                    }
                    GlassButton {
                        objectName: "askDjNextMatch"
                        iconName: "arrow_down"; tertiary: true
                        Accessible.name: root.tr("logs_next_match")
                        enabled: askDjPanel.matches.length > 0
                        onClicked: askDjPanel.moveMatch(1)
                    }
                    GlassButton {
                        objectName: "askDjSearchClose"
                        iconName: "close"; tertiary: true; Accessible.name: root.tr("close_search")
                        onClicked: askDjPanel.closeSearch(true)
                    }
                }
            }

            ScrollView {
                id: askDjScroll
                objectName: "askDjScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth

                ListView {
                    id: askDjMessagesView
                    objectName: "askDjMessagesView"
                    model: askDjPanel.displayedMessages
                    spacing: 10
                    cacheBuffer: root.lowMemoryProfile ? 160 : 320
                    onDragStarted: {
                        askDjPanel.chatKeyboardOpen = false
                        askDjPanel.searchKeyboardOpen = false
                    }

                    PullToRefresh {
                        objectName: "askDjPullToRefresh"
                        flickable: askDjMessagesView
                        canRefresh: askDjPanel.visible && root.active && !root.screenBlanked
                            && askDjPanel.canRefreshHistory
                            && djconnect.askDjMessages.length > 0
                        onRefreshRequested: askDjPanel.refreshHistory()
                    }

                    header: ColumnLayout {
                        width: askDjMessagesView.width
                        spacing: 10

                        IntroductionCard {
                            objectName: "askDjIntroduction"
                            compact: root.width < 900
                            iconName: "chat"
                            title: root.tr("ask_dj")
                            message: root.tr("ask_dj_introduction")
                        }

                        Text {
                            text: root.tr("ask_dj_empty")
                            visible: djconnect.askDjMessages.length === 0
                            color: BrandTheme.colors.muted
                            font.pixelSize: 24
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                            Layout.topMargin: 36
                        }
                    }

                    delegate: Item {
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        height: bubble.implicitHeight

                        GlassSurface {
                            id: bubble
                            border.color: root.moodColor("focus")
                            focused: askDjPanel.searchOpen && askDjPanel.selectedRow === index
                            property bool userBubble: modelData.role === "user"
                            property bool systemBubble: modelData.role === "system" || modelData.role === "status" || modelData.messageKind === "system" || modelData.messageKind === "status"

                            width: bubble.userBubble ? Math.min(parent.width * 0.78, 520) : parent.width
                            anchors.right: parent.right
                            height: implicitHeight
                            property int bubblePadding: root.wallProfile ? 24 : 14
                            implicitHeight: askDjBubbleContent.implicitHeight + 2 * bubble.bubblePadding
                            radius: root.wallProfile ? 34 : 22
                            introduction: !bubble.userBubble

                            selected: bubble.userBubble

                            ColumnLayout {
                                id: askDjBubbleContent
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: bubble.bubblePadding
                                spacing: 8

                                Text {
                                    text: modelData.displayTime || ""
                                    visible: modelData.displayTime && modelData.displayTime.length > 0
                                    color: bubble.systemBubble ? BrandTheme.colors.muted : (bubble.userBubble ? BrandTheme.colors.muted : BrandTheme.colors.purple)
                                    opacity: 0.82
                                    font.pixelSize: 14
                                    font.bold: false
                                    horizontalAlignment: bubble.userBubble ? Text.AlignRight : Text.AlignLeft
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: modelData.text
                                    textFormat: Text.PlainText
                                    color: BrandTheme.colors.text
                                    font.pixelSize: bubble.systemBubble ? 19 : 22
                                    font.weight: Font.Normal
                                    wrapMode: Text.Wrap
                                    Layout.fillWidth: true
                                }

                                Flow {
                                    visible: modelData.trackInsight && modelData.musicDnaMatch && modelData.musicDnaMatch.length > 0
                                    spacing: 8
                                    Layout.fillWidth: true

                                    Rectangle {
                                        width: trackInsightModeLabel.implicitWidth + 24
                                        height: 30
                                        radius: 8
                                        color: BrandTheme.alpha(BrandTheme.colors.surface, "33")
                                        border.color: root.moodColor("focus")
                                        border.width: 1

                                        Text {
                                            id: trackInsightModeLabel
                                            anchors.centerIn: parent
                                            text: root.tr("music_dna_match") + " " + (modelData.musicDnaMatch || "")
                                            color: BrandTheme.colors.cyan
                                            font.pixelSize: 15
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }
                                    }
                                }

                                Flow {
                                    visible: modelData.images && modelData.images.length > 0
                                    spacing: 8
                                    Layout.fillWidth: true

                                    Repeater {
                                        model: modelData.images || []
                                        Rectangle {
                                            width: 112
                                            height: 112
                                            radius: 8
                                            color: BrandTheme.colors.surface
                                            clip: true
                                            Image {
                                                anchors.fill: parent
                                                source: modelData.url || ""
                                                fillMode: Image.PreserveAspectCrop
                                                sourceSize.width: 112
                                                sourceSize.height: 112
                                                asynchronous: true
                                                cache: false
                                            }
                                        }
                                    }
                                }

                                Flow {
                                    visible: modelData.trackInsight && modelData.items && modelData.items.length > 0
                                    spacing: 8
                                    Layout.fillWidth: true

                                    Repeater {
                                        model: modelData.items || []

                                        GlassSurface {
                                            border.color: root.moodColor("focus")
                                            width: Math.min(askDjScroll.availableWidth, Math.max(132, trackInsightMetricContent.implicitWidth + 24))
                                            height: 58
                                            radius: 8
                                            clip: true

                                            ColumnLayout {
                                                id: trackInsightMetricContent
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 1

                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 6

                                                    Text {
                                                        text: root.trackInsightLabel(modelData.title || modelData.kind || "")
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: 14
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                        Layout.fillWidth: true
                                                    }

                                                    Text {
                                                        text: modelData.value || ""
                                                        color: BrandTheme.colors.text
                                                        font.pixelSize: 18
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                    }
                                                }

                                                Text {
                                                    text: [modelData.source || "", modelData.confidence || ""].filter(function(value) { return value.length > 0 }).join(" · ")
                                                    color: BrandTheme.colors.muted
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    elide: Text.ElideRight
                                                    maximumLineCount: 1
                                                    visible: text.length > 0
                                                    Layout.fillWidth: true
                                                }
                                            }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.sections && modelData.analysis.sections.length > 0
                                    Layout.fillWidth: true
                                    spacing: 6

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.sections ? modelData.analysis.sections : []

                                        GlassSurface {
                                            border.color: root.moodColor("focus")
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: trackInsightSectionContent.implicitHeight + 16
                                            radius: 8

                                            ColumnLayout {
                                                id: trackInsightSectionContent
                                                anchors.left: parent.left
                                                anchors.right: parent.right
                                                anchors.top: parent.top
                                                anchors.margins: 8
                                                spacing: 3

                                                Text {
                                                    text: root.trackInsightLabel(modelData.title || modelData.id || modelData.kind || "")
                                                    color: modelData.metadataContext ? BrandTheme.colors.cyan : BrandTheme.colors.text
                                                    font.pixelSize: 16
                                                    font.bold: true
                                                    elide: Text.ElideRight
                                                    maximumLineCount: 1
                                                    Layout.fillWidth: true
                                                }

                                                Text {
                                                    text: modelData.body || ""
                                                    color: BrandTheme.colors.muted
                                                    font.pixelSize: 14
                                                    wrapMode: Text.WordWrap
                                                    visible: text.length > 0
                                                    Layout.fillWidth: true
                                                }

                                                Repeater {
                                                    model: modelData.details || []

                                                    Text {
                                                        text: "• " + modelData
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: 13
                                                        wrapMode: Text.WordWrap
                                                        Layout.fillWidth: true
                                                    }
                                                }

                                                Text {
                                                    text: [modelData.source || "", modelData.confidence || ""].filter(function(value) { return value.length > 0 }).join(" · ")
                                                    color: BrandTheme.colors.muted
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    visible: text.length > 0
                                                    Layout.fillWidth: true
                                                }
                                            }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.timeline && modelData.analysis.timeline.length > 0
                                    Layout.fillWidth: true
                                    spacing: 4

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.timeline ? modelData.analysis.timeline : []

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8

                                            Text {
                                                text: [modelData.start || "", modelData.end || ""].filter(function(value) { return value.length > 0 }).join("–")
                                                color: BrandTheme.colors.cyan
                                                font.pixelSize: 13
                                                font.bold: true
                                                Layout.preferredWidth: 86
                                                visible: text.length > 0
                                            }

                                            Text {
                                                text: modelData.label || modelData.kind || modelData.id || ""
                                                color: BrandTheme.colors.text
                                                font.pixelSize: 14
                                                elide: Text.ElideRight
                                                maximumLineCount: 1
                                                Layout.fillWidth: true
                                            }

                                            Text {
                                                text: [modelData.source || "", modelData.confidence || ""].filter(function(value) { return value.length > 0 }).join(" · ")
                                                color: BrandTheme.colors.muted
                                                font.pixelSize: 11
                                                elide: Text.ElideRight
                                                maximumLineCount: 1
                                                visible: text.length > 0
                                                Layout.preferredWidth: 118
                                            }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.metadata && modelData.analysis.metadata.details && modelData.analysis.metadata.details.length > 0
                                    Layout.fillWidth: true
                                    spacing: 3

                                    Text {
                                        text: root.tr("analysis_context")
                                        color: BrandTheme.colors.cyan
                                        font.pixelSize: 13
                                        font.bold: true
                                        Layout.fillWidth: true
                                    }

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.metadata && modelData.analysis.metadata.details ? modelData.analysis.metadata.details : []

                                        Text {
                                            text: "• " + modelData
                                            color: BrandTheme.colors.muted
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.djTips && modelData.analysis.djTips.length > 0
                                    Layout.fillWidth: true
                                    spacing: 4

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.djTips ? modelData.analysis.djTips : []

                                        Text {
                                            text: "• " + [modelData.title || modelData.kind || "", modelData.text || ""].filter(function(value) { return value.length > 0 }).join(": ")
                                            color: BrandTheme.colors.muted
                                            font.pixelSize: 14
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: !modelData.trackInsight && modelData.items && modelData.items.length > 0
                                    Layout.fillWidth: true
                                    spacing: 8

                                    Repeater {
                                        model: modelData.items || []

                                        GlassSurface {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 70
                                            radius: 8
                                            clip: true

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 10

                                                Rectangle {
                                                    Layout.preferredWidth: 54
                                                    Layout.preferredHeight: 54
                                                    radius: 8
                                                    color: BrandTheme.colors.surface
                                                    clip: true

                                                    Image {
                                                        visible: modelData.imageUrl && modelData.imageUrl.length > 0
                                                        anchors.fill: parent
                                                        source: modelData.imageUrl || ""
                                                        fillMode: Image.PreserveAspectCrop
                                                        sourceSize.width: 54
                                                        sourceSize.height: 54
                                                        asynchronous: true
                                                        cache: false
                                                    }

                                                    Text {
                                                        anchors.centerIn: parent
                                                        visible: !modelData.imageUrl || modelData.imageUrl.length === 0
                                                        text: "♪"
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: 24
                                                        font.bold: true
                                                    }
                                                }

                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 2

                                                    Text {
                                                        text: modelData.title || ""
                                                        color: BrandTheme.colors.text
                                                        font.pixelSize: 17
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                        Layout.fillWidth: true
                                                    }

                                                    Text {
                                                        text: modelData.subtitle || ""
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: 15
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                        visible: text.length > 0
                                                        Layout.fillWidth: true
                                                    }
                                                }

                                                Text {
                                                    text: modelData.time || ""
                                                    color: BrandTheme.colors.muted
                                                    font.pixelSize: 14
                                                    font.bold: true
                                                    horizontalAlignment: Text.AlignRight
                                                    elide: Text.ElideRight
                                                    maximumLineCount: 1
                                                    Layout.preferredWidth: 112
                                                    visible: text.length > 0
                                                }
                                            }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.limitations && modelData.analysis.limitations.length > 0
                                    Layout.fillWidth: true
                                    spacing: 3

                                    Text {
                                        text: root.tr("analysis_limitations")
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 13
                                        font.bold: true
                                        Layout.fillWidth: true
                                    }

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.limitations ? modelData.analysis.limitations : []

                                        Text {
                                            text: "• " + (modelData.text || modelData)
                                            color: BrandTheme.colors.muted
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.trackInsight && modelData.analysis && modelData.analysis.providers && modelData.analysis.providers.length > 0
                                    Layout.fillWidth: true
                                    spacing: 3

                                    Text {
                                        text: root.tr("analysis_diagnostics")
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 13
                                        font.bold: true
                                        Layout.fillWidth: true
                                    }

                                    Repeater {
                                        model: modelData.analysis && modelData.analysis.providers ? modelData.analysis.providers : []

                                        Text {
                                            text: "• " + [modelData.label || modelData.providerId || root.tr("unknown"), modelData.status || root.tr("unknown"), modelData.reason || ""].filter(function(value) { return value.length > 0 }).join(" · ")
                                            color: BrandTheme.colors.muted
                                            font.pixelSize: 12
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.links && modelData.links.length > 0
                                    Layout.fillWidth: true
                                    spacing: 4

                                    Text {
                                        text: root.tr("sources")
                                        color: BrandTheme.colors.muted
                                        font.pixelSize: 16
                                        font.bold: true
                                        Layout.fillWidth: true
                                    }

                                    Repeater {
                                        model: modelData.links || []
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 28
                                            color: "#00000000"

                                            Text {
                                                anchors.fill: parent
                                                text: modelData.title || modelData.url || ""
                                                color: BrandTheme.colors.cyan
                                                font.pixelSize: 17
                                                elide: Text.ElideRight
                                                verticalAlignment: Text.AlignVCenter
                                            }

                                            MouseArea {
                                                anchors.fill: parent
                                                enabled: modelData.url && modelData.url.length > 0
                                                onClicked: Qt.openUrlExternally(modelData.url)
                                            }
                                        }
                                    }
                                }

                                Text {
                                    visible: modelData.announcementDelivery && modelData.announcementDelivery.length > 0
                                    text: modelData.announcementDelivery === "ha_speaker"
                                        ? root.tr("dj_announcement_delivered_ha_speaker") + " " + (modelData.announcementTargetName || root.tr("home_assistant"))
                                        : root.tr("dj_announcement_text_only")
                                    color: BrandTheme.colors.cyan
                                    font.pixelSize: 14
                                    font.bold: true
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                Repeater {
                                    model: modelData.announcementWarnings || []

                                    Text {
                                        text: modelData
                                        color: BrandTheme.colors.warning
                                        font.pixelSize: 13
                                        wrapMode: Text.WordWrap
                                        Layout.fillWidth: true
                                    }
                                }

                                ColumnLayout {
                                    visible: modelData.actions && modelData.actions.length > 0
                                    Layout.fillWidth: true
                                    spacing: 8

                                    Repeater {
                                        model: modelData.actions || []

                                        GlassSurface {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: (modelData.isMedia || modelData.isOutput) ? 88 : 50
                                            radius: 8
                                            clip: true

                                            tertiary: !modelData.isMedia && !modelData.isOutput

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: (modelData.isMedia || modelData.isOutput) ? 10 : 0
                                                spacing: 12

                                                Rectangle {
                                                    visible: modelData.isMedia || modelData.isOutput
                                                    Layout.preferredWidth: 68
                                                    Layout.preferredHeight: 68
                                                    radius: 8
                                                    color: BrandTheme.colors.surface
                                                    clip: true

                                                    Image {
                                                        visible: modelData.isMedia
                                                        anchors.fill: parent
                                                        source: modelData.imageUrl && modelData.imageUrl.length > 0 ? modelData.imageUrl : ""
                                                        fillMode: Image.PreserveAspectCrop
                                                        sourceSize.width: 68
                                                        sourceSize.height: 68
                                                        asynchronous: true
                                                        cache: false
                                                    }

                                                    Text {
                                                        anchors.centerIn: parent
                                                        visible: modelData.isOutput || !modelData.imageUrl || modelData.imageUrl.length === 0
                                                        text: modelData.isOutput ? "OUT" : "♪"
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: modelData.isOutput ? 18 : 28
                                                        font.bold: true
                                                    }
                                                }

                                                ColumnLayout {
                                                    visible: modelData.isMedia || modelData.isOutput
                                                    Layout.fillWidth: true
                                                    spacing: 3

                                                    Text {
                                                        text: modelData.title || root.tr("start")
                                                        color: BrandTheme.colors.text
                                                        font.pixelSize: 18
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                        Layout.fillWidth: true
                                                    }

                                                    Text {
                                                        text: modelData.subtitle || ""
                                                        color: BrandTheme.colors.muted
                                                        font.pixelSize: 16
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                        visible: text.length > 0
                                                        Layout.fillWidth: true
                                                    }

                                                    Rectangle {
                                                        Layout.preferredHeight: 24
                                                        Layout.preferredWidth: Math.max(78, actionKindLabel.implicitWidth + 24)
                                                        radius: 12
                                                        color: "#33ffffff"

                                                        Text {
                                                            id: actionKindLabel
                                                            anchors.centerIn: parent
                                                            text: modelData.kind || "media"
                                                            color: BrandTheme.colors.text
                                                            font.pixelSize: 14
                                                            font.bold: true
                                                            elide: Text.ElideRight
                                                        }
                                                    }
                                                }

                                                AskDjGradientButton {
                                                    id: askDjActionButton
                                                    text: modelData.isOutput ? root.tr("activate") : (modelData.isMedia ? root.tr("play_now") : (modelData.title || root.tr("start")))
                                                    font.pixelSize: (modelData.isMedia || modelData.isOutput) ? 16 : 18
                                                    Layout.fillWidth: !(modelData.isMedia || modelData.isOutput)
                                                    Layout.preferredWidth: (modelData.isMedia || modelData.isOutput) ? 118 : 0
                                                    Layout.preferredHeight: (modelData.isMedia || modelData.isOutput) ? 44 : 50
                                                    contentItem: RowLayout {
                                                        spacing: 8

                                                        Canvas {
                                                            visible: modelData.isMedia
                                                            Layout.preferredWidth: 18
                                                            Layout.preferredHeight: 18
                                                            antialiasing: true
                                                            onPaint: {
                                                                var ctx = getContext("2d")
                                                                ctx.clearRect(0, 0, width, height)
                                                                ctx.fillStyle = BrandTheme.colors.text
                                                                ctx.beginPath()
                                                                ctx.moveTo(width * 0.33, height * 0.22)
                                                                ctx.lineTo(width * 0.33, height * 0.78)
                                                                ctx.lineTo(width * 0.78, height * 0.5)
                                                                ctx.closePath()
                                                                ctx.fill()
                                                            }
                                                        }

                                                        Text {
                                                            id: actionLabel
                                                            text: askDjActionButton.text
                                                            font: askDjActionButton.font
                                                            color: BrandTheme.colors.text
                                                            horizontalAlignment: Text.AlignHCenter
                                                            verticalAlignment: Text.AlignVCenter
                                                            elide: Text.ElideRight
                                                            maximumLineCount: 1
                                                            Layout.fillWidth: true
                                                        }
                                                    }
                                                    onClicked: djconnect.sendAskDjAction(modelData.payload || "{}")
                                                }
                                            }
                                        }
                                    }
                                }

                            }
                        }
                    }
                }
            }

            GlassButton {
                parent: askDjScroll
                objectName: "askDjJumpTop"
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 12
                z: 2
                width: 52
                height: width
                iconName: "arrow_up"
                visible: askDjMessagesView.count > 0 && !askDjMessagesView.atYBeginning
                    && !askDjPanel.chatKeyboardOpen
                Accessible.name: root.tr("session_back_top")
                ToolTip.visible: hovered
                ToolTip.text: root.tr("session_back_top")
                onClicked: askDjPanel.scrollToTop()
            }

            GlassSurface {
                objectName: "askDjOfflineNotice"
                visible: askDjPanel.historyOffline
                Layout.fillWidth: true
                implicitHeight: offlineNoticeContent.implicitHeight + 20
                radius: 22

                RowLayout {
                    id: offlineNoticeContent
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    FeatureIcon {
                        iconName: "info"
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: root.tr("ask_dj_offline_title")
                            color: BrandTheme.colors.text
                            font.pixelSize: 17
                            font.bold: true
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Text {
                            objectName: "askDjOfflineHistory"
                            visible: djconnect.askDjMessages.length > 0
                            text: root.tr("ask_dj_offline_history")
                            color: BrandTheme.colors.muted
                            font.pixelSize: 15
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }

            GlassSurface {
                objectName: "askDjPendingQuestion"
                visible: !askDjPanel.searchOpen && askDjPanel.pendingQuestionText.length > 0
                Layout.fillWidth: true
                implicitHeight: pendingQuestionContent.implicitHeight + 20
                radius: 22

                ColumnLayout {
                    id: pendingQuestionContent
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 4
                    Text {
                        objectName: "askDjPendingQuestionText"
                        text: askDjPanel.pendingQuestionText
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 18
                        maximumLineCount: 3
                        wrapMode: Text.Wrap
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Text {
                        text: root.tr("session_question_sending")
                        color: BrandTheme.colors.muted
                        font.pixelSize: 15
                        Layout.fillWidth: true
                    }
                }
            }

            AskDjExamples {
                objectName: "askDjHelpPanel"
                visible: askDjPanel.helpOpen
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(340, askDjPanel.height * 0.36)
                backend: djconnect
                canChoose: askDjChatInput.enabled && askDjChatInput.text === ""
                onRefreshRequested: djconnect.openAskDjHelp()
                onCloseRequested: askDjPanel.closeHelp()
                onExampleChosen: entryId => djconnect.selectAskDjHelp(entryId, askDjPanel.draftEditRevision)
                onSuggestionChosen: entryId => djconnect.selectAskDjSuggestion(entryId, askDjPanel.draftEditRevision)
            }

            GlassSurface {
                objectName: "askDjChatBar"
                visible: !askDjPanel.searchOpen
                Layout.fillWidth: true
                Layout.preferredHeight: 66
                radius: 30; elevated: true

                RowLayout {
                    anchors.fill: parent; anchors.margins: 8
                    spacing: 8
                    TextField {
                        id: askDjChatInput
                        objectName: "askDjChatInput"
                        property bool draftReady: false
                        text: root.askDjDraft
                        onTextChanged: {
                            if (!draftReady) return
                            askDjPanel.draftEditRevision += 1
                            if (text !== root.askDjDraft) root.askDjDraft = text
                        }
                        Component.onCompleted: draftReady = true
                        Layout.fillWidth: true; Layout.fillHeight: true
                        enabled: (djconnect.paired || djconnect.demoMode) && !djconnect.askDjBusy
                        maximumLength: 4000
                        placeholderText: root.tr("session_question_placeholder")
                        Accessible.name: placeholderText
                        font.pixelSize: 21
                        selectByMouse: true
                        background: GlassSurface { radius: 24; interactive: true; focused: askDjChatInput.activeFocus }
                        TapHandler { onTapped: askDjPanel.chatKeyboardOpen = true }
                        onAccepted: askDjPanel.sendQuestion()
                    }
                    GlassButton {
                        objectName: "askDjHelpToggle"
                        iconName: "info"; tertiary: true
                        Accessible.name: root.tr("ask_dj_example_questions")
                        Layout.preferredWidth: 48; Layout.fillHeight: true
                        enabled: djconnect.paired || djconnect.demoMode
                        onClicked: {
                            if (askDjPanel.helpOpen) {
                                askDjPanel.closeHelp()
                            } else {
                                askDjPanel.chatKeyboardOpen = false
                                askDjPanel.helpOpen = true
                                djconnect.openAskDjHelp()
                            }
                        }
                    }
                    GlassButton {
                        id: askDjSend
                        objectName: "askDjSend"
                        iconName: "send"; Accessible.name: root.tr("session_send")
                        primary: true
                        Layout.preferredWidth: 52; Layout.fillHeight: true
                        enabled: !askDjPanel.historyOffline && askDjChatInput.enabled && askDjChatInput.text.trim().length > 0
                        onClicked: askDjPanel.sendQuestion()
                    }
                }
            }

            SearchKeyboardDock {
                visible: !askDjPanel.searchOpen && askDjPanel.chatKeyboardOpen
                Layout.fillWidth: true
                backend: djconnect
                target: askDjChatInput
                keyboardName: "askDjChatKeyboard"
                onSubmitted: askDjPanel.sendQuestion()
                onDismissed: askDjPanel.chatKeyboardOpen = false
            }

            SearchKeyboardDock {
                visible: askDjPanel.searchOpen && askDjPanel.searchKeyboardOpen
                Layout.fillWidth: true
                backend: djconnect
                target: askDjSearchInput
                keyboardName: "askDjHistorySearchKeyboard"
                onEdited: askDjPanel.startSearchAtFirstMatch()
                onSubmitted: askDjPanel.dismissSearchKeyboard()
                onDismissed: askDjPanel.dismissSearchKeyboard()
            }
        }

    }

    Item {
        anchors.fill: parent
        visible: root.activeScreen === "session" || root.activeScreen === "history" || root.activeScreen === "session-history"
        z: 17
        AppBackground { visible: !root.usesPageBackdrop }
        SessionScreen {
            id: sharedSessionScreen
            anchors.fill: parent
            backend: djconnect
            trackInsightMoodSurface: trackInsightLoader.item && trackInsightLoader.item.canChooseSessionMood
                ? trackInsightLoader.item : null
            pollingAllowed: root.active && !root.screenBlanked && !root.splashVisible && !vibeScapeLoader.active
                && !djconnect.logsVisible && !djconnect.versionMismatchVisible
            vibeScapeActive: vibeScapeLoader.active
            historical: root.activeScreen === "history" || root.activeScreen === "session-history"
            currentHistory: root.activeScreen === "session-history"
            onExpandTrackArtwork: function(source) { root.openTrackArtwork(source) }
            onOpenVibeScape: function(pickerTop) { root.openVibeScape(pickerTop) }
            onPairingRecoveryRequested: root.resetPairingConfirmOpen = true
            onNavigate: function(destination) {
                if (destination === "about") root.aboutOpen = true
                else root.activeScreen = destination
            }
        }
    }

    GamesPanel {
        id: gamesPanel
        anchors.fill: parent
        visible: gamesOpen
        z: 18
        onCloseRequested: root.activeScreen = "more"
    }

    GlassSurface {
        id: bottomNav
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 20
        width: Math.min(parent.width - 40, 760)
        height: 92
        radius: height / 2
        color: BrandTheme.alpha(BrandTheme.colors.surface, "dd")
        border.color: BrandTheme.colors.surfaceRaised
        border.width: 1
        visible: !root.splashVisible && (djconnect.paired || djconnect.demoMode) && !djconnect.logsVisible && !djconnect.versionMismatchVisible
            && !(queuePanelLoader.item && queuePanelLoader.item.keyboardOpen)
            && !(playlistsPanelLoader.item && playlistsPanelLoader.item.keyboardOpen)
        z: 25

        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 2
            Repeater {
                model: [
                    {screen: "session", key: "session_title", icon: "session"},
                    {screen: "queue", key: "queue", icon: "queue"},
                    {screen: "trackinsight", key: "track_insight", icon: "trackInsight"},
                    {screen: "discover", key: "music_discovery", icon: "discover"},
                    {screen: "more", key: "more", icon: "more"}
                ]
                NavButton {
                    required property var modelData
                    text: root.tr(modelData.key)
                    objectName: "appTab-" + modelData.screen
                    iconName: modelData.icon
                    checkable: true
                    checked: root.aboutOpen ? modelData.screen === "more" : root.activeScreen === modelData.screen || (modelData.screen === "session" && root.activeScreen === "session-history") || (modelData.screen === "more" && ["askdj", "history", "playlists", "games", "settings", "musicdna", "legal", "privacy", "feedback"].indexOf(root.activeScreen) >= 0)
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onClicked: {
                        root.aboutOpen = false
                        root.activeScreen = modelData.screen
                        if (modelData.screen === "queue") djconnect.loadQueue()
                    }
                }
            }
        }
    }

    CompactPageLoader {
        visible: !root.splashVisible && !djconnect.paired && !djconnect.demoMode
        asynchronous: false
        z: 30
        sourceComponent: PairingPage {}
    }

    component PairingPage: Rectangle {
        id: pairingPanel
        objectName: "pairingPanel"
        color: BrandTheme.colors.background

        AppBackground {}
        ModalBlocker {}

        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 640)
            spacing: 14

            AppBanner { detailText: root.tr("tagline") }

            Text {
                text: root.tr("pairing_screen_title")
                color: BrandTheme.colors.muted
                font.pixelSize: 38
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }

            Text {
                text: root.tr("pairing_hint")
                color: BrandTheme.colors.muted
                font.pixelSize: 20
                font.bold: true
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                radius: root.liquidGlassEnabled ? 16 : 8
                color: root.liquidGlassEnabled ? BrandTheme.alpha(BrandTheme.colors.surface, "70") : BrandTheme.alpha(BrandTheme.colors.surface, "55")
                border.width: root.liquidGlassEnabled ? 1 : 0
                border.color: "#48ffffff"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 3

                    Text {
                        text: root.tr("home_assistant")
                        color: BrandTheme.colors.muted
                        font.pixelSize: 17
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Text {
                        text: djconnect.haUrl.length ? djconnect.haUrl : "http://homeassistant.local:8123"
                        color: BrandTheme.colors.text
                        font.pixelSize: 22
                        font.bold: true
                        font.family: "monospace"
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 98
                radius: root.liquidGlassEnabled ? 16 : 8
                color: root.liquidGlassEnabled ? BrandTheme.alpha(BrandTheme.colors.surface, "70") : BrandTheme.alpha(BrandTheme.colors.surface, "55")
                border.width: root.liquidGlassEnabled ? 1 : 0
                border.color: "#48ffffff"

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: root.tr("pairing_code")
                            color: BrandTheme.colors.muted
                            font.pixelSize: 18
                            font.bold: true
                            Layout.fillWidth: true
                        }

                        Text {
                            text: djconnect.pairingCode
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.pixelSize: 36
                            font.bold: true
                            Layout.fillWidth: true
                        }
                    }

                    GlassButton {
                        objectName: "copyPairingCode"
                        readonly property string copyLabel: root.tr("copy_pairing_code")
                        iconName: "copy"; tertiary: true
                        Layout.preferredWidth: 56
                        Layout.preferredHeight: 56
                        Accessible.name: copyLabel
                        ToolTip.text: copyLabel
                        ToolTip.visible: hovered
                        enabled: djconnect.pairingCode.length > 0
                        onClicked: djconnect.copyPairingCode()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                radius: root.liquidGlassEnabled ? 16 : 8
                color: root.liquidGlassEnabled ? BrandTheme.alpha(BrandTheme.colors.surface, "70") : BrandTheme.alpha(BrandTheme.colors.surface, "55")
                border.width: root.liquidGlassEnabled ? 1 : 0
                border.color: "#48ffffff"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 3

                    Text {
                        text: root.tr("client_api_url")
                        color: BrandTheme.colors.muted
                        font.pixelSize: 17
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Text {
                        text: djconnect.localApiUrl
                        color: BrandTheme.colors.text
                        font.pixelSize: 22
                        font.bold: true
                        font.family: "monospace"
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 72
                radius: root.liquidGlassEnabled ? 16 : 8
                color: root.liquidGlassEnabled ? BrandTheme.alpha(BrandTheme.colors.surface, "70") : root.moodColor("chip")
                border.width: root.liquidGlassEnabled ? 1 : 0
                border.color: "#48ffffff"

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 18

                    LoadingSpinner {
                        running: pairingPanel.visible
                        implicitWidth: 38
                        implicitHeight: 38
                    }

                    Text {
                        text: root.tr("waiting_for_ha")
                        color: BrandTheme.colors.text
                        font.pixelSize: 22
                        font.bold: true
                        Layout.fillWidth: true
                    }
                }
            }

            PurpleButton {
                id: startDemoButton
                primary: true
                text: root.tr("start_demo_mode")
                font.pixelSize: 22
                Layout.fillWidth: true
                Layout.preferredHeight: 54
                contentItem: RowLayout {
                    spacing: 10

                    Item {
                        Layout.preferredWidth: 28
                        Layout.preferredHeight: 28

                        Canvas {
                            anchors.fill: parent
                            antialiasing: true
                            onPaint: {
                                var ctx = getContext("2d")
                                var s = Math.min(width, height)
                                ctx.clearRect(0, 0, width, height)
                                ctx.strokeStyle = BrandTheme.colors.text
                                ctx.fillStyle = BrandTheme.colors.text
                                ctx.lineWidth = 3
                                ctx.beginPath()
                                ctx.arc(width / 2, height / 2, s * 0.38, 0, Math.PI * 2)
                                ctx.stroke()
                                ctx.beginPath()
                                ctx.moveTo(width * 0.43, height * 0.33)
                                ctx.lineTo(width * 0.43, height * 0.67)
                                ctx.lineTo(width * 0.68, height * 0.5)
                                ctx.closePath()
                                ctx.fill()
                            }
                        }
                    }

                    Text {
                        text: startDemoButton.text
                        font: startDemoButton.font
                        color: startDemoButton.enabled ? BrandTheme.colors.text : BrandTheme.colors.disabledText
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        Layout.fillWidth: false
                    }
                }
                onClicked: djconnect.enterDemoMode()
            }

        }
    }

    CompactPageLoader {
        visible: !root.splashVisible && djconnect.pairingSuccessVisible
        asynchronous: false
        z: 34
        sourceComponent: PairingSuccessPage {}
    }

    component PairingSuccessPage: Rectangle {
        id: pairingSuccessPanel
        objectName: "pairingSuccessPanel"
        color: BrandTheme.colors.background

        AppBackground {}
        ModalBlocker {}

        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 640)
            spacing: 28

            AppBanner { detailText: root.tr("tagline") }

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 132
                Layout.preferredHeight: 132
                radius: 66
                color: BrandTheme.colors.success

                Canvas {
                    anchors.fill: parent
                    antialiasing: true
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.strokeStyle = BrandTheme.colors.surface
                        ctx.lineWidth = 14
                        ctx.lineCap = "round"
                        ctx.lineJoin = "round"
                        ctx.beginPath()
                        ctx.moveTo(width * 0.28, height * 0.52)
                        ctx.lineTo(width * 0.44, height * 0.68)
                        ctx.lineTo(width * 0.72, height * 0.32)
                        ctx.stroke()
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                Text {
                    text: root.tr("pairing_success_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 38
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("pairing_success_message")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 20
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
            }

            PurpleButton {
                text: root.tr("start")
                primary: true
                font.pixelSize: 24
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                onClicked: {
                    root.activeScreen = "session"
                    djconnect.startAfterPairing()
                }
            }
        }
    }

    Item {
        id: splashPanel
        objectName: "launchSplash"
        anchors.fill: parent
        opacity: root.splashVisible ? 1 : 0
        visible: opacity > 0
        z: 40
        readonly property bool animate: root.splashVisible && root.active && GlassStyle.awake && !GlassStyle.reduceMotion

        Behavior on opacity {
            NumberAnimation { duration: GlassStyle.reduceMotion ? 0 : root.motionStandard; easing.type: Easing.OutCubic }
        }

        SessionMoodCanvas { anchors.fill: parent; mood: ""; activeSession: false }
        ModalBlocker {}

        GlassCard {
            id: launchCard
            objectName: "launchCard"
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, root.wallProfile ? 560 : 430)
            height: implicitHeight
            padding: root.wallProfile ? 30 : 23
            spacing: root.wallProfile ? 24 : 17
            background: GlassSurface { introduction: true; radius: root.wallProfile ? 48 : 34 }

            AppBanner {
                detailText: root.tr("tagline")
                showCardBackground: false
                compact: !root.wallProfile
            }
            SessionWelcomeArtwork {
                objectName: "launchVinylArtwork"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Math.min(launchCard.availableWidth, root.wallProfile ? 360 : 240)
                Layout.preferredHeight: Layout.preferredWidth
                animate: splashPanel.animate
            }
            Text {
                objectName: "launchVersion"
                text: "v" + djconnect.version
                color: BrandTheme.colors.muted
                font.pixelSize: root.wallProfile ? 19 : 15
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }
            LoadingSpinner {
                objectName: "launchSpinner"
                spokes: true
                running: splashPanel.animate
                implicitWidth: root.wallProfile ? 31 : 27
                implicitHeight: implicitWidth
                Layout.alignment: Qt.AlignHCenter
                Accessible.role: Accessible.Indicator
                Accessible.name: root.tr("startup_message")
            }
        }
    }

    Rectangle {
        id: toast
        objectName: "appToast"
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 32
        width: Math.min(parent.width - 58, Math.max(300, toastText.implicitWidth + 112))
        height: Math.max(64, toastText.implicitHeight + 26)
        radius: height / 2
        border.color: "#b8ffffff"
        border.width: 2
        opacity: djconnect.toastVisible ? 1 : 0
        visible: opacity > 0
        z: djconnect.logsVisible && djconnect.toastIcon === "logs" ? logsPanel.z + 1 : 70
        y: djconnect.toastVisible ? 0 : -16
        scale: djconnect.toastVisible ? 1 : 0.94
        transformOrigin: Item.Top
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: root.liquidGlassEnabled ? "#bd" + root.moodColor("toastStart").slice(1) : root.moodColor("toastStart") }
            GradientStop { position: 0.52; color: root.liquidGlassEnabled ? "#a8" + root.moodColor("toastMid").slice(1) : root.moodColor("toastMid") }
            GradientStop { position: 1.0; color: root.liquidGlassEnabled ? "#ba" + root.moodColor("toastEnd").slice(1) : root.moodColor("toastEnd") }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 2
            height: 2
            radius: 2
            color: "#bfffffff"
            opacity: root.liquidGlassEnabled ? 0.72 : 0
        }

        Behavior on opacity { NumberAnimation { duration: root.motionFast; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: root.motionStandard; easing.type: Easing.OutBack } }
        Behavior on scale { NumberAnimation { duration: root.motionStandard; easing.type: Easing.OutBack } }

        SequentialAnimation on border.color {
            running: djconnect.toastVisible && root.wallProfile
            loops: Animation.Infinite
            ColorAnimation { to: BrandTheme.colors.muted; duration: 1200; easing.type: Easing.InOutSine }
            ColorAnimation { to: "#b8ffffff"; duration: 1200; easing.type: Easing.InOutSine }
        }

        Row {
            anchors.centerIn: parent
            spacing: 16

            MenuIcon {
                width: 28
                height: 28
                iconName: djconnect.toastIcon
                iconColor: BrandTheme.colors.text
                strokeWidth: 2.8
                anchors.verticalCenter: parent.verticalCenter
            }

            Text {
                id: toastText
                objectName: "appToastText"
                width: Math.min(root.width - 188, implicitWidth)
                anchors.verticalCenter: parent.verticalCenter
                text: djconnect.toastText
                color: BrandTheme.colors.text
                font.pixelSize: 24
                font.bold: true
                wrapMode: Text.NoWrap
                horizontalAlignment: Text.AlignLeft
                elide: Text.ElideRight
                maximumLineCount: 1
            }
        }
    }

    Rectangle {
        id: versionMismatchPanel
        anchors.fill: parent
        color: BrandTheme.alpha(BrandTheme.colors.background, "f2")
        visible: !root.splashVisible && djconnect.versionMismatchVisible
        z: 60

        ModalBlocker {}

        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 72, 560)
            spacing: 18

            Text {
                text: root.tr("version_mismatch_title")
                color: BrandTheme.colors.text
                font.pixelSize: 40
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }

            Text {
                text: djconnect.versionMismatchText
                color: BrandTheme.colors.muted
                font.pixelSize: 20
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 12

                BusyIndicator {
                    running: versionMismatchPanel.visible
                    implicitWidth: 34
                    implicitHeight: 34
                }

                Text {
                    text: root.tr("update_trying")
                    color: BrandTheme.colors.disabledText
                    font.pixelSize: 17
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                PurpleButton {
                    visible: djconnect.logLevel !== "OFF"
                    text: root.tr("view_logs")
                    font.pixelSize: 18
                    Layout.fillWidth: true
                    onClicked: djconnect.showLogs()
                }
            }
        }
    }

    Rectangle {
        id: logsPanel
        anchors.fill: parent
        color: BrandTheme.colors.background
        visible: djconnect.logsVisible
        z: 80

        AppBackground { sessionCanvas: true }
        ModalBlocker {}
        LogsView {
            anchors.fill: parent
            backend: djconnect
            confirmationOpen: root.clearLogsConfirmOpen
            findShortcutEnabled: root.active && !root.screenBlanked && !root.splashVisible
            onClearRequested: root.clearLogsConfirmOpen = true
        }
    }

    GlassSheet {
        id: musicDnaOptIn
        objectName: "musicDnaOptInSheet"
        property bool discoveryConsent: false
        property int profileRevision: -1
        readonly property bool currentProfile: profileRevision === djconnect.musicDnaContextRevision
        z: 86
        onClosed: profileRevision = -1

        contentItem: ScrollView {
            id: optInScroll
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: optInScroll.availableWidth
                spacing: 22
                Item { Layout.preferredHeight: musicDnaOptIn.compact ? 0 : 22 }
                AppBanner {
                    Layout.fillWidth: true
                    Layout.maximumWidth: musicDnaOptIn.compact ? optInScroll.width - 40 : 900
                    Layout.alignment: Qt.AlignHCenter
                    detailText: root.tr("tagline")
                }
                IntroductionCard {
                    iconName: "heart"
                    title: root.tr("music_dna_opt_in_title")
                    message: root.tr("music_dna_opt_in_description")
                    compact: musicDnaOptIn.compact
                    centeredMessage: false
                    Layout.maximumWidth: musicDnaOptIn.compact ? optInScroll.width - 40 : 900
                    Layout.alignment: Qt.AlignHCenter

                    Repeater {
                        model: [
                            {icon: "trash", key: "music_dna_opt_in_erase"},
                            {icon: "settings", key: "music_dna_opt_in_turn_off"}
                        ]
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 12
                            FeatureIcon {
                                iconName: modelData.icon
                                iconColor: modelData.icon === "trash" ? BrandTheme.colors.actionDanger : BrandTheme.colors.text
                                Layout.preferredWidth: 22; Layout.preferredHeight: 22
                            }
                            Text {
                                text: root.tr(modelData.key)
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.text
                                font.pixelSize: musicDnaOptIn.compact ? 17 : 20
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }
                    GlassButton {
                        objectName: "musicDnaConfirmEnable"
                        primary: true
                        text: root.tr("music_dna_opt_in_accept")
                        iconName: "discover"
                        Layout.fillWidth: true
                        Layout.preferredHeight: musicDnaOptIn.compact ? 56 : 68
                        enabled: (djconnect.paired || djconnect.demoMode) && !djconnect.musicDnaBusy
                            && !djconnect.musicDiscoveryBusy && musicDnaOptIn.currentProfile
                        onClicked: {
                            if (!musicDnaOptIn.opened || !enabled) return
                            const discoveryConsent = musicDnaOptIn.discoveryConsent
                            const revision = musicDnaOptIn.profileRevision
                            musicDnaOptIn.close()
                            if (discoveryConsent) djconnect.acceptMusicDiscoveryConsent(revision)
                            else djconnect.setMusicDnaEnabled(true, revision)
                        }
                    }
                    GlassButton {
                        objectName: "musicDnaDeclineEnable"
                        foregroundColor: BrandTheme.colors.muted
                        font.pixelSize: musicDnaOptIn.compact ? 18 : 21
                        text: root.tr("music_dna_opt_in_not_now")
                        tertiary: true
                        Layout.fillWidth: true
                        onClicked: {
                            if (musicDnaOptIn.currentProfile && musicDnaOptIn.discoveryConsent)
                                djconnect.rejectMusicDiscoveryConsent()
                            musicDnaOptIn.close()
                        }
                    }
                }
            }
        }
    }

    GlassSheet {
        id: clearLogsConfirmPanel
        objectName: "clearLogsConfirmSheet"
        visible: root.clearLogsConfirmOpen
        z: 86

        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: clearLogsConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: clearLogsConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("clear_logs_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("clear_logs_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    objectName: "confirmClearLogs"
                    text: root.tr("clear_logs")
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.clearLogsConfirmOpen = false
                        djconnect.clearLogs()
                    }
                }

                PurpleButton {
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.clearLogsConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: askDjClearConfirmPanel
        visible: root.askDjClearConfirmOpen
        z: 86

        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: askDjClearConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: askDjClearConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("ask_dj_clear_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("ask_dj_clear_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                LoadingSpinner {
                    visible: djconnect.askDjBusy
                    running: visible
                    Layout.preferredWidth: 42
                    Layout.preferredHeight: 42
                    Layout.alignment: Qt.AlignHCenter
                }

                DangerButton {
                    objectName: "askDjClearConfirmButton"
                    text: root.tr("clear")
                    enabled: !djconnect.askDjBusy && (djconnect.demoMode || (djconnect.paired
                        && djconnect.backendAvailable && !djconnect.versionMismatchVisible))
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: djconnect.clearAskDjHistory(root.askDjClearConfirmationRevision)
                }

                PurpleButton {
                    text: root.tr("cancel")
                    enabled: !djconnect.askDjBusy
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.askDjClearConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: musicDnaClearConfirmPanel
        visible: root.musicDnaClearConfirmOpen
        z: 86

        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: musicDnaClearConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: musicDnaClearConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("music_dna_clear_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("music_dna_clear_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    text: root.tr("music_dna_clear")
                    enabled: djconnect.musicDnaEnabled && !djconnect.musicDnaBusy
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.musicDnaClearConfirmOpen = false
                        djconnect.clearMusicDna(root.musicDnaConfirmationRevision)
                    }
                }

                PurpleButton {
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.musicDnaClearConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: musicDnaDisableConfirmPanel
        visible: root.musicDnaDisableConfirmOpen
        z: 86

        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: musicDnaDisableConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: musicDnaDisableConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("music_dna_disable_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("music_dna_disable_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    text: root.tr("music_dna_disable")
                    enabled: djconnect.musicDnaEnabled && !djconnect.musicDnaBusy
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.musicDnaDisableConfirmOpen = false
                        djconnect.setMusicDnaEnabled(false, root.musicDnaConfirmationRevision)
                    }
                }

                PurpleButton {
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.musicDnaDisableConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: resetPairingConfirmPanel
        objectName: "resetPairingConfirmPanel"
        visible: root.resetPairingConfirmOpen
        z: 84


        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: confirmContent.implicitHeight + 44

            ColumnLayout {
                id: confirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("reset_pairing_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("reset_pairing_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    objectName: "resetPairingConfirmAction"
                    text: root.tr("reset_pairing")
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.resetPairingConfirmOpen = false
                        djconnect.resetPairing()
                        root.activeScreen = "now"
                    }
                }

                PurpleButton {
                    objectName: "resetPairingCancel"
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.resetPairingConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: rebootConfirmPanel
        visible: root.rebootConfirmOpen
        z: 84


        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: rebootConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: rebootConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("reboot_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("reboot_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                WarningButton {
                    text: root.tr("reboot_device")
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.rebootConfirmOpen = false
                        djconnect.rebootDevice()
                    }
                }

                PurpleButton {
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.rebootConfirmOpen = false
                }
            }
        }
    }

    GlassSheet {
        id: shutdownConfirmPanel
        visible: root.shutdownConfirmOpen
        z: 84


        GlassSurface {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            introduction: true
            radius: 32
            border.width: 1

            implicitHeight: shutdownConfirmContent.implicitHeight + 44

            ColumnLayout {
                id: shutdownConfirmContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 22
                spacing: 18

                Text {
                    text: root.tr("shutdown_confirm_title")
                    color: BrandTheme.colors.text
                    font.pixelSize: 28
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: root.tr("shutdown_confirm_message")
                    color: BrandTheme.colors.text
                    font.pixelSize: 24
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                DangerButton {
                    objectName: "confirmSettingsShutdown"
                    text: root.tr("shutdown_device")
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: {
                        root.shutdownConfirmOpen = false
                        djconnect.shutdownDevice()
                    }
                }

                PurpleButton {
                    text: root.tr("cancel")
                    font.pixelSize: 24
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    onClicked: root.shutdownConfirmOpen = false
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: BrandTheme.colors.background
        visible: root.aboutOpen
        z: 22
        onVisibleChanged: {
            if (visible && aboutScroll.contentItem) {
                aboutScroll.contentItem.contentY = 0
            }
        }

        AppBackground { sessionCanvas: true }
        ModalBlocker {}

        PageToolbar {
            id: aboutToolbar
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 22
            title: root.tr("about")
            leading: [ GlassButton { iconName: "back"; tertiary: true; implicitWidth: 52; Accessible.name: root.tr("close"); onClicked: root.aboutOpen = false } ]
        }

        ScrollView {
            id: aboutScroll
            objectName: "aboutScroll"
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.top: aboutToolbar.bottom
            anchors.margins: 24
            anchors.bottomMargin: root.edge + 126
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            ColumnLayout {
                width: Math.max(0, aboutScroll.availableWidth)
                spacing: 18


                AppBanner {
                    detailText: root.tr("tagline")
                }

                GlassCard {
                    Layout.fillWidth: true
                Text {
                    text: root.tr("app_section")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 20
                    font.bold: true
                    Layout.fillWidth: true
                }

                GridLayout {
                    columns: 2
                    columnSpacing: 18
                    rowSpacing: 10
                    Layout.fillWidth: true

                    Text { text: root.tr("version"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text {
                        objectName: "aboutAppVersion"
                        text: djconnect.version
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 20
                        Layout.fillWidth: true
                    }
                    Text { text: root.tr("device_name"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text {
                        objectName: "aboutDeviceName"
                        text: djconnect.deviceName
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text
                        font.pixelSize: 20
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Text { text: root.tr("client_type"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: djconnect.clientType; color: BrandTheme.colors.text; font.pixelSize: 20; Layout.fillWidth: true }
                    Text { text: root.tr("website"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    GlassButton {
                        objectName: "aboutWebsiteLink"
                        text: "https://djconnect.dev"
                        iconName: "globe"
                        Layout.alignment: Qt.AlignLeft
                        onClicked: Qt.openUrlExternally(text)
                    }
                    Text {
                        text: root.tr("updates")
                        color: BrandTheme.colors.muted
                        font.pixelSize: 20; font.bold: true
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 190
                    }
                    GlassButton {
                        objectName: "aboutCheckUpdates"
                        text: djconnect.updateCheckRunning ? root.tr("checking_updates") : root.tr("check_updates")
                        iconName: djconnect.updateCheckRunning ? "" : "refresh"
                        wrapText: true
                        textAlignment: Text.AlignLeft
                        Layout.fillWidth: true
                        Layout.maximumWidth: 390
                        enabled: !djconnect.updateCheckRunning
                        onClicked: djconnect.checkForUpdates()
                    }
                    Text { text: root.tr("web_address"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: djconnect.webPortalUrl; color: BrandTheme.colors.text; font.pixelSize: 18; Layout.fillWidth: true; elide: Text.ElideMiddle }
                    Text { text: root.tr("device_id"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: djconnect.deviceId
                            textFormat: Text.PlainText
                            color: BrandTheme.colors.text
                            font.pixelSize: 18
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                        GlassButton {
                            objectName: "aboutCopyDeviceId"
                            iconName: "copy"; tertiary: true
                            Accessible.name: root.tr("copy_device_id")
                            ToolTip.text: root.tr("copy_device_id")
                            ToolTip.visible: hovered
                            enabled: djconnect.deviceId.length > 0
                            onClicked: djconnect.copyDeviceId()
                        }
                    }
                }
                }

                GlassCard {
                    id: aboutConnectionCard
                    Layout.fillWidth: true
                readonly property bool liveConnectionEligible: !djconnect.demoMode && djconnect.paired
                    && djconnect.backendAvailable && !djconnect.versionMismatchVisible
                readonly property bool hasActiveAddress: djconnect.aboutConnectionOrigin.length > 0
                readonly property string connectionFallback: djconnect.demoMode ? "demo_mode"
                    : !liveConnectionEligible ? "ha_address_inactive" : !hasActiveAddress ? "ha_connection_unconfirmed" : ""
                readonly property string connectionStatus: djconnect.demoMode ? "demo_mode"
                    : !djconnect.paired ? "not_connected_value"
                    : !djconnect.haUrl.trim().length ? "session_connection_address_missing"
                    : djconnect.versionMismatchVisible ? "version_mismatch_title"
                    : !djconnect.backendAvailable ? "ha_connection_unavailable"
                    : hasActiveAddress ? "connected" : "ha_connection_unconfirmed"
                Text {
                    text: root.tr("connection_section")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 20
                    font.bold: true
                    Layout.fillWidth: true
                }

                GridLayout {
                    columns: 2
                    columnSpacing: 18
                    rowSpacing: 10
                    Layout.fillWidth: true

                    Text { text: root.tr("home_assistant"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: djconnect.paired ? root.tr("paired") : root.tr("not_paired"); color: BrandTheme.colors.text; font.pixelSize: 20; Layout.fillWidth: true }
                    Text { text: root.tr("ha_connection_status"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190; wrapMode: Text.WordWrap }
                    Text {
                        objectName: "aboutHaConnectionStatus"
                        readonly property string status: aboutConnectionCard.connectionStatus
                        text: root.tr(status)
                        textFormat: Text.PlainText
                        color: status === "connected" ? BrandTheme.colors.success
                            : status === "ha_connection_unavailable" ? BrandTheme.colors.danger : BrandTheme.colors.muted
                        font.pixelSize: 20; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Text { text: root.tr("connection_type"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text {
                        objectName: "aboutHaConnectionType"
                        text: root.tr(aboutConnectionCard.connectionFallback || "local_only")
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text; font.pixelSize: 20; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Text { text: root.tr("transport"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text {
                        objectName: "aboutHaConnectionTransport"
                        text: root.tr(aboutConnectionCard.connectionFallback || djconnect.aboutConnectionTransport)
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text; font.pixelSize: 20; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Text { text: root.tr("music_backend"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text {
                        objectName: "aboutMusicBackendStatus"
                        readonly property string availability: djconnect.aboutMusicAvailability
                        text: root.tr("music_backend_" + availability + "_state")
                        color: availability === "available" ? BrandTheme.colors.success
                            : availability === "unavailable" ? BrandTheme.colors.danger : BrandTheme.colors.muted
                        font.pixelSize: 20; font.bold: true; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Text { text: root.tr("backend_name"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: djconnect.musicBackendName.length ? djconnect.musicBackendName : "-"; color: BrandTheme.colors.text; font.pixelSize: 18; Layout.fillWidth: true; elide: Text.ElideMiddle }
                    Text { text: root.tr("backend_error"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190; visible: djconnect.musicBackendError.length > 0 }
                    Text { text: djconnect.musicBackendError; color: BrandTheme.colors.danger; font.pixelSize: 18; Layout.fillWidth: true; wrapMode: Text.Wrap; visible: djconnect.musicBackendError.length > 0 }
                    Text { text: root.tr("client_api_url_label"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: djconnect.localApiUrl; color: BrandTheme.colors.text; font.pixelSize: 18; Layout.fillWidth: true; elide: Text.ElideMiddle }
                    Text { text: root.tr("active_ha_address"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190; wrapMode: Text.WordWrap }
                    Text {
                        objectName: "aboutActiveHaAddress"
                        text: djconnect.aboutConnectionOrigin || root.tr(aboutConnectionCard.liveConnectionEligible
                            ? "ha_address_unconfirmed" : "ha_address_inactive")
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.text; font.pixelSize: 18; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                }
                }

                GlassCard {
                    Layout.fillWidth: true
                Text {
                    text: root.tr("notices_section")
                    color: BrandTheme.colors.muted
                    font.pixelSize: 20
                    font.bold: true
                    Layout.fillWidth: true
                }

                GridLayout {
                    columns: 2
                    columnSpacing: 18
                    rowSpacing: 10
                    Layout.fillWidth: true

                    Text { text: root.tr("copyright"); color: BrandTheme.colors.muted; font.pixelSize: 20; font.bold: true; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 190 }
                    Text { text: "2026 Peter van Tol"; color: BrandTheme.colors.text; font.pixelSize: 20; Layout.fillWidth: true }
                    Text {
                        text: root.tr("about_source_repository")
                        color: BrandTheme.colors.muted
                        font.pixelSize: 20; font.bold: true
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 190
                    }
                    GlassButton {
                        objectName: "aboutSourceRepository"
                        text: "djconnect-music-dj/djconnect"
                        iconName: "globe"
                        wrapText: true
                        textAlignment: Text.AlignLeft
                        Layout.fillWidth: true
                        Layout.maximumWidth: 390
                        onClicked: Qt.openUrlExternally("https://github.com/djconnect-music-dj/djconnect")
                    }
                }
                }
            }
        }
    }

    Timer {
        id: trackArtworkDismissTimer
        interval: 5000
        onTriggered: root.trackArtworkOpen = false
    }

    Loader {
        anchors.fill: parent
        z: 198
        active: root.trackArtworkOpen && root.trackArtworkViewOpen
        focus: active
        onLoaded: item.focusOverlay()
        sourceComponent: FocusScope {
            id: artworkOverlay
            objectName: "trackArtworkOverlay"
            readonly property bool playbackButtonsEnabled: !!sharedSessionScreen.state.playback_control.enabled
            onPlaybackButtonsEnabledChanged: {
                if (!playbackButtonsEnabled) Qt.callLater(restoreControlFocus)
            }
            function focusOverlay() {
                if (root.trackArtworkOpeningControl) closeArtworkButton.forceActiveFocus(Qt.PopupFocusReason)
                else forceActiveFocus()
            }
            function restoreControlFocus() {
                if (!root.trackArtworkOpen || !visible || activeControls().includes(root.activeFocusItem)) return
                const fallback = artworkControlsToggle.enabled ? artworkControlsToggle : closeArtworkButton
                fallback.forceActiveFocus(Qt.PopupFocusReason)
            }
            Connections {
                target: root
                function onTrackArtworkCanControlChanged() {
                    if (!root.trackArtworkCanControl) Qt.callLater(artworkOverlay.restoreControlFocus)
                }
            }
            function activeControls() {
                const playback = artworkTransportLoader.item
                return [closeArtworkButton, artworkControlsToggle]
                    .concat(playback ? playback.artworkFocusOrder : [], [expandedSource.spotifyFocusTarget])
                    .filter(control => control.visible && control.enabled && control.activeFocusOnTab)
            }
            function moveControlFocus(backward) {
                const targets = activeControls()
                const index = targets.indexOf(root.activeFocusItem)
                const next = index < 0 ? (backward ? targets.length - 1 : 0)
                    : (index + (backward ? -1 : 1) + targets.length) % targets.length
                if (targets[next]) targets[next].forceActiveFocus(backward ? Qt.BacktabFocusReason : Qt.TabFocusReason)
            }
            Shortcut { sequence: "Tab"; onActivated: moveControlFocus(false) }
            Shortcut { sequence: "Shift+Tab"; onActivated: moveControlFocus(true) }
            Keys.onEscapePressed: {
                if (root.trackArtworkControlsOpen) {
                    root.toggleTrackArtworkControls()
                    artworkControlsToggle.forceActiveFocus(Qt.PopupFocusReason)
                } else {
                    root.trackArtworkOpen = false
                }
            }

            Rectangle {
                anchors.fill: parent
                color: BrandTheme.alpha(BrandTheme.colors.background, "ee")
            }
            ModalBlocker {
                onClicked: function(mouse) {
                    if (mouse.button === Qt.LeftButton && root.trackArtworkControlsOpen)
                        root.toggleTrackArtworkControls()
                }
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    GlassButton {
                        id: closeArtworkButton
                        objectName: "closeTrackArtwork"
                        iconName: "close"
                        Accessible.name: root.tr("close")
                        onClicked: root.trackArtworkOpen = false
                    }
                }

                Item {
                    id: artworkArea
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Column {
                        id: artworkColumn
                        anchors.centerIn: parent
                        width: parent.width
                        spacing: 12

                        GlassSurface {
                            id: expandedArtworkCard
                            readonly property int artworkInset: 10
                            readonly property real artworkRadius: radius - artworkInset
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: Math.max(0, Math.min(artworkArea.width,
                                artworkArea.height - expandedTitle.height - artworkColumn.spacing
                                - (expandedCredits.visible ? expandedCredits.height + artworkColumn.spacing : 0)))
                            height: width
                            radius: 28
                            elevated: true

                            VinylPlaceholder {
                                anchors.fill: parent
                                anchors.margins: expandedArtworkCard.artworkInset
                                radius: expandedArtworkCard.artworkRadius
                                visible: expandedArt.status !== Image.Ready
                            }
                            Image {
                                id: expandedArt
                                objectName: "expandedTrackArtwork"
                                anchors.fill: parent
                                anchors.margins: expandedArtworkCard.artworkInset
                                source: root.sessionTrackArtwork ? sharedSessionScreen.trackArtworkSource : djconnect.imageUrl
                                sourceSize.width: width
                                sourceSize.height: height
                                asynchronous: true
                                retainWhileLoading: false
                                fillMode: Image.PreserveAspectCrop
                                opacity: 0
                                visible: status === Image.Ready
                            }
                            Canvas {
                                id: roundedArtwork
                                anchors.fill: expandedArt
                                visible: expandedArt.visible
                                property url artworkSource: expandedArt.source
                                property url loadedSource
                                function loadArtwork() {
                                    if (!available) return
                                    if (loadedSource.toString().length) unloadImage(loadedSource)
                                    loadedSource = artworkSource
                                    if (loadedSource.toString().length) loadImage(loadedSource, Qt.size(width, height))
                                    requestPaint()
                                }
                                onAvailableChanged: loadArtwork()
                                onArtworkSourceChanged: loadArtwork()
                                onImageLoaded: requestPaint()
                                onVisibleChanged: if (visible) requestPaint()
                                onPaint: {
                                    const ctx = getContext("2d")
                                    ctx.clearRect(0, 0, width, height)
                                    if (!expandedArt.visible || !isImageLoaded(loadedSource)) return
                                    const radius = Math.min(expandedArtworkCard.artworkRadius, width / 2, height / 2)
                                    ctx.save()
                                    ctx.beginPath()
                                    ctx.roundedRect(0, 0, width, height, radius, radius)
                                    ctx.clip()
                                    const artWidth = expandedArt.paintedWidth
                                    const artHeight = expandedArt.paintedHeight
                                    ctx.drawImage(loadedSource, (width - artWidth) / 2, (height - artHeight) / 2,
                                                  artWidth, artHeight)
                                    ctx.restore()
                                }
                            }
                            Rectangle {
                                objectName: "trackArtworkControlsShade"
                                anchors.fill: parent
                                anchors.margins: expandedArtworkCard.artworkInset
                                radius: expandedArtworkCard.artworkRadius
                                visible: root.trackArtworkCanControl && root.trackArtworkControlsOpen
                                color: "#80000000"
                            }
                            Button {
                                id: artworkControlsToggle
                                objectName: "trackArtworkControlsToggle"
                                anchors.fill: parent
                                enabled: root.trackArtworkCanControl
                                Accessible.name: root.tr("session_playback_controls")
                                Accessible.checkable: true
                                Accessible.checked: root.trackArtworkControlsOpen
                                contentItem: Item {}
                                background: Rectangle {
                                    color: "transparent"
                                    radius: expandedArtworkCard.radius
                                    border.width: artworkControlsToggle.visualFocus ? 2 : 0
                                    border.color: BrandTheme.colors.focus
                                }
                                onClicked: root.toggleTrackArtworkControls()
                            }
                            Loader {
                                id: artworkTransportLoader
                                anchors.centerIn: parent
                                width: parent.width - 20
                                height: parent.height - 20
                                active: root.trackArtworkCanControl && root.trackArtworkControlsOpen
                                sourceComponent: sharedSessionScreen.artworkTransportControls
                                onLoaded: item.refreshFocusFallback = artworkControlsToggle
                            }
                        }

                        Text {
                            id: expandedTitle
                            objectName: "expandedTrackTitle"
                            width: parent.width
                            text: root.sessionTrackArtwork
                                ? (sharedSessionScreen.state.playback.title || "").trim() || root.tr("session_track_title_unknown")
                                : djconnect.title
                            textFormat: Text.PlainText
                            Accessible.role: Accessible.Heading
                            Accessible.name: text
                            color: BrandTheme.colors.text
                            font.pixelSize: root.wallProfile ? 42 : 32
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }

                        RowLayout {
                            id: expandedCredits
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: expandedArtist.visible ? parent.width : expandedSource.implicitWidth
                            visible: expandedArtist.text.length > 0 || expandedSource.visible
                            Item {
                                visible: expandedArtist.visible && expandedSource.visible
                                Layout.preferredWidth: expandedSource.implicitWidth
                            }
                            Text {
                                id: expandedArtist
                                objectName: "expandedTrackArtist"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                visible: text.length > 0
                                text: root.sessionTrackArtwork ? sharedSessionScreen.state.playback.artist || "" : djconnect.artist
                                textFormat: Text.PlainText
                                color: BrandTheme.colors.muted
                                font.pixelSize: root.wallProfile ? 30 : 24
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.Wrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                            }
                            SessionAttribution {
                                id: expandedSource
                                objectName: "expandedTrackSource"
                                visible: requiresSpotify && ready
                                requiresSpotify: root.sessionTrackArtwork && !!sharedSessionScreen.state.playback.requires_spotify_attribution
                                spotifyURL: requiresSpotify ? sharedSessionScreen.state.playback.source_url || "" : ""
                            }
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        id: djResponseOverlay
        anchors.centerIn: parent
        width: Math.min(parent.width - 52, 620)
        height: Math.min(parent.height - 120, Math.max(260, djResponseText.implicitHeight + 112))
        radius: 8
        color: BrandTheme.alpha(BrandTheme.colors.surface, "cc")
        border.color: BrandTheme.alpha(BrandTheme.colors.muted, "80")
        border.width: 1
        visible: opacity > 0
        opacity: djconnect.djResponseVisible && !root.splashVisible ? 1 : 0
        z: 190

        Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        ModalBlocker {}

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 16

            AppBanner {
                Layout.fillWidth: true
                detailText: root.tr("dj_response")
                dense: true
            }

            Text {
                id: djResponseText
                text: djconnect.djResponseText
                color: BrandTheme.colors.text
                font.pixelSize: 34
                font.bold: true
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                style: Text.Raised
                styleColor: "#aa000000"
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
        }

        TapHandler {
            onTapped: djconnect.clearDjResponse()
        }
    }

    Component.onCompleted: djconnect.edgeBackGesture.observe(root)
    onEdgeBackRouteChanged: djconnect.edgeBackGesture.cancel()
    Connections {
        target: djconnect.edgeBackGesture
        function onBackRequested(route) { root.navigateEdgeBack(route) }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        propagateComposedEvents: true
        z: 199
        onPressed: function(mouse) {
            if (root.screenBlanked) {
                mouse.accepted = true
                return
            }
            root.recordActivity()
            mouse.accepted = false
        }
        onClicked: function(mouse) {
            if (root.screenBlanked) {
                root.wakeDisplay()
                if (mouse.button === Qt.LeftButton)
                    root.showTrackArtwork(sharedSessionScreen.trackArtworkAvailable, true)
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "#000000"
        opacity: root.screenBlanked ? 1 : root.brightnessOverlayOpacity
        visible: opacity > 0
        z: 200

        Behavior on opacity { NumberAnimation { duration: 450 } }
    }
}
