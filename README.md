# Bitvavo Bot (Home Assistant)

Handelsbot voor Bitvavo (spot). Verzamelt koersdata, toetst strategieën in het
Lab en handelt daarna zelf: standaard **paper** (gesimuleerd op live koersen),
**live met echt geld** alleen achter een slot.

| Versie | Wat |
|---|---|
| 0.1 | Data + Lab (backtest, walk-forward) |
| 0.2 | Handelsmotor, paper trading, schakelaar paper/live met slot, risicogrenzen, noodstop, handelsscherm |
| 0.3 | Grid-orders eerlijk verdeeld over markten, jonge markten (< 240 dagen) uitgesloten, resultaatgrafiek |

## Hoe het werkt

1. Bij installatie kiest de bot de meest verhandelde EUR-markten (standaard 10)
   en haalt 2 jaar uurcandles op. Daarna elk uur het nieuwe stuk.
2. Het Lab toetst 12 vooraf vastgelegde hypotheses, walk-forward en na kosten.
   Zie [docs/PREREGISTRATIE.md](docs/PREREGISTRATIE.md).
3. De bot kiest de Lab-kandidaat. Is er geen, dan de beste hypothese, zichtbaar
   gemarkeerd als **onbewezen**. Per markt kiest hij de parameters op de laatste
   180 dagen en herkiest die elke 60 dagen, precies zoals in de backtest.
4. Paper trading draait altijd, met dezelfde kosten als de backtest.

## Live en het slot

Live gaat pas open als alle drie waar zijn: het Lab heeft een kandidaat, paper
draait 28 dagen, en paper staat netto in de plus. Overbruggen kan alleen met de
service `bitvavo_bot.override_lock` en de letterlijke bevestiging.

Voor live leest de bot `bitvavo_api_key` en `bitvavo_api_secret` uit
`secrets.yaml`. Zet bij Bitvavo de opnamerechten van die sleutel uit.

Grenzen die altijd gelden: maximum per koop, maximum totaal ingezet, maximaal
dagverlies (daarna die dag geen nieuwe aankopen) en de noodstop (alles
annuleren, alles verkopen, live uit, handel gepauzeerd).

Margin/hefboom wordt niet gebruikt: Bitvavo biedt margin niet via de API aan.

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
