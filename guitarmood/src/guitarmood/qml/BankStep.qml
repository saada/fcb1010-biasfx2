import QtQuick

// The FCB's UP / DOWN pedal: which bank one tap takes you to.
Rectangle {
    id: step
    property bool up: true
    property var target: null
    property real u: 1
    readonly property var c: rig.colors

    radius: 12 * u
    color: c.dark_background
    border.width: Math.max(1, u)
    border.color: c.selection
    opacity: target ? 1 : 0.4

    Text {
        anchors { left: parent.left; top: parent.top; margins: 12 * step.u }
        text: step.up ? "▲ UP" : "▼ DOWN"
        color: step.c.dark_foreground
        font { family: rig.font; pixelSize: 15 * step.u; bold: true; letterSpacing: 1.5 * step.u }
    }
    Column {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 14 * step.u }
        spacing: 4 * step.u
        Text {
            width: parent.width
            text: step.target ? "Bank " + step.target.number : "end"
            color: step.c.accent
            font { family: rig.font; pixelSize: 15 * step.u; bold: true }
        }
        Text {
            width: parent.width
            text: step.target ? step.target.title : ""
            color: step.c.foreground
            font { family: rig.font; pixelSize: 19 * step.u; bold: true }
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
    }
}
