# oscmidi-bridge — notes for maintainers

OSC ↔ MIDI bridge between any OSC application and a MIDI controller.
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

## Projections — the line that must not blur

**Nothing application-specific belongs in this repository.** Gestures, the
addresses to watch no matter what, the ones that refuse a subscription, and the
one that witnesses a document change: all four live in a projection module, and
the contract is in `projection.py`.

When adding anything, ask whether Reaper or QLab would need it. If not, it is the
projection's. That line is why [oscmidi-ableton](https://github.com/Beennnn/oscmidi-ableton)
exists, and letting it blur would undo the split.

## Traps, all of them paid for at least once

- **A gesture cannot be validated while parsing**: the projection is only known
  when the bridge starts. It is checked there instead — an unknown gesture would
  otherwise match nothing and stay silent for a whole show.
- **7 bits**: a value above 127 is clamped **and logged**, never silently
  truncated. Pitch bend is 14.
- **Message types differ on three points, all of them quiet when wrong**: length
  (Program Change and channel pressure are two bytes), whether there is a number
  to address at all (channel pressure and pitch bend have none), and value width.
  All three live in `midi.py`, in one table, and nowhere else.
- **An unknown message type is refused at PARSE time**, by `--verify`. A typo
  would otherwise match nothing and say nothing for a whole show.
- **Never split a config line on whitespace alone.** Application names contain
  spaces, and a plain `split()` hands OSC half a name. `tokenise` keeps quoted
  groups — and keeps the quotes, because they distinguish `"12"` from `12`.
- **A bad configuration line refuses the *reload*, not the process**: the previous
  configuration stays live and the error is logged with its line number.
- **The bridge owns the reply port and fans out.** Some OSC servers force their
  reply port and do not answer the source port, so only one process can receive
  their replies.

## Working loop

```bash
python3 -m oscmidi_bridge --verify    # pre-show check
python3 -m oscmidi_bridge --ports     # write available MIDI ports into the config
python3 -m oscmidi_bridge             # foreground, for debugging
```

The configuration lives outside this repository — see `_default_config()`; projections are ordinary importable modules.
