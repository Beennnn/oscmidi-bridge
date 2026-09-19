# oscmidi-bridge

**Une passerelle OSC ↔ MIDI entre Ableton Live et un rig de scène.** Les touches et
molettes envoient du MIDI comme avant ; Live répond, et **l'état revient** — ce que
le MIDI seul n'a jamais su faire.

> Pensée pour un rig live : elle n'est **jamais nécessaire pour jouer**. Aucune note,
> aucun son, aucune zone n'y transite. Si elle tombe en concert, le rig continue et
> seul le retour d'état gèle.

## Ce qu'elle fait

```
  Stream Deck ──MIDI canal 16──►  passerelle  ──OSC──►  AbletonOSC ──► Live
                                      ▲                                  │
       touches allumées ◄──MIDI CC────┴──────────OSC (état)◄─────────────┘
       noms de pistes  ◄──state.json──┘
```

- **commandes** : un CC entrant devient un message OSC, avec un typage explicite
  (`12` entier, `12.0` float, `"x"` chaîne) — AbletonOSC refuse un index de piste
  envoyé en float ;
- **gestes** : ce qu'une adresse seule ne sait pas faire — solo exclusif, scène
  suivante, bascule lecture/arrêt — calculés sur l'état observé ;
- **retour d'état** : les valeurs de Live repartent en CC, avec un débit limitable
  (`every 120ms`) indispensable pour les VU-mètres ;
- **textes** : ce que le MIDI ne peut pas porter (noms de pistes et de scènes) part
  dans un `state.json` que le client relit.

## Installer

```bash
pip install python-rtmidi            # seule dépendance
python3 -m oscmidi_bridge --ports    # inscrit les ports MIDI dans la configuration
python3 -m oscmidi_bridge --verify   # contrôle avant concert
python3 -m oscmidi_bridge            # au premier plan
```

Côté Live : [AbletonOSC](https://github.com/ideoforms/AbletonOSC) dans un emplacement
de Control Surface. Rien à régler par set, par piste ou par clip.

## La configuration

Un fichier texte, une instruction par ligne, aucune imbrication, `#` commente jusqu'à
la fin de la ligne. Voir [`examples/common.txt`](examples/common.txt).

```sh
channel 16
send  cc 24   /live/song/tap_tempo
send  cc 25   $v+50   /live/song/set/tempo $a.0
verb  cc 61   scene.next
watch /live/song/get/is_playing  0  ->  cc 100
text  /live/song/get/track_names    ->  tracks
```

Elle est **rechargée à chaud** : éditer et sauver suffit. Et une faute de syntaxe
**ne coupe pas** la passerelle — l'ancienne configuration reste en place, l'erreur est
journalisée avec son numéro de ligne et le texte fautif. Éditer pendant une répétition
ne peut donc pas couper le retour d'état au milieu d'un morceau.

La configuration d'un rig réel ne vit pas dans ce dépôt : elle décrit une installation
précise. La passerelle la cherche via `OSCMIDI_CONFIG`, puis
`~/dev/music/rig-config/oscmidi/common.txt`, puis l'exemple livré.

## Faire cohabiter plusieurs clients OSC

AbletonOSC **force** son port de réponse à 11001 et ne répond pas au port source :
un seul processus peut recevoir l'état de Live. La passerelle le détient — c'est elle
qui sert le concert — et redistribue à qui veut :

```sh
fanout  127.0.0.1:11101      # par exemple un serveur MCP
```

## Licence

MIT.
