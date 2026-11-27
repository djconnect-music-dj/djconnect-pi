import QtQuick
import "BrandTheme.js" as BrandTheme
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: editor
    property var backend
    property var draft: ({})
    property string editToken: ""
    property bool dirty: false
    property var changedFields: ({})
    property bool saving: false
    property bool checkingOutcome: false
    readonly property bool outcomeUncertain: backend.sharedSettings.error === "uncertain"
        || !!backend.sharedSettings.operation_pending || !!backend.sharedSettings.operation_unknown
    readonly property bool progressVisible: backend.sharedSettingsBusy
        && (saving || checkingOutcome || !backend.sharedSettings.values)
    readonly property var allFields: ["persona", "voice", "gender", "mood", "presence", "announcement_output"]
    property var fields: allFields
    readonly property var displayedFields: backend.sharedSettings.selection_required ? allFields : fields
    readonly property bool textOnly: draft.announcement_output === "text_only"
    spacing: 12

    function tr(key) { backend.translationVersion; return backend.t(key) }
    function loadConfirmed() {
        var state = backend.sharedSettings
        draft = JSON.parse(JSON.stringify(state.values || {}))
        editToken = state.edit_token || ""
        dirty = false
        changedFields = ({})
    }
    function options(field) {
        var list = (backend.sharedSettings.options || {})[field] || []
        if (field === "voice") return list
        return list.map(function(value) { return { value: value, label_key: "shared_" + field + "_" + value } })
    }
    function label(item) { return item.label_key ? tr(item.label_key) : item.label }
    function canChoose(field, item) {
        return field !== "voice" || item.value.kind === "provider_default" || !draft.gender
            || draft.gender === "default" || item.gender === draft.gender
    }
    function selectedIndex(field) {
        var list = options(field)
        for (var i = 0; i < list.length; ++i)
            if (sameValue(list[i].value, draft[field])) return i
        return -1
    }
    function sameValue(a, b) {
        if (a && typeof a === "object" && b && typeof b === "object")
            return a.kind === b.kind && a.provider_id === b.provider_id && a.voice_id === b.voice_id
        return a === b
    }
    function choose(field, value) {
        var next = JSON.parse(JSON.stringify(draft))
        next[field] = value
        draft = next
        var changed = Object.assign({}, changedFields)
        changed[field] = true
        changedFields = changed
        dirty = true
    }
    function errorText() {
        var error = backend.sharedSettings.error || ""
        if (error === "unsupported") return tr("shared_error_unsupported")
        if (error === "denied") return tr("shared_error_denied")
        if (error === "conflict") return tr("shared_error_conflict")
        if (error === "selection_required") return tr("shared_selection_required")
        if (outcomeUncertain) return tr("shared_error_uncertain")
        if (error === "offline") return tr("shared_error_offline")
        if (error) return tr("shared_error_rejected")
        return ""
    }

    Connections {
        target: editor.backend
        function onSharedSettingsChanged() {
            if (editor.checkingOutcome && !editor.backend.sharedSettingsBusy) {
                editor.checkingOutcome = false
                if (editor.backend.sharedSettings.available && !editor.outcomeUncertain) editor.loadConfirmed()
            }
            if (editor.saving && !editor.backend.sharedSettingsBusy) {
                editor.saving = false
                if (editor.backend.sharedSettings.available && !editor.backend.sharedSettings.operation_pending &&
                    !editor.backend.sharedSettings.operation_unknown)
                    editor.loadConfirmed()
            } else if (!editor.dirty) editor.loadConfirmed()
            if (!editor.backend.sharedSettings.values) editor.loadConfirmed()
        }
    }
    Component.onCompleted: loadConfirmed()

    Label {
        text: editor.tr("shared_title")
        color: BrandTheme.colors.text
        font.pixelSize: 28
        font.bold: true
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        Accessible.role: Accessible.Heading
    }
    Label {
        text: editor.tr(editor.backend.sharedSettings.active ? "shared_scope_active" : "shared_scope_defaults")
        color: BrandTheme.colors.muted
        font.pixelSize: 20
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        visible: editor.backend.sharedSettings.selection_required || false
        text: editor.tr("shared_selection_required")
        color: "#ffdca8"
        font.pixelSize: 20
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        visible: editor.errorText().length > 0
        text: editor.errorText()
        color: "#ffdca8"
        font.pixelSize: 20
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        Accessible.role: Accessible.AlertMessage
    }
    Label {
        visible: editor.dirty && editor.editToken !== (editor.backend.sharedSettings.edit_token || "")
        text: editor.tr("shared_error_conflict")
        color: "#ffdca8"
        font.pixelSize: 20
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Repeater {
        model: editor.backend.sharedSettings.values ? editor.displayedFields : []
        delegate: ColumnLayout {
            id: fieldRow
            required property string modelData
            visible: !editor.textOnly || !["voice", "gender"].includes(modelData)
            Layout.fillWidth: true
            spacing: 6
            Label {
                text: editor.tr("shared_field_" + fieldRow.modelData)
                color: BrandTheme.colors.muted
                font.pixelSize: 22
            }
            BrandComboBox {
                id: choice
                objectName: "shared_choice_" + fieldRow.modelData
                Layout.fillWidth: true
                Layout.minimumHeight: 54
                font.pixelSize: 24
                model: editor.options(fieldRow.modelData)
                currentIndex: editor.selectedIndex(fieldRow.modelData)
                displayText: currentIndex >= 0 ? editor.label(model[currentIndex]) : editor.tr("shared_unavailable_selection")
                enabled: !!editor.backend.sharedSettings.available && !editor.backend.sharedSettingsBusy &&
                         !editor.backend.sharedSettings.operation_pending &&
                         (editor.backend.sharedSettings.writable || []).indexOf(fieldRow.modelData) >= 0
                Accessible.name: editor.tr("shared_field_" + fieldRow.modelData)
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    width: choice.width
                    text: editor.label(modelData)
                    font.pixelSize: 24
                    highlighted: choice.highlightedIndex === index
                    enabled: editor.canChoose(fieldRow.modelData, modelData)
                }
                onActivated: function(index) {
                    const item = model[index]
                    if (item && editor.canChoose(fieldRow.modelData, item)) editor.choose(fieldRow.modelData, item.value)
                    else currentIndex = Qt.binding(() => editor.selectedIndex(fieldRow.modelData))
                }
            }
        }
    }
    Label {
        text: editor.tr("shared_pi_no_audio")
        color: BrandTheme.colors.muted
        font.pixelSize: 18
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        visible: !editor.backend.sharedSettings.speaker_available
            && ["ha_speaker", "both"].includes(editor.draft.announcement_output)
        text: editor.tr("dj_announcement_speaker_unavailable")
        color: BrandTheme.colors.muted
        font.pixelSize: 18
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        visible: !!editor.backend.sharedSettings.values && !editor.textOnly && !editor.backend.sharedSettings.voice_available
        text: editor.tr("shared_voice_unavailable")
        color: BrandTheme.colors.muted
        font.pixelSize: 18
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        objectName: "sharedVoicePreserved"
        visible: editor.textOnly
        text: editor.tr("shared_voice_preserved_text_only")
        color: BrandTheme.colors.muted
        font.pixelSize: 18
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    RowLayout {
        visible: editor.progressVisible
        Layout.fillWidth: true

        BusyIndicator {
            running: editor.progressVisible
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
        }
        Label {
            objectName: "sharedSettingsProgress"
            text: editor.tr(editor.saving ? "shared_saving" : "loading")
            color: BrandTheme.colors.muted
            font.pixelSize: 18
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Button {
            objectName: "sharedReload"
            text: editor.tr(editor.outcomeUncertain ? "shared_check_outcome" : "shared_reload")
            Layout.fillWidth: true
            Layout.minimumHeight: 54
            font.pixelSize: 22
            enabled: !editor.backend.sharedSettingsBusy
            onClicked: {
                editor.checkingOutcome = editor.outcomeUncertain
                if (!editor.checkingOutcome) editor.dirty = false
                editor.backend.pollSharedSettings()
                if (!editor.backend.sharedSettingsBusy) editor.checkingOutcome = false
            }
        }
        GlassButton {
            objectName: "sharedApply"
            primary: true
            text: editor.tr("shared_apply")
            Layout.fillWidth: true
            Layout.minimumHeight: 54
            font.pixelSize: 22
            enabled: !!editor.backend.sharedSettings.available && !editor.backend.sharedSettingsBusy &&
                     !editor.backend.sharedSettings.operation_pending && editor.editToken.length > 0 &&
                     (editor.dirty || editor.backend.sharedSettings.selection_required)
            onClicked: {
                var patch = {}
                var fields = editor.backend.sharedSettings.selection_required ? editor.allFields : Object.keys(editor.changedFields)
                for (var i = 0; i < fields.length; ++i) patch[fields[i]] = editor.draft[fields[i]]
                patch._edit_token = editor.editToken
                editor.saving = true
                editor.backend.saveSharedSettings(patch)
                if (!editor.backend.sharedSettingsBusy) editor.saving = false
            }
        }
    }
}
