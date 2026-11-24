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
