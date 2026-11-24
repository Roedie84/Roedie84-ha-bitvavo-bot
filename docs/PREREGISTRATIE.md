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
