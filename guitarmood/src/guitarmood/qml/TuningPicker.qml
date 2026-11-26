import QtQuick

// The rig's tuning: one button per choice (A, A#, B, C, C# standard). The guitar stays in B;
// TONE3000's pitch shifter moves the whole rig. While a retune runs, the target pulses and
// the rest wait.
Row {
    id: picker
    required property var pitch  // rig.status.pitch: {st, name, phase, detail, choices}
    required property var c
    required property real u
    required property string fontFamily
    property bool showLabel: true
    readonly property bool busy: pitch && pitch.phase === "applying"
    signal picked(int st)

    spacing: 6 * u

    Text {
        visible: picker.showLabel
        anchors.verticalCenter: parent.verticalCenter
        rightPadding: 4 * picker.u
        text: "TUNING"
        color: picker.c.dark_foreground
        font { family: picker.fontFamily; pixelSize: 13 * picker.u; bold: true; letterSpacing: 2 * picker.u }
    }

    Repeater {
        model: picker.pitch ? picker.pitch.choices : []
        Rectangle {
            id: chip
            readonly property bool current: picker.pitch.st === modelData.st
            readonly property bool target: current && picker.busy
            readonly property color hue: modelData.st === 0 ? picker.c.accent : picker.c.yellow
            width: Math.max(label.implicitWidth + 18 * picker.u, 40 * picker.u)
            height: label.implicitHeight + 12 * picker.u
            radius: 8 * picker.u
            color: current ? Qt.rgba(hue.r, hue.g, hue.b, target ? 0.12 : 0.25)
                           : hit.containsMouse && !picker.busy ? picker.c.lighter_background : "transparent"
            border.width: Math.max(1, (current ? 2 : 1) * picker.u)
            border.color: current ? hue : picker.c.selection
            property real pulse: 1
            opacity: picker.busy && !current ? 0.4 : target ? pulse : 1

            SequentialAnimation on pulse {
                running: chip.target
                loops: Animation.Infinite
                alwaysRunToEnd: true
                NumberAnimation { to: 0.35; duration: 450 }
                NumberAnimation { to: 1; duration: 450 }
            }

            Text {
                id: label
                anchors.centerIn: parent
                text: modelData.note
                color: chip.current ? picker.c.bright_foreground : picker.c.foreground
                font { family: picker.fontFamily; pixelSize: 16 * picker.u; bold: chip.current }
            }
            MouseArea {
                id: hit
                anchors.fill: parent
                hoverEnabled: true
                enabled: !picker.busy
                cursorShape: Qt.PointingHandCursor
                onClicked: picker.picked(modelData.st)
            }
        }
    }
}
