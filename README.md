# Bitvavo Bot (Home Assistant)

Handelsbot voor Bitvavo (spot). Verzamelt koersdata, toetst strategieën in het
Lab en handelt daarna zelf: standaard **paper** (gesimuleerd op live koersen),
**live met echt geld** alleen achter een slot.

| Versie | Wat |
|---|---|
| 0.1 | Data + Lab (backtest, walk-forward) |
| 0.2 | Handelsmotor, paper trading, schakelaar paper/live met slot, risicogrenzen, noodstop, handelsscherm |
| 0.3 | Grid-orders eerlijk verdeeld over markten, jonge markten (< 240 dagen) uitgesloten, resultaatgrafiek |
| 0.4 | Preregistratie v2: 3 nieuwe strategieën (relatieve sterkte, trend met marktfilter, volatiliteitsuitbraak), 4 jaar historie, automatisch overstappen op een Lab-kandidaat |
| 0.5 | Grafiek met muisfuncties: kruisdraad, uitlezing per candle, zoomen, slepen |
| 0.6 | Directe koersstroom van Bitvavo: koersen per transactie, lopende candle in de grafiek, scherm ververst per seconde, paper-orders elke 5 s getoetst |
| 0.9 | Preregistratie v4: grid met trendfilter en grid met noodrem per markt; automatisch overstappen op de beste kandidaat na elke Lab-run |
| 0.8.1 | Open orders tonen wanneer ze geplaatst en voor het laatst bijgesteld zijn |
| 0.8 | Preregistratie v3: short-strategieën en hefboomtabel, alleen als simulatie in het Lab |
| 0.7 | Houdbaarheidscheck als vierde slotvoorwaarde, paper met echte bied-laatprijzen, meldingen (direct + dagoverzicht 18:00), saldo-afstemming in live, vergelijking met de markt, CSV-export, echte kosten van je account |
| 0.6.1 | Controle: cycli kunnen niet meer overlappen (geen dubbele orders), geen valse nullen na herstart, slot en strategiewissel alleen voor beheerders, melding als live na herstart niet hervat |

## Hoe het werkt

1. Bij installatie kiest de bot de meest verhandelde EUR-markten (standaard 10)
   en haalt 4 jaar uurcandles op. Daarna elk uur het nieuwe stuk.
2. Het Lab toetst 25 vooraf vastgelegde hypotheses, walk-forward en na kosten.
   Zie [docs/PREREGISTRATIE.md](docs/PREREGISTRATIE.md).
3. De bot kiest de Lab-kandidaat met het hoogste netto rendement per trade en
   herkiest na elke Lab-run. Is er geen, dan de beste hypothese, zichtbaar
   gemarkeerd als **onbewezen**. Per markt kiest hij de parameters op de laatste
   180 dagen en herkiest die elke 60 dagen, precies zoals in de backtest.
4. Paper trading draait altijd, met dezelfde kosten als de backtest.

## Live en het slot

Live gaat pas open als alle vier waar zijn: het Lab heeft een kandidaat, paper
draait 28 dagen, paper staat netto in de plus, en de gekozen strategie was ook
over de laatste 12 maanden (out-of-sample) netto positief met minstens 20 trades.
Die laatste controle wordt elke 30 dagen ververst en kan het slot alleen dichthouden. Overbruggen kan alleen met de
service `bitvavo_bot.override_lock` en de letterlijke bevestiging.

Voor live leest de bot `bitvavo_api_key` en `bitvavo_api_secret` uit
`secrets.yaml`. Zet bij Bitvavo de opnamerechten van die sleutel uit.

Grenzen die altijd gelden: maximum per koop, maximum totaal ingezet, maximaal
dagverlies (daarna die dag geen nieuwe aankopen) en de noodstop (alles
annuleren, alles verkopen, live uit, handel gepauzeerd).

Margin/hefboom wordt niet verhandeld: Bitvavo biedt margin niet via de API aan.
Het Lab rekent wel door wat short gaan en hefboom zouden hebben gedaan (tabblad Lab).

## Meldingen

Instellingen → Apparaten & diensten → Bitvavo Bot → Configureren: kies een
meldingsdienst (bv. `notify.mobile_app_…`). Direct gemeld worden: dagstop, fouten,
live aan, noodstop, live-aankopen en -verkopen, en afwijkingen tussen het boek en
je Bitvavo-saldo. Om 18:00 volgt een dagoverzicht (uit te zetten).

## Installatie

HACS → Aangepaste repository → `Roedie84/Roedie84-ha-bitvavo-bot` (Integratie) →
installeren → HA herstarten → Integratie toevoegen → **Bitvavo Bot**.

Dashboard: `dashboard_template.yaml`. De kaart `custom:bitvavo-bot-card` wordt
door de integratie zelf geladen.

## Entiteiten en services

| | |
|---|---|
| `switch.bitvavo_bot_live` | live aan/uit (geweigerd zolang het slot dicht is) |
| `switch.bitvavo_bot_handel_actief` | pauzeert nieuwe aankopen |
| `button.bitvavo_bot_noodstop` | noodstop |
| `number.bitvavo_bot_max_per_koop` / `_max_totaal` / `_max_dagverlies` | risicogrenzen |
| `sensor.bitvavo_bot_paper_resultaat` / `_live_resultaat` | resultaat in euro |
| `sensor.bitvavo_bot_strategie`, `_live_slot`, `_lab_oordeel`, `_data` | status |
| `bitvavo_bot.update_data`, `run_lab`, `kies_strategie`, `override_lock`, `restore_lock` | services |

## Ontwikkelen

```
pip install pytest pytest-homeassistant-custom-component
pytest
python tools/maak_bestandscontrole.py
```
