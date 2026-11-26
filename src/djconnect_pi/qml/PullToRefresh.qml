import QtQuick

QtObject {
    id: gesture
    required property Flickable flickable
    property bool canRefresh: false
    property bool armed: false
    signal refreshRequested()
    onCanRefreshChanged: if (!canRefresh) armed = false
    onFlickableChanged: armed = false

    property Connections dragSignals: Connections {
        target: gesture.flickable
        function onDragStarted() { gesture.armed = false }
        function onVerticalOvershootChanged() {
            if (target.dragging && target.verticalOvershoot < -64 && gesture.canRefresh) gesture.armed = true
        }
        function onDragEnded() {
            const refresh = gesture.armed && gesture.canRefresh
            gesture.armed = false
            if (refresh) gesture.refreshRequested()
        }
    }
}
