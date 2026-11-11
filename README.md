# anthias-weather

**Automatic severe weather mode for [Anthias](https://github.com/Screenly/Anthias) digital signage.**

While a National Weather Service warning covers your location, anthias-weather switches your Anthias playlist down to just your weather pages, for example a weather alerts page and a radar page. When the warning expires or is cancelled, your normal playlist comes back exactly as it was: same assets, same order.

It runs as a small service next to Anthias on the same device and controls Anthias only through its standard [v2 REST API](https://anthias.screenly.io/api/). Anthias itself isn't modified, so it keeps updating as usual.

> This is an independent project, not made or supported by Screenly or the Anthias team. Weather data comes from the [National Weather Service API](https://www.weather.gov/documentation/services-web-api), which covers the United States and its territories.

## How it works

- **Checks the NWS every 60 seconds** for active alerts at your location (a latitude/longitude point, NWS zone codes, or both).
- **When a listed warning starts** (by default Tornado Warning and Severe Thunderstorm Warning; you can add others), it:
    1. saves which assets are switched on and the playlist order,
    2. switches off everything except your alert pages,
    3. jumps the screen straight to the first alert page.
- **When no listed warnings remain** for two checks in a row, it switches the saved assets back on, restores the saved order, and moves to the next asset. Assets you had switched off yourself stay off. Assets added during a warning are kept and go at the end.
- **If the NWS can't be reached during a warning**, it stays in alert mode until the NWS answers again or the warning's own end time passes. If the NWS can't be reached in normal mode, nothing changes.
- **A restart or power cut during a warning** is safe: the saved playlist is kept on disk and restored when the warning ends.
- **Watches, advisories, test messages and cancellations** never trigger alert mode unless you list their event names.
- **Pages that appear more than once in your loop**: only one copy plays during alerts (the first one in the loop, or one you choose by asset ID), so each page plays once per rotation.

## Requirements

- Anthias on a Debian 12 or 13 PC (x86), installed the [standard way](https://anthias.screenly.io/docs/pc/). Developed against Anthias `master` in November 2026, using the v2 API.
- Python 3.11 or newer, which Debian 12 and 13 include. No other packages are needed.
- Internet access to `api.weather.gov`.

Raspberry Pi OS Bookworm installs of Anthias should also work, but haven't been tested. balenaOS installs aren't supported, because the service runs on the host system.

## Install

See **[INSTALL.md](INSTALL.md)** for step-by-step instructions written for people new to Linux, with two methods:

- **Over SSH** from another computer.
- **With a USB flash drive** and a keyboard at the PC, for when SSH isn't set up.

Short version, for people comfortable with Linux:

```bash
curl -L -o anthias-weather.tar.gz https://github.com/CaliMiller/anthias-weather/archive/refs/heads/main.tar.gz
tar xzf anthias-weather.tar.gz && cd anthias-weather-main
sudo bash install.sh
sudo nano /etc/anthias-weather/config.toml   # location, email, alert page names
sudo anthias-weather check
sudo systemctl enable --now anthias-weather
```

The installer puts the program in `/opt/anthias-weather`, the settings in `/etc/anthias-weather/config.toml`, its saved state in `/var/lib/anthias-weather`, and a systemd service that runs as its own unprivileged `anthias-weather` user. Re-running it upgrades the program and keeps your settings.

## Settings

All settings live in `/etc/anthias-weather/config.toml`. [config.example.toml](config.example.toml) explains each one.

| Setting | What it does | Default |
| --- | --- | --- |
| `anthias.url` | Where the Anthias API is | `http://127.0.0.1` |
| `anthias.username`, `anthias.password` | Only if Anthias authentication is turned on | none |
| `alert_mode.assets` | Asset names (or asset IDs) to play during a warning, in order | required |
| `alert_mode.switch_delay_seconds` | Wait before switching the screen, so the viewer has the new playlist | `3` |
| `nws.point` | Your location as `"latitude,longitude"` | `point` or `zones` required |
| `nws.zones` | NWS zone or county codes, e.g. `["INC097"]` | none |
| `nws.events` | NWS event names that trigger alert mode | Tornado Warning, Severe Thunderstorm Warning |
| `nws.user_agent` | Identifies you to the NWS, which asks for a contact | required |
| `nws.poll_seconds` | How often to check the NWS (minimum 30) | `60` |
| `nws.clear_after_polls` | Clear checks in a row before resuming normal mode | `2` |

**Point or zones?** With `point`, alert mode starts only when your location is inside the warning. Tornado and severe thunderstorm warnings are drawn as storm-based polygons, often much smaller than a county. With a county code in `zones`, it starts whenever any part of that county is warned. To find your codes, open `https://api.weather.gov/points/<lat>,<lon>`; the `county` and `forecastZone` links end with them.

## Commands

| Command | What it does |
| --- | --- |
| `sudo anthias-weather check` | Tests the settings, the Anthias connection and the NWS lookup, and lists current alerts marked TRIGGERS or ignored |
| `sudo anthias-weather list-assets` | Lists the playlist in play order. `*` marks the pages used during alerts |
| `sudo anthias-weather status` | Shows normal or alert mode, which alerts caused it, and any override |
| `sudo anthias-weather force alert` | Alert mode now, whatever the weather, until `force auto` |
| `sudo anthias-weather force alert --minutes 20` | Alert mode for 20 minutes, then back to automatic |
| `sudo anthias-weather force normal` | Normal mode, ignoring NWS alerts, until `force auto` (also takes `--minutes`) |
| `sudo anthias-weather force auto` | Hands control back to the NWS |
| `sudo anthias-weather restore --yes` | Puts the saved playlist back while the service is stopped |
| `sudo journalctl -u anthias-weather -f` | Follows the service log |

The `force` commands write a small override file that the service checks every 5 seconds. That's also how other tools, such as a Home Assistant integration, can control it.

## Good to know

- **Playlist edits made in the Anthias dashboard during a warning are undone** when it ends, because the saved playlist is restored. Use `force normal` first if you need to edit during a warning.
- **Alert pages with schedules**: an alert page with start/end dates or play times that make it inactive won't show, even when switched on. The log warns about this.
- **Playlist order**: Anthias renumbers the playlist each time an asset is switched on or off through its API, in the order the assets were created rather than their play order. anthias-weather works around this by saving the order and setting it explicitly afterwards.
- **Shuffle**: if shuffle is turned on in Anthias, alert pages play in shuffled order too.

## Development

The service is a single Python file using only the standard library. Tests run against a fake Anthias server, which renumbers the playlist like the real one, and a fake NWS server, so they don't touch a real sign:

```bash
python3 -m unittest discover -s tests -v
```

## Roadmap

- A Home Assistant integration, so Home Assistant can switch modes alongside or instead of the built-in NWS check.

## Credits

- Code and design developed in collaboration with Claude (Anthropic).
