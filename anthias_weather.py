#!/usr/bin/env python3
"""anthias-weather: put an Anthias playlist into "alert mode" during NWS warnings.

When a configured NWS alert (e.g. Tornado Warning) is active for your location,
every asset except the configured alert assets is disabled. When the alert ends
or is cancelled, every asset is put back exactly as it was before.

Standard library only (Python 3.11+), so it runs on a stock Debian 12/13 host.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import logging
import os
import signal
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

VERSION = "0.3.0"
DEFAULT_CONFIG = "/etc/anthias-weather/config.toml"
DEFAULT_STATE_DIR = "/var/lib/anthias-weather"
DEFAULT_EVENTS = ["Tornado Warning", "Severe Thunderstorm Warning"]
OVERRIDE_VALUES = ("alert", "normal")
TICK_SECONDS = 5  # how often the override file is checked

log = logging.getLogger("anthias-weather")


class ConfigError(Exception):
    pass


class AnthiasError(Exception):
    pass


class NWSError(Exception):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- config


@dataclass
class Config:
    anthias_url: str
    anthias_username: str | None
    anthias_password: str | None
    alert_assets: list[str]
    switch_delay: float
    nws_point: str | None
    nws_zones: list[str]
    nws_events: list[str]
    user_agent: str
    poll_seconds: int
    clear_polls: int
    nws_url: str
    state_dir: str
    http_timeout: float


def _normalize_point(value: str) -> str:
    try:
        lat_s, lon_s = (part.strip() for part in value.split(","))
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise ConfigError(f'nws.point must look like "39.7684,-86.1581", got {value!r}')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ConfigError(f"nws.point is out of range: {value!r}")
    # api.weather.gov only accepts up to 4 decimal places.
    return f"{round(lat, 4):g},{round(lon, 4):g}"


def load_config(path: str) -> Config:
    try:
        with open(path, "rb") as fh:
            # utf-8-sig drops the byte-order mark some Windows editors add.
            text = fh.read().decode("utf-8-sig")
        raw = tomllib.loads(text)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except PermissionError:
        raise ConfigError(f"no permission to read {path}; run the command with sudo")
    except UnicodeDecodeError:
        raise ConfigError(f"{path} is not plain text; save it from Notepad or TextEdit as plain text")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file {path} has a typo near: {exc}")

    anthias = raw.get("anthias", {})
    alert = raw.get("alert_mode", {})
    nws = raw.get("nws", {})
    service = raw.get("service", {})

    alert_assets = [str(a).strip() for a in alert.get("assets", []) if str(a).strip()]
    if not alert_assets:
        raise ConfigError("alert_mode.assets must list at least one asset name")

    point = nws.get("point")
    point = _normalize_point(point) if point else None
    zones = [str(z).strip().upper() for z in nws.get("zones", []) if str(z).strip()]
    if not point and not zones:
        raise ConfigError("set nws.point (latitude,longitude) and/or nws.zones")

    events = [str(e).strip() for e in nws.get("events", DEFAULT_EVENTS) if str(e).strip()]
    if not events:
        raise ConfigError("nws.events must list at least one event name")

    user_agent = str(nws.get("user_agent", "")).strip()
    if not user_agent or "example.com" in user_agent:
        raise ConfigError(
            "nws.user_agent must identify you, e.g. \"anthias-weather (you@yourdomain)\"; "
            "the NWS API asks for a contact in the User-Agent"
        )

    username = anthias.get("username") or None
    password = anthias.get("password") or None
    if bool(username) != bool(password):
        raise ConfigError("set both anthias.username and anthias.password, or neither")

    poll_seconds = int(nws.get("poll_seconds", 60))
    if poll_seconds < 30:
        raise ConfigError("nws.poll_seconds must be at least 30")

    return Config(
        anthias_url=str(anthias.get("url", "http://127.0.0.1")).rstrip("/"),
        anthias_username=username,
        anthias_password=password,
        alert_assets=alert_assets,
        switch_delay=float(alert.get("switch_delay_seconds", 3)),
        nws_point=point,
        nws_zones=zones,
        nws_events=events,
        user_agent=user_agent,
        poll_seconds=poll_seconds,
        clear_polls=max(1, int(nws.get("clear_after_polls", 2))),
        nws_url=str(nws.get("api_url", "https://api.weather.gov")).rstrip("/"),
        state_dir=str(service.get("state_dir", DEFAULT_STATE_DIR)),
        http_timeout=float(service.get("http_timeout_seconds", 15)),
    )


# --------------------------------------------------------------------------- Anthias


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Anthias answers unauthenticated API calls with a redirect to its login
    form instead of a 401, so redirects are surfaced as errors, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthiasClient:
    def __init__(self, base_url: str, username: str | None, password: str | None, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._auth = None
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._auth = f"Basic {token}"

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self._auth:
            req.add_header("Authorization", self._auth)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise AnthiasError(
                    f"{method} {path} was redirected to {exc.headers.get('Location')!r}; "
                    "Anthias authentication is probably on, so set anthias.username/password"
                )
            if exc.code in (401, 403):
                raise AnthiasError(f"{method} {path}: Anthias rejected the credentials (HTTP {exc.code})")
            detail = exc.read()[:300].decode(errors="replace")
            raise AnthiasError(f"{method} {path}: HTTP {exc.code} {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AnthiasError(f"cannot reach Anthias at {self.base_url}: {exc}")
        if not raw:
            return None
        if "json" in ctype:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise AnthiasError(f"{method} {path}: response was not valid JSON")
        return raw.decode(errors="replace")

    def list_assets(self) -> list[dict]:
        result = self._request("GET", "/api/v2/assets")
        if not isinstance(result, list):
            raise AnthiasError("GET /api/v2/assets did not return a list (is this Anthias with the v2 API?)")
        return result

    def set_enabled(self, asset_id: str, enabled: bool) -> dict | None:
        path = f"/api/v2/assets/{urllib.parse.quote(asset_id, safe='')}"
        return self._request("PATCH", path, {"is_enabled": enabled})

    def set_order(self, asset_ids: list[str]) -> None:
        """Set play_order to 0, 1, 2... for these assets, in this order."""
        if asset_ids:
            self._request("POST", "/api/v2/assets/order", {"ids": ",".join(asset_ids)})

    def control(self, command: str) -> None:
        self._request("GET", "/api/v2/assets/control/" + urllib.parse.quote(command, safe="&"))


def playlist_order(assets: list[dict]) -> list[str]:
    """Asset ids in the order Anthias plays them (ties keep list order)."""
    indexed = list(enumerate(assets))
    indexed.sort(key=lambda pair: (pair[1].get("play_order") or 0, pair[0]))
    return [a["asset_id"] for _, a in indexed]


# --------------------------------------------------------------------------- NWS


class NWSClient:
    def __init__(self, base_url: str, user_agent: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout

    def _get(self, path_and_query: str) -> dict:
        req = urllib.request.Request(self.base_url + path_and_query)
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept", "application/geo+json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise NWSError(f"NWS API HTTP {exc.code} for {path_and_query}: {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NWSError(f"cannot reach NWS API: {exc}")
        except json.JSONDecodeError:
            raise NWSError(f"NWS API returned invalid JSON for {path_and_query}")

    def active_alerts(self, point: str | None, zones: list[str]) -> list[dict]:
        """Return the properties of every active alert for the point and/or zones."""
        queries = []
        if point:
            queries.append({"point": point})
        if zones:
            queries.append({"zone": ",".join(zones)})
        found: dict[str, dict] = {}
        for query in queries:
            data = self._get("/alerts/active?" + urllib.parse.urlencode(query))
            features = data.get("features")
            if not isinstance(features, list):
                raise NWSError("NWS API response had no 'features' list")
            for feature in features:
                props = feature.get("properties") or {}
                key = props.get("id") or feature.get("id") or json.dumps(props, sort_keys=True)
                found[key] = props
        return list(found.values())


def parse_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def alert_end(props: dict) -> dt.datetime | None:
    return parse_time(props.get("ends")) or parse_time(props.get("expires"))


def is_matching_alert(props: dict, events: list[str], now: dt.datetime) -> bool:
    if str(props.get("status", "")).lower() != "actual":  # skip Test / Exercise / Draft
        return False
    if str(props.get("messageType", "")).lower() == "cancel":
        return False
    if str(props.get("event", "")).casefold() not in {e.casefold() for e in events}:
        return False
    end = alert_end(props)
    return end is None or end > now


def summarize_alert(props: dict) -> dict:
    end = alert_end(props)
    return {
        "id": props.get("id"),
        "event": props.get("event"),
        "headline": props.get("headline"),
        "area": props.get("areaDesc"),
        "ends": end.isoformat() if end else None,
    }


# --------------------------------------------------------------------------- state


class StateStore:
    """The daemon's memory, kept on disk so a reboot mid-warning still restores
    the original playlist. Only the daemon writes state.json; the CLI writes
    the separate `override` file."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.state_path = os.path.join(state_dir, "state.json")
        self.override_path = os.path.join(state_dir, "override")

    @staticmethod
    def blank() -> dict:
        return {"mode": "normal", "snapshot": None, "order": None, "entered_at": None, "reason": None,
                "alerts": []}

    def load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except FileNotFoundError:
            return self.blank()
        except (OSError, json.JSONDecodeError) as exc:
            log.error("could not read %s (%s); starting from normal mode", self.state_path, exc)
            return self.blank()
        merged = self.blank()
        merged.update(state)
        return merged

    def save(self, state: dict) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)

    def read_override_detail(self) -> tuple[str | None, dt.datetime | None]:
        """(value, until). `until` is set for a timed override; an expired
        timed override reads as no override."""
        try:
            with open(self.override_path, encoding="utf-8") as fh:
                parts = fh.read().strip().lower().split()
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            log.warning("could not read override file: %s", exc)
            return None, None
        if not parts:
            return None, None
        value = parts[0]
        if value not in OVERRIDE_VALUES:
            log.warning("ignoring unknown override %r in %s", value, self.override_path)
            return None, None
        until = parse_time(parts[1]) if len(parts) > 1 else None
        if len(parts) > 1 and until is None:
            log.warning("ignoring override with an unreadable end time in %s", self.override_path)
            return None, None
        if until is not None and until <= utcnow():
            return None, until
        return value, until

    def read_override(self) -> str | None:
        return self.read_override_detail()[0]

    def write_override(self, value: str | None, until: dt.datetime | None = None) -> None:
        if value is None:
            try:
                os.remove(self.override_path)
            except FileNotFoundError:
                pass
            return
        os.makedirs(self.state_dir, exist_ok=True)
        line = value if until is None else f"{value} {until.astimezone(dt.timezone.utc).isoformat()}"
        with open(self.override_path, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        os.chmod(self.override_path, 0o644)


# --------------------------------------------------------------------------- controller


class Controller:
    """Moves the playlist between normal and alert mode. Every step is
    idempotent: if Anthias is unreachable halfway through, the next call
    finishes the job."""

    def __init__(self, cfg: Config, anthias: AnthiasClient, store: StateStore, sleep=time.sleep):
        self.cfg = cfg
        self.anthias = anthias
        self.store = store
        self.sleep = sleep
        self.state = store.load()
        # After a restart in alert mode, re-apply once in case Anthias changed.
        self.dirty = self.state["mode"] == "alert"

    @property
    def mode(self) -> str:
        return self.state["mode"]

    def resolve_alert_ids(self, assets: list[dict]) -> list[str]:
        """One asset id per configured alert asset, in the configured order."""
        order = playlist_order(assets)
        ids: list[str] = []
        missing: list[str] = []
        for wanted in self.cfg.alert_assets:
            hits = [
                a["asset_id"] for a in assets
                if a.get("asset_id") == wanted or str(a.get("name") or "").casefold() == wanted.casefold()
            ]
            # Several assets can share a name (e.g. the same weather page placed
            # twice in the loop). Use only one: an exact asset id wins, otherwise
            # the copy that comes first in the playlist.
            exact = [h for h in hits if h == wanted]
            if exact:
                hits = exact
            hits = [h for h in order if h in hits]
            chosen = next((h for h in hits if h not in ids), None)
            if chosen:
                ids.append(chosen)
            elif not hits:
                missing.append(wanted)
        if missing:
            raise AnthiasError(
                "alert asset(s) not found in Anthias: " + ", ".join(repr(m) for m in missing)
                + " (run `anthias-weather list-assets` to see the exact names)"
            )
        return ids

    def ensure(self, desired: str, reason: str, alerts: list[dict] | None = None) -> None:
        if desired == "alert":
            if self.mode != "alert":
                self._enter(reason, alerts or [])
            elif self.dirty:
                self._apply_alert_set(jump=False)
            elif alerts is not None and alerts != self.state["alerts"]:
                self.state["alerts"] = alerts
                self.state["reason"] = reason
                self.store.save(self.state)
        elif self.mode == "alert":
            self._exit(reason)

    def _enter(self, reason: str, alerts: list[dict]) -> None:
        assets = self.anthias.list_assets()
        self.resolve_alert_ids(assets)  # fail before touching anything
        snapshot = {a["asset_id"]: bool(a.get("is_enabled")) for a in assets}
        log.warning("ENTERING ALERT MODE: %s", reason)
        # Save the snapshot first so a crash mid-switch can still restore.
        self.state = {
            "mode": "alert",
            "snapshot": snapshot,
            "order": playlist_order(assets),
            "entered_at": utcnow().isoformat(),
            "reason": reason,
            "alerts": alerts,
        }
        self.store.save(self.state)
        self.dirty = True
        self._apply_alert_set(jump=True, assets=assets)

    def _apply_alert_set(self, jump: bool, assets: list[dict] | None = None) -> None:
        assets = assets if assets is not None else self.anthias.list_assets()
        alert_ids = self.resolve_alert_ids(assets)
        changed = 0
        for asset in assets:
            aid = asset["asset_id"]
            desired = aid in alert_ids
            if bool(asset.get("is_enabled")) != desired:
                result = self.anthias.set_enabled(aid, desired)
                changed += 1
                log.info("  %s %r", "enabled " if desired else "disabled", asset.get("name"))
                if desired and isinstance(result, dict) and result.get("is_active") is False:
                    log.warning(
                        "  %r is enabled but not active; check its start/end dates and play times in Anthias",
                        asset.get("name"),
                    )
        # Anthias renumbers the playlist on every enable/disable, so set the
        # alert pages' order explicitly afterwards (first alert asset first).
        if changed or playlist_order([a for a in assets if a["asset_id"] in alert_ids]) != alert_ids:
            self.anthias.set_order(alert_ids)
        self.dirty = False
        if jump and alert_ids:
            self._jump(f"asset&{alert_ids[0]}")
        log.info("alert set applied (%d change(s))", changed)

    def _exit(self, reason: str) -> None:
        log.warning("LEAVING ALERT MODE: %s", reason)
        snapshot = self.state.get("snapshot") or {}
        assets = self.anthias.list_assets()
        restored = 0
        for asset in assets:
            aid = asset["asset_id"]
            if aid not in snapshot:  # added during the alert: leave it alone
                continue
            if bool(asset.get("is_enabled")) != snapshot[aid]:
                self.anthias.set_enabled(aid, snapshot[aid])
                restored += 1
                log.info("  %s %r", "enabled " if snapshot[aid] else "disabled", asset.get("name"))
        # Anthias scrambles play_order while assets are switched on and off, so
        # put back the saved order last. Assets added during the alert go at
        # the end, in their current order.
        saved_order = self.state.get("order")
        if saved_order:
            current = {a["asset_id"] for a in assets}
            order = [aid for aid in saved_order if aid in current]
            order += [aid for aid in playlist_order(assets) if aid not in saved_order]
            self.anthias.set_order(order)
            log.info("playlist order restored (%d asset(s))", len(order))
        # Only forget the snapshot once every asset is back.
        self.state = StateStore.blank()
        self.store.save(self.state)
        self.dirty = False
        log.info("normal playlist restored (%d change(s))", restored)
        self._jump("next")

    def _jump(self, command: str) -> None:
        if self.cfg.switch_delay > 0:
            self.sleep(self.cfg.switch_delay)  # let the viewer pick up the new playlist
        try:
            self.anthias.control(command)
        except AnthiasError as exc:
            log.warning("could not switch the screen right away (%s); it will change at the next asset", exc)
            if command != "next":
                try:
                    self.anthias.control("next")
                except AnthiasError:
                    pass


# --------------------------------------------------------------------------- daemon


class Daemon:
    def __init__(self, cfg: Config, controller: Controller, nws: NWSClient, store: StateStore, now=utcnow):
        self.cfg = cfg
        self.controller = controller
        self.nws = nws
        self.store = store
        self.now = now
        self.clear_count = 0
        self.stop_event = threading.Event()

    def tick(self, override: str | None) -> None:
        """One decision: work out the desired mode and apply it."""
        if override == "alert":
            self.controller.ensure("alert", "manual override (anthias-weather force alert)",
                                   [{"event": "Manual override", "headline": None, "area": None,
                                     "ends": None, "id": "override"}])
            return
        if override == "normal":
            self.controller.ensure("normal", "manual override (anthias-weather force normal)")
            return

        try:
            props = self.nws.active_alerts(self.cfg.nws_point, self.cfg.nws_zones)
        except NWSError as exc:
            self._handle_nws_outage(exc)
            return

        now = self.now()
        matches = [p for p in props if is_matching_alert(p, self.cfg.nws_events, now)]
        if matches:
            self.clear_count = 0
            summaries = sorted((summarize_alert(p) for p in matches), key=lambda s: str(s["id"]))
            reason = "; ".join(sorted({s["event"] for s in summaries}))
            self.controller.ensure("alert", reason, summaries)
            return

        if self.controller.mode != "alert":
            self.clear_count = 0
            return
        if self._entered_by_override():
            self.controller.ensure("normal", "manual override cleared and no matching alerts")
            return
        self.clear_count += 1
        if self.clear_count >= self.cfg.clear_polls:
            self.controller.ensure("normal", "no matching NWS alerts remain (expired or cancelled)")
            self.clear_count = 0
        else:
            log.info("no matching alerts on this poll; confirming on the next one before resuming")
            if self.controller.dirty:
                self.controller.ensure("alert", self.controller.state.get("reason") or "")

    def _entered_by_override(self) -> bool:
        alerts = self.controller.state.get("alerts") or []
        return bool(alerts) and all(a.get("id") == "override" for a in alerts)

    def _handle_nws_outage(self, exc: Exception) -> None:
        if self.controller.mode != "alert":
            log.warning("%s; staying in normal mode", exc)
            return
        if self._entered_by_override():
            self.controller.ensure("normal", "manual override cleared (NWS currently unreachable)")
            return
        ends = [parse_time(a.get("ends")) for a in self.controller.state.get("alerts") or []]
        if ends and all(e is not None and e <= self.now() for e in ends):
            self.controller.ensure("normal", "NWS unreachable and every known alert has passed its end time")
        else:
            log.warning("%s; staying in alert mode until the NWS confirms or the alerts' end times pass", exc)
            if self.controller.dirty:
                self.controller.ensure("alert", self.controller.state.get("reason") or "")

    def run(self) -> None:
        log.info("anthias-weather %s watching %s for: %s", VERSION,
                 " + ".join(filter(None, [self.cfg.nws_point and f"point {self.cfg.nws_point}",
                                          self.cfg.nws_zones and f"zones {','.join(self.cfg.nws_zones)}"])),
                 ", ".join(self.cfg.nws_events))
        if self.controller.mode == "alert":
            log.warning("started in ALERT MODE (from saved state): %s", self.controller.state.get("reason"))
        next_poll = 0.0
        last_override = "<unset>"
        while not self.stop_event.is_set():
            override = self.store.read_override()
            if override != last_override or time.monotonic() >= next_poll:
                if override != last_override and last_override != "<unset>":
                    log.info("override changed to %s", override or "auto")
                last_override = override
                try:
                    self.tick(override)
                    next_poll = time.monotonic() + self.cfg.poll_seconds
                except AnthiasError as exc:
                    # Anthias may still be starting at boot; retry sooner than a full poll.
                    log.error("Anthias: %s (will retry)", exc)
                    if self.controller.mode == "alert":
                        self.controller.dirty = True
                    next_poll = time.monotonic() + min(self.cfg.poll_seconds, 30)
                except Exception:  # never let one bad response kill the service
                    log.exception("unexpected error (will retry)")
                    next_poll = time.monotonic() + self.cfg.poll_seconds
            self.stop_event.wait(TICK_SECONDS)
        log.info("stopped (mode stays %s; it resumes when the service starts again)", self.controller.mode)


# --------------------------------------------------------------------------- CLI


def build(cfg: Config) -> tuple[AnthiasClient, NWSClient, StateStore]:
    anthias = AnthiasClient(cfg.anthias_url, cfg.anthias_username, cfg.anthias_password, cfg.http_timeout)
    nws = NWSClient(cfg.nws_url, cfg.user_agent, cfg.http_timeout)
    return anthias, nws, StateStore(cfg.state_dir)


def cmd_run(cfg: Config, args) -> int:
    anthias, nws, store = build(cfg)
    daemon = Daemon(cfg, Controller(cfg, anthias, store), nws, store)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: daemon.stop_event.set())
    daemon.run()
    return 0


