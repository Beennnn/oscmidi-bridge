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
- **Never split a config line on whitespace alone.** Live names contain spaces
  ("Ext. Out", "C-Cue Left") and a plain `split()` hands OSC half a name, which
  Live then fails to match without saying why. `tokenise` keeps quoted groups —
  and keeps the quotes, because they are what distinguishes `"12"` from `12`.
- **Output routing is matched by display name**, not by index: send the string
  Live shows ("Ext. Out", "1/2"). No index to resolve, and it survives a change
  of audio interface.
- **Neither Main nor Cue is in `song.tracks`.** `tracks[-1]` resolves silently to
  the LAST regular track, and `tracks[-2]` to the one before it — so both indices
  have to be intercepted before they reach the list. See `patches/`: -1 is the
  Main track, -2 a thin proxy over the Cue bus, whose level and routing hang off
  `song` rather than off any track.
- **`cue_volume` is attested, cue routing is not.** Live's own remote scripts use
  `song.cue_volume`; nothing in them mentions a cue routing attribute. The proxy
  therefore fetches routing by name and says which attribute is missing, instead
  of failing from somewhere deeper.
- **0 dB is 0.8498443365097046**, measured on a fresh master, not computed. A
  controller scale (0-127) lands half a decibel off when mapped linearly.

## Working loop

```bash
python3 -m oscmidi_bridge --verify    # pre-show check
python3 -m oscmidi_bridge --ports     # write available MIDI ports into the config
python3 -m oscmidi_bridge             # foreground, for debugging
```

The configuration lives outside this repository — see `_default_config()`.
