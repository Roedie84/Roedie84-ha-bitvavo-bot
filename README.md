# Bitvavo Bot (Home Assistant)

Handelsbot voor Bitvavo, gebouwd in stappen. **v0.1 plaatst geen orders** en heeft
geen API-sleutel nodig: hij verzamelt koersdata en toetst of er überhaupt een
strategie is die na kosten geld verdient.

| Versie | Wat | Status |
|---|---|---|
| 0.1 | Data + Lab (backtest, walk-forward) | deze versie |
| 0.2 | Paper trading van de winnaar(s) op live koersen | alleen als het Lab een kandidaat oplevert |
| 0.3 | Echt geld, met harde limieten en noodstop | alleen als paper de backtest bevestigt |

## Wat v0.1 doet

1. Kiest bij installatie de meest verhandelde EUR-markten (standaard 10, zonder
   stablecoins) en legt die lijst vast.
2. Haalt 2 jaar uurcandles op (eenmalig enkele minuten), daarna elke nacht om
   03:17 alleen het nieuwe stuk. Opslag: `config/bitvavo_bot/`.
3. Draait het Lab: 4 strategieën × 3 tijdschalen = 12 vooraf vastgelegde
   hypotheses, walk-forward en na kosten. Zie [docs/PREREGISTRATIE.md](docs/PREREGISTRATIE.md).
4. Meldt de uitslag als notificatie en in drie sensoren.

De uitslag kan "geen edge" zijn. Dat is dan het antwoord; de drempels worden
niet achteraf bijgesteld.

## Installatie

HACS → Aangepaste repository → `Roedie84/Roedie84-ha-bitvavo-bot` (Integratie) →
installeren → HA herstarten → Instellingen → Apparaten & diensten → Integratie
toevoegen → **Bitvavo Bot**.

Dashboard: `dashboard_template.yaml` (nieuw dashboard → YAML-editor → plakken).

## Entiteiten en services

| | |
|---|---|
| `sensor.bitvavo_bot_data` | `downloaden` / `gereed` / `fout`, met dekking per markt |
| `sensor.bitvavo_bot_lab_oordeel` | `nog niet gedraaid` / `bezig` / `geen edge` / `kandidaat`, met de volledige tabel |
| `sensor.bitvavo_bot_beste_netto_per_trade` | netto % per trade (OOS) van de beste hypothese |
| `bitvavo_bot.update_data` | data nu bijwerken |
| `bitvavo_bot.run_lab` | Lab opnieuw draaien |

## Ontwikkelen

```
pip install pytest pytest-homeassistant-custom-component
pytest
python tools/maak_bestandscontrole.py
```
