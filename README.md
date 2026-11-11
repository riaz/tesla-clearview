# tesla-clearview

Tell it when you're leaving. It gets your Tesla's glass clear and the cabin ready.
Then it shuts everything off if you don't go.

```
$ clearview say "leaving for work in 10 min"
```

- Fog and frost check: compares an estimate of the glass temperature with the dew point (from Open-Meteo weather at the car), plus a Claude vision check of the latest Sentry/Dashcam frames.
- Climate actions: Max Defrost (front and rear defrosters, airflow to the windscreen and demist vents) when fog or frost is likely, otherwise normal climate. Seat and steering-wheel heat when it's cold.
- Camera cleaning alerts: a push notification per camera with how much of the view is blocked (mud, snow, ice, water). It includes the snapshot and the car's own "camera blocked" alerts.
- Battery-safe: nothing polls the car. clearview only contacts the car after you trigger it. It wakes the car once, starts climate at the right lead time, and turns climate off `STOP_GRACE_MIN` after your departure time if the car hasn't moved. It won't precondition below `MIN_BATTERY_PCT`.

## What Tesla's API can and can't do

| Want | Fleet API? | How clearview handles it |
|---|---|---|
| Live camera / Sentry view | **No.** Live Camera (Premium Connectivity) only works in Tesla's own app. The Fleet API has no camera or video endpoint. | Reads the newest clips from the car's **USB drive** (`TESLACAM_PATH`), e.g. a Raspberry Pi acting as the USB drive and syncing over Wi-Fi ([TeslaUSB](https://github.com/marcone/teslausb)). Falls back to the weather model when there are no recent clips. |
| Front + rear defrost | Yes: `set_preconditioning_max` | Used when fog or frost risk is high |
| Point vents at side windows | **No**: vent direction can't be set remotely | Max Defrost already sends air to the windscreen and side demist vents. A/C on dries the cabin air. |
| Camera-blocked warnings | Partly: `recent_alerts` | Matched for camera-related alerts |
| Climate, seat/wheel heat | Yes | Used |

The front camera sits behind the windscreen, so fog there shows up in its frames. Tesla heats that small patch of glass, though, so the weather model still matters. No camera sees the rear window.

## Setup

See **[docs/SETUP.md](docs/SETUP.md)** for the one-time Tesla developer registration (app, key pair, public-key hosting, virtual-key pairing, signing proxy).

```bash
uv sync
cp .env.example .env          # fill in
uv run clearview auth login
uv run clearview vehicles
uv run clearview depart work --in 10 --dry-run
```

## Usage

```bash
clearview say "leaving for the gym at 6:30am"
clearview depart work --in 15
clearview analyze /Volumes/TESLADRIVE/TeslaCam --notify   # camera check only, no car connection
clearview status --wake
clearview serve                                            # HTTP API for Siri / Shortcuts
```

### From your phone (Siri / iOS Shortcut)

Run `clearview serve` on an always-on machine (Mac mini, Pi, NAS). Reach it over [Tailscale](https://tailscale.com) rather than opening a port. Then create a Shortcut:

1. *Dictate Text* → "leaving for work in 10 minutes"
2. *Get Contents of URL* → `POST http://<host>:8787/depart`, header `Authorization: Bearer <CLEARVIEW_API_TOKEN>`, JSON body `{"text": "<Dictated Text>"}`

Say "Hey Siri, *Car ready*". Notifications arrive in the [ntfy](https://ntfy.sh) app (subscribe to your `NTFY_TOPIC`). `DELETE /sessions/{id}` cancels a session and turns climate off.

## Costs

- **Tesla Fleet API** is pay-per-use. Personal accounts currently get a monthly credit that comfortably covers this usage. A session is about 1 wake, 2 data reads, 1 alerts read and 2-6 commands.
- **Claude vision** is one request per session with up to 6 frames, at low effort. Set `ANTHROPIC_API_KEY`.

## Development

```bash
uv run pytest
```
