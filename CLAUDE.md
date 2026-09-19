# oscmidi-bridge — notes pour les sessions Claude

Passerelle **OSC ↔ MIDI** entre Ableton Live (via AbletonOSC) et le rig Stream Deck
(plugin trevligaspel). Daemon Python sous launchd, comme les autres `com.benoit.*`.

## La règle qui prime sur tout

**La passerelle n'est jamais nécessaire pour jouer.** Aucune note, aucun son,
aucune zone ne transite par elle : elle ne porte que du pilotage de Live et de
l'affichage. Si elle tombe en concert, le rig continue et seul le retour d'état
gèle. Toute évolution qui violerait ça est à refuser.

## Choix de conception, et pourquoi

- **Canal 16 d'« Ableton Loopback », en CC uniquement.** Vérifié le 2026-09-19 :
  les CC y sont **entièrement libres** (0 utilisé par le rig, 0 mappage Cmd+M dans
  le set Funk). Les **notes** du canal 16 sont prises par les déclencheurs FX et
  drums d'Evy — ne pas y toucher.
- **Pas de port virtuel.** On se branche sur le port que le Stream Deck utilise
  déjà (son `GlobalPort_1`). Un port de plus serait un port de plus à configurer
  dans chaque touche et à perdre quand Bome tombe. Repli sur un port virtuel
  seulement si « Ableton Loopback » est absent — et la passerelle le **dit**.
- **Aucune dépendance hors `python-rtmidi`** (déjà installé, 5.0.0). Le codec OSC
  fait 80 lignes dans `osc.py` : un paquet de moins à réinstaller après une mise à
  jour de Python.
- **Le texte ne passe pas en MIDI.** Noms de pistes et de scènes vont dans
  `state.json`, relu par le JS du Stream Deck avec `io.loadFile` — le motif déjà
  éprouvé par `songs/loader.js`.
- **Ré-abonnement toutes les 30 s** : si Live redémarre, les abonnements meurent
  sans prévenir. C'est idempotent côté AbletonOSC.

## Pièges

- **`start_listen` ET `get` initial** : Live ne diffuse rien tant qu'on n'a pas
  demandé. Sans le `get`, l'état reste vide jusqu'au premier changement.
- **Le typage OSC est significatif** : AbletonOSC refuse un tempo entier et un
  index de piste en float. D'où `$a.0` pour forcer le float.
- **7 bits** : une valeur > 127 est bornée **et journalisée**, jamais tronquée en
  silence.
- **Une ligne de configuration illisible refuse le démarrage** avec le numéro de
  ligne et le texte fautif. Le silence sur une commande rejetée est ce qui coûte
  des heures — voir le langage de trevligaspel.

## Commandes

```bash
python3 -m oscmidi_bridge --verify          # controle avant concert
python3 -m oscmidi_bridge                   # au premier plan, pour deboguer
launchctl bootstrap gui/$(id -u) launchd/com.benoit.oscmidi-bridge.plist
launchctl bootout   gui/$(id -u)/com.benoit.oscmidi-bridge
```
