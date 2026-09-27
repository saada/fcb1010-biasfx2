import QtQuick

// One FCB1010 footswitch: number, what it does in this bank, and whether it is on.
Rectangle {
    id: tile
    property var s: ({})
    property real u: 1
    property color hue: rig.colors.accent
    readonly property var c: rig.colors
    readonly property bool on: s.on === true
    readonly property bool known: s.known !== false
    readonly property bool empty: s.kind === "empty"
    readonly property bool isScene: s.kind === "scene" || s.kind === "song"

    radius: 12 * u
    signal clicked()
    scale: hit.pressed ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: 80 } }

    MouseArea {
        id: hit
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: tile.clicked()
    }
    Rectangle {  // hover sheen
        anchors.fill: parent
        radius: parent.radius
        color: rig.colors.foreground
        opacity: hit.containsMouse ? 0.06 : 0
    }
    color: on ? Qt.rgba(hue.r, hue.g, hue.b, 0.20) : c.lighter_background
    border.width: on ? Math.max(2, 3 * u) : Math.max(1, u)
    border.color: on ? hue : (known ? c.selection : c.muted)
    opacity: empty ? 0.35 : 1
    Behavior on color { ColorAnimation { duration: 120 } }

    // Footswitch number, like the silkscreen on the board.
    Text {
        anchors { left: parent.left; top: parent.top; margins: 12 * tile.u }
        text: "SW" + tile.s.sw
        color: tile.on ? tile.hue : tile.c.dark_foreground
        font { family: rig.font; pixelSize: 15 * tile.u; bold: true; letterSpacing: 1.5 * tile.u }
    }

    // LED: lit = on; hollow = the rig can't know until the next press.
    Rectangle {
        id: led
        anchors { right: parent.right; top: parent.top; margins: 13 * tile.u }
        width: 18 * tile.u; height: width; radius: width / 2
        color: tile.on ? tile.hue : "transparent"
        border.width: Math.max(1, 2 * tile.u)
        border.color: tile.on ? tile.hue : tile.c.muted
        Rectangle {  // glow
            anchors.centerIn: parent
            visible: tile.on
            width: parent.width * 2.2; height: width; radius: width / 2
            color: Qt.rgba(tile.hue.r, tile.hue.g, tile.hue.b, 0.25)
            z: -1
        }
        Text {
            anchors.centerIn: parent
            visible: !tile.known
            text: "?"
            color: tile.c.muted
            font { family: rig.font; pixelSize: 13 * tile.u; bold: true }
        }
    }

    Column {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 14 * tile.u }
        spacing: 4 * tile.u
        Text {
            width: parent.width
            text: tile.s.label || ""
            color: tile.on ? tile.c.bright_foreground : (tile.known ? tile.c.foreground : tile.c.dark_foreground)
            font { family: rig.font; pixelSize: Math.min((tile.isScene ? 44 : 36) * tile.u, tile.height * 0.2); bold: true }
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: 10
            elide: Text.ElideRight
        }
        Text {
            width: parent.width
            text: tile.s.detail || ""
            visible: text.length > 0
            color: tile.on ? tile.hue : tile.c.dark_foreground
            font { family: rig.font; pixelSize: Math.min(19 * tile.u, tile.height * 0.1) }
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
    }
}
