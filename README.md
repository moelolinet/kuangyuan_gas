<div align="center">

<img src="custom_components/kuangyuan_gas/brand/icon@2x.png" width="150" alt="Kuangyuan Gas">

# Kuangyuan Gas

Home Assistant integration for the Kuangyuan Energy customer portal

**English** | [简体中文](README.zh-CN.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/moelolinet/kuangyuan_gas)](https://github.com/moelolinet/kuangyuan_gas/releases)

</div>

---

Read the gas balance, cumulative usage, locally computed daily/monthly usage and recharge history of every meter bound to your Kuangyuan Energy online account (`www.kynyyyt.com`) directly in Home Assistant.

> This integration only calls the **read-only** endpoints of the portal (login, meter list, balance query, recharge history). It never starts a payment, a recharge, a phone-number change or a meter removal.

## Features

- **Multiple accounts**: every account is its own config entry with its own credentials and settings
- Choose **which meters** (`户号`) to expose for each account
- Choose **which entities** to create for each meter
- Configurable polling interval (seconds), default 1800, minimum 300
- Configurable low-balance threshold; a `binary_sensor` turns on below it
- Automatic re-login once when the portal session expires; a re-auth prompt when the password is wrong
- One **Refresh now** button per meter

## Daily / monthly usage (computed locally)

These two values are **never** read from the portal. They are calculated from the
`Total gas usage` sensor's own history stored by the Home Assistant
[recorder](https://www.home-assistant.io/integrations/recorder/):

| Entity | Calculation |
|---|---|
| Daily usage | `current cumulative usage − cumulative usage at today 00:00` |
| Monthly usage | `current cumulative usage − cumulative usage at the 1st of this month 00:00` |

The baseline is the last recorded reading **at or before** that midnight, so a value
computed at 09:00 still measures from 00:00 rather than from the first poll of the day.
If nothing was recorded before midnight (fresh install), the first reading after it is
used instead. With no history at all the entity stays unavailable instead of reporting
a fake `0`.

Every poll recomputes the value from the freshly read cumulative usage, so when the
meter reading increases, both the daily and the monthly value increase immediately.

Baseline fallback (so the values are never stuck at "unknown"):

1. the last reading at or before the boundary - exact answer,
2. the first reading shortly after the boundary - covers the polling gap,
3. the earliest reading ever recorded - covers an install that happened after
   the boundary (for example, installed mid-month).

On a completely fresh install the first poll becomes the baseline, so the value
starts at `0` and grows with the meter from then on.

Notes:

- Requires the built-in **recorder** integration (enabled by default).
- If the meter reading goes backwards (meter swap / reset) the window reports
  unavailable instead of a negative number.
- Both entities carry `source: home_assistant_recorder` and `baseline_source`
  (`before_boundary` / `after_boundary` / `first_recorded`) so the origin is
  verifiable.

## Entities

Every selected meter becomes its own device. Each device can expose:

| Entity | Type | Description |
|---|---|---|
| Balance | sensor | CNY, 4 decimals stored (2 shown) |
| Total gas usage | sensor | m³, `total_increasing`, from the portal |
| Daily usage (local) | sensor | m³, from recorder history |
| Monthly usage (local) | sensor | m³, from recorder history |
| Gas supply | binary_sensor | `on` = gas connected |
| Low balance | binary_sensor | `on` when below the threshold (`problem`) |
| Data timestamp | sensor | query time reported by the portal |
| Last recharge amount | sensor | newest recharge record |
| Last recharge time | sensor | newest recharge record |
| Recharge count | sensor | records in the fetched range |
| Refresh now | button | poll this meter immediately |

> Owner name, address and phone number are **not** exposed as entities. They are only
> kept locally in the config entry to identify the meter.

## Installation

### HACS

1. HACS → Integrations → `⋮` → **Custom repositories**
2. Add `https://github.com/moelolinet/kuangyuan_gas` with category **Integration**
3. Search for `Kuangyuan Gas` and install
4. Restart Home Assistant

### Manual

Copy `custom_components/kuangyuan_gas` into your Home Assistant `custom_components`
folder and restart.

## Configuration

Settings → Devices & Services → **Add integration** → search `Kuangyuan Gas`:

1. Enter the portal mobile number and password (optional custom portal URL)
2. Select the meters to show
3. Select the entities to enable
4. Set the refresh interval and low-balance threshold

The entry (hub) is named after the login account.

### Multiple accounts

Add the integration again. Each account is an independent entry: credentials, meter
selection, interval and login session are fully isolated.

### Changing settings

Integration card → **Configure**. Meters, entities and the interval can be changed at
any time; the entry reloads automatically and entities that are no longer selected are
removed.

## Refresh interval

The portal is protected by a cloud WAF that rate-limits aggressive clients. Requests are
serialised with a small delay; an interval of **600 seconds or more** is recommended and
300 seconds is the minimum.

Recorder baselines are read once per day (and once per month), so a fast polling
interval does not add database load.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Wrong mobile number or password" | Passwords are case sensitive. If the website works but this does not, check for SMS two-factor authentication |
| "Cannot reach the portal" | Network issue or rate limiting; retry later |
| Entity unavailable | That meter's query failed; the last value is kept and `last_error` is set |
| Daily/monthly usage unavailable | Recorder disabled, or not enough history yet |
| Re-auth keeps appearing | Password changed on the portal, or the account is restricted |

## Disclaimer

Personal project, not affiliated with Kuangyuan Energy Co., Ltd. Balance values are as
reported by the portal. Please do not poll aggressively.

## License

MIT