def cmd_list_assets(cfg: Config, args) -> int:
    anthias, _, _ = build(cfg)
    raw = anthias.list_assets()
    by_id = {a["asset_id"]: a for a in raw}
    try:
        chosen = Controller(cfg, anthias, StateStore(cfg.state_dir)).resolve_alert_ids(raw)
    except AnthiasError as exc:
        chosen = []
        print(f"note: {exc}\n")
    wanted = {w.casefold() for w in cfg.alert_assets}
    print(f"{'#':>3} {'ENABLED':8} {'ACTIVE':7} {'ALERT':6} {'NAME':40} ASSET ID")
    for n, aid in enumerate(playlist_order(raw), 1):
        a = by_id[aid]
        if aid in chosen:
            mark = "*"
        elif str(a.get("name") or "").casefold() in wanted:
            mark = "copy"  # same name as an alert asset; stays off during alerts
        else:
            mark = ""
        print(f"{n:>3} {'yes' if a.get('is_enabled') else 'no':8} {'yes' if a.get('is_active') else 'no':7} "
              f"{mark:6} {str(a.get('name'))[:40]:40} {aid}")
    if any(str(a.get("name") or "").casefold() in wanted and a["asset_id"] not in chosen for a in raw):
        print("\n* = plays during alerts. 'copy' = another asset with the same name; during alerts only")
        print("    the * copy plays. To pick a different copy, put its ASSET ID in alert_mode.assets.")
    return 0


