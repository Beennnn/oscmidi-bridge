"""Gestures: what a single OSC address cannot express.

Some controller functions are not one address but a *move*: "next scene",
"exclusive solo", "toggle playback". Live has no address for those — they are
loops, or arithmetic over some current state. The bridge knows that state: it
already watches the selection, the track and scene counts, playback and tempo.

Each gesture returns a LIST of messages to send. None of them reads the state
directly — it is passed in, which makes them testable without Live running.
"""

from __future__ import annotations

from typing import Any, Callable

Msg = tuple[str, list[Any]]
# State known to the bridge: scene, track, scenes (count), tracks (count),
# playing, tempo
State = dict[str, Any]


def _rel(v: int) -> int:
    """Decode a relative CC in two's complement — the encoder convention.

    1-63 means up, 65-127 means down (65 being −1). That is what endless knobs
    send; reading it as an absolute value would look like a huge jump.
    """
    return v if v < 64 else v - 128


def _clamp(n: int, upper: int | None) -> int:
    n = max(0, n)
    return n if upper is None else min(n, max(0, upper - 1))


# ── navigation ───────────────────────────────────────────────────────────────

def scene_prev(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [_clamp(int(e.get("scene", 0)) - 1, None)])]


def scene_next(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [_clamp(int(e.get("scene", 0)) + 1, e.get("scenes"))])]


def scene_first(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [0])]


def scene_last(e: State, v: int) -> list[Msg]:
    n = e.get("scenes")
    return [("/live/view/set/selected_scene", [max(0, int(n) - 1)])] if n else []


def scene_scroll(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene",
             [_clamp(int(e.get("scene", 0)) + _rel(v), e.get("scenes"))])]


def track_prev(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [_clamp(int(e.get("track", 0)) - 1, None)])]


def track_next(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [_clamp(int(e.get("track", 0)) + 1, e.get("tracks"))])]


def track_first(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [0])]


def track_last(e: State, v: int) -> list[Msg]:
    n = e.get("tracks")
    return [("/live/view/set/selected_track", [max(0, int(n) - 1)])] if n else []


def track_scroll(e: State, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track",
             [_clamp(int(e.get("track", 0)) + _rel(v), e.get("tracks"))])]


def scene_play_next(e: State, v: int) -> list[Msg]:
    m = scene_next(e, v)
    return m + [("/live/scene/fire", [m[0][1][0]])]


def scene_play_prev(e: State, v: int) -> list[Msg]:
    m = scene_prev(e, v)
    return m + [("/live/scene/fire", [m[0][1][0]])]


# ── transport ────────────────────────────────────────────────────────────────

def transport_toggle(e: State, v: int) -> list[Msg]:
    playing = bool(e.get("playing"))
    return [("/live/song/stop_playing" if playing else "/live/song/start_playing", [])]


def transport_pause(e: State, v: int) -> list[Msg]:
    # "pause" = stop without rewinding: Live resumes with continue_playing.
    playing = bool(e.get("playing"))
    return [("/live/song/stop_playing" if playing else "/live/song/continue_playing", [])]


def tempo_nudge(e: State, v: int) -> list[Msg]:
    t = e.get("tempo")
    if t is None:
        return []
    return [("/live/song/set/tempo", [float(t) + _rel(v)])]


def tempo_up(e: State, v: int) -> list[Msg]:
    t = e.get("tempo")
    return [("/live/song/set/tempo", [float(t) + 1])] if t is not None else []


def tempo_down(e: State, v: int) -> list[Msg]:
    t = e.get("tempo")
    return [("/live/song/set/tempo", [float(t) - 1])] if t is not None else []


# ── exclusive and kill, across every track ───────────────────────────────────
# These are loops: Live has no "exclusive solo" address, it has one solo per track.

def _loop(prop: str, e: State, only_selected: bool) -> list[Msg]:
    n = e.get("tracks")
    if not n:
        return []           # without the track count we do not loop blindly
    sel = int(e.get("track", 0))
    return [(f"/live/track/set/{prop}", [i, bool(only_selected and i == sel)]) for i in range(int(n))]


def arm_exclusive(e: State, v: int) -> list[Msg]:  return _loop("arm", e, True)
def solo_exclusive(e: State, v: int) -> list[Msg]: return _loop("solo", e, True)
def mute_exclusive(e: State, v: int) -> list[Msg]: return _loop("mute", e, True)
def arm_kill(e: State, v: int) -> list[Msg]:  return _loop("arm", e, False)
def solo_kill(e: State, v: int) -> list[Msg]: return _loop("solo", e, False)
def mute_kill(e: State, v: int) -> list[Msg]: return _loop("mute", e, False)


GESTURES: dict[str, Callable[[State, int], list[Msg]]] = {
    "scene.prev": scene_prev, "scene.next": scene_next,
    "scene.first": scene_first, "scene.last": scene_last,
    "scene.scroll": scene_scroll,
    "scene.play_next": scene_play_next, "scene.play_prev": scene_play_prev,
    "track.prev": track_prev, "track.next": track_next,
    "track.first": track_first, "track.last": track_last,
    "track.scroll": track_scroll,
    "transport.toggle": transport_toggle, "transport.pause": transport_pause,
    "tempo.nudge": tempo_nudge, "tempo.up": tempo_up, "tempo.down": tempo_down,
    "arm.exclusive": arm_exclusive, "solo.exclusive": solo_exclusive,
    "mute.exclusive": mute_exclusive,
    "arm.kill": arm_kill, "solo.kill": solo_kill, "mute.kill": mute_kill,
}
