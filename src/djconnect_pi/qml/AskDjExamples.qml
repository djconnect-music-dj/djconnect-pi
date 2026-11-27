import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

GlassSurface {
    id: panel
    required property var backend
    property bool canChoose: false
    property bool expanded: false
    signal refreshRequested()
    signal closeRequested()
    signal exampleChosen(string entryId)
    signal suggestionChosen(string entryId)
    function tr(key) { backend.translationVersion; return backend.t(key) }
    onVisibleChanged: if (!visible) expanded = false
    onExpandedChanged: if (answers && answers.contentItem) answers.contentItem.contentY = answers.contentItem.originY
    radius: 22; elevated: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        RowLayout {
            Layout.fillWidth: true
            GlassButton {
                objectName: "askDjHelpBackToSuggestions"
                visible: panel.expanded
                iconName: "back"; tertiary: true
                Accessible.name: panel.tr("ask_dj_suggestions")
                onClicked: panel.expanded = false
            }
            Text {
                text: panel.tr(panel.expanded ? "ask_dj_example_questions" : "ask_dj_suggestions")
                color: BrandTheme.colors.text
                font.bold: true
                font.pixelSize: 19
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            GlassButton {
                iconName: "refresh"; tertiary: true
                Accessible.name: panel.tr("refresh")
                enabled: !panel.backend.askDjHelpBusy
                onClicked: panel.refreshRequested()
            }
            GlassButton {
                iconName: "close"; tertiary: true
                Accessible.name: panel.tr("close")
                onClicked: panel.closeRequested()
            }
        }
        Text {
            objectName: "askDjHelpStatus"
            visible: panel.backend.askDjHelpBusy
                || (panel.expanded ? panel.backend.askDjHelpSections.length === 0 : panel.backend.askDjSuggestions.length === 0)
            text: panel.tr(panel.backend.askDjHelpBusy ? "loading" : "ask_dj_examples_unavailable")
            color: BrandTheme.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ScrollView {
            id: answers
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: parent.width
                Repeater {
                    model: panel.visible && !panel.expanded ? panel.backend.askDjSuggestions : []
                    delegate: GlassButton {
                        required property var modelData
                        objectName: "askDjSuggestion"
                        text: modelData.text
                        tertiary: true
                        wrapText: true
                        textAlignment: Text.AlignLeft
                        font.bold: false
                        font.pixelSize: 16
                        cornerRadius: 14
                        Layout.fillWidth: true
                        enabled: !panel.backend.askDjHelpBusy && panel.canChoose
                        onClicked: panel.suggestionChosen(modelData.id)
                    }
                }
                Repeater {
                    model: panel.visible && panel.expanded ? panel.backend.askDjHelpSections : []
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text {
                            text: modelData.title
                            color: BrandTheme.colors.text
                            font.bold: true
                            font.pixelSize: 17
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: modelData.examples
                            delegate: GlassButton {
                                required property var modelData
                                objectName: "askDjHelpExample"
                                text: modelData.text
                                iconName: modelData.placeholder ? "" : "send"
                                tertiary: true
                                wrapText: true
                                textAlignment: Text.AlignLeft
                                font.bold: false
                                font.pixelSize: 16
                                cornerRadius: 14
                                Layout.fillWidth: true
                                enabled: !panel.backend.askDjHelpBusy && panel.canChoose
                                onClicked: panel.exampleChosen(modelData.id)
                            }
                        }
                    }
                }
            }
        }
        GlassButton {
            objectName: "askDjHelpMoreExamples"
            visible: !panel.expanded
            text: panel.tr("more")
            tertiary: true
            Layout.alignment: Qt.AlignRight
            onClicked: panel.expanded = true
        }
    }
}
