---
name: plan-a-trip
description: Plan a trip day by day, on a map, with real places, travel times, costs and a packing list
---
1. Pin down what decides everything, in one form block if more than one is missing: where, dates, who is going, budget, pace (packed or slow), and anything fixed (a wedding, a flight already booked). Assume the rest and say what you assumed.
2. Find real places: maps nearby around where they will stay (sights, food, a pharmacy, the station), maps find for anything named. Read opening days and hours: a museum closed on the day you planned is the classic failure.
3. Getting there and back: web_search for flights, trains and buses with prices for their dates, and give two or three options in a compare block (price, time door to door, changes). Prices move: say when you checked and link where to book. Never book or pay without a clear yes.
4. Build the days around geography, not a list: one area per half day, maps route between the stops with walking or driving times, a gap for meals and rest, the big sight early or late to dodge crowds. One map block per day, its stops numbered in order with "route".
5. Money: a stats or card block with the total and its parts (travel, stay, food, tickets), in their currency, the sums done in code.
6. Hand over: the days in a checklist or steps block, a packing checklist for the weather (web_search the forecast or the climate for those dates), and offer to put the days in the calendar.

Pitfalls:
- Coordinates come from maps, never from memory. A place maps cannot find is said so, not guessed.
- Travel time is the route, not the straight line: two "close" sights across a river can be 40 minutes apart.
- Check visas, entry rules and holidays for their dates and passports (web_search the government page), and say it is worth confirming.

## Examples

```
maps {"action": "nearby", "what": "museum", "near": "Alfama, Lisbon", "radius": 2000, "limit": 8}
```

```
maps {"action": "route", "places": ["Castelo de São Jorge, Lisbon", "Sé de Lisboa", "Praça do Comércio, Lisbon"], "mode": "walking"}
```

```
web_search {"query": "Lisbon weather late April average temperature rain days", "max_results": 5}
```

```
calendar {"action": "add", "title": "Lisbon day 1: Alfama and the castle", "start": "2027-04-24 09:30", "end": "2027-04-24 18:00", "where": "Alfama, Lisbon"}
```
