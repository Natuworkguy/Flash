---
name: control-the-smart-home
description: Turn lights, plugs and fans on or off, set the thermostat, blinds and speakers, run scenes, and read sensors
---
smart_home works through the user's Home Assistant, once it is connected (Settings, Connectors, or /smarthome connect <address>).

- Call devices what the user calls them ("living room lamp", "thermostat"); the closest name in Home Assistant is used. When unsure what exists, list first, narrowed by a word ("kitchen", "temperature").
- turn_on, turn_off and toggle for lights, plugs, fans and switches; set for a light's brightness (0-100) and color, a thermostat's temperature or mode, blinds' position, a fan's percentage, a speaker's volume; activate for a scene or script ("movie night").
- state reads a device or sensor: how warm it is, whether the door is locked, what is playing.
- Changes may ask the user first; unlocking, opening a garage door and alarms always ask. If they say no, leave it.
- Several devices ("all the downstairs lights"): one call each, by the names list shows.
- Say what happened in a line ("The kitchen lights are off."), not the raw state.

## Examples

```
smart_home {"action": "list", "device": "kitchen"}
```

```
smart_home {"action": "set", "device": "living room lamp", "brightness": 30, "color": "warm white"}
```

```
smart_home {"action": "set", "device": "thermostat", "temperature": 21}
```

```
smart_home {"action": "activate", "device": "movie night"}
```
