import QtQuick
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: win
    width: 1400
    height: 820
    minimumWidth: 320
    minimumHeight: 220
    visible: true
    title: "GuitarMood"

    readonly property var c: rig.colors
    readonly property var st: rig.state
    readonly property var status: rig.status
    readonly property var sw: st.switches
    readonly property bool compact: width < 700 || height < 430
    readonly property bool wide: !compact && width > height * 1.45
    // Everything is sized in u, so the board fills whatever tile Hyprland gives it.
    readonly property real u: compact ? Math.max(0.55, Math.min(width / 560, height / 380))
                                      : Math.max(0.45, Math.min(width / (wide ? 1760 : 1320), height / 820))
    readonly property var active: sw.find(s => s.on && (s.kind === "scene" || s.kind === "song"))
    readonly property string engine: status.engine || "starting"

    color: c.background
    // Palette values arrive as strings; typed colors expose .r/.g/.b for tints.
    readonly property color yellow: c.yellow
    readonly property color engineColor: engineHue()
    readonly property color dim: c.darker_background

    function hueFor(s) {
        if (!s) return c.accent
        if (s.kind === "scene")
            return ({ rhythm: c.red, solo: c.yellow, clean: c.bright_cyan, acoustic: c.green, crunch: c.orange })[s.key] || c.accent
        if (s.kind === "song") return c.accent
        return ({ tuner: c.yellow, harmony: c.bright_magenta, wah: c.magenta })[s.key] || c.green
    }
    function engineHue() {
        return ({ live: c.green, demo: c.cyan, starting: c.yellow, stopping: c.yellow, error: c.red })[engine] || c.muted
    }
    function engineText() {
        return ({ live: "RIG LIVE", demo: "DISPLAY ONLY", starting: "STARTING", stopping: "STOPPING", error: "ERROR" })[engine] || engine.toUpperCase()
    }

    // Closing the window (SUPER+W, the launcher toggle, logout) turns the rig off cleanly.
    onClosing: function (close) {
        close.accepted = false
        rig.shutdown()
    }

    // ------------------------------------------------------------------ full board
    Item {
        anchors.fill: parent
        anchors.margins: 22 * win.u
        visible: !win.compact

        RowLayout {
            anchors.fill: parent
            spacing: 22 * win.u

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 18 * win.u

                // Header: bank, era, song facts; what is playing; rig status.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20 * win.u

                    Rectangle {
                        Layout.preferredWidth: 132 * win.u
                        Layout.preferredHeight: 140 * win.u
                        radius: 16 * win.u
                        color: win.c.lighter_background
                        border.width: Math.max(2, 3 * win.u)
                        border.color: win.st.synced ? win.c.accent : win.c.muted
                        Column {
                            anchors.centerIn: parent
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: "BANK"
                                color: win.c.dark_foreground
                                font { family: rig.font; pixelSize: 16 * win.u; bold: true; letterSpacing: 3 * win.u }
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: win.st.synced ? win.st.bank : "?"
                                color: win.c.accent
                                font { family: rig.font; pixelSize: 76 * win.u; bold: true }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6 * win.u
                        Text {
                            Layout.fillWidth: true
                            text: win.st.title
                            color: win.c.bright_foreground
                            font { family: rig.font; pixelSize: 44 * win.u; bold: true }
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: win.st.subtitle
                            color: win.c.foreground
                            font { family: rig.font; pixelSize: 22 * win.u }
                            elide: Text.ElideRight
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 10 * win.u
                            Repeater {
                                model: win.st.facts
                                Rectangle {
                                    width: factText.implicitWidth + 22 * win.u
                                    height: factText.implicitHeight + 10 * win.u
                                    radius: height / 2
                                    color: win.c.selection
                                    Text {
                                        id: factText
                                        anchors.centerIn: parent
                                        text: modelData
                                        color: win.c.light_foreground
                                        font { family: rig.font; pixelSize: 16 * win.u; bold: true }
                                    }
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.alignment: Qt.AlignRight | Qt.AlignTop
                        spacing: 6 * win.u
                        Rectangle {  // rig status pill
                            Layout.alignment: Qt.AlignRight
                            Layout.preferredWidth: pillRow.implicitWidth + 26 * win.u
                            Layout.preferredHeight: pillRow.implicitHeight + 12 * win.u
                            radius: height / 2
                            color: Qt.rgba(win.engineColor.r, win.engineColor.g, win.engineColor.b, 0.15)
                            border.width: Math.max(1, win.u)
                            border.color: win.engineHue()
                            Row {
                                id: pillRow
                                anchors.centerIn: parent
                                spacing: 8 * win.u
                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 11 * win.u; height: width; radius: width / 2
                                    color: win.engineHue()
                                }
                                Text {
                                    text: win.engineText()
                                    color: win.engineHue()
                                    font { family: rig.font; pixelSize: 15 * win.u; bold: true; letterSpacing: 1.5 * win.u }
                                }
                            }
                        }
                        Text {
                            Layout.alignment: Qt.AlignRight
                            text: win.st.tuning ? "TUNER" : win.active ? win.active.label.toUpperCase() : (win.st.synced ? "" : "PRESS A SCENE")
                            color: win.st.tuning ? win.c.yellow : win.hueFor(win.active)
                            font { family: rig.font; pixelSize: 50 * win.u; bold: true; letterSpacing: 2 * win.u }
                        }
                        Text {
                            Layout.alignment: Qt.AlignRight
                            text: win.st.tuning ? "rig muted · tune in the Fretwise panel · SW10 to play" : "last press · " + win.st.last
                            color: win.c.dark_foreground
                            font { family: rig.font; pixelSize: 15 * win.u }
                        }
                    }
                }

                // The board, as it sits under your feet: SW6-10 + UP on top, SW1-5 + DOWN below,
                // expression pedals on the right.
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    RowLayout {
                        anchors.fill: parent
                        spacing: 14 * win.u

                        GridLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            columns: 6
                            rowSpacing: 14 * win.u
                            columnSpacing: 14 * win.u
                            Repeater {
                                model: win.sw.slice(5, 10)
                                SwitchTile {
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    Layout.preferredWidth: 100; Layout.preferredHeight: 100
                                    s: modelData; u: win.u; hue: win.hueFor(modelData)
                                    opacity: win.st.tuning && modelData.key !== "tuner" ? 0.35 : 1
                                }
                            }
                            BankStep {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                Layout.preferredWidth: 100; Layout.preferredHeight: 100
                                up: true; target: win.st.up; u: win.u
                            }
                            Repeater {
                                model: win.sw.slice(0, 5)
                                SwitchTile {
                                    Layout.fillWidth: true; Layout.fillHeight: true
                                    Layout.preferredWidth: 100; Layout.preferredHeight: 100
                                    s: modelData; u: win.u; hue: win.hueFor(modelData)
                                    opacity: win.st.tuning && modelData.key !== "tuner" ? 0.35 : 1
                                }
                            }
                            BankStep {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                Layout.preferredWidth: 100; Layout.preferredHeight: 100
                                up: false; target: win.st.down; u: win.u
                            }
                        }
                        Pedal {
                            Layout.fillHeight: true
                            Layout.preferredWidth: 104 * win.u
                            name: "EXP A"; p: win.st.expA; u: win.u; hue: win.c.magenta
                        }
                        Pedal {
                            Layout.fillHeight: true
                            Layout.preferredWidth: 104 * win.u
                            name: "EXP B"; p: win.st.expB; u: win.u; hue: win.c.accent
                        }
                    }

                }

                // Footer: the FCB link and how to turn the rig off.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16 * win.u
                    Text {
                        text: win.status.fcb === false ? "● FCB1010 not connected: " + win.status.fcbDetail
                              : win.status.fcb ? "● FCB1010 connected" : "● waiting for the FCB1010"
                        color: win.status.fcb === false ? win.c.red : win.status.fcb ? win.c.green : win.c.dark_foreground
                        font { family: rig.font; pixelSize: 14 * win.u }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: win.status.warning ? "⚠ " + win.status.warning : win.status.detail || ""
                        color: win.status.warning ? win.c.orange : win.c.dark_foreground
                        font { family: rig.font; pixelSize: 14 * win.u }
                        elide: Text.ElideRight
                    }
                    Text {
                        text: "close this window to stop the rig"
                        color: win.c.dark_foreground
                        font { family: rig.font; pixelSize: 14 * win.u }
                    }
                }
            }

            // Setlist: every bank on the board, current one lit (wide tiles only).
            ColumnLayout {
                visible: win.wide
                Layout.preferredWidth: 340 * win.u
                Layout.maximumWidth: 340 * win.u
                Layout.fillHeight: true
                spacing: 8 * win.u
                Text {
                    text: "SETLIST"
                    color: win.c.dark_foreground
                    font { family: rig.font; pixelSize: 15 * win.u; bold: true; letterSpacing: 3 * win.u }
                }
                Repeater {
                    model: win.st.banks
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.maximumHeight: 82 * win.u
                        radius: 10 * win.u
                        color: modelData.current ? win.c.selection : "transparent"
                        border.width: modelData.current ? Math.max(1, 2 * win.u) : 0
                        border.color: win.c.accent
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8 * win.u
                            spacing: 12 * win.u
                            Text {
                                Layout.preferredWidth: 34 * win.u
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData.number
                                color: modelData.current ? win.c.accent : win.c.dark_foreground
                                font { family: rig.font; pixelSize: 28 * win.u; bold: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title
                                    color: modelData.current ? win.c.bright_foreground : win.c.foreground
                                    font { family: rig.font; pixelSize: 17 * win.u; bold: true }
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.subtitle
                                    color: win.c.dark_foreground
                                    font { family: rig.font; pixelSize: 13 * win.u }
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ compact tile
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16 * win.u
        visible: win.compact
        spacing: 8 * win.u

        RowLayout {
            Layout.fillWidth: true
            spacing: 12 * win.u
            Text {
                text: win.st.synced ? win.st.bank : "?"
                color: win.c.accent
                font { family: rig.font; pixelSize: 44 * win.u; bold: true }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                Text {
                    Layout.fillWidth: true
                    text: win.st.title
                    color: win.c.bright_foreground
                    font { family: rig.font; pixelSize: 22 * win.u; bold: true }
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    text: win.st.subtitle
                    color: win.c.foreground
                    font { family: rig.font; pixelSize: 14 * win.u }
                    elide: Text.ElideRight
                }
            }
            Rectangle {
                width: 14 * win.u; height: width; radius: width / 2
                color: win.engineHue()
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.fillHeight: true
            verticalAlignment: Text.AlignVCenter
            text: win.st.tuning ? "TUNER" : win.active ? win.active.label.toUpperCase() : "—"
            color: win.st.tuning ? win.c.yellow : win.hueFor(win.active)
            font { family: rig.font; pixelSize: 80 * win.u; bold: true }
            fontSizeMode: Text.Fit
            minimumPixelSize: 16
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8 * win.u
            Repeater {
                model: win.sw.filter(s => s.kind === "toggle")
                Rectangle {
                    width: chip.implicitWidth + 20 * win.u
                    height: chip.implicitHeight + 10 * win.u
                    radius: height / 2
                    readonly property color hue: win.hueFor(modelData)
                    color: modelData.on ? Qt.rgba(hue.r, hue.g, hue.b, 0.22) : "transparent"
                    border.width: Math.max(1, win.u)
                    border.color: modelData.on ? hue : win.c.selection
                    Text {
                        id: chip
                        anchors.centerIn: parent
                        text: modelData.sw + " " + modelData.label + (modelData.known ? "" : " ?")
                        color: modelData.on ? win.c.bright_foreground : win.c.dark_foreground
                        font { family: rig.font; pixelSize: 15 * win.u; bold: modelData.on }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ start / stop / error
    Rectangle {
        anchors.fill: parent
        visible: win.engine === "starting" || win.engine === "stopping" || win.engine === "error"
        color: Qt.rgba(win.dim.r, win.dim.g, win.dim.b, 0.88)
        Column {
            anchors.centerIn: parent
            width: parent.width * 0.8
            spacing: 18 * win.u
            Rectangle {  // a slow spinner, only while starting or stopping
                anchors.horizontalCenter: parent.horizontalCenter
                visible: win.engine !== "error"
                width: 54 * win.u; height: width; radius: width / 2
                color: "transparent"
                border.width: Math.max(2, 5 * win.u)
                border.color: win.c.selection
                Rectangle {
                    width: parent.border.width * 1.6; height: width; radius: width / 2
                    color: win.engineHue()
                    x: parent.width / 2 - width / 2 + (parent.width / 2 - parent.border.width / 2) * Math.cos(spin.angle)
                    y: parent.height / 2 - height / 2 + (parent.height / 2 - parent.border.width / 2) * Math.sin(spin.angle)
                }
                QtObject { id: spin; property real angle: 0 }
                NumberAnimation {
                    target: spin; property: "angle"; from: 0; to: 2 * Math.PI; duration: 1100
                    loops: Animation.Infinite
                    running: win.engine === "starting" || win.engine === "stopping"
                }
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: win.engineText()
                color: win.engineHue()
                font { family: rig.font; pixelSize: 34 * win.u; bold: true; letterSpacing: 3 * win.u }
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: (win.status.detail || "") + (win.engine === "error" ? "\n\nClose the window to quit." : "")
                color: win.c.foreground
                font { family: rig.font; pixelSize: 20 * win.u }
            }
        }
    }
}
