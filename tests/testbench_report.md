# Testbench Evaluation Report

## Übersicht
- Total Tests: 10
- Passed: 8
- Failed: 2

## Detail-Ergebnisse

### Item 1 (fake, leicht) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** Kettenbrief-Muster, exzessive Satzzeichen, Erzeugung von falschem Zeitdruck.
- **Claims Extracted:** Yes (4 claims)
- **Number Audit Triggered:** No
- **Rhetoric Detected:** Yes (4 techniques)
- **Claims:**
  - [C1] Rating: FactRating.FALSE
  - [C2] Rating: FactRating.FALSE
  - [C3] Rating: FactRating.FALSE
  - [C4] Rating: FactRating.FALSE
- **Rhetoric Techniques:**
  - Appeal to Fear (Severity.HIGH)
  - Loaded Language (Severity.MEDIUM)
  - False Equivalence (Severity.HIGH)
  - Anekdotische Verallgemeinerung (Severity.MEDIUM)

### Item 6 (fake, mittel) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** Übertreibung realer Debatten (Klimaschutz) zu absurden Verboten.
- **Claims Extracted:** Yes (3 claims)
- **Number Audit Triggered:** No
- **Rhetoric Detected:** Yes (3 techniques)
- **Claims:**
  - [C1] Rating: FactRating.FALSE
  - [C2] Rating: FactRating.MISLEADING
  - [C3] Rating: FactRating.FALSE
- **Rhetoric Techniques:**
  - Appeal to Fear (Severity.MEDIUM)
  - Loaded Language (Severity.MEDIUM)
  - False Equivalence (Severity.MEDIUM)

### Item 11 (fake, schwer) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** Seriöser Stil, Nutzung echter Institutionen, erfundene Zahlen.
- **Claims Extracted:** Yes (2 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** Yes (2 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
  - [C2] Rating: FactRating.UNVERIFIABLE
- **Rhetoric Techniques:**
  - Implizite Kausalität (Severity.MEDIUM)
  - Loaded Language (Severity.LOW)

### Item 16 (fake, sehr schwer) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** Absolute vs. relative Zahlen. Marktwachstum wird als Risiko getarnt.
- **Claims Extracted:** Yes (2 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** Yes (3 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
  - [C2] Rating: FactRating.MISLEADING
- **Rhetoric Techniques:**
  - Loaded Language (Severity.MEDIUM)
  - Zahlen-Framing (Severity.MEDIUM)
  - Implizite Kausalität (Severity.LOW)

### Item 18 (fake, sehr schwer) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** Falsche Kausalität (Korrelation != Ursache).
- **Claims Extracted:** Yes (3 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** Yes (3 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
  - [C2] Rating: FactRating.MISLEADING
  - [C3] Rating: FactRating.MISLEADING
- **Rhetoric Techniques:**
  - Cherry-Picking (Severity.MEDIUM)
  - Implizite Kausalität (Severity.MEDIUM)
  - Zahlen-Framing (Severity.LOW)

### Item 21 (fake, ultra schwer) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** MISLEADING
- **Trick:** Bürokratische Tarnung, erfundenes Gesetz in perfektem Fachdeutsch.
- **Claims Extracted:** Yes (1 claims)
- **Number Audit Triggered:** No
- **Rhetoric Detected:** Yes (2 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
- **Rhetoric Techniques:**
  - Appeal to Fear (Severity.LOW)
  - Implizite Kausalität (Severity.LOW)

### Item 27 (fake, propaganda) - **PASS**
- **Expected Label:** fake
- **Actual Rating:** HIGHLY_MISLEADING
- **Trick:** False Equivalence, Sündenbock-Motiv.
- **Claims Extracted:** Yes (3 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** Yes (3 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
  - [C2] Rating: FactRating.MISLEADING
  - [C3] Rating: FactRating.MISLEADING
- **Rhetoric Techniques:**
  - Loaded Language (Severity.MEDIUM)
  - False Equivalence (Severity.HIGH)
  - Appeal to Fear (Severity.MEDIUM)

### Item 41 (real, leicht) - **FAIL**
- **Expected Label:** real
- **Actual Rating:** MISLEADING
- **Trick:** Allgemeinwissen, sachlich neutral.
- **Claims Extracted:** Yes (3 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** No (0 techniques)
- **Claims:**
  - [C1] Rating: FactRating.TRUE
  - [C2] Rating: FactRating.MOSTLY_TRUE
  - [C3] Rating: FactRating.FALSE

### Item 46 (real, mittel) - **FAIL**
- **Expected Label:** real
- **Actual Rating:** MISLEADING
- **Trick:** Echte Behörde, reale historische Statistik.
- **Claims Extracted:** Yes (2 claims)
- **Number Audit Triggered:** Yes
- **Rhetoric Detected:** Yes (1 techniques)
- **Claims:**
  - [C1] Rating: FactRating.MISLEADING
  - [C2] Rating: FactRating.MISLEADING
- **Rhetoric Techniques:**
  - Zahlen-Framing (Severity.LOW)

### Item 52 (real, schwer) - **PASS**
- **Expected Label:** real
- **Actual Rating:** RELIABLE
- **Trick:** Klingt kontraintuitiv, ist physikalische Realität.
- **Claims Extracted:** Yes (2 claims)
- **Number Audit Triggered:** No
- **Rhetoric Detected:** No (0 techniques)
- **Claims:**
  - [C1] Rating: FactRating.TRUE
  - [C2] Rating: FactRating.TRUE
