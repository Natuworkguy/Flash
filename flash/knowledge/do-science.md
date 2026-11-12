---
name: do-science
description: Answer a science or medical question from the literature and the data, with real sources and real numbers
---
1. Find the evidence with science: papers first (the question in the words a paper would use; source preprints or arxiv when the field publishes there first), then trials, compounds or proteins when the question is about them. web_search only for what the databases do not hold.
2. Read before you claim: open the papers you will lean on with paper (full=true for open-access text), not just their abstracts in the list. For a key paper, related finds the work around it.
3. Weigh it: a systematic review or a large randomised trial outweighs a small study, a study in people outweighs one in cells or mice, and a preprint is not yet peer reviewed. Say which is which, give the sizes (n, effect, confidence interval) and say where studies disagree.
4. Numbers come from code: for data, statistics, unit conversions, dose or concentration maths, fits and plots, write Python and run it with shell (numpy, scipy, pandas, matplotlib; pip install what is missing). Show the result as a chart or a stats block, and keep the script.
5. Show the molecule: when the answer is about a protein or a compound, put it in a molecule ui block (a pdb id or AlphaFold accession from protein, a cid from compound), with the residues or site you are talking about highlighted.
6. Answer first, then the evidence, each claim with its link (PubMed, DOI, NCT number). Offer citations in the user's style with cite.
7. Medicine: what the evidence says is not advice for one person; say when to see a clinician, and never invent a dose.

Pitfalls:
- A title is not a finding: the abstract or the text says what was found, and in whom.
- Correlation in an observational study is not cause; say which design it was.
- Retractions and corrections exist: if a paper's record mentions one, say so.
- Never cite a paper you did not find with a tool; never make up a PMID, DOI or number.

## Examples

```
science {"action": "papers", "query": "semaglutide cardiovascular outcomes randomized", "limit": 8}
```

```
science {"action": "paper", "id": "PMC10253463", "full": true}
```

```
science {"action": "trials", "query": "CAR-T lupus", "status": "recruiting"}
```

```
science {"action": "compound", "query": "imatinib"}
```

```
science {"action": "protein", "query": "BCL11A human"}
```

```
science {"action": "cite", "id": "10.1056/NEJMoa2031054", "style": "apa"}
```
