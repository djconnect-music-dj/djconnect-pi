import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "BrandTheme.js" as BrandTheme

Item {
    id: page
    property var backend
    property bool privacy: false
    property bool feedback: false
    property bool feedbackOpenFailed: false
    property string fontLicense: ""
    signal back()
    function tr(key) { backend.translationVersion; return backend.t(key) }

    component SelectableNotice: TextEdit {
        id: notice
        Layout.fillWidth: true
        textFormat: TextEdit.PlainText
        color: BrandTheme.colors.text
        font.pixelSize: 22
        wrapMode: TextEdit.Wrap
        readOnly: true
        selectByMouse: true
        selectByKeyboard: true

        TapHandler {
            onLongPressed: {
                noticeCopyMenu.notice = notice
                noticeCopyMenu.popup(notice, point.position)
            }
        }
    }

    Menu {
        id: noticeCopyMenu
        objectName: "informationNoticeCopyMenu"
        property TextEdit notice: null
        onClosed: notice = null
        MenuItem {
            objectName: "informationCopyNotice"
            text: page.tr("copy_notice")
            onTriggered: {
                const source = noticeCopyMenu.notice
                if (!source || !source.visible) return
                source.selectAll()
                source.copy()
                source.deselect()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20
        PageToolbar {
            title: page.tr(page.feedback ? "feedback" : page.privacy ? "privacy" : "legal")
            leading: [ GlassButton { objectName: "informationBack"; iconName: "back"; tertiary: true; implicitWidth: 52; Accessible.name: page.tr("more"); onClicked: page.back() } ]
        }
        ScrollView {
            id: scroll
            objectName: "informationScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth; clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 20
                AppBanner { detailText: page.tr("tagline") }
                GlassCard {
                    visible: page.feedback
                    Layout.fillWidth: true
                    Text { text: page.tr("feedback_share"); textFormat: Text.PlainText; color: BrandTheme.colors.text; font.pixelSize: 20; font.weight: Font.DemiBold; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Text { text: page.tr("feedback_help"); textFormat: Text.PlainText; color: BrandTheme.colors.text; font.pixelSize: 22; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    GlassButton {
                        objectName: "feedbackOpenIssue"
                        primary: true
                        text: page.tr("feedback_open")
                        iconName: "external_link"
                        Layout.fillWidth: true
                        onClicked: {
                            page.feedbackOpenFailed = !page.backend.openFeedbackIssue()
                            if (!page.feedbackOpenFailed) page.back()
                        }
                    }
                    Text { objectName: "feedbackOpenError"; visible: page.feedbackOpenFailed; text: page.tr("feedback_unavailable"); textFormat: Text.PlainText; color: BrandTheme.colors.muted; font.pixelSize: 22; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    GlassButton {
                        objectName: "feedbackNotNow"
                        tertiary: true
                        text: page.tr("feedback_not_now")
                        Layout.fillWidth: true
                        onClicked: page.back()
                    }
                }
                GlassCard {
                    visible: page.privacy && !page.feedback
                    Layout.fillWidth: true
                    Text { text: page.tr("privacy"); textFormat: Text.PlainText; color: BrandTheme.colors.text; font.pixelSize: 20; font.weight: Font.DemiBold; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Repeater {
                        model: ["privacy_data_body", "privacy_tokens_body", "privacy_ai_body", "privacy_choices_body", "privacy_diagnostics_body"]
                        SelectableNotice {
                            required property string modelData
                            objectName: modelData
                            text: page.tr(modelData)
                        }
                    }
                    GlassButton {
                        objectName: "privacyOpenStatement"
                        text: page.tr("privacy_full_statement")
                        iconName: "globe"
                        Layout.fillWidth: true
                        onClicked: page.backend.openPrivacyStatement()
                    }
                }
                GlassCard {
                    visible: !page.privacy && !page.feedback
                    Layout.fillWidth: true
                    Text { text: page.tr("legal"); textFormat: Text.PlainText; color: BrandTheme.colors.text; font.pixelSize: 20; font.weight: Font.DemiBold; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    SelectableNotice { objectName: "legalIndependenceNotice"; text: page.tr("legal_independence") }
                    SelectableNotice { objectName: "legalTrademarksNotice"; text: page.tr("legal_trademarks") }
                }
                GlassCard {
                    visible: !page.privacy && !page.feedback
                    Layout.fillWidth: true
                    Text { text: page.tr("legal_oss"); textFormat: Text.PlainText; color: BrandTheme.colors.text; font.pixelSize: 26; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    SelectableNotice { objectName: "legalOssNotice"; text: page.tr("legal_oss_body") }
                    GlassButton { text: page.tr("legal_license"); onClicked: Qt.openUrlExternally("https://github.com/djconnect-music-dj/djconnect-pi/blob/main/LICENSE") }
                    GlassButton {
                        id: fontLicenseDisclosure
                        objectName: "legalFontLicenseDisclosure"
                        text: page.tr("legal_inter_license")
                        checkable: true
                        iconName: checked ? "arrow_down" : "chevron_right"
                        textAlignment: Text.AlignLeft
                        wrapText: true
                        Layout.fillWidth: true
                        onClicked: if (checked && !page.fontLicense) page.fontLicense = page.backend.bundledFontLicense()
                    }
                    TextArea {
                        objectName: "legalFontLicenseText"
                        visible: fontLicenseDisclosure.checked
                        text: visible ? (page.fontLicense || page.tr("legal_license_unavailable")) : ""
                        textFormat: TextEdit.PlainText
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        color: BrandTheme.colors.text
                        font.pixelSize: 17
                        background: Item {}
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }
    onVisibleChanged: {
        feedbackOpenFailed = false
        fontLicenseDisclosure.checked = false
        if (visible) scroll.contentItem.contentY = 0
        else noticeCopyMenu.close()
    }
    onPrivacyChanged: noticeCopyMenu.close()
    onFeedbackChanged: {
        noticeCopyMenu.close()
        feedbackOpenFailed = false
        scroll.contentItem.contentY = 0
    }
}