def cmd_check(cfg: Config, args) -> int:
    anthias, nws, store = build(cfg)
    ok = True
    print(f"Anthias at {cfg.anthias_url}:")
    try:
        assets = anthias.list_assets()
        print(f"  reachable, {len(assets)} asset(s)")
        try:
            ids = Controller(cfg, anthias, store).resolve_alert_ids(assets)
            names = {a["asset_id"]: a.get("name") for a in assets}
            print("  alert-mode assets: " + ", ".join(repr(names[i]) for i in ids))
        except AnthiasError as exc:
            ok = False
            print(f"  PROBLEM: {exc}")
    except AnthiasError as exc:
        ok = False
        print(f"  PROBLEM: {exc}")
        if "cannot reach" in str(exc):
            print("  (Expected if Anthias is switched off right now, e.g. during a flash drive install.)")

    where = " + ".join(filter(None, [cfg.nws_point and f"point {cfg.nws_point}",
                                     cfg.nws_zones and f"zones {','.join(cfg.nws_zones)}"]))
    print(f"NWS alerts for {where}:")
    try:
        props = nws.active_alerts(cfg.nws_point, cfg.nws_zones)
        now = utcnow()
        if not props:
            print("  reachable, no active alerts right now")
        for p in props:
            flag = "TRIGGERS" if is_matching_alert(p, cfg.nws_events, now) else "ignored "
            end = alert_end(p)
            print(f"  [{flag}] {p.get('event')} until {end.isoformat() if end else '?'}")
        print("  trigger events: " + ", ".join(cfg.nws_events))
    except NWSError as exc:
        ok = False
        print(f"  PROBLEM: {exc}")
    print("All good." if ok else "Fix the problems above, then run check again.")
    return 0 if ok else 1


