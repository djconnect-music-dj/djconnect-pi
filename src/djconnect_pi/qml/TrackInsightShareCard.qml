import QtQuick
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

Rectangle {
    id: card
    required property bool story
    required property string trackTitle
    required property string artist
    required property string summary
    required property string artworkUrl
    required property string badge
    required property string creditLabel
    readonly property real contentPadding: width * 0.07
    radius: width * 0.06
    clip: true
    gradient: Gradient {
        GradientStop { position: 0; color: BrandTheme.colors.backgroundStart }
        GradientStop { position: 0.5; color: BrandTheme.colors.backgroundMid }
        GradientStop { position: 1; color: BrandTheme.colors.opaqueGlass }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: card.contentPadding
        spacing: card.width * 0.033

        Text {
            text: card.badge
            textFormat: Text.PlainText
            color: BrandTheme.colors.cyan
            font.bold: true; font.pixelSize: Math.max(12, card.width * 0.047)
            Layout.fillWidth: true
        }
        Image {
            id: art
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: card.width * (card.story ? 0.45 : 0.32)
            Layout.preferredHeight: Layout.preferredWidth
            source: card.artworkUrl
            asynchronous: true
            fillMode: Image.PreserveAspectCrop

            FeatureIcon {
                visible: art.status !== Image.Ready
                anchors.centerIn: parent
                width: parent.width * 0.7; height: width
                iconName: "trackInsight"; iconColor: BrandTheme.colors.purple
            }
        }
        Text {
            text: card.trackTitle
            textFormat: Text.PlainText; color: BrandTheme.colors.text
            font.bold: true; font.pixelSize: Math.max(16, card.width * 0.085)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
            Layout.fillWidth: true
        }
        Text {
            text: card.artist
            visible: text.length > 0
            textFormat: Text.PlainText; color: BrandTheme.colors.muted
            font.pixelSize: Math.max(12, card.width * 0.056)
            horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
            Layout.fillWidth: true
        }
        Text {
            text: card.summary
            textFormat: Text.PlainText; color: BrandTheme.colors.text
            font.pixelSize: Math.max(12, card.width * 0.05)
            verticalAlignment: Text.AlignTop
            wrapMode: Text.Wrap; maximumLineCount: card.story ? 8 : 3; elide: Text.ElideRight
            Layout.fillWidth: true; Layout.fillHeight: true
        }
        Text {
            text: card.creditLabel
            visible: text.length > 0
            textFormat: Text.PlainText; color: BrandTheme.colors.muted
            font.pixelSize: Math.max(10, card.width * 0.043)
            Layout.fillWidth: true
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
    }
}
