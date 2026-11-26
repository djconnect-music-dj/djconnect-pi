import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

Item {
    id: view
    objectName: "logsView"
    required property var backend
    property bool confirmationOpen: false
    property bool findShortcutEnabled: true
    signal clearRequested()

    readonly property bool compact: width < 900
    readonly property int inset: compact ? 14 : 26
    readonly property var lines: backend.logsText ? backend.logsText.split("\n") : []
    property bool searching: false
    property bool keyboardOpen: false
    property int matchIndex: -1
    property string matchQuery: ""
    readonly property string query: searchInput.text.trim()
    readonly property var searchMatches: findMatches(lines, query)
    readonly property var matches: searchMatches.ordered
    readonly property var currentMatch: matches[matchIndex] || null

    function tr(key) { backend.translationVersion; return backend.t(key) }
    function foldAccents(text) {
        const decomposed = text.normalize("NFD")
        const plain = decomposed.replace(/[\u0300-\u036f]/g, "")
        return plain === decomposed ? text : plain.normalize("NFC")
    }
    function searchableLine(line) {
        if (foldAccents(line) === line) return {text: line, offsets: null}
        // Keep offsets into the original text, including combining accents.
        const clusters = /[\s\S][\u0300-\u036f]*/gu
        const offsets = []
        var text = ""
        var cluster
        while ((cluster = clusters.exec(line)) !== null) {
            const folded = foldAccents(cluster[0])
            for (var offset = 0; offset < folded.length; offset++)
                offsets.push({start: cluster.index, end: clusters.lastIndex})
            text += folded
        }
        return {text: text, offsets: offsets}
    }
    function findMatches(rows, query) {
        const result = {ordered: [], byRow: {}}
        const needle = searchableLine(query).text
        if (!needle) return result
        const pattern = new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "giu")
        for (var row = 0; row < rows.length; row++) {
            const line = searchableLine(rows[row])
            pattern.lastIndex = 0
            var hit
            while ((hit = pattern.exec(line.text)) !== null) {
                const end = pattern.lastIndex
                const match = {row: row,
                    start: line.offsets ? line.offsets[hit.index].start : hit.index,
                    end: line.offsets ? line.offsets[end - 1].end : end}
                result.ordered.push(match)
                if (!result.byRow[row]) result.byRow[row] = []
                result.byRow[row].push(match)
            }
        }
        return result
    }
    function escapeText(value) {
        return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    }
    function lineText(row, text) {
        const rowMatches = searchMatches.byRow[row]
        if (!rowMatches) return escapeText(text)
        const selected = currentMatch
        var styled = ""
        var cursor = 0
        for (const hit of rowMatches) {
            const active = selected && selected.row === row && selected.start === hit.start
            const color = active ? BrandTheme.colors.actionAccent : BrandTheme.colors.warning
            const highlight = '<font color="' + color + '">' + escapeText(text.slice(hit.start, hit.end)) + '</font>'
            styled += escapeText(text.slice(cursor, hit.start)) + (active ? '<b>' + highlight + '</b>' : highlight)
            cursor = hit.end
        }
        return styled + escapeText(text.slice(cursor))
    }
    function moveMatch(step) {
        if (!matches.length) return false
        matchIndex = (matchIndex + step + matches.length) % matches.length
        positionCurrentMatch()
        return true
    }
    function positionCurrentMatch() {
        if (visible && currentMatch) logRows.positionViewAtIndex(currentMatch.row, ListView.Contain)
    }
    function showSearch(showKeyboard) {
        searching = true
        keyboardOpen = showKeyboard
        searchInput.forceActiveFocus()
    }
    function closeSearch() {
        const restoreFocus = searching && visible && !confirmationOpen
        keyboardOpen = false
        searching = false
        searchInput.clear()
        if (restoreFocus && searchToggle.enabled) searchToggle.forceActiveFocus()
    }
    function submitSearch() {
        keyboardOpen = false
        positionCurrentMatch()
    }
    onMatchesChanged: {
        const previousIndex = matchQuery === query ? Math.max(0, matchIndex) : 0
        matchQuery = query
        matchIndex = Math.min(previousIndex, matches.length - 1)
        Qt.callLater(positionCurrentMatch)
    }
    onVisibleChanged: if (!visible) closeSearch()
    onLinesChanged: if (!lines.length && searching) closeSearch()

    Shortcut {
        sequences: [StandardKey.Find]
        enabled: view.visible && view.findShortcutEnabled && !view.confirmationOpen && searchToggle.enabled
        onActivated: view.showSearch(false)
    }

    component LogAction: GlassButton {
        implicitWidth: 48; implicitHeight: 48; padding: 9
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            Layout.leftMargin: view.inset; Layout.rightMargin: view.inset
            Layout.topMargin: view.inset
            LogAction {
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                iconName: "back"; Accessible.name: view.tr("close")
                onClicked: view.backend.hideLogs()
            }
            Text {
                anchors.centerIn: parent
                text: view.tr("logs"); font.pixelSize: view.compact ? 24 : 29
                font.bold: true; color: BrandTheme.colors.text
            }
            GlassSurface {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                width: tools.implicitWidth + 12; height: 54; radius: height / 2
                RowLayout {
                    id: tools
                    anchors.centerIn: parent
                    spacing: 0
                    LogAction {
                        id: searchToggle
                        objectName: "logsSearchOpen"
                        iconName: "search"; Accessible.name: view.tr(view.searching ? "close_search" : "logs_search")
                        checked: view.searching
                        enabled: view.lines.length > 0
                        background: Item {}
                        foregroundColor: checked ? BrandTheme.colors.actionAccent : BrandTheme.colors.text
                        onClicked: view.searching ? view.closeSearch() : view.showSearch(true)
                    }
                    LogAction {
                        objectName: "logsRefresh"
                        iconName: "refresh"; Accessible.name: view.tr("refresh")
                        background: Item {}
                        onClicked: { view.backend.showLogs(); if (!view.currentMatch) logRows.positionViewAtBeginning() }
                    }
                    LogAction {
                        objectName: "logsCopy"
                        iconName: "copy"; Accessible.name: view.tr("copy_logs")
                        enabled: view.lines.length > 0; background: Item {}
                        onClicked: view.backend.copyLogs()
                    }
                    LogAction {
                        objectName: "logsClear"
                        iconName: "trash"; Accessible.name: view.tr("clear_logs")
                        tint: BrandTheme.colors.actionDanger; background: Item {}
                        onClicked: view.clearRequested()
                    }
                }
            }
        }
        GlassCard {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
            Layout.leftMargin: view.inset; Layout.rightMargin: view.inset
            padding: view.compact ? 12 : 20
            spacing: 10

            Control {
                visible: view.searching
                Layout.fillWidth: true
                padding: 4
                background: GlassSurface {
                    radius: 20; interactive: true; focused: searchInput.activeFocus
                }
                contentItem: RowLayout {
                    spacing: 2
                    FeatureIcon { iconName: "search"; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                    TextField {
                        id: searchInput
                        objectName: "logsSearchInput"
                        Layout.fillWidth: true
                        Layout.minimumHeight: 48
                        background: Item {}
                        placeholderText: view.tr("logs_search")
                        Accessible.name: placeholderText
                        font.pixelSize: view.compact ? 18 : 21
                        maximumLength: 120
                        selectByMouse: true
                        TapHandler { onTapped: view.keyboardOpen = true }
                        Keys.onEscapePressed: if (view.keyboardOpen) view.submitSearch(); else view.closeSearch()
                        Keys.onUpPressed: function(event) { event.accepted = view.moveMatch(-1) }
                        Keys.onDownPressed: function(event) { event.accepted = view.moveMatch(1) }
                        onAccepted: {
                            if (view.keyboardOpen) view.submitSearch()
                            else view.moveMatch(1)
                        }
                    }
                    Text {
                        objectName: "logsSearchMatchCount"
                        visible: view.query.length > 0
                        text: view.matches.length ? (view.matchIndex + 1) + "/" + view.matches.length : "0"
                        Layout.minimumWidth: 44
                        horizontalAlignment: Text.AlignRight
                        Accessible.role: Accessible.StaticText
                        Accessible.name: view.matches.length ? text : view.tr("logs_no_matches")
                        color: BrandTheme.colors.muted; font.pixelSize: 15
                    }
                    LogAction {
                        objectName: "logsPreviousMatch"
                        iconName: "arrow_up"; Accessible.name: view.tr("logs_previous_match")
                        enabled: view.matches.length > 0
                        background: Item {}
                        onClicked: view.moveMatch(-1)
                    }
                    LogAction {
                        objectName: "logsNextMatch"
                        iconName: "arrow_down"; Accessible.name: view.tr("logs_next_match")
                        enabled: view.matches.length > 0
                        background: Item {}
                        onClicked: view.moveMatch(1)
                    }
                    LogAction {
                        objectName: "logsSearchClose"
                        iconName: "close"; Accessible.name: view.tr("close_search")
                        background: Item {}
                        onClicked: view.closeSearch()
                    }
                }
            }
            ListView {
                id: logRows
                objectName: "logRows"
                visible: view.lines.length > 0
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
                model: view.lines
                clip: true
                spacing: 3
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    id: entry
                    required property int index
                    required property string modelData
                    width: logRows.width - 10
                    spacing: 12
                    Text {
                        text: entry.index + 1
                        font.family: "monospace"; font.pixelSize: view.compact ? 16 : 20
                        color: BrandTheme.colors.disabledText
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 46; Layout.alignment: Qt.AlignTop
                    }
                    Text {
                        text: view.lineText(entry.index, entry.modelData)
                        textFormat: Text.StyledText
                        font.family: "monospace"; font.pixelSize: view.compact ? 16 : 20
                        color: BrandTheme.colors.text
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
            Item {
                visible: view.lines.length === 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.centerIn: parent
                    width: Math.min(parent.width, 420)
                    spacing: 12
                    FeatureIcon {
                        iconName: "logs"
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                    }
                    Text {
                        objectName: "logsEmptyMessage"
                        text: view.tr("logs_empty")
                        color: BrandTheme.colors.muted
                        font.pixelSize: view.compact ? 19 : 23
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }
        Text {
            visible: !view.keyboardOpen
            Layout.fillWidth: true
            Layout.leftMargin: view.inset; Layout.rightMargin: view.inset
            Layout.bottomMargin: view.inset
            text: view.tr("log") + ": " + view.backend.logFile
            textFormat: Text.PlainText
            elide: Text.ElideMiddle
            font.pixelSize: 15
            color: BrandTheme.colors.muted
        }
        SearchKeyboardDock {
            objectName: "logsSearchDock"
            keyboardName: "logsTouchKeyboard"
            visible: view.keyboardOpen && !view.confirmationOpen
            Layout.fillWidth: true
            backend: view.backend
            target: searchInput
            onSubmitted: view.submitSearch()
            onDismissed: view.keyboardOpen = false
        }
    }
}
