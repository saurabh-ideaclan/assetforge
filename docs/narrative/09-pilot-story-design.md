# 09 · Pilot story design: *The Unrung*

Power Fantasy (regression, progression, revenge-rise, hidden power), korean-webnovel tradition, Western-inspired
high fantasy, global adults, simple English. Original; only techniques are borrowed.

## 1. Premise sheet

| | |
|---|---|
| **Title** | The Unrung |
| **Logline** | Executed as a heretic after the kingdom's great bell cracked, a rope-hand ranked worthless wakes forty days earlier with a forbidden power that silences any strength he has heard, and one season to break the man who cracked the bell before the purge takes his sister. |
| **Reader's fantasy** | Be the one ranked as nothing who can switch the mighty off, and choose when they find out. |
| **Want / cost** | Save his sister Nell and bring down High Reckoner Sable. Every use of the Hush costs his hearing; every public step up the ranks brings Sable's attention closer. |
| **Antagonist / agenda** | Aurelian Sable, High Reckoner of the Bellhold, Seventh Bell. The dying king will read a Reform at Midwinter letting the Unrung train. Sable believes rank is the truth of a soul and the Reform will ruin the kingdom, so he will crack the Crown Bell at the Reckoning, blame hidden "Hushers" among the Unrung, purge them, and rule as Regent. |
| **Lost on failure** | Nell and every Unrung family in Highbell; the throne passes to Sable; Lucan dies on the same steps again. |
| **Central question (answered in ch 10)** | Can a man the kingdom ranks as nothing break the man who decides what everyone is worth, and what will he have become when he does? |
| **Why this story** | A ladder learned in two chapters (seven bells), a hidden power that works *against* it, and a clock the reader has already seen end in death. |

## 2. The regression and the power system

**First life (writer only; reveal one or two facts per chapter, never as a dump):**
- Lucan Dray (19), Unrung rope-hand, raises his sister Nell (12). At the Lesser Reckoning, Conrad Kell overstruck a bell, lost his arm's Toll and his house's standing.
- At the Midwinter Reckoning the Crown Bell cracked mid-toll. Sable declared a Quiet Heresy among the Unrung.
- Conrad, bought with his house's restoration, swore he saw Lucan in the bell-loft that night.
- Nell was taken in the roundup and died in the Marsh Pens before spring.
- In the cells, the deaf heretic Brother Hesketh Lind taught Lucan cold-iron tuning and told him he was a born Husher. Hesketh was taken before he finished.
- On the scaffold, Maren Calloway read the sentence. Lucan's Hush woke for one breath. In that silence he heard Sable's Toll still humming inside the cracked bell. Then the axe. He wakes on Day 1, the morning of the Lesser Reckoning, 40 days before Midwinter.

