import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

RowLayout {
    id: selection
    required property var translate
    property var preview: ({})
    property string previewObjectName: ""
    signal cleared()

    ColumnLayout {
        Layout.fillWidth: true
        Text {
            text: selection.translate("session_question_selected")
            color: BrandTheme.colors.muted
            font.pixelSize: 18
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            Layout.fillWidth: true
        }
        Text {
            objectName: selection.previewObjectName
            visible: text.length > 0
            text: sources.ready ? selection.preview.text || "" : ""
            color: BrandTheme.colors.text
            font.pixelSize: 18
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
            Layout.fillWidth: true
        }
        ScrollView {
            visible: !!selection.preview.text && (sources.requiresSpotify || sources.sources.length > 0)
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(72, sources.implicitHeight)
            contentWidth: availableWidth
            clip: true
            SessionAttribution {
                id: sources
                sources: selection.preview.sources || []
                requiresSpotify: selection.preview.requires_spotify_attribution || false
                spotifyURL: selection.preview.spotify_source_url || ""
            }
        }
    }
    GlassButton {
        objectName: "askDjClearAnswerContext"
        iconName: "end"
        tertiary: true
        Accessible.name: selection.translate("session_clear_reference")
        ToolTip.text: Accessible.name
        ToolTip.visible: hovered
        onClicked: selection.cleared()
    }
}
