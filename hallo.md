FakeNewsGuard % .venv/bin/python main.py "Die letzte Erhöhung des Mindestlohns entpuppt sich als Job-Killer. Auswertungen der Bundesagentur für Arbeit zeigen, dass die geleisteten Arbeitsstunden im Gastgewerbe im darauffolgenden Quartal um 8 % zurückgegangen sind. Die künstliche Lohnspirale vernichtet massiv Arbeitsplätze und schadet den Ärmsten der Gesellschaft." 
============================================================
FAKTENCHECK GESTARTET
============================================================

📋 PHASE 1: Claims extrahieren
  🔍 [Claim Extractor] Starte ...
  🔍 [Claim Extractor] 5 Claims extrahiert, 3 implizite
  🔍 [Claim Extractor] Fertig.
  C1 [CAUSAL]: Die letzte Mindestlohnerhöhung führt zum Verlust von Arbeitsplätzen.
  C2 [FACTUAL]: Die Bundesagentur für Arbeit hat Auswertungen veröffentlicht, die einen Rückgang der Arbeitsstunden belegen.
  C3 [STATISTICAL]: Die geleisteten Arbeitsstunden im Gastgewerbe sind im Quartal nach der Mindestlohnerhöhung um 8 % zurückgegangen.
  C4 [CONTEXTUAL]: Die Mindestlohnerhöhung stellt eine künstliche Lohnspirale dar.
  C5 [CAUSAL]: Die Mindestlohnerhöhung schadet den Ärmsten der Gesellschaft.

🔄 PHASE 2: Claims prüfen

  ── Fact-Check für C1 ──
  ✅ [Fact Checker] Starte ...
  ✅ [Fact Checker] Suche nach: ['Die letzte Mindestlohnerhöhung führt zum Verlust von Arbeitsplätzen.', 'Die letzte Mindestlohnerhöhung führt zum Verlust von Arbeitsplätzen. faktencheck']
  ✅ [Fact Checker] Claim C1: MISLEADING
  ✅ [Fact Checker] Fertig.

  ── Fact-Check für C2 ──
  ✅ [Fact Checker] Starte ...
  ✅ [Fact Checker] Suche nach: ['Die Bundesagentur für Arbeit hat Auswertungen veröffentlicht, die einen Rückgang der Arbeitsstunden belegen.', 'Die Bundesagentur für Arbeit hat Auswertungen veröffentlicht, die einen Rückgang der Arbeitsstunden belegen. faktencheck']
  ✅ [Fact Checker] Claim C2: TRUE
  ✅ [Fact Checker] Fertig.

  ── Fact-Check für C3 ──
  ✅ [Fact Checker] Starte ...
  ✅ [Fact Checker] Suche nach: ['Die geleisteten Arbeitsstunden im Gastgewerbe sind im Quartal nach der Mindestlohnerhöhung um 8 % zurückgegangen.', 'Die geleisteten Arbeitsstunden im Gastgewerbe sind im Quartal nach der Mindestlohnerhöhung um 8 % zurückgegangen. faktencheck', 'Die geleisteten Arbeitsstunden im Gastgewerbe sind im Quartal nach der Mindestlohnerhöhung um 8 % zurückgegangen. statistik daten']
  ✅ [Fact Checker] Claim C3: MISLEADING
  ✅ [Fact Checker] Fertig.
  ── Number-Audit für C3 ──
  🔢 [Number Auditor] Starte ...
  🔢 [Number Auditor] Claim C3: Manipulation = CATEGORY_ERROR
  🔢 [Number Auditor] Fertig.

  ── Fact-Check für C4 ──
  ✅ [Fact Checker] Starte ...
  ✅ [Fact Checker] Suche nach: ['Die Mindestlohnerhöhung stellt eine künstliche Lohnspirale dar.', 'Die Mindestlohnerhöhung stellt eine künstliche Lohnspirale dar. faktencheck']
  ✅ [Fact Checker] Claim C4: MISLEADING
  ✅ [Fact Checker] Fertig.

  ── Fact-Check für C5 ──
  ✅ [Fact Checker] Starte ...
  ✅ [Fact Checker] Suche nach: ['Die Mindestlohnerhöhung schadet den Ärmsten der Gesellschaft.', 'Die Mindestlohnerhöhung schadet den Ärmsten der Gesellschaft. faktencheck']
  ✅ [Fact Checker] Claim C5: MOSTLY_FALSE
  ✅ [Fact Checker] Fertig.

🎭 PHASE 3: Rhetoric-Analyse
  🎭 [Rhetoric Analyzer] Starte ...
  🎭 [Rhetoric Analyzer] 4 Techniken erkannt
  🎭 [Rhetoric Analyzer] Fertig.

