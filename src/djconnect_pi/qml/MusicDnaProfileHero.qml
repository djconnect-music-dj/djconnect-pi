import QtQuick
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme
import "MoodTheme.js" as MoodTheme

GlassCard {
    id: heroCard
    required property string description
    required property string mood
    required property bool animate
    property bool compact: true
    hero: true
    padding: compact ? 14 : 22
    spacing: 10

    Canvas {
        id: graphic
        objectName: "musicDnaHelix"
        readonly property string startColor: MoodTheme.moodColor(heroCard.mood, "gradientStart")
        readonly property string midColor: MoodTheme.moodColor(heroCard.mood, "gradientMid")
        readonly property string endColor: MoodTheme.moodColor(heroCard.mood, "gradientEnd")
        property real phase: 0
        onPhaseChanged: requestPaint()
        onStartColorChanged: requestPaint()
        onMidColorChanged: requestPaint()
        onEndColorChanged: requestPaint()
        Layout.fillWidth: true
        Layout.preferredHeight: heroCard.compact ? 142 : 220
        Accessible.ignored: true

        Timer {
            interval: 80
            repeat: true
            running: heroCard.animate && heroCard.visible
            onTriggered: graphic.phase = (graphic.phase + 0.075) % (2 * Math.PI)
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            const amplitude = Math.min(width * 0.25, height * 1.12)
            for (let index = 0; index < 28; index++) {
                const progress = index / 27
                const y = height * (0.10 + progress * 0.80)
                const displacement = amplitude * Math.sin(progress * 4 * Math.PI + phase)
                const left = width / 2 + displacement
                const right = width / 2 - displacement
                const color = progress < 0.5 ? MoodTheme.mix(startColor, midColor, progress * 2)
                    : MoodTheme.mix(midColor, endColor, progress * 2 - 1)
                ctx.strokeStyle = BrandTheme.alpha(color, "58")
                ctx.fillStyle = color
                ctx.lineWidth = 1
                ctx.beginPath()
                ctx.moveTo(left, y)
                ctx.lineTo(right, y)
                ctx.stroke()
                ctx.beginPath()
                ctx.arc(left, y, 3.8, 0, 2 * Math.PI)
                ctx.fill()
                ctx.fillStyle = BrandTheme.alpha(color, "A8")
                ctx.beginPath()
                ctx.arc(right, y, 2.8, 0, 2 * Math.PI)
                ctx.fill()
            }
        }
    }
    Text {
        objectName: "musicDnaHeroDescription"
        text: heroCard.description
        textFormat: Text.PlainText
        color: BrandTheme.colors.text
        horizontalAlignment: Text.AlignHCenter
        font.pixelSize: heroCard.compact ? 17 : 20
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
