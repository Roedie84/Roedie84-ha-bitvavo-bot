# Preregistratie v1 – vastgelegd vóór de eerste run op echte data

Doel: eerlijk vaststellen of een strategie op Bitvavo na kosten geld verdient
door timing, en niet door toeval of door simpelweg mee te liften op een
stijgende markt. Alles hieronder ligt vast in code (`strategies.py`, `lab.py`).
Wijzigen kan alleen als nieuwe versie (v2), met de oude uitslag erbij.

## Data
- Uurcandles van de bij installatie meest verhandelde EUR-markten (zonder
  stablecoins), 2 jaar. Tijdschalen 4h en 1d worden uit 1h samengesteld.
- Bekende beperking: markten gekozen op de omzet van vandaag (survivorship).
  Dat maakt de toets eerder te gunstig dan te streng.

## Kosten (laagste Bitvavo-niveau)
- Marktorder: 0,25% per zijde + 0,05% slippage per zijde (≈ 0,6% per rondje).
- Limietorder (alleen grid): 0,15% per zijde; alleen gevuld als de koers
  strikt dóór de limiet gaat.

## Hypotheses (4 × 3 = 12)
| Strategie | Regel | Parametersets |
|---|---|---|
| donchian | koop boven hoogste high van N bars, verkoop onder laagste low van M | N 20/55/100 × M 10/20 |
| ema_cross | long zolang snelle EMA > trage EMA | 10/30, 20/50, 20/100, 50/200 |
| rsi_dip | koop RSI-dip boven SMA-trend, verkoop bij RSI > 60 of na 10 bars | RSI 2<10, 3<15, 5<25 × trend 100/200 |
| grid | kooplimieten onder SMA50 op vaste stappen, elk lot +1 stap eruit, na 200 bars sluiten | stap 2/3/5% × 3/5 niveaus |

Alleen long (spot). Signalen op de slotkoers, uitvoering op de volgende open.

## Walk-forward
- Train 180 dagen → test 60 dagen, steeds 60 dagen opschuiven.
- Per venster: de parameterset met het hoogste gemiddelde netto rendement per
  trade in train (min. 5 afgesloten trades, en > 0) handelt in test. Werkte
  niets in train, dan wordt in dat venster niet gehandeld.
- Alleen de test-trades tellen (out-of-sample).

## Slagen = alle zeven
1. ≥ 100 OOS-trades (alle markten samen).
2. ≥ 20 weken met trades.
3. Gemiddeld netto rendement per trade > 0.
4. Gemiddelde alpha > 0. Alpha = netto rendement min wat de markt gemiddeld
   opleverde in dezelfde tijdsduur.
5. Alpha significant > 0: eenzijdige toets op weekgemiddelden (markten bewegen
   samen, losse trades zijn niet onafhankelijk), p < 0,05 / 12 = 0,0042
   (Bonferroni).
6. Positief in ≥ 60% van de markten.
7. Positief in zowel de eerste als de tweede helft van de OOS-trades.

## Controle van de toets zelf
Op synthetische data zonder patroon (60 universums van 10 markten × 2 jaar:
zijwaarts, sterk stijgend, dalend) kwam 0× een vals "kandidaat". Met een
ingebouwd patroon slaat de toets wél aan.

## Wat een "kandidaat" betekent
Niet: bewezen winstgevend. Wel: goed genoeg om in v0.2 met gesimuleerde orders
op live koersen te volgen. Pas als dat de backtest bevestigt komt echt geld in beeld.

---

# Uitslag v1 (24-11-2026, 2 jaar data, 10 markten)

**Geen edge.** Geen van de 12 hypotheses haalde de drempel. Kopen-en-vasthouden
over dezelfde periode: −19,0%.

