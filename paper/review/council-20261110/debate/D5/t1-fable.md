POSITION: NARROW. I withdraw "every confirmatory claim was bound to fail under all four audit readings whatever the data". I still defend: at the two strict readings, including the one Amendment 3 uses to assign the label, all four claims that held had to fail at these error rates, so "not robust" was settled before any contrast was computed. The proposed main-text sentence stands, because it is tied to the 11 to 33% rates.

ARGUMENT: I tested each claim against each reading in `amendment3_analysis.json`. A pooled contrast cannot fall outside the pooled error rates (4.34% with no queries, 5.11% with one, 4.29% with six).
Strict readings (smallest shift 11.4 points, 15.0 at the upper bound): the peak needs e1 − e6 above 44.6 points; either exclusion needs e_b − e0 below about −10, and it cannot go below −4.34; the world-document comparison needs a gap above 33. All impossible, whatever the sign or precision of the contrasts. Section 5 assigns "not robust" from the strict upper bound alone.
Answer-revealing readings: the peak (needs e1 − e6 above 6.8; e1 is 5.11) and the world-document comparison (needs a gap above 5.0; e6 is 4.29) had to fail. The exclusions did not. The shift on e1 − e0 is only 1.79 points at the estimate, so the one-query exclusion survives if one query lowers error by about 0.7 points at the observed standard error, or by about 2.6 at the upper bound. The two-query exclusion survives the estimate with a fall of about 2.1 points; at the upper bound it needs 4.40, past the 4.34 floor.
So 13 of the 16 claim-by-reading cells were forced and 3 were not. My round-2 figure of 6.8 points was the shift on e1 − e6; an exclusion needs only one of its two differences, which I missed. The tipping points remain informative, and my replacement keeps them.

IF CONCEDING: Member B's point that excluding a peak depends on the direction of the contrasts: a clear fall in error at one query would have survived both answer-revealing readings.

WHAT WOULD SETTLE IT: For each claim and reading, check whether the contrast needed to survive the recorded shift lies between −e0 and +e_b. Or run `claims()` from `main_analysis_amendment3.py` with one-query error lowered to about 3.5%: the one-query exclusion should hold at `leak_exposed:estimate` and fail at both strict readings.

NOTHING NEW: NO
