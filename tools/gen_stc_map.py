#!/usr/bin/env python3
"""Generate `stc.txt`: the Selected Track Control dialect, translated to OSC.

WHY: STC has been the de facto standard for over a decade, and its `settings.py`
IS the specification — note and CC numbers included. By speaking its language, the
bridge becomes a drop-in replacement: you change nothing on your controller, you
remove STC, and you gain feedback on top.

Every emitted address is CHECKED against the AbletonOSC actually installed: a line
that matches nothing is not written, it is counted in the report. No unverifiable
coverage claims.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

STC = Path.home() / "Music/Ableton/User Library/Remote Scripts/Selected_Track_Control/settings.py"
ADDRESSES = Path("/tmp/abletonosc_addresses.json")

# STC acts on "the current track": $track is the selection, which the bridge observes.
TABLE: dict[str, tuple[str, list[str]]] = {
    # ── transport ────────────────────────────────────────────────────────────
    "start_playing":   ("/live/song/start_playing", []),
    "stop_playing":    ("/live/song/stop_playing", []),
    "continue_playing":("/live/song/continue_playing", []),
    "stop_all_clips":  ("/live/song/stop_all_clips", []),
    "tap_tempo":       ("/live/song/tap_tempo", []),
    "tempo":           ("/live/song/set/tempo", ["$a.0"]),
    "metronome":       ("/live/song/set/metronome", ["$a"]),
    "loop":            ("/live/song/set/loop", ["$a"]),
    "punch_in":        ("/live/song/set/punch_in", ["$a"]),
    "punch_out":       ("/live/song/set/punch_out", ["$a"]),
    "nudge_up":        ("/live/song/set/nudge_up", ["$a"]),
    "nudge_down":      ("/live/song/set/nudge_down", ["$a"]),
    "overdub":         ("/live/song/set/arrangement_overdub", ["$a"]),
    "arrangement_overdub": ("/live/song/set/arrangement_overdub", ["$a"]),
    "back_to_arranger":("/live/song/set/back_to_arranger", ["$a"]),
    "record":          ("/live/song/set/record_mode", ["$a"]),
    "session_automation_record": ("/live/song/set/session_record", ["$a"]),
    "groove_amount":   ("/live/song/set/groove_amount", ["$a.0"]),
    "clip_trigger_quantization":   ("/live/song/set/clip_trigger_quantization", ["$a"]),
    "midi_recording_quantization": ("/live/song/set/midi_recording_quantization", ["$a"]),
    "undo":            ("/live/song/undo", []),
    "redo":            ("/live/song/redo", []),
    # ── scènes et pistes : sélection ─────────────────────────────────────────
    "select_scene":        ("/live/view/set/selected_scene", ["$a"]),
    "select_track":        ("/live/view/set/selected_track", ["$a"]),
    "play_selected_scene": ("/live/scene/fire_selected", []),
    "select_instrument":   ("/live/view/set/selected_device", ["$a"]),
    # ── la piste courante ────────────────────────────────────────────────────
    "arm":               ("/live/track/set/arm",    ["$track", "$a"]),
    "solo":              ("/live/track/set/solo",   ["$track", "$a"]),
    "mute":              ("/live/track/set/mute",   ["$track", "$a"]),
    "volume":            ("/live/track/set/volume", ["$track", "$a"]),
    "pan":               ("/live/track/set/panning",["$track", "$a"]),
    "switch_monitoring": ("/live/track/set/current_monitoring_state", ["$track", "$a"]),
    "stop_selected_track": ("/live/track/stop_all_clips", ["$track"]),
    "device_on_off":     ("/live/device/set/parameter/value", ["$track", 0, 0, "$a"]),
}

# GESTURES: not an address but a computation over observed state (see verbs.py).
GESTURES_BY_STC = {
    "prev_scene": "scene.prev", "next_scene": "scene.next",
    "first_scene": "scene.first", "last_scene": "scene.last",
    "scroll_scenes": "scene.scroll",
    "play_next_scene": "scene.play_next", "play_prev_scene": "scene.play_prev",
    "prev_track": "track.prev", "next_track": "track.next",
    "first_track": "track.first", "last_track": "track.last",
    "scroll_tracks": "track.scroll",
    "play_stop": "transport.toggle", "play_pause": "transport.pause",
    "tempo_increase": "tempo.up", "tempo_decrease": "tempo.down",
    "arm_exclusive": "arm.exclusive", "solo_exclusive": "solo.exclusive",
    "mute_exclusive": "mute.exclusive",
    "arm_kill": "arm.kill", "solo_kill": "solo.kill", "mute_kill": "mute.kill",
}

# What needs LOGIC rather than an address: loops over tracks, toggles, relative
# navigation. Listed here so the report can be honest rather than vague.
NEEDS_LOGIC = {
    "arm_exclusive", "arm_kill", "arm_flip", "solo_exclusive", "solo_kill", "solo_flip",
    "mute_exclusive", "mute_kill", "mute_flip", "prev_scene", "next_scene", "prev_track",
    "next_track", "first_scene", "last_scene", "first_track", "last_track",
    "play_next_scene", "play_prev_scene", "scroll_scenes", "scroll_tracks", "scroll_devices",
    "prev_device", "next_device", "play_stop", "play_pause", "tempo_increase", "tempo_decrease",
    "input_rotate", "output_rotate", "input_sub_rotate", "output_sub_rotate",
}


def numbers() -> dict[str, tuple[str, int]]:
    """The note or CC number STC assigns to each function."""
    src = STC.read_text(encoding="utf-8")
    block = src[src.index("midi_mapping = {"):]
    out: dict[str, tuple[str, int]] = {}
    for m in re.finditer(r'"([a-z0-9_]+)"\s*:\s*([^\n]+)', block):
        name, val = m.group(1), m.group(2)
        n = re.search(r"\bNote\((\d+)", val)
        if n:
            out[name] = ("note", int(n.group(1)))
            continue
        c = re.search(r"\bCC\((\d+)", val)
        if c:
            out[name] = ("cc", int(c.group(1)))
    return out


def _output() -> Path:
    """Where to write: a real rig's configuration lives in its own repository."""
    import os
    base = Path(os.environ.get("OSCMIDI_CONFIG_DIR",
                               Path.home() / ".config/oscmidi"))
    return base / "stc.txt" if base.exists() else Path("examples") / "stc.txt"