| Hypothese | Trades | Netto/trade | Alpha | p |
|---|---|---|---|---|
| grid 1h (beste met genoeg trades) | 501 | +0,048% | +0,021% | 0,201 |
| rsi_dip 1d | 7 | +3,187% | +2,164% | 0,069 (te weinig trades) |
| donchian 4h | 117 | −0,302% | −0,552% | 0,847 |
| ema_cross 1h | 130 | −0,360% | −0,590% | 0,527 |
| ema_cross 4h | 92 | −0,397% | −0,652% | 0,689 |
| donchian 1h | 341 | −0,439% | −0,457% | 0,965 |
| grid 4h | 389 | −0,502% | −0,612% | 0,307 |
| rsi_dip 1h | 169 | −0,520% | −0,553% | 1,000 |
| rsi_dip 4h | 131 | −0,677% | −0,765% | 0,944 |
| grid 1d | 252 | −2,290% | −2,377% | 0,384 |
| donchian 1d, ema_cross 1d | 0 | – | – | – |

Het volledige rapport blijft bewaard als `config/bitvavo_bot/lab_report_v1.json`.

# Preregistratie v2 – vastgelegd op 24-11-2026, vóór de eerste run

Aanleiding: per markt los handelen op trend of terugkeer verliest na kosten.
v2 zoekt in een andere hoek: minder trades, en alleen handelen als de markt als
geheel meewerkt. Dit is de laatste ronde: komt hier weer "geen edge" uit, dan
volgt geen v3 op dezelfde data, want elke extra ronde vergroot de kans op een
toevalstreffer.

## Wat verandert
- **Data:** 4 jaar uurcandles in plaats van 2 (meer waarnemingen voor regels
  die zelden handelen). De v1-hypotheses worden op dezelfde 4 jaar opnieuw
  gerapporteerd; hun v1-uitslag hierboven blijft staan.
- **Correctie:** de drempel telt alle 17 hypotheses (12 + 5): p < 0,05 / 17 = 0,0029.
- **Marktregime:** "aan" als de dagslotkoers van BTC-EUR boven zijn 200-daags
  gemiddelde ligt. Gebruikt alleen dagen die op het beslismoment gesloten zijn.
- Alle overige eisen (zeven voorwaarden, walk-forward 180/60, kosten) blijven gelijk.

## Nieuwe hypotheses (5)
| Strategie | Regel | Parametersets | Tijdschaal |
|---|---|---|---|
| rel_strength | Elke maandag 00:00 UTC de 3 markten met het hoogste rendement over N dagen; long zolang de markt in die top 3 staat én het regime aan is | N 30/60/90 | 1d |
| donchian_regime | Donchian-uitbraak (als v1), instappen alleen als het regime aan is; uitstappen volgens de Donchian-regel | N 20/55/100 × M 10/20 | 4h, 1d |
| vol_breakout | Koop als de slotkoers meer dan k × de gemiddelde absolute beweging van 20 bars stijgt; verkoop onder de hoogste slotkoers sinds instap min m × die beweging | k 1,5/2/2,5 × m 2/3 | 4h, 1d |

Bij rel_strength wordt N voor alle markten samen gekozen (de strategie kiest
zelf tussen markten); bij de andere twee per markt, zoals in v1.

## Controle van de toets zelf
Op synthetische data zonder patroon (24 universums van 10 markten × 4 jaar:
zijwaarts, sterk stijgend, dalend) kwam 0× een vals "kandidaat", bij gemiddeld
1.300–2.800 v2-trades per universum.

## Wat er daarna gebeurt
- Kandidaat: de bot stapt automatisch over op die strategie (paper begint
  opnieuw te tellen voor het slot).
- Geen kandidaat: het huidige paper-plan loopt door en het slot blijft dicht.

---

# Uitslag v2 (24-11-2026, 4 jaar data, 10 markten, 17 hypotheses)

**Kandidaat: grid 1h** (1.304 trades, +1,638% netto per trade, alpha +1,150%,
p = 0,00005, positief in alle markten). Geen van de vijf nieuwe v2-hypotheses
haalde de drempel; beste was vol_breakout 1d (+3,591%, p = 0,081).

Kanttekening: op alleen de laatste 2 jaar scoorde grid 1h in v1 +0,048%
(p = 0,20). De houdbaarheidscheck van 26-11-2026 over de laatste 12 maanden
gaf +1,075% per trade over 173 trades. Het volledige rapport blijft bewaard
als `config/bitvavo_bot/lab_report_v2.json`.

# Preregistratie v3 – margin (alleen simulatie), vastgelegd op 26-11-2026

