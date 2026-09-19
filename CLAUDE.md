# oscmidi-bridge — notes for maintainers

OSC ↔ MIDI bridge between Ableton Live (via **AbletonOSC**) and a MIDI controller.
Python daemon, no dependency beyond `python-rtmidi`.

## The rule that outranks everything

**The bridge is never required to play.** No note, no sound, no keyboard zone goes
through it: it carries only Live control and display. If it dies mid-show the rig
keeps playing and only the feedback freezes. Any change that would break that rule
is to be refused.

## Design choices, and why

- **One MIDI channel on a port the controller already uses.** No extra virtual port:
  one more port is one more thing to configure on every key and one more to lose.
  Falls back to a virtual port only if the named one is missing — and *says so*.
- **No dependency but `python-rtmidi`.** The OSC codec is 80 lines in `osc.py`: one
  less package to reinstall after a Python upgrade.
- **Text cannot travel over MIDI.** Track and scene names go to `state.json`, which
  the client re-reads.
- **Re-subscribe every 30 s**: if Live restarts, subscriptions die silently.
  Re-subscribing is idempotent on the AbletonOSC side.
- **The bridge owns the reply port and fans out.** AbletonOSC *forces* its reply port
  and does not answer the source port, so only one process can receive Live's state.
  The bridge holds it — it serves the show — and relays to other clients.

## Traps, all of them paid for at least once

- **`start_listen` AND an initial `get`**: Live broadcasts nothing until asked.
  Without the `get`, state stays empty until the first change.
- **Some addresses accept no subscription** (`track_names`, `scenes/name`):
  AbletonOSC answers *Unknown OSC address*. Poll them instead.
- **OSC typing is significant**: AbletonOSC refuses an integer tempo and a float
  track index. Hence `$a.0` to force a float.
- **7 bits**: a value above 127 is clamped **and logged**, never silently truncated.
- **A bad configuration line refuses the *reload*, not the process**: the previous
  configuration stays live and the error is logged with its line number.
- **The master track is not in `song.tracks`**, and `tracks[-1]` silently resolves to
  the *last regular track*. See `patches/`.

## Working loop

```bash
python3 -m oscmidi_bridge --verify    # pre-show check
python3 -m oscmidi_bridge --ports     # write available MIDI ports into the config
python3 -m oscmidi_bridge             # foreground, for debugging
```

The configuration lives outside this repository — see `_config_par_defaut()`.
