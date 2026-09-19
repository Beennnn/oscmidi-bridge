"""La passerelle : MIDI ↔ OSC, plus un fichier d'état pour ce que le MIDI ne porte pas.

RÈGLE DE CONCEPTION, à ne pas transgresser : **la passerelle n'est jamais nécessaire
pour jouer.** Aucune note, aucun son, aucune zone ne transite par elle. Elle ne
porte que du pilotage de Live et de l'affichage. Si elle tombe en concert, le rig
continue et seul le retour d'état gèle. C'est ce qui fait la différence entre un
maillon de plus et un point de défaillance de plus.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import rtmidi

from . import osc
from .mapping import Mapping
from .verbs import VERBES

# Port MIDI par défaut : celui que le Stream Deck utilise DÉJÀ.
#
# On ne crée pas de port virtuel : le plugin trevligaspel émet vers « Ableton
# Loopback » (son GlobalPort_1), et Bome le fournit en permanence. Un port de plus
# serait un port de plus à configurer dans chaque touche, et un de plus à perdre
# quand Bome tombe. On se branche sur l'existant et on filtre sur le canal.
PORT_DEFAUT = "Ableton Loopback"


class Bridge:
    def __init__(self, m: Mapping, state_file: Path, log=print, port_name: str = PORT_DEFAUT,
                 config: Path | None = None):
        self.m = m
        self.config = config          # fichier charge, pour le rechargement a chaud
        self._mtime = self._empreinte()
        self.state_file = state_file
        self.log = log
        self.port_name = port_name
        self.state: dict[str, object] = {}
        self._stop = threading.Event()
        # index des correspondances, pour ne pas balayer les listes à chaque message
        self._sends = {(s.kind, s.channel, s.number): s for s in m.sends}
        self._verbs = {(v.kind, v.channel, v.number): v for v in m.verbs}
        self._watches: dict[str, list] = {}
        for w in m.watches:
            self._watches.setdefault(w.address, []).append(w)
        self._dernier: dict[int, float] = {}   # debit des watch limites
        self._sale = False                     # etat modifie depuis la derniere ecriture
        self._ecrit = 0.0                      # date de la derniere ecriture
        self._texts: dict[str, list] = {}
        for t in m.texts:
            self._texts.setdefault(t.address, []).append(t)

    # ── rechargement a chaud ────────────────────────────────────────────────
    def _empreinte(self) -> float:
        """Date de modification la plus recente du dossier de configuration.

        On regarde tout le dossier, pas seulement le fichier charge : un `include`
        edite ne changerait pas la date du fichier principal, et le rechargement
        passerait a cote sans rien dire.
        """
        if not self.config:
            return 0.0
        try:
            return max(f.stat().st_mtime for f in self.config.parent.glob("*.map"))
        except ValueError:
            return 0.0

    def _indexer(self) -> None:
        self._sends = {(x.kind, x.channel, x.number): x for x in self.m.sends}
        self._verbs = {(v.kind, v.channel, v.number): v for v in self.m.verbs}
        self._watches, self._texts = {}, {}
        for w in self.m.watches:
            self._watches.setdefault(w.address, []).append(w)
        for t in self.m.texts:
            self._texts.setdefault(t.address, []).append(t)

    def _recharger(self) -> None:
        """Relit la configuration sans redemarrer.

        Une erreur de syntaxe NE DOIT PAS arreter la passerelle : on garde la
        configuration precedente et on le dit. Editer un fichier pendant une
        repetition ne peut donc pas couper le retour d'etat au milieu d'un morceau.
        """
        from .mapping import load
        try:
            neuf = load(self.config)
        except Exception as e:
            self.log(f"configuration REFUSEE, l'ancienne reste en place —\n{e}")
            return
        self.m = neuf
        self._indexer()
        self._abonner()
        self.log(f"rechargee : {len(neuf.sends)} commandes · {len(neuf.verbs)} gestes · "
                 f"{len(neuf.watches)} retours · {len(neuf.texts)} textes")

    # ── MIDI ────────────────────────────────────────────────────────────────
    @staticmethod
    def _trouver(ports: list[str], nom: str) -> int | None:
        for i, p in enumerate(ports):
            if nom.lower() in p.lower():
                return i
        return None

    def _ouvrir_midi(self):
        self.midi_in = rtmidi.MidiIn()
        self.midi_out = rtmidi.MidiOut()
        i_in = self._trouver(self.midi_in.get_ports(), self.port_name)
        i_out = self._trouver(self.midi_out.get_ports(), self.port_name)
        if i_in is None or i_out is None:
            # Bome absent : on ouvre un port virtuel pour que la passerelle démarre
            # quand même. Elle ne sert alors à rien, mais elle le DIT et elle ne
            # meurt pas — le watchdog n'aurait rien à relancer.
            self.log(f"port « {self.port_name} » introuvable — Bome tourne-t-il ? repli sur un port virtuel")
            self.midi_in.open_virtual_port("OSC Bridge In")
            self.midi_out.open_virtual_port("OSC Bridge Out")
        else:
            self.midi_in.open_port(i_in)
            self.midi_out.open_port(i_out)
            self.log(f"MIDI sur « {self.midi_in.get_ports()[i_in]} »")
        self.midi_in.set_callback(self._sur_midi)

    def _sur_midi(self, evenement, _data=None):
        message, _dt = evenement
        if len(message) < 3:
            return
        statut, d1, d2 = message[0], message[1], message[2]
        kind = "cc" if 0xB0 <= statut <= 0xBF else "note" if 0x90 <= statut <= 0x9F else None
        if kind is None:
            return
        canal = (statut & 0x0F) + 1
        cle = (kind, canal, d1)
        v = self._verbs.get(cle)
        if v is not None:
            # Un geste : il calcule sur l'état observé et rend plusieurs messages.
            for adresse, args in VERBES[v.name](self._etat(), d2):
                self._envoyer(adresse, args)
            return
        s = self._sends.get(cle)
        if s is None:
            return
        val = s.transfo(d2) if s.transfo else d2
        # Substitutions disponibles dans les arguments :
        #   $a    la valeur du CC, après transformation      $a.0  la même, forcée en float
        #   $track la piste SÉLECTIONNÉE, $scene la scène sélectionnée
        # Les deux dernières sont ce qui rend le dialecte de Selected Track Control
        # traduisible : STC agit sur « la piste courante », notion qu'AbletonOSC
        # n'a pas — il veut un index. La passerelle le connaît, elle l'observe.
        subs = {"$a": val, "$a.0": float(val),
                "$track": int(self.state.get("_track", 0)),
                "$scene": int(self.state.get("_scene", 0))}
        args = [subs.get(a, a) if isinstance(a, str) else a for a in s.args]
        self._envoyer(s.address, args)

    def _envoyer(self, adresse: str, args: list):
        try:
            self.sock.sendto(osc.encode(adresse, args), (self.m.host, self.m.send_port))
        except Exception as e:
            self.log(f"envoi OSC {adresse} : {e}")

    def _etat(self) -> dict:
        """L'état que les gestes consultent — jamais lu ailleurs que par eux."""
        return {"scene": self.state.get("_scene", 0), "track": self.state.get("_track", 0),
                "scenes": self.state.get("_scenes"), "tracks": self.state.get("_tracks"),
                "playing": self.state.get("_playing"), "tempo": self.state.get("_tempo")}

    def _emettre_cc(self, w, valeur: float):
        if w.every_ms:
            # Limitation de débit : un VU-mètre envoie des dizaines de messages par
            # seconde et le deck ne sait pas se redessiner aussi vite. On laisse
            # tomber les valeurs intermédiaires plutôt que d'engorger le MIDI.
            maintenant = time.time() * 1000
            precedent = self._dernier.get(id(w), 0)
            if maintenant - precedent < w.every_ms:
                return
            self._dernier[id(w)] = maintenant
        v = int(round(valeur))
        if not 0 <= v <= 127:
            # Le MIDI est en 7 bits : on le DIT plutôt que de tronquer en silence.
            self.log(f"{w.source} : {w.address} = {valeur} hors 0-127, borné")
            v = max(0, min(127, v))
        statut = (0xB0 if w.kind == "cc" else 0x90) | (w.channel - 1)
        self.midi_out.send_message([statut, w.number, v])

    # ── OSC ─────────────────────────────────────────────────────────────────
    def _ouvrir_osc(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.m.recv_port))
        self.sock.settimeout(0.5)
        self.log(f"OSC → {self.m.host}:{self.m.send_port}, retour sur {self.m.recv_port}")

    def _abonner(self):
        """Live ne diffuse rien tant qu'on n'a pas demandé.

        Il faut donc un `start_listen` ET un `get` initial, sinon l'état reste vide
        jusqu'au premier changement — et une touche allumée à tort est pire qu'une
        touche éteinte.
        """
        # Ces six-là sont observés QUOI QU'IL ARRIVE : ce sont eux qui rendent les
        # gestes calculables, même si la configuration ne les renvoie pas en MIDI.
        essentiels = ["/live/view/get/selected_track", "/live/view/get/selected_scene",
                      "/live/song/get/num_scenes", "/live/song/get/num_tracks",
                      "/live/song/get/is_playing", "/live/song/get/tempo"]
        for adresse in list(dict.fromkeys(list(self._watches) + list(self._texts) + essentiels)):
            # Les listes (track_names, scenes/name) n'ont PAS de start_listen : AbletonOSC
            # repond « Unknown OSC address ». On se contente de les relire au rappel.
            if adresse not in self._texts:
                self.sock.sendto(osc.encode(adresse.replace("/get/", "/start_listen/")),
                                 (self.m.host, self.m.send_port))
            self.sock.sendto(osc.encode(adresse), (self.m.host, self.m.send_port))
        self.log(f"{len(self._watches)} abonnements, {len(self._texts)} textes")

    def _sur_osc(self, adresse: str, args: list):
        # La sélection courante est retenue quoi qu'il arrive : c'est elle qui rend
        # « la piste courante » exprimable, même si aucun watch ne la renvoie en MIDI.
        if adresse == "/live/view/get/selected_track" and args:
            self.state["_track"] = args[0]
        elif adresse == "/live/view/get/selected_scene" and args:
            self.state["_scene"] = args[0]
        elif adresse == "/live/song/get/num_scenes" and args:
            self.state["_scenes"] = args[0]
        elif adresse == "/live/song/get/num_tracks" and args:
            self.state["_tracks"] = args[0]
        elif adresse == "/live/song/get/is_playing" and args:
            self.state["_playing"] = args[0]
        elif adresse == "/live/song/get/tempo" and args:
            self.state["_tempo"] = args[0]
        if adresse.startswith("/live/") and args:
            self._sale = True
        for w in self._watches.get(adresse, []):
            if w.arg < len(args):
                v = args[w.arg]
                v = 1 if v is True else 0 if v is False else v
                if isinstance(v, (int, float)):
                    self._emettre_cc(w, w.transfo(v) if w.transfo else v)
        for t in self._texts.get(adresse, []):
            # Le texte ne passe pas en MIDI : il va dans le fichier que le JS du
            # Stream Deck relit — le motif déjà éprouvé par songs/loader.js.
            self.state[t.key] = args if len(args) != 1 else args[0]
            self._sale = True

    def _ecrire_etat(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)   # remplacement atomique : jamais de fichier à moitié écrit

    # ── boucle ──────────────────────────────────────────────────────────────
    def run(self):
        self._ouvrir_osc()
        self._ouvrir_midi()
        self._abonner()
        dernier_rappel = 0.0
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                data = None
            except OSError as e:
                self.log(f"socket : {e}")
                time.sleep(1)
                continue
            if data:
                # Redistribution : AbletonOSC force ses reponses sur 11001 et ne
                # repond pas au port source, donc un seul processus peut les lire.
                # La passerelle le detient — c'est elle qui sert le concert — et
                # relaie aux autres clients (le serveur MCP, un moniteur...).
                for cible in self.m.fanout:
                    try:
                        self.sock.sendto(data, cible)
                    except OSError:
                        pass          # un client absent ne doit rien interrompre
                msg = osc.decode(data)
                if msg:
                    self._sur_osc(*msg)
            # Ecriture groupee de l'etat : au plus quatre fois par seconde. Sans ca on
            # reecrirait le fichier a chaque message recu — le meme reflexe que le
            # debit des VU, qui avait sature le CPU du plugin MIDI le 13/09.
            if self._sale and time.time() - self._ecrit > 0.25:
                self._ecrit = time.time(); self._sale = False
                self._ecrire_etat()
            # Rechargement a chaud : on edite le fichier, on sauve, la passerelle suit.
            emp = self._empreinte()
            if emp > self._mtime:
                self._mtime = emp
                self._recharger()
            # Si Live redémarre, nos abonnements meurent avec lui sans prévenir :
            # on les repose périodiquement. C'est idempotent côté AbletonOSC.
            if time.time() - dernier_rappel > 30:
                dernier_rappel = time.time()
                self._abonner()

    def stop(self):
        self._stop.set()