Bitvavo biedt margin niet via de API aan; niets hieronder kan dus met echt geld
door de bot worden gehandeld. Het doel is zwart op wit te zien of short gaan of
hefboom iets toevoegt.

## Short-hypotheses (4 nieuw, totaal 21, drempel p < 0,05 / 21 = 0,0024)
| Strategie | Regel | Parametersets | Tijdschaal |
|---|---|---|---|
| donchian_short | Short bij een slot onder de laagste low van N bars, alleen als BTC onder zijn 200-daags gemiddelde staat; dekken boven de hoogste high van M bars | N 20/55/100 × M 10/20 | 4h, 1d |
| ema_cross_short | Short zolang de snelle EMA onder de trage ligt én BTC onder zijn 200-daags gemiddelde staat | 10/30, 20/50, 20/100, 50/200 | 4h, 1d |

- Zonder hefboom (1×): je leent de munt voor de volle positie.
- Kosten: 0,25% + 0,05% slippage per zijde, plus leenkosten van 0,05% per dag
  (aanname; Bitvavo publiceert dagtarieven per munt die wisselen).
- Alpha voor short = netto rendement plus wat de markt in dezelfde tijd deed:
  in een dalende markt verdient elke short, dat telt niet als vaardigheid.
- Een geslaagde short-hypothese wordt gemeld maar niet verhandeld.

## Hefboomtabel (geen toets, wel een risicoberekening)
Voor de beste long-hypothese worden dezelfde out-of-sample trades doorgerekend
met 1×, 2×, 3× en 5× hefboom. Aannames: leenkosten 0,05% per dag over het
geleende deel; liquidatie zodra de koers (1/hefboom − 5%) tegen de positie in
gaat, gemeten op de laagste koers tijdens de looptijd; 2% liquidatiekosten over
de hele positie (dat laatste is het tarief dat Bitvavo noemt; de 5% buffer is
een aanname omdat Bitvavo de grens niet publiceert).

## Controle van de toets zelf
24 universums zonder patroon (10 markten × 4 jaar; zijwaarts, stijgend,
dalend): 0× een vals "kandidaat", bij gemiddeld 145–854 short-trades.

---

# Uitslag v3 (26-11-2026, 4 jaar data, 21 hypotheses)

Kandidaat blijft **grid 1h**. Geen van de vier short-hypotheses haalde de
drempel: donchian_short 4h −0,505% per trade (50 trades), ema_cross_short 4h
−0,898% (93 trades); de 1d-varianten maakten 0 en 2 trades.

# Preregistratie v4 – grid met bescherming, vastgelegd op 27-11-2026

Aanleiding: de grid koopt dalingen zonder stop. In een langdurige crash blijft
hij loten kopen tot zijn maximum. v4 toetst twee beschermde varianten. De
parametersets zijn exact die van de gewone grid (stap 2/3/5% × 3/5 niveaus,
gemiddelde over 50 bars, tijdstop na 200 bars). Bestaande lots lopen in alle
varianten gewoon door (verkooplimiet of tijdstop); alleen nieuwe aankopen
worden tegengehouden.

| Strategie | Regel | Tijdschaal |
|---|---|---|
| grid_trend | Geen nieuwe kooporders zolang de dagslotkoers van BTC-EUR onder zijn 200-daags gemiddelde ligt | 1h, 4h |
| grid_brake | Per markt: geen nieuwe kooporders zodra de slotkoers meer dan 2 stappen onder het laagste gridniveau sluit; weer aan zodra hij boven het laagste niveau sluit | 1h, 4h |

- Totaal 25 hypotheses: drempel p < 0,05 / 25 = 0,002.
- Overige eisen ongewijzigd.

## Overstappen
Na elke Lab-run kiest de bot de geslaagde, verhandelbare hypothese met het
hoogste netto rendement per trade. Is dat een andere dan de huidige, dan gaan
alle posities dicht, stapt de bot over, en begint de 28-dagentelling voor het
live-slot opnieuw. Je krijgt daar een melding van.

## Controle van de toets zelf
24 universums zonder patroon (10 markten × 4 jaar; zijwaarts, stijgend,
dalend): 0× een vals "kandidaat", bij gemiddeld 2.900–6.900 v4-trades.
