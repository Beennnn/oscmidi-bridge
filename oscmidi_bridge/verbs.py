"""Les verbes : ce qu'une adresse OSC seule ne sait pas faire.

Selected Track Control expose 127 fonctions ; 36 se traduisent par une adresse.
Les autres sont des **gestes** : « la scène suivante », « solo exclusif », « inverser
la lecture ». Live n'a pas d'adresse pour ça — ce sont des boucles ou des calculs
sur un état. La passerelle, elle, connaît cet état : elle observe déjà la sélection,
le nombre de pistes et de scènes, la lecture et le tempo.

Chaque verbe rend une LISTE de messages à envoyer. Aucun ne lit l'état directement :
il le reçoit, ce qui les rend testables sans Live.
"""

from __future__ import annotations

from typing import Any, Callable

Msg = tuple[str, list[Any]]
# état connu de la passerelle : scene, track, scenes (nombre), tracks (nombre),
# playing, tempo
Etat = dict[str, Any]


def _rel(v: int) -> int:
    """Décode un CC relatif en complément à deux — la convention des encodeurs.

    1-63 = vers le haut, 65-127 = vers le bas (65 valant −1). C'est ce qu'émettent
    les molettes ; une valeur absolue passerait pour un saut énorme.
    """
    return v if v < 64 else v - 128


def _borne(n: int, haut: int | None) -> int:
    n = max(0, n)
    return n if haut is None else min(n, max(0, haut - 1))


# ── navigation ───────────────────────────────────────────────────────────────

def scene_prev(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [_borne(int(e.get("scene", 0)) - 1, None)])]


def scene_next(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [_borne(int(e.get("scene", 0)) + 1, e.get("scenes"))])]


def scene_first(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene", [0])]


def scene_last(e: Etat, v: int) -> list[Msg]:
    n = e.get("scenes")
    return [("/live/view/set/selected_scene", [max(0, int(n) - 1)])] if n else []


def scene_scroll(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_scene",
             [_borne(int(e.get("scene", 0)) + _rel(v), e.get("scenes"))])]


def track_prev(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [_borne(int(e.get("track", 0)) - 1, None)])]


def track_next(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [_borne(int(e.get("track", 0)) + 1, e.get("tracks"))])]


def track_first(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track", [0])]


def track_last(e: Etat, v: int) -> list[Msg]:
    n = e.get("tracks")
    return [("/live/view/set/selected_track", [max(0, int(n) - 1)])] if n else []


def track_scroll(e: Etat, v: int) -> list[Msg]:
    return [("/live/view/set/selected_track",
             [_borne(int(e.get("track", 0)) + _rel(v), e.get("tracks"))])]


def scene_play_next(e: Etat, v: int) -> list[Msg]:
    m = scene_next(e, v)
    return m + [("/live/scene/fire", [m[0][1][0]])]


def scene_play_prev(e: Etat, v: int) -> list[Msg]:
    m = scene_prev(e, v)
    return m + [("/live/scene/fire", [m[0][1][0]])]


# ── transport ────────────────────────────────────────────────────────────────

def transport_toggle(e: Etat, v: int) -> list[Msg]:
    joue = bool(e.get("playing"))
    return [("/live/song/stop_playing" if joue else "/live/song/start_playing", [])]


def transport_pause(e: Etat, v: int) -> list[Msg]:
    # « pause » = arrêter sans revenir au début : Live reprend avec continue_playing.
    joue = bool(e.get("playing"))
    return [("/live/song/stop_playing" if joue else "/live/song/continue_playing", [])]


def tempo_nudge(e: Etat, v: int) -> list[Msg]:
    t = e.get("tempo")
    if t is None:
        return []
    return [("/live/song/set/tempo", [float(t) + _rel(v)])]


def tempo_up(e: Etat, v: int) -> list[Msg]:
    t = e.get("tempo")
    return [("/live/song/set/tempo", [float(t) + 1])] if t is not None else []


def tempo_down(e: Etat, v: int) -> list[Msg]:
    t = e.get("tempo")
    return [("/live/song/set/tempo", [float(t) - 1])] if t is not None else []


# ── exclusif et extinction, sur toutes les pistes ────────────────────────────
# STC les appelle *_exclusive et *_kill. Ce sont des boucles : Live n'a pas
# d'adresse « solo exclusif », il a un solo par piste.

def _boucle(prop: str, e: Etat, seule: bool) -> list[Msg]:
    n = e.get("tracks")
    if not n:
        return []           # sans le nombre de pistes on ne boucle pas à l'aveugle
    sel = int(e.get("track", 0))
    return [(f"/live/track/set/{prop}", [i, bool(seule and i == sel)]) for i in range(int(n))]


def arm_exclusive(e: Etat, v: int) -> list[Msg]:  return _boucle("arm", e, True)
def solo_exclusive(e: Etat, v: int) -> list[Msg]: return _boucle("solo", e, True)
def mute_exclusive(e: Etat, v: int) -> list[Msg]: return _boucle("mute", e, True)
def arm_kill(e: Etat, v: int) -> list[Msg]:  return _boucle("arm", e, False)
def solo_kill(e: Etat, v: int) -> list[Msg]: return _boucle("solo", e, False)
def mute_kill(e: Etat, v: int) -> list[Msg]: return _boucle("mute", e, False)


VERBES: dict[str, Callable[[Etat, int], list[Msg]]] = {
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