def _local(when: dt.datetime) -> str:
    return when.astimezone().strftime("%I:%M %p").lstrip("0")


def cmd_status(cfg: Config, args) -> int:
    store = StateStore(cfg.state_dir)
    state = store.load()
    override, until = store.read_override_detail()
    print(f"mode:     {state['mode'].upper()}")
    if override and until:
        print(f"override: {override} until {_local(until)}, then automatic")
    elif override:
        print(f"override: {override} (until `anthias-weather force auto`)")
    elif until:
        print(f"override: none (the timed {_local(until)} override has ended; automatic)")
    else:
        print("override: none (automatic)")
    if state["mode"] == "alert":
        print(f"since:    {state.get('entered_at')}")
        print(f"reason:   {state.get('reason')}")
        for a in state.get("alerts") or []:
            print(f"  - {a.get('event')}: {a.get('headline') or ''} (ends {a.get('ends') or '?'})")
        snap = state.get("snapshot") or {}
        print(f"saved playlist: {sum(snap.values())} of {len(snap)} asset(s) enabled before the alert")
    return 0


def cmd_force(cfg: Config, args) -> int:
    store = StateStore(cfg.state_dir)
    value = None if args.mode == "auto" else args.mode
    if args.minutes is not None and value is None:
        print("--minutes only goes with `force alert` or `force normal`", file=sys.stderr)
        return 2
    if args.minutes is not None and not 1 <= args.minutes <= 1440:
        print("--minutes must be between 1 and 1440", file=sys.stderr)
        return 2
    until = utcnow() + dt.timedelta(minutes=args.minutes) if args.minutes else None
    try:
        store.write_override(value, until)
    except PermissionError:
        print(f"permission denied writing {store.override_path}; try again with sudo", file=sys.stderr)
        return 1
    if value and until:
        print(f"Forcing {value} mode until {_local(until)} ({args.minutes} min), then back to automatic.")
    else:
        print({"alert": "Forcing alert mode. Run `force auto` to hand control back to the NWS.",
               "normal": "Forcing normal mode; NWS alerts are ignored until `force auto`.",
               None: "Automatic mode: the NWS decides."}[value])
    print(f"A running service applies this within {TICK_SECONDS} seconds.")
    return 0