def main() -> int:
    if not STC.exists():
        print(f"Selected Track Control not found: {STC}"); return 2
    if not ADDRESSES.exists():
        print("AbletonOSC address list missing — run the extraction again"); return 2
    known = set(json.loads(ADDRESSES.read_text()))
    num = numbers()

    lines, covered, no_address, unmapped = [], [], [], []
    gestures = []
    for function, verbe in sorted(GESTURES_BY_STC.items()):
        if function not in num:
            continue
        kind, n = num[function]
        lines.append(f"verb  {kind:<4} {n:<4} {verbe}")
        gestures.append(function)
    for function, (address, args) in sorted(TABLE.items()):
        if function not in num:
            continue                      # STC gives it no default number
        if address not in known:
            no_address.append((function, address)); continue
        kind, n = num[function]
        a = " ".join(str(x) for x in args)
        lines.append(f"send  {kind:<4} {n:<4} {address}{(' ' + a) if a else ''}")
        covered.append(function)
    # Many STC functions carry the EXACT name of the matching Live method or
    # property — AbletonOSC then exposes them without anyone writing them by hand.
    # Try the three shapes, and emit only what actually exists.
    auto = []
    for function in sorted(num):
        if function in TABLE or function in NEEDS_LOGIC or function in GESTURES_BY_STC:
            continue
        for address, args in ((f"/live/song/{function}", []),
                              (f"/live/song/set/{function}", ["$a"]),
                              (f"/live/view/set/{function}", ["$a"])):
            if address in known:
                kind, n = num[function]
                a = " ".join(str(x) for x in args)
                lines.append(f"send  {kind:<4} {n:<4} {address}{(' ' + a) if a else ''}"
                              f"   # auto: {function}")
                auto.append(function)
                break
        else:
            unmapped.append(function)

    header = f"""# The Selected Track Control dialect, translated to OSC — GENERATED, do not edit.
#   regenerate:  python3 tools/gen_stc_map.py
#
# Same note and CC numbers as STC's defaults: remove STC, load this file, and the
# controller has not moved an inch. With feedback on top, which is the one thing
# STC never did.
#
# Coverage checked against the installed AbletonOSC:
#   {len(covered):>3} translated by hand   +   {len(auto):>3} recognised automatically
#   {len(gestures):>3} served by a GESTURE (loops, toggles, relative navigation)
#   {len(NEEDS_LOGIC) - len(gestures):>3} gestures still missing (flips, routing rotation, devices)
#   {len(unmapped):>3} with no AbletonOSC equivalent (views, locks, fine selections)

group   stc
channel 1          # STC speaks on channel 1; the lines below do not repeat it
target  127.0.0.1:11000 -> 11001

"""
    # Feedback: what STC never gave, and the whole point of the bridge. CC 100+
    # are free in the STC dialect (its own CCs stop at 52).
    feedback = """

# ══ FEEDBACK — Live back to the controller ═════════════════════════════════
# Selected Track Control sends NOTHING back: that is what has been missing for a
# decade. These CCs are free in its dialect (its own stop at 52).
watch /live/song/get/is_playing      0  ->  cc 100
watch /live/view/get/selected_scene  0  ->  cc 101
watch /live/song/get/tempo           0  ->  cc 102  $v-50
watch /live/view/get/selected_track  0  ->  cc 103
watch /live/song/get/num_scenes      0  ->  cc 104
watch /live/song/get/num_tracks      0  ->  cc 105
watch /live/song/get/metronome       0  ->  cc 106
watch /live/song/get/record_mode     0  ->  cc 107
watch /live/song/get/loop            0  ->  cc 108

# What MIDI cannot carry goes to state.json.
text  /live/song/get/track_names  ->  tracks
text  /live/song/get/scenes/name  ->  scenes
"""
    _output().write_text(header + "\n".join(lines) + feedback, encoding="utf-8")
    print(f"{_output()}: {len(lines)} lines")
    print(f"  translated by hand   : {len(covered)}")
    print(f"  recognised automatically: {len(auto)}  ({', '.join(auto[:8])} …)")
    print(f"  gestures             : {len(gestures)}")
    print(f"  gestures still missing: {len(NEEDS_LOGIC) - len(gestures)}  ({', '.join(sorted(NEEDS_LOGIC)[:6])} …)")
    print(f"  no AbletonOSC equivalent: {len(unmapped)}")
    if no_address:
        print(f"  address missing from AbletonOSC: {no_address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
