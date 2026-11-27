import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

Button {
    id: control
    property string iconName: ""
    property string outputType: ""
    property bool busy: false
    property int textAlignment: Text.AlignHCenter
    property bool wrapText: false
    property real cornerRadius: height / 2
    property bool primary: false
    property color tint: BrandTheme.colors.actionAccent
    property bool destructive: tint === BrandTheme.colors.danger || tint === BrandTheme.colors.actionDanger
    property bool tertiary: false
    property color selectionFill: "transparent"
    property color foregroundColor: destructive ? BrandTheme.colors.actionDanger : BrandTheme.colors.text
    scale: down && enabled && GlassStyle.motionEnabled ? GlassStyle.pressedScale : 1
    Behavior on scale {
        NumberAnimation { duration: GlassStyle.motionEnabled ? GlassStyle.pressDuration : 0; easing.type: Easing.OutCubic }
    }
    opacity: enabled ? 1 : 0.4
    implicitHeight: 56
    implicitWidth: Math.max(56, contentItem.implicitWidth + 36)
    padding: 14
    font.pixelSize: 20
    font.bold: true
    Accessible.name: text
    background: GlassSurface {
        radius: control.cornerRadius
        interactive: true
        primary: control.primary
        selected: control.checked
        destructive: control.destructive
        tertiary: control.tertiary
        pressed: control.down
        focused: control.activeFocus
        tint: control.tint
        selectionFill: control.selectionFill
    }
    contentItem: RowLayout {
        spacing: 0
        Item { visible: control.primary; Layout.fillWidth: true }
        FeatureIcon {
            visible: !control.busy && control.iconName.length > 0
            iconName: control.iconName
            outputType: control.outputType
            iconColor: control.foregroundColor
            Layout.preferredWidth: 26
            Layout.preferredHeight: 26
            Layout.alignment: Qt.AlignHCenter
        }
        Loader {
            active: control.busy && control.visible
            visible: active
            Layout.preferredWidth: 26
            Layout.preferredHeight: 26
            Layout.alignment: Qt.AlignHCenter
            sourceComponent: BusyIndicator {
                objectName: "buttonBusyIndicator"
                running: true
            }
        }
        Text {
            visible: control.text.length > 0
            text: control.text
            font: control.font
            color: control.foregroundColor
            textFormat: Text.PlainText
            horizontalAlignment: control.textAlignment
            verticalAlignment: Text.AlignVCenter
            wrapMode: control.wrapText ? Text.Wrap : Text.NoWrap
            elide: control.wrapText ? Text.ElideNone : Text.ElideRight
            Layout.leftMargin: control.busy || control.iconName.length > 0 ? 10 : 0
            Layout.fillWidth: !control.primary
            Layout.minimumWidth: 0
        }
        Item { visible: control.primary; Layout.fillWidth: true }
    }
}
