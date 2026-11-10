# Genre and tradition research (evidence for genres.yaml and traditions.yaml)

Method: web search plus fetches in November 2026. Several primary pages blocked fetching (Royal Road's Genres & Tags page returned HTTP 403; one blog fetch came back empty). Where a claim rests on a secondary or vendor source, it is marked. No statistics are used beyond the two quoted below. Nothing here describes any specific work's plot, characters or setting.

Tags: **[VERIFIED]** = stated by a cited source. **[EDITORIAL]** = our synthesis of technique from reading the genre; plausible, not sourced.

## 1. Korean web novel / manhwa

Sources
- https://en.wikipedia.org/wiki/Web_novels_in_South_Korea (platforms, serialization, pricing, genre groups)
- https://www.youngju.dev/blog/storytelling/web-novel-grammar.en (essay; blog-level, search snippet only)
- https://en.namu.wiki/w/%EC%9B%B9%EC%86%8C%EC%84%A4/%ED%8A%B9%EC%A7%95/%EC%84%9C%EC%88%A0 (community wiki on traits; secondary)
- https://noveling.dev/guide/en/blog/korean-web-novel-platforms/ (platform guide; secondary)

- [VERIFIED] Naver Web Novel launched in January 2013 and KakaoPage in April 2013; readers buy episodes (typically about 100 won each) or use wait-for-free models (Wikipedia).
- [VERIFIED] Genres are commonly grouped as romance, fantasy, martial arts and modern; romance is the most produced and consumed. Wikipedia quotes one 2015 Naver store split of 64.08% romance, 17.23% sci-fi and fantasy, 11.78% martial arts, 4.36% mystery. That is one platform in one month, ten years old; do not generalize it.
- [VERIFIED] Writers are advised to end episodes on exciting moments or cliffhangers (Wikipedia).
- [VERIFIED, secondary] Episodes are short and frequent, about 3,000 to 6,000 Korean characters, five to seven releases a week; "refreshing" (cider-like) pacing is a stated trait. Sources disagree on exact numbers and mix characters with words.
- [VERIFIED, blog] Regression, possession and reincarnation are the usual openers and set the protagonist's motive within an episode.
- [EDITORIAL] Explicit hierarchy, a named antagonist per arc, escalation every few episodes, short declarative monologue, and reaction beats as payoffs. These match how the form reads but were not quoted from an editor interview; none was found.

## 2. Japanese light novel / web-posted serial

Sources
- https://www.animenewsnetwork.com/feature/2020-06-12/welcome-to-the-isekai-otome-game-villainesses/.159287
- https://www.nippon.com/en/japan-topics/g01206/isekai-boom-offers-better-lives-in-other-worlds.html
- https://en.wikipedia.org/wiki/Progression_fantasy (LitRPG section: isekai overlap)

- [VERIFIED] The reincarnated-villainess line began as a web serial on a user-generated site in July 2014, and its foreknowledge premise is described as borrowed from Korean web fiction (ANN and secondary commentary).
- [VERIFIED] Many isekai stories draw on classic Japanese RPG conventions (hero, demon lord, pseudo-medieval society); isekai often overlaps with LitRPG.
- [EDITORIAL] Voice-forward narration, genre self-awareness, ensemble of types, comedy-warmth mix. Searches did not return first-person/chapter-structure sources.

## 3. Chinese web novel

Sources
- https://www.wuxiaworld.com/page/talisman-emperor/glossary and https://www.wuxiaworld.com/page/unsheathed/glossary (per-novel glossaries; realm names vary by work)
- https://en.wikipedia.org/wiki/Xianxia

- [VERIFIED] Xianxia is a fantasy-fied relative of wuxia with immortals and metaphysical themes; qi cultivation is central; stage terms (early, middle, late, peak) are widely used; realm names differ by novel.
- [EDITORIAL] Face, sect hierarchy, opportunity hunts, dismissed-then-shown-wrong beats. Wuxiaworld's general glossary could not be fetched; these come from reading, not citation. Length and chapter count claims are format-level generalization.

## 4. Western fantasy / progression / LitRPG

Sources
- https://en.wikipedia.org/wiki/Progression_fantasy
- https://www.royalroad.com/support/knowledgebase/98 (tag page; 403 on fetch, not read)
- Reader discussion mirrors of r/royalroad (community opinion only)

- [VERIFIED] Progression fantasy centres on intentional, measurable increase in power; the term was coined in 2019 by Andrew Rowe and Will Wight. LitRPG puts visible game stats at the core and the term dates to 2013; GameLit covers game settings without necessarily leveling.
- [EDITORIAL] Costs for power, rules-tested systems, found-family anchors. Royal Road's own tag wording was not verified.

## 5. Short-form drama

Sources
- https://www.npr.org/2025/03/19/nx-s1-5330470/micro-drama-soap-opera-app
- https://filmthreat.com/features/what-are-the-characteristics-of-vertical-micro-dramas-key-insights-on-length-and-format/
- https://vitrina.ai/blog/what-is-micro-drama-a-guide-to-the-new-short-form-sensation/ (vendor blog)

- [VERIFIED] Episodes are roughly a minute or a few minutes (sources vary, 60-90 seconds most cited); cliffhanger endings; early episodes free, later episodes paid at a cliffhanger. Several sources are vendor marketing.
- [EDITORIAL] Hook-then-emotional-turn structure, role readability, hidden-identity reversals.

## 6. Cinematic interactive drama

- No source was fetched for this tradition. All of it is [EDITORIAL], drawn from general knowledge of choice-based narrative design. Treat as the weakest-evidenced profile.

## 7. Power-fantasy subgenre family

Sources: the Progression fantasy and Xianxia pages above, the Korean web novel pages, and the villainess pieces above.
- [VERIFIED] progression, LitRPG/system, cultivation realms, regression/possession/reincarnation as openers, hunter and academy as common frames (blog and ranking-site level), villainess premise with death-flag mechanic and foreknowledge. The "return hunter" label has no formal definition; we do not use it.
- [EDITORIAL] The `mechanics`, `chapter_beats`, `pairs_well`, `conflicts_with` and `cliches_to_avoid` fields throughout genres.yaml, and the `kind` assignments.

## Five things a writer must get right: Korean-webnovel-style power fantasy, text plus sprites, with choices

1. **Establish the gap and the ladder in the first scenes.** The reader needs the protagonist's low standing, the visible ranks, and the first goal, within a minute or two of play. Use a status reversal or a premise event (regression, system prompt) as the opening, not backstory.
2. **Make power legible and costly.** Show growth as a number, a rank, a reaction or a new capability, and give each use a limit. Without cost, tension disappears; the overpowered-protagonist combination needs a narrative reason (a problem that force cannot solve).
3. **Pay off on a rhythm and end every chapter on a turn.** One small payoff per chapter (a win, a reaction shot, a reward) and an ending on a reveal, confrontation or choice. Cliffhangers must be paid in the next chapter.
4. **Make choices express strategy and character, and remember them.** Choices should pick between methods (bluff, prepare, ally) rather than right or wrong, and at least one later scene must call back to an earlier choice. Reconverge branches on pillars so the status reversal still lands.
5. **Use the sprite format for reaction beats.** Expression changes on onlookers are the payoff; keep monologue short and declarative, and cast a rival and an observer who can react. Keep terminology to a few terms per chapter.

### Conventions that depend on the serial/long-form format

- **Length and cadence.** Episodes of thousands of characters released several times a week presume a reader who returns daily; a 6-8 minute branching chapter has one sitting, so one gap, one ladder step, one payoff.
- **Slow escalation across dozens of episodes.** Realm tiers, rival recurrence and the expanding map need hundreds of chapters; compress to one tier change per chapter and a single recurring rival.
- **Cliffhanger monetization.** Paywall-at-cliffhanger is a platform economics behavior, not a craft requirement; the chapter ending still needs a turn but should not be manufactured with no resolution.
- **Heavy foreknowledge and world lore.** Regression payoffs build over many episodes; in short chapters, limit foreknowledge to one or two specific facts per chapter.
- **Large cast and long antagonist arcs.** Use a small cast with named roles; branch consequences must be trackable in a few variables.