def cmd_restore(cfg: Config, args) -> int:
    """Put the saved playlist back without the service running (e.g. after uninstalling)."""
    anthias, _, store = build(cfg)
    controller = Controller(cfg, anthias, store)
    if controller.mode != "alert":
        print("Not in alert mode; nothing to restore.")
        return 0
    if not args.yes:
        print("This restores the playlist saved when alert mode started.\n"
              "Stop the service first (sudo systemctl stop anthias-weather), then re-run with --yes.")
        return 1
    controller.cfg.switch_delay = 0
    controller.ensure("normal", "manual restore")
    print("Playlist restored.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anthias-weather", description=__doc__.splitlines()[0])
    parser.add_argument("-c", "--config", default=os.environ.get("ANTHIAS_WEATHER_CONFIG", DEFAULT_CONFIG))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the service (used by systemd)")
    sub.add_parser("check", help="test the config, Anthias connection and NWS lookup")
    sub.add_parser("list-assets", help="show Anthias assets and which are alert-mode assets")
    sub.add_parser("status", help="show the current mode")
    force = sub.add_parser("force", help="override the NWS: alert, normal, or auto")
    force.add_argument("mode", choices=["alert", "normal", "auto"])
    force.add_argument("--minutes", type=int, help="end the override automatically after this many minutes")
    restore = sub.add_parser("restore", help="restore the saved playlist while the service is stopped")
    restore.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    journald = "INVOCATION_ID" in os.environ
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s" if journald else "%(asctime)s %(levelname)s %(message)s",
    )
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    handlers = {"run": cmd_run, "check": cmd_check, "list-assets": cmd_list_assets,
                "status": cmd_status, "force": cmd_force, "restore": cmd_restore}
    try:
        return handlers[args.command](cfg, args)
    except AnthiasError as exc:
        print(f"Anthias error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
