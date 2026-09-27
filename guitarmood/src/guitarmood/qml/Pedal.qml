import QtQuick

// An expression pedal: live position, and whether it is doing anything right now.
Rectangle {
    id: pedal
    property string name: "EXP A"
    property var p: ({})
    property real u: 1
    property color hue: rig.colors.accent
    readonly property var c: rig.colors
    readonly property bool moved: p.value !== null && p.value !== undefined
    readonly property real level: moved ? p.value : 0

    radius: 12 * u
    color: c.lighter_background
    border.width: Math.max(1, u)
    border.color: c.selection

    Text {
        id: head
        anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 12 * pedal.u }
        text: pedal.name
        color: pedal.p.live ? pedal.hue : pedal.c.dark_foreground
        font { family: rig.font; pixelSize: 15 * pedal.u; bold: true; letterSpacing: 1.5 * pedal.u }
    }
    Text {
        id: what
        anchors { top: head.bottom; horizontalCenter: parent.horizontalCenter; topMargin: 2 * pedal.u }
        width: parent.width - 12 * pedal.u
        horizontalAlignment: Text.AlignHCenter
        text: (pedal.p.label || "") + (pedal.p.live ? "" : "\n(off)")
        color: pedal.c.foreground
        font { family: rig.font; pixelSize: 13 * pedal.u }
        wrapMode: Text.WordWrap
    }

    Rectangle {  // the travel, heel at the bottom, toe at the top
        id: track
        anchors { top: what.bottom; bottom: pct.top; horizontalCenter: parent.horizontalCenter; margins: 12 * pedal.u }
        width: Math.min(parent.width * 0.42, 46 * pedal.u)
        radius: 8 * pedal.u
        color: pedal.c.dark_background
        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: parent.height * pedal.level
            radius: parent.radius
            color: pedal.p.live ? pedal.hue : pedal.c.muted
            Behavior on height { NumberAnimation { duration: 60 } }
        }
    }
    Text {
        id: pct
        anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter; bottomMargin: 12 * pedal.u }
        text: pedal.moved ? Math.round(pedal.level * 100) + "%" : "—"
        color: pedal.c.bright_foreground
        font { family: rig.font; pixelSize: 20 * pedal.u; bold: true }
    }
}
