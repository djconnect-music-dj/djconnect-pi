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
    required property string genre
    required property string vibe
    required property var meters
    property bool animate: false
    property real motionPhase: 0
    readonly property real contentPadding: width * 0.07
    radius: width * 0.06
    clip: true
    gradient: Gradient {
        GradientStop { position: 0; color: BrandTheme.colors.backgroundStart }
        GradientStop { position: 0.5; color: BrandTheme.colors.backgroundMid }
        GradientStop { position: 1; color: BrandTheme.colors.opaqueGlass }
    }

    NumberAnimation {
        target: card
        property: "motionPhase"
        from: 0; to: 1; duration: 6000
        loops: Animation.Infinite
        running: card.animate
    }
    onAnimateChanged: if (!animate) motionPhase = 0

    Rectangle {
        width: card.width * 1.2
        height: width
        radius: width / 2
        x: card.width * (-0.55 + 0.22 * Math.sin(card.motionPhase * 2 * Math.PI))
        y: card.height * (-0.25 + 0.16 * Math.cos(card.motionPhase * 2 * Math.PI))
        color: BrandTheme.alpha(BrandTheme.colors.actionBlue, "12")
    }
    Rectangle {
        width: card.width * 1.2
        height: width
        radius: width / 2
        x: card.width * (0.36 + 0.18 * Math.cos(card.motionPhase * 2 * Math.PI))
        y: card.height * (0.50 + 0.16 * Math.sin(card.motionPhase * 2 * Math.PI))
        color: BrandTheme.alpha(BrandTheme.colors.actionPurple, "12")
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
        RowLayout {
            visible: !!card.genre || !!card.vibe
            Layout.fillWidth: true
            spacing: card.width * 0.03
            Rectangle {
                visible: !!card.genre
                Layout.preferredWidth: Math.min(genreLabel.implicitWidth + 12, parent.width * (card.vibe ? 0.47 : 1))
                Layout.preferredHeight: genreLabel.implicitHeight + 6
                radius: height / 2
                color: BrandTheme.alpha(BrandTheme.colors.actionBlue, "30")
                Text {
                    id: genreLabel
                    objectName: "trackInsightShareGenre"
                    anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6
                    text: card.genre
                    textFormat: Text.PlainText; color: BrandTheme.colors.text
                    font.pixelSize: Math.max(12, card.width * 0.049)
                    verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }
            }
            Text {
                objectName: "trackInsightShareVibe"
                visible: !!card.vibe
                text: card.vibe
                textFormat: Text.PlainText; color: BrandTheme.colors.muted
                font.pixelSize: Math.max(12, card.width * 0.049)
                elide: Text.ElideRight; Layout.fillWidth: true
            }
        }
        RowLayout {
            objectName: "trackInsightShareMeters"
            visible: card.meters.length > 0
            Layout.fillWidth: true
            spacing: card.width * 0.025
            Repeater {
                model: card.meters
                delegate: ColumnLayout {
                    id: meter
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: 2
                    Text {
                        Layout.fillWidth: true
                        text: meter.modelData.label
                        textFormat: Text.PlainText
                        color: BrandTheme.colors.muted
                        font.pixelSize: Math.max(10, card.width * 0.043)
                        elide: Text.ElideRight
                    }
                    Text {
                        text: Math.round(meter.modelData.fraction * 100) + "%"
                        color: BrandTheme.colors.text
                        font.pixelSize: Math.max(10, card.width * 0.043)
                    }
                    Rectangle {
                        id: meterTrack
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(2, card.width * 0.014)
                        radius: height / 2
                        color: BrandTheme.alpha(BrandTheme.colors.text, "25")
                        Rectangle {
                            width: meterTrack.width * meter.modelData.fraction
                            height: meterTrack.height
                            radius: meterTrack.radius
                            color: BrandTheme.colors.cyan
                        }
                    }
                }
            }
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
