# Kite Season

_Fifteen-year-old Ravi stepped on his classmate Ira's competition kite. The festival is on Saturday._

## World

**Logline:** A boy who broke a kite has two days to help build a better one — and to decide whether to admit it.

**Setting:** A small Konkan-coast town in the week of its kite festival. Monsoon is over; the sea wind is back.

**Art direction:** Soft painted anime with warm afternoon light, teal sea, saffron and coral festival colours, clean line art.

**Music:** Light acoustic guitar, flute and hand percussion, rising to strings on festival day.

## Characters

- **Ravi** (`ravi`, protagonist): Careful, kind, quietly funny. _Look:_ Slim Indian boy, messy black hair, brown eyes, white school shirt with rolled sleeves, grey trousers, a red thread bracelet. _Expressions:_ neutral, nervous, smile, determined, sheepish
- **Ira** (`ira`, friend): Sharp, stubborn, generous once she trusts you. _Look:_ Indian girl, long black braid, dark eyes, yellow kurta over jeans, paint-stained fingers, a kite-string reel clipped to her belt. _Expressions:_ neutral, angry, sad, smile, surprised, laugh
- **Mr. D'Souza** (`dsouza`, mentor): Gruff, generous, loves a bargain. _Look:_ Old Goan man, white stubble, round glasses, faded blue shirt, leather apron with pliers in the pocket. _Expressions:_ neutral, smile, stern

## Locations

- **School roof** (`school_roof`, day/evening): Flat concrete school rooftop with blue water tanks, washing lines and a low wall; the sea beyond.
- **D'Souza Repairs** (`repair_shop`, day/evening): Cramped repair shop: shelves of spools, bamboo strips, radios, a workbench under a hanging bulb.
- **Festival beach** (`beach`, day/evening): Wide grey-sand beach with fishing boats pulled up, festival flags and hundreds of kites in the sky.

## Endings

- **Two Hands, One String** (`e_together`, good): Ira and Ravi fly the rebuilt kite together, and it stays up longer than any kite on the beach.
- **Flying Alone** (`e_solo`, neutral): The kite flies, but Ira flies it alone. Ravi watches from the boats, still owing her the truth.

## The Broken Kite (`s_roof` · school_roof · day · calm)

> The lunch bell is still ringing when you reach the roof.
> Ira's kite lies against the water tank. Its spine is snapped clean in two.
_[show ira sad]_
**Ira** _(sad)_: Two months of work. Somebody stepped right on it.
_[show ravi nervous]_
> You did. This morning, backing away from the edge, you felt the crack under your shoe.
- [ ] Tell her it was you.
  - Ira: You? ...You came up here to tell me that?
  - Ira: At least you didn't let me blame Arjun.
- [ ] Promise to help her fix it.
  - Ira: Fix it? With what, glue and hope?
  - Ravi: With whatever it takes.
- [ ] Say nothing. Look at the sea.
  - The wind fills the silence. Ira doesn't look up.
**Ira** _(neutral)_: The festival is on Saturday. I need a new bamboo spine and proper line.
> A gust lifts the torn sail off the tank and snags it on the washing line above the stairwell.
- [ ] Climb the tank ladder and grab it.
  - The ladder is hot and wobbly. You unhook the sail with two fingers and jump down.
  - Ira: Okay. That was a little bit heroic.
- [ ] Wait for the wind to drop.
  - You wait. The gust passes, and the sail slides down into your hands.
  - Ira: Patient. My grandmother would like you.
_if bold >= 1:_
**Ira** _(smile)_: You're braver than you look, you know.
_if bold <= -1:_
**Ira** _(neutral)_: You always think before you move. That's useful, with kites.
**Ira** _(neutral)_: Mr. D'Souza has bamboo. Meet me at his shop after school.
_→ repair_shop · evening · playful_ After school, the repair shop smells of solder and old rope.
_[show dsouza neutral]_
_[show ira neutral]_
**Mr. D'Souza** _(stern)_: A competition spine? Good bamboo is forty rupees a strip.
- [ ] Pay with your bus money.
  - Mr. D'Souza: Spent like a true kite flyer. Here.
  - You'll be walking home this week.
- [ ] Offer to sweep the shop for it.
  - Mr. D'Souza: Broom's behind the door. Corners too.
  - Ira: I'll supervise.
- [ ] Ask for broken kites to strip for parts.
  - Mr. D'Souza: Clever boy. The box under the counter is full of them.
_if told_truth == 1:_
**Ira** _(smile)_: Ravi. Thanks for telling me the truth today. Most people wouldn't.
→ `s_workbench`


## After Closing (`s_workbench` · repair_shop · evening · calm)

> The shutters come down. Ira spreads the pieces across the workbench under the hanging bulb.
_[show ira neutral]_
**Ira** _(neutral)_: The line is the real problem. My old reel is one big bird's nest.
- [ ] Untangle it together, knot by knot.
  - It takes forty minutes. Somewhere around minute twenty, you both start laughing at nothing.
  - Ira: We are terrible at this.
- [ ] Cut out the tangle and splice the ends.
  - Ira: Bold. If the splice holds, you're a genius.
_[show dsouza neutral]_
**Mr. D'Souza** _(neutral)_: Closing time, you two. The bulb costs money.
- [ ] Ask Ira why this kite matters so much.
  - Ira: My dad built kites. This is the first one I've made without him.
  - She says it lightly. It isn't light.
- [ ] Ask Mr. D'Souza for festival tips.
  - Mr. D'Souza: Watch the flags on the boats. The wind tells them its plans first.
_if trust_ira >= 3:_
**Ira** _(smile)_: Same time tomorrow? Bring your terrible knots.
_if trust_ira < 3:_
**Ira** _(neutral)_: See you Saturday, I guess.
→ `s_beach`


## Wind Day (`s_beach` · beach · evening · tense)

> Saturday. The beach is a forest of strings and colour.
_[show ira neutral]_
_[show ravi nervous]_
**Ira** _(neutral)_: The wind's coming off the sea in gusts. We get one launch.
- [ ] Launch now, before it gets worse.
  - The kite leaps, dips, and catches. Your arms burn.
- [ ] Wait for the boat flags to settle.
  - You watch the flags, the way Mr. D'Souza said. Then: one long, steady breath of wind.
  - Ira: Now!
_♪ heroic_
> Halfway up, a gust bends the new spine like a bow.
- [ ] Hand Ira the reel. It's her kite.
  - Ira: Together?
  - Ravi: Together.
- [ ] Hold the line yourself.
  - The string sings in your fingers. The kite shudders, and holds.
- [ ] Brace it with the spare spine from the box.
  - The spare strip slides in behind the spine. When the next gust hits, the kite doesn't even flinch.
→ `e_together_scene` if trust_ira >= 3 and parts >= 2
→ `e_solo_scene` otherwise


## Two Hands, One String (`e_together_scene` · beach · evening · triumphant)

_[show ira laugh]_
> Two hands on one reel. The kite climbs above every other kite on the beach.
**Ira** _(laugh)_: Dad would have hated your knots. He'd have loved this.
**ENDING:** `e_together`


## Flying Alone (`e_solo_scene` · beach · evening · sad)

> The kite flies. Ira flies it alone, and you watch from the boats.
**Ira** _(smile)_: Thanks for the help, Ravi. Really.
> You still owe her something. Maybe next season.
**ENDING:** `e_solo`

