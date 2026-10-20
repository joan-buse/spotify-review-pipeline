# Product-Decision Memo: Where Should Next Quarter's Effort Go?

## Recommendation

**Priority: usability, led by the free-tier experience.** Three usability issues rank 3rd, 4th and 5th on priority score and together account for the largest topic-level severity total after nothing else in the table is larger: usability severity sum is 67,460 across 29,301 complaints (topic totals computed from saved aggregates). The lead issue is **usability.u1, "too many or intrusive ads"** (title: too many intrusive ads between songs): 14,770 complaints [C009], severity sum 31,401 [C010], mean severity 2.125999 [C011].

Closely related are **usability.u2, "shuffle / forced order / playback controls"** (free users can't choose or replay songs): 6,758 complaints [C013], mean severity 2.589080 [C015]; and **usability.u3, "UI, navigation, queue or playlist management"**: 6,260 complaints [C017], severity sum 15,241 [C018].

Representative reviews: u1 `d2f3874f-d15d-4a2f-bb97-4f1213defa46`, `3e8bdc92-4934-457e-85cc-8b9d14bf51c0`; u2 `3ddb3f4e-f7b9-430b-b335-eadc6d321f46`, `a3605554-5c3a-441d-836e-e9fdea83eb2e`; u3 `7084ced1-0428-4093-9328-442f74719b4f`.

## Alternatives considered

- **other.o1, "general criticism"** ranks first (priority score 63,448 [C004]; 31,775 complaints [C001]) but is non-actionable by definition: brief negative verdicts naming no concrete problem. It should not drive roadmap work.
- **billing.b3, "features locked behind premium"** ranks second (12,743 complaints [C005]; severity sum 34,611 [C006]; mean severity 2.716079 [C007]). It is higher-severity per complaint than u1 but is largely a pricing/packaging question, not a product-quality fix. Reviews: `16d640e9-b281-4746-9562-c7dbcf7126b6`, `21915fcd-9a35-48ba-b277-6296c4154603`. Note u2 and b3 summaries overlap (controls moved behind Premium), so a joint packaging review is worthwhile.
- **playback.p1, "crashes or freezes"**: only 3,543 complaints [C021] but mean severity 3.392323 [C023]. A targeted stability fix is a cheap parallel track. Reviews: `55cc3abf-c295-4591-8c3a-78085cfdefaa`, `66a29b3c-c14d-440e-b722-b23e0c99ae82`.
- **access.a1, "cannot log in / login loop"**: 2,427 complaints [C029] but the highest mean severity of any listed issue, 3.920066 [C031]. Small volume, high pain; suitable for a sustaining fix. Review: `15ff50dc-b813-40d5-90d7-e7a7f2426613`.
- **Downloads, catalog, support** have the lowest topic volumes (2,055, 7,929 and 241 complaints respectively) and do not justify a quarter of focus.

## Why usability

It combines scale (largest topic severity total) with three distinct, definable problems that product work can address. Billing and playback are better treated as targeted side bets than as the main investment.

## Limitations

- Reviews are self-selected and historical; they do not represent all users or current app state.
- No revenue, churn or retention data was available; nothing here estimates revenue at risk or implies causation.
- Cancellation language reflects intent, not realized churn.
- First and last months in the data are partial.
- Coverage is sampled: 240,000 of 660,622 source rows were labelled, with 420,609 not selected for budget scope and 13 empty-text reviews quarantined. 58,372 records reused exact-text cache results.
- Issue labels are model-assigned and carry classification error; the large "other" bucket shows how much volume is unspecific.

## Next step

Validate u1/u2 and the b3 overlap against product analytics before committing scope.
