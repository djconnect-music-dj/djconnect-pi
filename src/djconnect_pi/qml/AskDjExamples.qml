import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

GlassSurface {
    id: panel
    required property var backend
    property bool canChoose: false
    signal refreshRequested()
    signal closeRequested()
    signal exampleChosen(string entryId)
    function tr(key) { backend.translationVersion; return backend.t(key) }
    radius: 22; elevated: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: panel.tr("ask_dj_example_questions")
                color: BrandTheme.colors.text
                font.bold: true
                font.pixelSize: 19
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
            visible: panel.backend.askDjHelpBusy || panel.backend.askDjHelpSections.length === 0
            text: panel.tr(panel.backend.askDjHelpBusy ? "loading" : "ask_dj_examples_unavailable")
            color: BrandTheme.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: parent.width
                Repeater {
                    model: panel.visible ? panel.backend.askDjHelpSections : []
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
    }
}
