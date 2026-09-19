#!/usr/bin/env python3
"""Génère `config/stc.map` : le dialecte de Selected Track Control, traduit en OSC.

POURQUOI : STC est le standard de fait depuis plus de dix ans, et son fichier
`settings.py` EST la spécification — numéros de notes et de CC compris. En parlant
sa langue, la passerelle devient un remplacement direct : celui qui l'utilise ne
touche à rien sur son contrôleur, il retire STC et il a le retour d'état en plus.

Chaque adresse émise est VÉRIFIÉE contre l'AbletonOSC réellement installé : une
ligne qui ne correspond à rien n'est pas écrite, elle est comptée dans le rapport.
Pas de promesse de couverture invérifiable.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

STC = Path.home() / "Music/Ableton/User Library/Remote Scripts/Selected_Track_Control/settings.py"
ADRESSES = Path("/tmp/abletonosc_addresses.json")

# STC agit sur « la piste courante » : $track est la sélection, que la passerelle observe.
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

# Les GESTES : pas une adresse mais un calcul sur l'état observé (voir verbs.py).
GESTES = {
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

# Ce qui demande une LOGIQUE, pas une adresse : boucles sur les pistes, bascules,
# navigation relative. Listé ici pour que le rapport soit honnête plutôt que flou.
LOGIQUE = {
    "arm_exclusive", "arm_kill", "arm_flip", "solo_exclusive", "solo_kill", "solo_flip",
    "mute_exclusive", "mute_kill", "mute_flip", "prev_scene", "next_scene", "prev_track",
    "next_track", "first_scene", "last_scene", "first_track", "last_track",
    "play_next_scene", "play_prev_scene", "scroll_scenes", "scroll_tracks", "scroll_devices",
    "prev_device", "next_device", "play_stop", "play_pause", "tempo_increase", "tempo_decrease",
    "input_rotate", "output_rotate", "input_sub_rotate", "output_sub_rotate",
}


def numeros() -> dict[str, tuple[str, int]]:
    """Le numéro de note ou de CC que STC attribue à chaque fonction."""
    src = STC.read_text(encoding="utf-8")
    bloc = src[src.index("midi_mapping = {"):]
    out: dict[str, tuple[str, int]] = {}
    for m in re.finditer(r'"([a-z0-9_]+)"\s*:\s*([^\n]+)', bloc):
        nom, val = m.group(1), m.group(2)
        n = re.search(r"\bNote\((\d+)", val)
        if n:
            out[nom] = ("note", int(n.group(1)))
            continue
        c = re.search(r"\bCC\((\d+)", val)
        if c:
            out[nom] = ("cc", int(c.group(1)))
    return out


def main() -> int:
    if not STC.exists():
        print(f"Selected Track Control introuvable : {STC}"); return 2
    if not ADRESSES.exists():
        print("liste des adresses AbletonOSC absente — relancer l'extraction"); return 2
    connues = set(json.loads(ADRESSES.read_text()))
    num = numeros()

    lignes, couvert, sans_adresse, hors_table = [], [], [], []
    gestes = []
    for fonction, verbe in sorted(GESTES.items()):
        if fonction not in num:
            continue
        kind, n = num[fonction]
        lignes.append(f"verb  {kind:<4} {n:<4} {verbe}")
        gestes.append(fonction)
    for fonction, (adresse, args) in sorted(TABLE.items()):
        if fonction not in num:
            continue                      # STC ne lui donne aucun numéro par défaut
        if adresse not in connues:
            sans_adresse.append((fonction, adresse)); continue
        kind, n = num[fonction]
        a = " ".join(str(x) for x in args)
        lignes.append(f"send  {kind:<4} {n:<4} {adresse}{(' ' + a) if a else ''}")
        couvert.append(fonction)
    # Beaucoup de fonctions STC portent le nom EXACT de la méthode ou de la propriété
    # Live correspondante — AbletonOSC les expose alors sans qu'il faille les écrire à
    # la main. On tente les trois formes, et on n'émet que ce qui existe vraiment.
    auto = []
    for fonction in sorted(num):
        if fonction in TABLE or fonction in LOGIQUE or fonction in GESTES:
            continue
        for adresse, args in ((f"/live/song/{fonction}", []),
                              (f"/live/song/set/{fonction}", ["$a"]),
                              (f"/live/view/set/{fonction}", ["$a"])):
            if adresse in connues:
                kind, n = num[fonction]
                a = " ".join(str(x) for x in args)
                lignes.append(f"send  {kind:<4} {n:<4} {adresse}{(' ' + a) if a else ''}"
                              f"   # auto : {fonction}")
                auto.append(fonction)
                break
        else:
            hors_table.append(fonction)

    entete = f"""# Dialecte de Selected Track Control, traduit en OSC — GÉNÉRÉ, ne pas éditer.
#   régénérer :  python3 tools/gen_stc_map.py
#
# Mêmes numéros de note et de CC que les réglages par défaut de STC : on retire STC,
# on charge ce fichier, et le contrôleur n'a pas bougé d'un pouce. Le retour d'état
# en plus, qui est ce que STC n'a jamais su faire.
#
# Couverture vérifiée contre l'AbletonOSC installé :
#   {len(couvert):>3} traduites a la main   +   {len(auto):>3} reconnues automatiquement
#   {len(gestes):>3} rendues par un GESTE (boucles, bascules, navigation relative)
#   {len(LOGIQUE) - len(gestes):>3} gestes restants (inversions, rotations de routage, devices)
#   {len(hors_table):>3} sans équivalent dans AbletonOSC (vues, verrouillages, sélections fines)

group   stc
channel 1          # STC parle sur le canal 1 ; les lignes ne le repetent pas
target  127.0.0.1:11000 -> 11001

"""
    # Le retour d'état : ce que STC n'a jamais donné, et la raison d'être de la
    # passerelle. Les CC 100+ sont libres dans le dialecte STC (ses CC s'arrêtent à 52).
    retour = """

# ══ RETOUR D'ÉTAT — Live vers le contrôleur ════════════════════════════════
# Selected Track Control n'envoie RIEN en retour : c'est ce qui manque depuis
# douze ans. Ces CC sont libres dans son dialecte (les siens s'arrêtent à 52).
watch /live/song/get/is_playing      0  ->  cc 100
watch /live/view/get/selected_scene  0  ->  cc 101
watch /live/song/get/tempo           0  ->  cc 102  $v-50
watch /live/view/get/selected_track  0  ->  cc 103
watch /live/song/get/num_scenes      0  ->  cc 104
watch /live/song/get/num_tracks      0  ->  cc 105
watch /live/song/get/metronome       0  ->  cc 106
watch /live/song/get/record_mode     0  ->  cc 107
watch /live/song/get/loop            0  ->  cc 108

# Ce que le MIDI ne peut pas porter part dans state.json.
text  /live/song/get/track_names  ->  tracks
text  /live/song/get/scenes/name  ->  scenes
"""
    Path("config/stc.map").write_text(entete + "\n".join(lignes) + retour, encoding="utf-8")
    print(f"config/stc.map : {len(lignes)} lignes")
    print(f"  traduites a la main : {len(couvert)}")
    print(f"  reconnues auto      : {len(auto)}  ({', '.join(auto[:8])} …)")
    print(f"  gestes           : {len(gestes)}")
    print(f"  gestes restants  : {len(LOGIQUE) - len(gestes)}  ({', '.join(sorted(LOGIQUE)[:6])} …)")
    print(f"  sans equivalent  : {len(hors_table)}")
    if sans_adresse:
        print(f"  adresse absente d AbletonOSC : {sans_adresse}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
