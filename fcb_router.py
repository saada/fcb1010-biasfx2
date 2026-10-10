# /// script
# requires-python = ">=3.10"
# dependencies = ["alsa-midi>=1.0.4"]
# ///
"""FCB1010 router: the pedal is a fixed address generator, flashed once; all mapping is here.

The FCB holds the universal map (`rig.py syx --universal`): every switch sends only its own
address on MIDI channel 16. This router, a persistent ALSA sequencer client, sits between the
FCB and Qtractor's "FCB" bus. It turns each address into exactly the messages rig.py's layout
defines for that (bank, switch) (`rig.py layout`, from `rig.build()`), and reloads the layout
whenever rig.py, tone3000.py or rigs/ change on disk, so a layout change is live at once and
never needs a flash.

  uv run fcb_router.py              run in the foreground (the helper unit and GuitarMood run it in-process)
  uv run fcb_router.py table        print every address and what it sends now
  uv run fcb_router.py bench [N]    measure the added latency on test ports (no FCB, no Qtractor)

Address scheme (channel 16, which nothing in the rig uses):
  bank b, SW1-5    Program Change b*10 + (sw - 1)        (0-99)
  bank b, SW6-10   CC 102-106 (SW6..SW10), value b       (0-9)
Everything else passes straight through: EXP A/B (CC 27/7 on channel 1), and every message of
the old per-layout flash, so the rig plays the same before and after the one-time flash.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
ADDRESS_CHANNEL = 15  # MIDI channel 16 (0-based)
ADDRESS_CC = {6: 102, 7: 103, 8: 104, 9: 105, 10: 106}  # SW6..SW10; the value is the bank
SWITCH_BY_CC = {cc: sw for sw, cc in ADDRESS_CC.items()}
CLIENT = "FCB Router"
IN_PORT, OUT_PORT = "from FCB", "to rig"
FCB_CLIENT = "USB Midi"  # the FCB1010's USB-MIDI interface
DEST = ("Qtractor", "FCB")  # Qtractor's first MIDI input bus
# Files the layout is generated from: any mtime change reloads it.
WATCH = [REPO / "rig.py", REPO / "tone3000.py", REPO / "lib/fcb1010.py", REPO / "rigs"]


# ---------------------------------------------------------------- addresses (pure, no ALSA)
def address(bank, sw):
    """The message the universal flash sends for (bank 0-9, switch 1-10)."""
    if sw <= 5:
        return ("pc", ADDRESS_CHANNEL, bank * 10 + sw - 1)
    return ("cc", ADDRESS_CHANNEL, ADDRESS_CC[sw], bank)


def decode(ev):
    """("pc"|"cc", ch, ...) -> (bank, sw) when it is a universal address, else None."""
    if ev[1] != ADDRESS_CHANNEL:
        return None
    if ev[0] == "pc" and 0 <= ev[2] <= 99 and ev[2] % 10 < 5:
        return divmod(ev[2], 10)[0], ev[2] % 10 + 1
    if ev[0] == "cc" and ev[2] in SWITCH_BY_CC and 0 <= ev[3] <= 9:
        return ev[3], SWITCH_BY_CC[ev[2]]
    return None


def parse_aseqdump(line):
    """One aseqdump line -> ("pc", ch, program) | ("cc", ch, cc, value) | None."""
    parts = line.split()
    if "Program" in parts and "change" in parts:
        nums = [int(p.rstrip(",")) for p in parts if p.rstrip(",").isdigit()]
        return ("pc", nums[-2], nums[-1]) if len(nums) >= 2 else None
    if "Control" in parts and "change" in parts:
        nums = [int(p.rstrip(",")) for p in parts if p.rstrip(",").isdigit()]
        return ("cc", nums[-3], nums[-2], nums[-1]) if len(nums) >= 3 else None
    return None


def load_layout():
    """{(bank, sw): [events]} from `rig.py layout`, run in a fresh interpreter so every edit
    to rig.py, tone3000.py or rigs/ is seen, and a half-saved edit can't break this process."""
    out = subprocess.run([sys.executable, str(REPO / "rig.py"), "layout"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError((out.stderr.strip().splitlines() or ["rig.py layout failed"])[-1])
    return {(b, s): [tuple(e) for e in events] for b, s, events in json.loads(out.stdout)["layout"]}


def layout_stamp():
    stamp = []
    for path in WATCH:
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        stamp += [(str(f), f.stat().st_mtime_ns) for f in files if f.is_file()]
    return tuple(stamp)


class Translator:
    """Address -> the layout's messages. `table` is swapped whole on reload (atomic)."""

    def __init__(self, table=None):
        self.table = load_layout() if table is None else table

    def translate(self, ev):
        """-> ((bank, sw) | None, events to send). Non-address messages pass through unchanged."""
        addr = decode(ev)
        if addr is None:  # stray channel-16 messages are dropped, everything else passes
            return None, [] if ev[1] == ADDRESS_CHANNEL else [ev]
        return addr, list(self.table.get(addr, ()))


# ---------------------------------------------------------------- the ALSA client
class Router:
    """FCB -> [translate] -> Qtractor's FCB bus. The router makes and keeps its own connections:
    the FCB into its input, its output into Qtractor, and no direct FCB -> Qtractor link (that
    would double every message). It re-checks them every second, so unplugging the FCB or
    restarting Qtractor heals by itself.

    on_events(addr, events) runs after each burst is sent (helper work, GuitarMood's board);
    keep it non-blocking. on_status(dict) reports fcb/qtractor/layout/router changes."""

    def __init__(self, on_events=None, on_status=None, source=FCB_CLIENT, dest=DEST, name=CLIENT,
                 table=None, watch=True, restore_direct=True):
        self.on_events = on_events or (lambda addr, events: None)
        self.on_status = on_status or (lambda patch: None)
        self.source_name, self.dest_name, self.name = source, dest, name
        self.translator = Translator(table)
        self.watch, self.restore_direct = watch, restore_direct
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.client = self.inp = self.out = None
        self._links = {}
        self._stamp = layout_stamp() if watch else None

    # ---- lifecycle
    def stop(self):
        self.stopping.set()

    def run(self):
        """Blocking. Raises RuntimeError if another router already owns the FCB."""
        from alsa_midi import EventType, PortCaps, PortType, SequencerClient
        self._EventType, self._events = EventType, {}
        probe = SequencerClient(f"{self.name} probe")
        try:
            if any(p.client_name == self.name for p in probe.list_ports(include_no_export=True)):
                raise RuntimeError(f"another '{self.name}' is already running (one owner only)")
        finally:
            probe.close()
        self.client = SequencerClient(self.name)
        self.inp = self.client.create_port(IN_PORT, PortCaps.WRITE | PortCaps.SUBS_WRITE,
                                           PortType.MIDI_GENERIC | PortType.APPLICATION)
        self.out = self.client.create_port(OUT_PORT, PortCaps.READ | PortCaps.SUBS_READ,
                                           PortType.MIDI_GENERIC | PortType.APPLICATION)
        self.on_status({"router": True, "routerDetail": f"{len(self.translator.table)} switches mapped"})
        if self.watch:
            threading.Thread(target=self._watch_loop, daemon=True, name="router-reload").start()
        try:
            self._connect()
            self.ready.set()
            next_check = time.monotonic() + 1
            while not self.stopping.is_set():
                ev = self.client.event_input(timeout=0.25)
                if ev is not None:
                    self._handle(ev)
                if time.monotonic() >= next_check:
                    self._connect()
                    next_check = time.monotonic() + 1
        finally:
            self._close()

    # ---- the hot path
    def _alsa_event(self, e):
        """("pc"|"cc", ...) -> an alsa-midi event, built once per distinct message."""
        out = self._events.get(e)
        if out is None:
            from alsa_midi import ControlChangeEvent, ProgramChangeEvent
            out = ProgramChangeEvent(channel=e[1], value=e[2]) if e[0] == "pc" else \
                ControlChangeEvent(channel=e[1], param=e[2], value=e[3])
            self._events[e] = out
        return out

    def _handle(self, ev):
        EventType = self._EventType
        if ev.type == EventType.PGMCHANGE:
            msg = ("pc", ev.channel, ev.value)
        elif ev.type == EventType.CONTROLLER:
            msg = ("cc", ev.channel, ev.param, ev.value)
        else:  # anything else (MMC, notes, port events) goes straight on
            if ev.type in (EventType.PORT_SUBSCRIBED, EventType.PORT_UNSUBSCRIBED):
                return
            ev.dest = ev.source = None
            self.client.event_output(ev, port=self.out)
            self.client.drain_output()
            return
        addr, events = self.translator.translate(msg)
        for e in events:
            self.client.event_output(self._alsa_event(e), port=self.out)
        self.client.drain_output()  # one write for the whole burst (alsa-midi 1.0.4's *_direct buffers too)
        self.on_events(addr, events)

    # ---- layout reload
    def _watch_loop(self):
        while not self.stopping.wait(1):
            try:
                stamp = layout_stamp()
                if stamp == self._stamp:
                    continue
                time.sleep(0.3)  # let an editor finish writing
                stamp = layout_stamp()
                self.translator.table = load_layout()
                self._stamp = stamp
                n = len(self.translator.table)
                print(f"layout reloaded: {n} switches mapped", flush=True)
                self.on_status({"routerDetail": f"layout reloaded · {n} switches mapped", "routerWarning": ""})
            except Exception as e:  # noqa: BLE001 - a broken edit keeps the last good layout
                self._stamp = stamp
                print(f"layout reload failed, keeping the last one: {e}", flush=True)
                self.on_status({"routerWarning": f"layout reload failed, kept the last one: {e}"})

    # ---- connections
    def _find(self, client, port=None):
        for p in self.client.list_ports(include_no_export=True, only_connectable=False):
            if p.client_name == client and (port is None or p.name.strip() == port):
                return p
        return None

    def _subscribers(self, port):
        from alsa_midi import SubscriptionQueryType
        return {(s.addr.client_id, s.addr.port_id)
                for s in self.client.list_port_subscribers(port, SubscriptionQueryType.READ)}

    def _connect(self):
        src = self._find(self.source_name) if isinstance(self.source_name, str) else self._find(*self.source_name)
        dst = self._find(*self.dest_name)
        me_in = (self.client.client_id, self.inp.port_id)
        me_out = (self.client.client_id, self.out.port_id)
        links = {"fcb": False, "qtractor": False}
        if src:
            subs = self._subscribers(src)
            if me_in not in subs:
                self.client.subscribe_port(src, me_in)
            if dst and (dst.client_id, dst.port_id) in subs:  # the old direct link would double everything
                self.client.unsubscribe_port(src, dst)
                print(f"removed the direct {src.client_name} -> {dst.client_name} link", flush=True)
            links["fcb"] = True
        if dst:
            if (dst.client_id, dst.port_id) not in self._subscribers(me_out):
                self.client.subscribe_port(me_out, dst)
                print(f"connected to {dst.client_name}:{dst.name.strip()}", flush=True)
            links["qtractor"] = True
        if links != self._links:
            self._links = links
            self.on_status({"fcb": links["fcb"],
                            "fcbDetail": "" if links["fcb"] else "plug in the FCB1010's USB MIDI cable",
                            "routerQtractor": links["qtractor"]})

    def _close(self):
        """Clean exit: hand the FCB straight back to Qtractor, so a pedal on the old flash
        keeps playing (a universal flash's channel-16 addresses are ignored by the session)."""
        try:
            if self.restore_direct:
                src = self._find(self.source_name) if isinstance(self.source_name, str) else self._find(*self.source_name)
                dst = self._find(*self.dest_name)
                if src and dst and (dst.client_id, dst.port_id) not in self._subscribers(src):
                    self.client.subscribe_port(src, dst)
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        self.client.close()
        self.closed.set()
        self.on_status({"router": False, "routerDetail": "stopped"})


def router_running():
    """Is a router client on the ALSA sequencer (whoever owns it)?"""
    out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
    return f"'{CLIENT}'" in out


# ---------------------------------------------------------------- CLI
def table():
    t = Translator().table
    for bank in range(10):
        rows = [(sw, t.get((bank, sw), [])) for sw in range(1, 11)]
        if not any(events for _, events in rows):
            continue
        print(f"BANK {bank}")
        for sw, events in rows:
            a = address(bank, sw)
            addr = f"PC {a[2]}" if a[0] == "pc" else f"CC {a[2]}={a[3]}"
            sent = ", ".join(f"PC {e[2]} ch{e[1] + 1}" if e[0] == "pc" else f"CC {e[2]}={e[3]} ch{e[1] + 1}"
                             for e in events) or "(nothing)"
            print(f"  SW{sw:<2} ch16 {addr:<9} -> {sent}")


def bench(n=1000):
    """Added latency, router in -> router out, on test ports. A bench client sends each address
    from its own port into the router and stamps it; the router's output comes back into the
    bench client, stamped on arrival (same process, same monotonic clock). The baseline is the
    same hop with no router (bench out -> bench in direct), so `added` is the router's cost."""
    from alsa_midi import PortCaps, ProgramChangeEvent, SequencerClient, ControlChangeEvent
    bench_client = SequencerClient("FCB Router bench")
    tx = bench_client.create_port("tx", PortCaps.READ | PortCaps.SUBS_READ)
    rx = bench_client.create_port("rx", PortCaps.WRITE | PortCaps.SUBS_WRITE)
    table = Translator().table
    addrs = [a for a, events in sorted(table.items()) if events]

    def run_once(direct):
        stamps = []
        for i in range(n):
            bank, sw = addrs[i % len(addrs)]
            a = address(bank, sw)
            ev = ProgramChangeEvent(channel=a[1], value=a[2]) if a[0] == "pc" else \
                ControlChangeEvent(channel=a[1], param=a[2], value=a[3])
            want = 1 if direct else len(table[(bank, sw)])
            t0 = time.perf_counter_ns()
            bench_client.event_output(ev, port=tx)
            bench_client.drain_output()
            got, first = 0, None
            while got < want:
                e = bench_client.event_input(timeout=1)
                if e is None:
                    raise RuntimeError(f"bank {bank} SW{sw}: {got}/{want} messages came back")
                if e.type.name in ("PGMCHANGE", "CONTROLLER"):
                    got += 1
                    first = first or time.perf_counter_ns()
            stamps.append(((first - t0) / 1000, (time.perf_counter_ns() - t0) / 1000))
            time.sleep(0.001)
        return stamps

    def stats(xs):
        xs = sorted(xs)
        return {"median": xs[len(xs) // 2], "p99": xs[int(len(xs) * 0.99)], "max": xs[-1]}

    bench_client.subscribe_port(tx, rx)
    base = run_once(direct=True)
    bench_client.unsubscribe_port(tx, rx)
    router = Router(source=("FCB Router bench", "tx"), dest=("FCB Router bench", "rx"), name="FCB Router (bench)",
                    table=table, watch=False, restore_direct=False)
    threading.Thread(target=router.run, daemon=True).start()
    router.ready.wait(5)
    time.sleep(0.2)
    routed = run_once(direct=False)
    router.stop()
    time.sleep(0.4)
    bench_client.close()
    b, f, l = stats([x[0] for x in base]), stats([x[0] for x in routed]), stats([x[1] for x in routed])
    print(f"{n} presses over {len(addrs)} mapped switches (µs)")
    print(f"  direct link (no router)      median {b['median']:7.1f}  p99 {b['p99']:7.1f}  max {b['max']:7.1f}")
    print(f"  via router, first message    median {f['median']:7.1f}  p99 {f['p99']:7.1f}  max {f['max']:7.1f}")
    print(f"  via router, whole burst      median {l['median']:7.1f}  p99 {l['p99']:7.1f}  max {l['max']:7.1f}")
    print(f"  added by the router (median) {f['median'] - b['median']:7.1f}")
    return base, routed


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    if cmd == "table":
        return table()
    if cmd == "bench":
        return bench(int(args[1]) if len(args) > 1 else 1000)
    if cmd != "run":
        sys.exit(__doc__)
    log = lambda addr, events: os.environ.get("FCB_ROUTER_VERBOSE") and print(addr, events, flush=True)
    router = Router(on_events=log, on_status=lambda s: print(s, flush=True))
    signal.signal(signal.SIGTERM, lambda *_: router.stop())  # systemctl stop: exit cleanly
    try:
        router.run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
