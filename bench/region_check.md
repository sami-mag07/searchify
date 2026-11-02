# Region check: Dutch questions, wt-wt vs nl-nl

5 questions x 3 runs per region, 4 s gap, DuckDuckGo via ddgs. Found = answer pattern in title or snippet of the top 5.

| Region | Found | Missed | Errors | Hits (sum) |
|---|---|---|---|---|
| wt-wt | 7 | 0 | 8 (error 8) | 35 |
| nl-nl | 12 | 3 | 0 | 70 |

Found per question (of 3 runs):

| Question | wt-wt | nl-nl |
|---|---|---|
| Wat is de hoofdstad van Nederland? | 1 | 3 |
| Wanneer is Koningsdag in Nederland? | 2 | 3 |
| Hoeveel inwoners heeft Nederland? | 0 | 0 |
| Wie is de directeur van het Rijksmuseum? | 1 | 3 |
| Hoe hoog is de Domtoren in Utrecht? | 3 | 3 |

Read with care: wt-wt lost 8 of 15 searches to DuckDuckGo errors (counted apart, not as misses), nl-nl none, so the found counts are not like for like. Per answered search: wt-wt 7 of 7, nl-nl 12 of 15. The inhabitants question matched neither region (the pattern may be too strict). Only one run of this script; DuckDuckGo varies between runs.