**Toll and the Seven Bells (learnable in two chapters):**
- *Toll*: inner force (strength, impact, shield), seen as rings of light. Rank = the highest of the Bellhold's seven bells your Toll makes sing at a Reckoning.
- Ranks: **Unrung** (servants; forbidden to train) · **First–Second Bell** (guards, soldiers) · **Third–Fourth** (knights) · **Fifth–Sixth** (commanders, lords' champions) · **Seventh / Crown** (three living people, Sable among them).
- Rule: higher Bell wins unless something changes the fight.

**The Hush (hidden):**
- Lucan stops a Toll he has *heard* (one exchange is enough) for about three heartbeats.
- Limit: he can Hush no one more than **two Bells above his own public rank**. Unrung reaches Second Bell; to touch Sable (Seventh) he must stand at Fifth.
- Cost: hearing. A light Hush leaves minutes of white silence; a heavy one takes a point of `hearing` for good (5 → 0; text boxes go muted).
- Hidden because Hushers burn under the Quiet Heresy law; Sable's *listening forks* go dead near one.

**Public progression (cold iron: training against the Cracked Bell in the undercroft, which only Hesketh knows):**

| Chapter | Public rank | Hush reach | Milestone |
|---|---|---|---|
| 1–2 | Unrung | Second | First Hush of this life |
| 3 | First (or Second, by choice) | Third (Fourth) | Rings a bell at a Second Hearing |
| 5 | Third | Fifth | Bellhold or tourney trial |
| 7 | Fourth | Sixth | Wins a public duel |
| 9 | Fifth | **Seventh** | Two Hushers in tune, or a forced trial |

## 3. Cast

| Name | Role | Want | Wants from Lucan | Lie they believe | Voice | Arc |
|---|---|---|---|---|---|---|
| **Lucan Dray** (RESERVED) | Protagonist, Unrung rope-hand, regressor | Nell alive, Sable broken | — | "I can do this alone, in the dark." | Short, flat, certain. "Forty days. Enough." | From a hidden weapon to a man who chooses what to reveal, and to whom |
| **Aurelian Sable** (RESERVED) | Antagonist, High Reckoner, Seventh Bell | Stop the Reform; rule as Regent | A model "merit boy" to parade, then a Husher to burn | "Rank is mercy. Without it the weak are crushed by chaos." | Courteous, unhurried, precise. "Everyone rings true eventually." | Moves every chapter; falls according to the ending |
| **Maren Calloway** (RESERVED) | Romance, Bell-Auditor, Fifth Bell, Sable's ward | Prove the Reckoning is honest | The truth about how an Unrung rang | "If the measure is fair, the outcome is just." | Exact, dry, asks one question too many. "That's not an answer. Try again." | Learns her brother Tobin was ranked Unrung and taken because he was a Husher; chooses the truth over her guardian |
| **Conrad Kell** (RESERVED) | Rival-or-ally, noble heir, Third Bell candidate | Restore House Kell | Silence, or a debt repaid | "My name is worth any testimony." | Proud, quick, cruel when cornered. "Pull straight, rope." | First life he sold Lucan; now he becomes witness or Sable's creature |
| Brother Hesketh Lind | Mentor, deaf bellwright, lapsed Husher | Let the Hush die with him | A reason to teach again | "The Hush is a curse." | Hand-signs; blunt. *"You push like a goat."* | Teaches, then stands up in public |
| Prince Felix Ardane | Wildcard, the king's second son, secretly Unrung | The throne and the Reform | A secret blade | "Anyone can be bought if you know their price." | Light, mocking, never quite joking. "Charming. Now what can you do?" | Ally or broker; his pact is the second ending's spine |
| Nell Dray, Tobin Calloway, Wat | Stakes: sister; Maren's captive brother; a stable boy the forks mistake | | | | | |

## 4. Season architecture

Clock: every chapter opens with "Day X · N days to the Reckoning". It changes only once, as a stated antagonist move in ch 5.

**Stats.** Relationships: `trust_maren`, `trust_conrad`, `trust_hesketh`, `trust_felix`, `sable_suspicion`. Personality: `mercy`, `pride`. Resource: `hearing` (5). Every stat is read at least twice after ch 3.

| Ch (day) | Title | Opens | Closes | Antagonist move | Costly decision (flag: options → trade-off) | Read in | Reversal | Ending turn |
|---|---|---|---|---|---|---|---|---|
| 1 (D1) | Unrung | Can he change anything? | Is the Hush real in this life? (yes) | Sable attends the Reckoning in person | `flag_saved_conrad`: Hush at the source (save the man who killed him; −2 hearing, seen moving) / Hush only the wave (Nell safe, unseen; Conrad maimed) | 2, 3, 7, E1 | Unrung saves a knight | "For one breath my bells were silent. Bar the doors." |
| 2 (D1) | The Listening | Who does the fork take? | How to fool a fork | Fork sweep of every throat | `flag_wat_taken`: let Wat be taken (safe, mercy −1) / take ten lashes for him (suspicion +2, Wat's family owe him) | 3, 4, 8 | The fork misreads | Maren's lantern on the undercroft stair: "The fork went dead. Down here." |
| 3 (D1–6) | First Bell | Can an Unrung rank? | Yes, publicly | Summons Lucan *and his sister* | `flag_rang_second`: ring First (safe) / ring Second (pride, suspicion +2, Hush reach +1) | 4, 5, 8 | Rope-hand rings a bell | Sable's seal: "Bring your sister." |
| 4 (D7) | The Reckoner's Offer | What does Sable want? | A parade piece, and Nell as hostage | Patronage + Unrung census "for winter bread" | **Route** `flag_felix_pact`: Felix's pact (Nell hidden in the palace; Felix holds your secret) → 5A / Sable's patronage as cover (inside the Bellhold; Nell in his choir) → 5B | 5, 9B, E2 | The villain is kind | Felix: "You rang a bell you can't touch. So did I." / Sable: "Welcome home." |
| 5A / 5B (D18) | The Prince's Table / The Reckoner's Boy | Who in power is Sable's? | One name, one key | **Moves the Reckoning to Day 28** (the king is failing) | 5A `flag_hushed_councillor`: Hush a Sable councillor mid-vote (hearing −1) / win with foreknowledge (Felix now fears you). 5B `flag_informed`: sell Conrad's debts for the stair key (trust_conrad −2) / steal it (hearing −1, suspicion +2) | 7, 9B, 10 | Rank: Third Bell | "The Reckoning will be held in ten days." |
| 6 (D20) | The Quiet Room | How will Sable crack the bell? | A captive Husher: Tobin Calloway | Names Maren "Hush-finder" | `flag_told_maren`: tell her (Tobin *and* yourself) / keep it (safe now; in ch 9 she finds out as a betrayal) | 9, 10, E1 | Maren's brother is alive | Behind the door, a voice signs Maren's childhood name |
| 7 (D23) | Conrad's Price | Will Conrad sell him again? | Not if he's beaten and believed | Buys Conrad's testimony (the first-life deal) | `flag_conrad_witness`: spare and recruit (works fully only if `flag_saved_conrad` or `trust_conrad`≥3) / break him before the court (pride; Sable loses his witness, so do you) | 9, 10, E1 | Rank: Fourth Bell; onlookers turn | Conrad: "He has a second witness. It's someone you love." |
| 8 (D26) | Two Doors | Proof: a man or a book? | Only one can be carried out | Loft trap (scaled by `sable_suspicion`) | **Route** `flag_tobin_freed` (Tobin, the living proof → 9A) / `flag_took_ledger` (Sable's ledger of bought ranks and plans → 9B) | 9, 10, E1/E2 | Hushes a Sixth-Bell captain | Bells above toll the alarm |
| 9A / 9B (D27) | The Brother / The Ledger | Who pays for the theft? | Hesketh | Arrests Hesketh; proclaims Lucan the Husher | 9A `flag_hesketh_rescued`: free Hesketh tonight (hearing −1, suspicion max) / hold the plan (Hesketh brought to the steps to burn). 9B `flag_hidden_oath`: swear to stay unseen forever for Felix's Reform (offered only if `flag_felix_pact` or `trust_felix`≥3) / refuse | 10, endings | Rank: Fifth Bell | "Bring Lucan Dray to the Reckoning alive." |
| 10 (D28) | Midwinter | The central question | The central question | Strikes the Crown Bell | Final action, options gated by flags (below) | — | First life's scaffold, reversed | Ending |

**Endings** (each gate is foreshadowed by a line right after the flag is set):

| Ending | Gate | Why it satisfies |
|---|---|---|
| **Every Bell Rings** (public) | `flag_tobin_freed` + `flag_told_maren` + `flag_conrad_witness` | On the steps where he died, Lucan Hushes Sable before the kingdom while Tobin, Maren and Conrad testify. The heresy law falls. `mercy`≥2: Sable is re-ranked Unrung; else Lucan silences his Toll for good and the crowd fears him. The romance is earned. |
| **The Prince's Hand** (shadow) | `flag_took_ledger` + `flag_hidden_oath` | The court takes Sable with the ledger; Lucan never leaves the rope gallery. Felix is heir, the Reform passes, Lucan is the most powerful unseen man in Carrowmere. Hushers stay outlaws. |
| **The Last Rope** (fallback) | Otherwise (also choosable) | Lucan Hushes the Crown Bell itself as Sable strikes. No crack, no purge; Nell lives, the first-life wound changed at the same place. He spends all his hearing; Sable keeps office but loses the Regency. `trust_maren`≥3: Maren signs to him in Hesketh's hand-speech. A real win at a personal price, with a sequel hook. |

## 5. Chapter beat sheets (1–3)

Choice types: **E** expression, **R** relationship, **P** plot (costly), **Rt** route.

### Chapter 1 · Unrung (Day 1 · 40 days)
**Scenes:** S1 Reckoning Hall rope line, morning → S2 Reckoning floor → S3 hall aftermath → S4 gallery steps.
**Opening turn:** (1) "The last thing I heard was the axe." (2) A rope-master's shove: "Pull, or lose the hand." (3) Nell crosses the hall with a water jug. *Alive.* (4) Maren calls a name from the dais and a merchant's daughter makes the First Bell sing. (5) "Forty days. Then the Crown Bell breaks, and they burn us for it. Nell first." (6) Conrad Kell walks out to strike. *Today his own bell takes his arm.*
**Beats:** the Reckoning shows gap and ladder → Conrad overstrikes → bell-shock rolls toward Nell → the Hush; onlookers' faces → Sable descends.

| # | Type | Choice | Costs / sets |
|---|---|---|---|
| 1 | E | Nell: "Why are you staring?" → "Glad you're here" / "Kitchens. Now." | flavour; the second keeps her off the floor |
| 2 | R | Conrad: "Pull straight, rope." → warn him that the bell lip is cracked / lower your eyes / "It's the Bellhold's bell, my lord." | trust_conrad +1 (and Maren overhears, trust_maren +1) / — / pride +1 |
| 3 | **P** | Conrad's strike: **Hush him at the source** / **Hush only the wave** | `flag_saved_conrad`=1, hearing −2, sable_suspicion +1, mercy +1 / =0, hearing −1, mercy −1 |
| 4 | R | Maren: "You were closest. What did you see?" → "Check the lip" / "Nothing, my lady" | trust_maren +1 ("How does a rope-hand know that?") / — |
| 5 | E | Watching Sable: "I'll stop it before it starts" / "I'll take everything you took" | mercy +1 / pride +1 |
| 6 | R | Sable: "Who stood nearest?" → step forward / stay in line | pride +1, sable_suspicion +1 (he learns the name) / — |

**CG:** the Hush, with rings of light frozen mid-air, Lucan's raised hand, blood at one ear, and Nell behind him.
**Cliffhanger:** Sable, softly: "For one breath, my bells were silent. Bar the doors."

### Chapter 2 · The Listening (Day 1, afternoon to night · 40 days)
**Scenes:** S1 barred Reckoning Hall, afternoon → S2 rope-hands' loft, evening → S3 undercroft and the Cracked Bell, night.
**Opening turn:** doors barred; Auditors walk the lines with silver forks; Lucan's ear still rings. Hesketh's lesson from the cell: *the fork listens for silence, so give it noise.*
**Callback:** `flag_saved_conrad`=1: Conrad, arm whole, says "You moved. I saw you move." =0: Conrad on a stretcher, whispering to Sable's clerk (the first-life deal begins early).

| # | Type | Choice | Costs / sets |
|---|---|---|---|
| 1 | R | Conrad (variant by flag) → deflect with bell-lore / meet his eyes | trust_conrad ±1 |
| 2 | **P** | The fork goes dead beside Wat: **let them take him** / **"Take me. I stood there first."** | `flag_wat_taken`=1, mercy −1 / =0, ten lashes, sable_suspicion +2, mercy +1 |
| 3 | E | Nell in the loft → "If anyone asks, you're the laundress's girl" / "Nothing bad happens this winter." | `flag_nell_warned` (read ch 4) / — |
| 4 | R | Hesketh is deaf; sign the rope-cant he taught you → the truth ("You taught me this in a cell that hasn't happened yet") / a lie ("My mother was a Husher") | trust_hesketh +2, `flag_told_hesketh` / +1, he holds back the Hush's limits (ch 5 surprise) |
| 5 | E | *"What is the power for?"* → Nell / the Reckoner's tower / never kneeling again | mercy +1 / pride +1 / — |
| 6 | P-lite | Cold iron: push until the Cracked Bell answers / stop when he taps your wrist | faster First Bell, sable_suspicion +1 / slower, safe |

**CG:** Lucan's palms on the Cracked Bell, rings of light sinking into the iron, Hesketh watching.
**Cliffhanger:** a lantern on the stair, and Maren: "The fork went dead again. Down here."

### Chapter 3 · First Bell (Day 1 night, then Day 6 · 40→34 days)
**Scenes:** S1 undercroft stair, night → S2 practice yard, Day 6 morning → S3 Hearing Hall, noon → S4 rope loft, dusk.
**Opening turn:** Maren's fork at his throat in the dark, Hesketh signing frantically behind her. Then: the Second Hearing is in five days, and an Unrung needs a ranked sponsor.
**Callbacks:** `flag_wat_taken`=0: Maren sees the lash marks: "You're the one who took Wat's lashes." Sponsor options depend on `flag_saved_conrad`.

| # | Type | Choice | Costs / sets |
|---|---|---|---|
| 1 | R | On the stair → let Hesketh lie for you / step into the light: "Rope-hands are allowed curiosity." | trust_hesketh +1, Hesketh is questioned (read ch 8) / pride +1, trust_maren +1 |
| 2 | R | Sponsor: Conrad (if saved: "I owe a debt I can't name") / Maren (if trust_maren≥2) / Hesketh (lapsed Third Bell) | trust +1 with the sponsor; Sable notes who (ch 4) |
| 3 | E | A Third-Bell squire: "Unrung don't petition. They pull." → "Then watch me pull." / silence | pride +1 / — |
| 4 | **P** | At the bells: **ring only the First** / **ring the Second** | `flag_rang_second`=0, safe / =1, pride +1, sable_suspicion +2, Hush reach +1 |
| 5 | R | Nell runs to him in the cheering → lift her up / send her away before anyone sees her face | pride +1 / `flag_nell_unseen` (Sable's clerk can't find her name) |
| 6 | R | Maren at dusk: "Second Hearings fail. How?" → "Someone taught me. He's gone." / "Maybe your measures were wrong before." / "Ask me off duty." | trust_maren +1 each; the second draws out her Unrung brother who "had no Toll at all" (Tobin plant) |

**Payoff:** the bell sings for an Unrung; the squire drops his ledger; rope-hands pull the ropes in rhythm (the "rope-cheer"). **CG:** Lucan under the ringing bell, Maren on the dais not writing.
**Cliffhanger:** a page with Sable's seven-ring seal: "The High Reckoner will receive the rope-hand who rang. Tomorrow. Bring your sister." (`flag_nell_unseen`: "…whose name the clerk could not find.")

**Free-to-paid unlock:** after ch 3, on the summons: a rank just won, a question open (what does Sable want with Nell?). Show a "the story remembers" card (Conrad, Wat, which bell) first. Never after a loss.

## 6. Pipeline brief (paste-ready)

**(a) PREMISE**
Lucan Dray, an Unrung rope-hand ranked as worthless, was executed as a heretic when the Crown Bell cracked at the Midwinter Reckoning. He wakes forty days earlier, on the morning of the Lesser Reckoning, with a forbidden power, the Hush: he can silence any Toll he has heard, at the cost of his own hearing. High Reckoner Aurelian Sable cracked the bell to purge the Unrung and seize the regency, and Lucan's sister Nell died in that purge. Now Lucan must climb the seven Bells in secret, decide whom to trust, and break Sable before the Reckoning, without becoming a man who ranks people the way Sable does.

**(b) CANON**
World: medieval kingdom of Carrowmere, capital Highbell. The Bellhold (tower, temple, academy) ranks every adult at Reckonings.
Power: Toll, inner force seen as rings of light. Rank = highest of seven bells your Toll makes sing: Unrung (servants, forbidden to train), First–Second (guards), Third–Fourth (knights), Fifth–Sixth (commanders), Seventh (three living). Hushers silence a heard Toll for three heartbeats, up to two Bells above their own rank, paid in hearing; they burn under the Quiet Heresy law; listening forks go dead near them. Cold iron (the undercroft's Cracked Bell) lets Unrung train.
Cast: Lucan Dray (19, regressor; wants Nell alive, Sable broken). Aurelian Sable (High Reckoner, Seventh; will crack the Crown Bell with a captive Husher to stop the king's Reform and become Regent). Maren Calloway (Bell-Auditor, Fifth, Sable's ward; wants an honest Reckoning; her brother Tobin is the captive). Conrad Kell (noble heir, Third; wants his house restored). Brother Hesketh Lind (deaf mentor, lapsed Husher; signs). Prince Felix Ardane (secretly Unrung; wants the throne and the Reform). Nell Dray (12). Wat (stable boy).
First life: Conrad was maimed, then bought to testify; the bell cracked; Nell died in the Marsh Pens; Hesketh taught Lucan in the cells; Maren read his sentence; on the scaffold Lucan heard Sable's Toll inside the bell.
Endings: Every Bell Rings (public truth); The Prince's Hand (ledger, secret oath to Felix); The Last Rope (he Hushes the bell itself, loses his hearing; Nell lives).

**(c) INSTRUCTIONS**
Open every chapter with "Day X · N days to the Reckoning" and a situation already going wrong. The reader learns rank, gap and goal in ch 1. Sable or his agents act on screen every chapter. Use one or two first-life facts per chapter, never more. At most two new terms per chapter. Every costly choice is called back by its flag, in a scene that names what the player did. Never re-offer a decided choice. Inner monologue is short and declarative. Each chapter has one payoff with onlookers reacting, and ends on a reveal, confrontation or decision that the next chapter pays. Show every Hush's cost.

**(d) EXCLUSIONS**
No real-world places, religions, brands or modern idioms. No status windows, game menus or numbers floating in the air. No explicit sex or gore. No reveal of Tobin before ch 6 or of the full plan before ch 9. No "whatever you chose". No cruelty without motive.

**(e) RESERVED NAMES**
Lucan Dray · Aurelian Sable · Maren Calloway · Conrad Kell

**(f) WORLD PREFERENCES**
- `technology`: Medieval; bell-founding is the high craft; lanterns, quills, no gunpowder.
- `politics`: An ailing king, a church-academy that ranks everyone, and a Reform edict due at Midwinter.
- `magic`: Toll, ranked by seven bells; the Hush is outlawed; cold iron trains the forbidden.
- `hierarchy`: Rank by Bell, displayed on collar rings; Unrung serve and may not train.
- `religion`: The Chantry of the Seven Bells; heresy is "silence".
- `conflict`: A purge prepared under the cover of law; a revenge carried out in secret.
- `progression`: One Bell per major milestone (ch 3, 5, 7, 9); the Hush's reach is always rank + 2.

**(g) LOCATIONS**
- Reckoning Hall, Bellhold: morning (ceremony) and late afternoon (barred, lamp-lit).
- Undercroft with the Cracked Bell: night, a single lantern.
- Rope-hands' loft under the tower: dusk, light through rope holes.
- Bellhold steps in snow: Midwinter dawn (the scaffold of the first life, and the finale).

## 7. Risks

- **The Hush makes Lucan unbeatable.** Ch 1–3 must enforce the rank + 2 cap and show the hearing cost; one problem per chapter the Hush can't solve.
- **Terminology overload.** Seven terms arrive by ch 3. Check no chapter adds more than two and rank is always a number.
- **Too much regression lore.** If ch 1 explains more than the axe, Nell and "forty days", cut it.
- **Conrad's maimed branch goes dead.** At `flag_saved_conrad`=0 he must still matter (Sable re-tunes him by ch 7). Read ch 2–3 in both variants.
- **The romance becomes an approval meter.** Maren needs friction (fork at his throat, duty, the brother plant), never warmth without a cost or question.