📊 PHASE 4: Synthese
  📊 [Synthesizer] Starte ...
  📊 [Synthesizer] Fertig.

============================================================
FAKTENCHECK ABGESCHLOSSEN
============================================================

╔══════════════════════════════════════════════════════════╗
║  ⚠️  GESAMTBEWERTUNG: Irreführend                            ║
║     Confidence: 90%                                        ║
╚══════════════════════════════════════════════════════════╝

📝 ZUSAMMENFASSUNG
──────────────────────────────────────────────────────────
Der Text stellt die Mindestlohnerhöhung als nachgewiesenen „Job-Killer

🔍 CLAIMS IM DETAIL
──────────────────────────────────────────────────────────
  ⚠️ [C1] MISLEADING
     Evidenz: Die Behauptung stellt eine Kausalität als Fakt dar, die empirisch nicht belegt ist. Zwar geben in Umfragen des Ifo-Instituts und des DIHK etwa 20–22 % der direkt betroffenen Unternehmen an, Stellenabb
     Korrektur: Es ist nicht belegt, dass die Mindestlohnerhöhung tatsächlich zu einem Netto-Verlust von Arbeitsplätzen führt. Es liegen lediglich Unternehmensumfragen vor, die Absichten zum Stellenabbau bei einem Te
     Fehlender Kontext: 1. Unterscheidung zwischen Unternehmensabsichten (Umfragen) und realisierten Arbeitsmarktdaten (Statistik). 2. Historischer Kontext: Bisherige Erhöhungen (2015, 2022) führten nicht zu steigender Arbei

  ✅ [C2] TRUE
     Evidenz: Das Institut für Arbeitsmarkt- und Berufsforschung (IAB), welches eine Forschungseinrichtung der Bundesagentur für Arbeit (BA) ist, veröffentlicht regelmäßig Auswertungen zum Arbeitsmarkt. Im IAB-Fors
     Korrektur: Keine direkte Korrektur notwendig, da die Kernaussage faktisch belegt ist. Es ist jedoch zu präzisieren, dass die offiziellen volkswirtschaftlichen Gesamtrechnungen (VGR) zum Arbeitsvolumen formal vom
     Fehlender Kontext: Der belegte Rückgang bezieht sich im zitierten Kontext spezifisch auf die Arbeitsstunden pro Arbeitnehmer (durch Zunahme von Teilzeit) sowie auf den pandemiebedingten Einbruch. Das Gesamtarbeitsvolume

  ⚠️ [C3] MISLEADING
     Evidenz: Die Behauptung vermischt verschiedene Datenpunkte. Eine Reduktion der Arbeitszeit um rund 8 % wird im 'Vierten Bericht der Mindestlohnkommission' (2023) erwähnt, bezieht sich jedoch spezifisch auf die

  ⚠️ [C4] MISLEADING
     Evidenz: Unabhängige Wirtschaftsinstitute wie das DIW Berlin und das IMK der Hans-Böckler-Stiftung stufen die Sorge vor einer Lohn-Preis-Spirale als unbegründet ein. Der Inflationseffekt wird auf maximal 0,3 b
     Korrektur: Die Behauptung stellt eine wirtschaftspolitische Befürchtung als Fakt dar. Führende Wirtschaftsinstitute (DIW, IMK, WSI) bezeichnen die Angst vor einer Lohn-Preis-Spirale durch Mindestlohnerhöhungen a
     Fehlender Kontext: Es wird verschwiegen, dass der Mindestlohn primär zu einer Umverteilung von Einkommen führt und nicht zu schädlicher Inflation. Zudem wird der Konsens unter vielen Ökonomen ignoriert, dass die Sorge v

  ❌ [C5] MOSTLY_FALSE
     Evidenz: Empirische Studien und Daten deutscher Forschungsinstitute (WSI, DIW, IMK) sowie des Statistischen Bundesamtes zeigen, dass Mindestlohnerhöhungen die Einkommen von Geringverdienern signifikant steiger
     Korrektur: Die Behauptung, dass Mindestlohnerhöhungen den Ärmsten schaden, ist durch die Datenlage nicht gedeckt. Im Gegenteil profitieren Geringverdiener direkt durch höhere Löhne, und die Abhängigkeit von staa
     Fehlender Kontext: Es wird nicht zwischen 'working poor' (die profitieren) und nicht-erwerbstätigen Armen (die nicht direkt profitieren) unterschieden. Zudem werden die positiven Makroeffekte (höhere Konsumnachfrage, ge

🔢 ZAHLEN-PRÜFUNG
──────────────────────────────────────────────────────────
  [C3] Manipulation: CATEGORY_ERROR
     Rechnung: Die 8%-Zahl stammt aus dem Vierten Bericht der Mindestlohnkommission (2023), bezieht sich aber auf 'bezahlte Arbeitszeit bei geringfügig Beschäftigten im Mindestlohnbereich' nach der Mindestlohneinfüh
     Korrekt: Die 8% Reduktion bezieht sich spezifisch auf bezahlte Arbeitsstunden bei geringfügig Beschäftigten im Mindestlohnbereich nach der Erst-Einführung 2015, nicht auf das gesamte Gastgewerbe nach der 2025/

🎭 MANIPULATIONSTECHNIKEN
──────────────────────────────────────────────────────────
  • Loaded Language [MEDIUM]
    Emotional aufgeladene Begriffe, die eine wirtschaftliche Maßnahme als aktiv zerstörerisch und unnatürlich darstellen, um Ablehnung zu provozieren.
    Beispiel: "Job-Killer"
  • Implicit Causality [MEDIUM]
    Es wird ein direkter Kausalzusammenhang suggeriert, ohne andere Faktoren wie Saisonalität oder Energiekosten zu berücksichtigen. Korrelation wird als Kausalität behandelt.
    Beispiel: "Die letzte Erhöhung des Mindestlohns entpuppt sich als Job-Killer. Auswertungen ... zeigen, dass die geleisteten Arbeitsstunden ... um 8 % zurückgegangen sind."
  • Cherry-Picking [MEDIUM]
    Es wird nur ein spezifischer Sektor und ein kurzer Zeitraum ausgewählt, der die These stützt, während Gesamtdaten oder andere Branchen ignoriert werden.
    Beispiel: "im Gastgewerbe im darauffolgenden Quartal"
  • Number Framing [LOW]
    Der Rückgang von Arbeitsstunden wird implizit gleichgesetzt mit dem Verlust von Arbeitsplätzen, um die negative Wirkung dramatischer erscheinen zu lassen.
    Beispiel: "Arbeitsstunden ... um 8 % zurückgegangen sind ... vernichtet massiv Arbeitsplätze"

📚 QUELLEN
──────────────────────────────────────────────────────────
  • https://www.zeit.de/wirtschaft/2025-06/erhoehung-mindestlohn-15-euro-debatte-arbeitspolitik
  • https://www.tagesschau.de/wirtschaft/unternehmen/mindestlohn-erhoehung-106.html
  • https://www.springerprofessional.de/verguetung/arbeitsrecht/mindestlohnerhoehung-fuehrt-zu-jobabbau/51782350
  • https://www.boeckler.de/de/boeckler-impuls-warnung-vor-arbeitsplatzverlusten-auf-wackeligem-fundament-9737.htm
  • https://doku.iab.de/forschungsbericht/2025/fb1225.pdf
  • https://www.destatis.de/DE/Themen/Arbeit/Arbeitsmarkt/Erwerbstaetigkeit/Methoden/Erlaeuterungen/erlaueterungen-arbeitszeit-arbeitsvolumen.html
  • https://www.sozialpolitik-aktuell.de/files/sozialpolitik-aktuell/_Politikfelder/Einkommen-Armut/Dokumente/2023_07_Mindestlohnkommision_Auswirkungen_Vierter_Bericht.pdf
  • https://ftp.iza.org/report_pdfs/iza_report_83.pdf
  • https://www.hotelvor9.de/inside/neuer-mindestlohn-betrifft-jeden-zweiten-job-im-gastgewerbe
  • https://www.diw.de/de/diw_01.c.965491.de/nachrichten/die_fuenf_groessten_irrtuemer_ueber_den_mindestlohn.html
  • https://www.boeckler.de/de/auf-einen-blick-17945-12-euro-mindestlohn-studien-und-einschaetzungen-41626.htm
  • https://www.imk-boeckler.de/de/pressemitteilungen-15992-erhohung-des-mindestlohns-auf-12-euro-38638.htm
  • https://www.handelsblatt.com/unternehmen/handel-konsumgueter/arbeitsmarkt-mindestlohnbranchen-sehen-gefahr-einer-lohnspirale/100137818.html
  • https://www.wsi.de/de/pressemitteilungen-15991-mindestlohn-verringerung-regionaler-lohnungleichheiten-62733.htm
  • https://www.destatis.de/DE/Presse/Pressemitteilungen/2025/07/PD25_256_62.html
  • https://www.imk-boeckler.de/fpdf/HBS-008230/p_imk_pb_116_2022.pdf