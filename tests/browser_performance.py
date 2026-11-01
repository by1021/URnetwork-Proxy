"""Chromium performance/regressions; temporary server, offline data, no live proxies.

Run: CHROMIUM_PATH=/usr/bin/chromium python -m tests.browser_performance
Compare old JS: add --source /path/to/app.js --measure-only
Only the intercepted test script exposes renderer handles; production has no hooks.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from core.web_server import ManagementApp, ManagementServer
from tests.browser_smoke import BrowserClient


ROOT = Path(__file__).resolve().parents[1]


def wait_js(page, expression):
    # Keep production CSP intact: poll through CDP, not in-page string eval.
    deadline = time.monotonic() + 15
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, f"Timed out: {expression}"
        page.wait_for_timeout(20)


def main():
    from playwright.sync_api import sync_playwright, expect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "web/app.js")
    parser.add_argument("--measure-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.read_text(encoding="utf-8")
    marker = "  connect(null, true);"
    assert source.count(marker) == 1
    source = source.replace(marker, """
  window.__uiTest = {state, renderGateways, renderEvents, renderCountries,
    loadGateways, loadEvents, loadCountries, startPolling, endSession};
""" + marker)
    gateways = [dict(id=f"g-{i}", name=f"Gateway {i}", host="::", port=11000 + i,
        mode="country", country_code="US", location_id="US", enabled=True,
        status="running", exit_ip="203.0.113.10", latency_ms=38.12, connections=0,
        auto_heal=True, interval=30, failures=0, failures_before_rotate=3,
        rotations=0, last_error="", last_check="2026-11-01T00:00:00Z") for i in range(100)]
    events = [dict(id=i, gateway_id=f"g-{i % 100}", level="info", message="upstream_ready",
                   time="2026-11-01T00:00:00Z") for i in range(1, 301)]
    countries = [dict(code=chr(65 + i // 26) + chr(65 + i % 26), name=f"Region {i}",
        location_id=f"location-{i}", providers=i % 9, stable=True) for i in range(200)]
    countries += [dict(code="US", name="United States", location_id="US", providers=20000),
                  dict(code="JP", name="Japan", location_id="JP", providers=500)]
    calls = Counter()
    failures = set()

    with tempfile.TemporaryDirectory() as directory:
        app = ManagementApp({}, directory, client=BrowserClient(),
                            config_path=Path(directory) / "config.json")
        server = ManagementServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH"))
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.add_init_script("""(() => {
                  const interval = window.setInterval.bind(window);
                  window.__intervals = [];
                  window.setInterval = (callback, delay, ...args) => {
                    window.__intervals.push({callback, delay});
                    return interval(callback, delay, ...args);
                  };
                })();""")

                def api_route(route):
                    url = urlsplit(route.request.url)
                    calls[url.path] += 1
                    if url.path in failures:
                        route.fulfill(status=503, json={"error": "fixture unavailable"})
                        return
                    data = {
                        "/api/session": {"csrf": "fixture-csrf"},
                        "/api/health": {"status": "ok"},
                        "/api/runtime": app.runtime(),
                        "/api/config": app.configuration(),
                        "/api/account": app.client.get_account_status(),
                        "/api/gateways": {"gateways": gateways},
                        "/api/countries": {"countries": countries, "source": "fixture", "stale": False},
                        "/api/countries/refresh": {},
                        "/api/events": {"events": [event for event in events
                            if event["id"] > int(parse_qs(url.query).get("after", ["0"])[0])]},
                    }.get(url.path)
                    assert data is not None, f"Unexpected fixture request: {url.path}"
                    route.fulfill(json=data)

                page.route("**/api/**", api_route)
                page.route("**/app.js*", lambda route: route.fulfill(content_type="text/javascript", body=source))
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                wait_js(page, "window.__uiTest?.state.settingsReady && __uiTest.state.loads.size === 0")
                expect(page.locator(".gateway-card")).to_have_count(100)
                page.evaluate("__uiTest.state.timers.forEach(clearInterval)")

                # Includes synchronous layout, not merely time spent making nodes.
                metrics = page.evaluate("""() => {
                  const ui = __uiTest, state = ui.state;
                  const measure = (change, render, target) => {
                    const samples = [];
                    for (let i = 0; i < 9; i++) {
                      change(i);
                      const start = performance.now(); render();
                      document.getElementById(target).offsetHeight;
                      if (i > 1) samples.push(performance.now() - start);
                    }
                    return samples;
                  };
                  const cards = [...document.querySelectorAll('.gateway-card')];
                  state.gateways[0] = {...state.gateways[0], connections: 1};
                  ui.renderGateways();
                  const retained = [...document.querySelectorAll('.gateway-card')]
                    .filter((card, i) => card === cards[i]).length;
                  const initialHiddenRows = document.querySelectorAll('#event-list .event-row').length;
                  const initialHiddenCountries = document.querySelectorAll('#country-list .country-card').length;
                  const single = measure(i => { state.gateways[0].connections = i + 2; },
                    ui.renderGateways, 'gateway-list');
                  const all = measure(i => state.gateways.forEach(g => { g.connections = i + 20; }),
                    ui.renderGateways, 'gateway-list');
                  document.querySelector('[data-view="events"]').click();
                  ui.renderEvents();
                  const eventNoop = measure(() => {}, ui.renderEvents, 'event-list');
                  document.querySelector('[data-view="countries"]').click();
                  ui.renderCountries();
                  const countryNoop = measure(() => {}, ui.renderCountries, 'country-list');
                  return {single_gateway_ms: single, all_gateways_ms: all,
                    unchanged_events_ms: eventNoop, unchanged_countries_ms: countryNoop,
                    retained_cards: retained, initial_hidden_events: initialHiddenRows,
                    initial_hidden_countries: initialHiddenCountries};
                }""")
                wait_js(page, "__uiTest.state.loads.size === 0")
                metrics["median_ms"] = {key: round(statistics.median(value), 3)
                    for key, value in metrics.items() if key.endswith("_ms")}
                metrics["browser"] = browser.version
                if args.output:
                    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(metrics, ensure_ascii=False), flush=True)
                if args.measure_only:
                    browser.close()
                    return

                assert metrics["retained_cards"] >= 99, "Changing one gateway rebuilt unaffected cards"
                assert metrics["initial_hidden_events"] == 0, "Hidden events eagerly rendered"
                assert metrics["initial_hidden_countries"] == 0, "Hidden countries eagerly rendered"
                page.locator('.nav-item[data-view="gateways"]').click()
                page.evaluate("__uiTest.loadGateways()")
                page.locator('[data-gateway-id="g-1"] [data-action="copy-http"]').focus()
                page.evaluate("window.__untouched = document.querySelector('[data-gateway-id=\"g-1\"]')")
                gateways[0]["connections"] = 81
                page.evaluate("__uiTest.loadGateways()")
                assert page.evaluate("__untouched === document.querySelector('[data-gateway-id=\"g-1\"]')")
                assert page.evaluate("document.activeElement.dataset.action === 'copy-http'")
                expect(page.locator('[data-gateway-id="g-0"] .gateway-facts')).to_contain_text("81")
                # Simultaneous health/connection updates patch text, not entire cards.
                page.evaluate("window.__cards = [...document.querySelectorAll('.gateway-card')]")
                for gateway in gateways:
                    gateway.update(connections=13, latency_ms=12.34, exit_ip="203.0.113.25",
                                   failures=2, rotations=7, last_check="2026-11-01T01:02:03Z")
                page.evaluate("__uiTest.loadGateways()")
                assert page.evaluate("__cards.every((card, i) => card === document.querySelectorAll('.gateway-card')[i])")
                expect(page.locator('[data-gateway-id="g-0"] .gateway-facts')).to_contain_text("12.34 ms")
                expect(page.locator('[data-gateway-id="g-0"] .gateway-meta')).to_contain_text("失败 2 / 3")
                expect(page.locator('[data-gateway-id="g-0"] .gateway-meta')).to_contain_text("换线 7 次")
                # A layout/config change must replace closures, not leave old edit/copy data.
                gateways[1].update(name="Updated fixture", port=12101, country_code="JP",
                                   location_id="JP", username="fixture-user", password="fixture-pass",
                                   enabled=False, status="stopped")
                page.evaluate("__uiTest.loadGateways()")
                card = page.locator('[data-gateway-id="g-1"]')
                expect(card.locator('[data-action="start"]')).to_be_enabled()
                expect(card.locator('[data-action="check"]')).to_be_disabled()
                expect(card.locator('.address-row code').first).to_have_text("fixture-user:fixture-pass@127.0.0.1:12101")
                card.locator('[data-action="edit"]').click()
                expect(page.locator("#gateway-name")).to_have_value("Updated fixture")
                expect(page.locator("#gateway-port")).to_have_value("12101")
                expect(page.locator("#gateway-country")).to_have_value("JP")
                page.evaluate("window.__countryOption = document.querySelector('#gateway-country option[value=JP]')")
                page.evaluate("__uiTest.loadCountries()")
                expect(page.locator("#gateway-country")).to_have_value("JP")
                assert page.evaluate("__countryOption === document.querySelector('#gateway-country option[value=JP]')")
                page.locator("#gateway-cancel").click()
                page.locator("#gateway-filter").select_option("stopped")
                expect(page.locator(".gateway-card")).to_have_count(1)
                page.locator("#gateway-filter").select_option("all")

                # Unchanged requests must not detach select options or log rows.
                page.locator('.nav-item[data-view="events"]').click()
                wait_js(page, "__uiTest.state.loads.size === 0")
                page.evaluate("window.__row = document.querySelector('#event-list .event-row'); window.__option = document.querySelector('#event-gateway option:nth-child(2)')")
                page.evaluate("__uiTest.loadEvents()")
                page.evaluate("__uiTest.loadGateways()")
                assert page.evaluate("__row === document.querySelector('#event-list .event-row') && __option === document.querySelector('#event-gateway option:nth-child(2)')")
                expect(page.locator("#event-list .event-row")).to_have_count(300)
                gateways[0]["name"] = "Renamed gateway"
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator("#event-list .event-row").first).to_contain_text("Renamed gateway")
                events.append(dict(id=301, gateway_id="g-0", level="warn", message="network_offline", time="2026-11-01T00:00:01Z"))
                page.evaluate("__uiTest.loadEvents()")
                expect(page.locator("#event-list .event-row")).to_have_count(300)
                expect(page.locator("#event-list .event-row").first).to_contain_text("网络离线")
                page.locator("#event-level").select_option("warning")
                expect(page.locator("#event-list .event-row")).to_have_count(1)
                page.locator("#event-level").select_option("")

                # Deletion still keeps an event filter for historic gateway IDs.
                del gateways[0]
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator('#event-gateway option[value="g-0"]')).to_contain_text("已删除网关")
                page.locator('.nav-item[data-view="gateways"]').click()
                expect(page.locator(".gateway-card")).to_have_count(99)
                gateways.reverse()
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator(".gateway-card").first).to_have_attribute("data-gateway-id", "g-99")
                page.locator("#gateway-search").fill("Gateway 99")
                expect(page.locator(".gateway-card")).to_have_count(1)
                page.locator("#gateway-search").fill("missing fixture")
                expect(page.locator("#gateway-list")).to_contain_text("没有匹配")
                page.locator("#gateway-search").fill("")
                expect(page.locator(".gateway-card")).to_have_count(99)
                gateways.append(dict(gateways[0], id="g-new", name="New fixture", port=12001))
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator(".gateway-card")).to_have_count(100)

                # Failed/empty -> successful reload must not leave a cached empty state.
                saved = gateways[:]
                gateways.clear()
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator("#gateway-list")).to_contain_text("暂无网关")
                failures.add("/api/gateways")
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator("#gateways-error")).to_be_visible()
                failures.clear()
                page.evaluate("__uiTest.loadGateways()")
                expect(page.locator("#gateway-list")).to_contain_text("暂无网关")
                gateways.extend(saved)
                page.evaluate("__uiTest.loadGateways()")

                page.locator('.nav-item[data-view="countries"]').click()
                page.locator("#country-search").fill("日本")
                expect(page.locator(".country-card")).to_have_count(1)
                expect(page.locator(".country-card")).to_contain_text("JP")
                page.locator("#country-search").fill("")
                page.locator("#country-sort").select_option("providers-desc")
                expect(page.locator(".country-card").first).to_contain_text("US")
                page.locator("#country-sort").select_option("providers-asc")
                expect(page.locator(".country-card").first.locator(".country-provider-count strong")).to_have_text("0")
                # A burst of input events should do no synchronous list work and one final render.
                assert page.evaluate("""async () => {
                  const list = document.getElementById('country-list'), before = list.firstElementChild;
                  const input = document.getElementById('country-search');
                  for (const value of ['J', 'JP', '日本']) {
                    input.value = value; input.dispatchEvent(new Event('input'));
                  }
                  const deferred = before === list.firstElementChild;
                  await new Promise(requestAnimationFrame);
                  return deferred && list.children.length === 1 && list.textContent.includes('JP');
                }"""), "Search did synchronous work or ignored the latest input"

                saved_countries = countries[:]
                countries.clear()
                page.evaluate("__uiTest.loadCountries()")
                expect(page.locator("#country-list")).to_contain_text("当前没有可用国家")
                page.locator("#country-search").fill("missing fixture")
                countries.extend(saved_countries)
                page.evaluate("__uiTest.loadCountries()")
                expect(page.locator("#country-list")).to_contain_text("没有匹配的国家")
                page.locator("#country-search").fill("")
                expect(page.locator(".country-card")).to_have_count(len(countries))

                # Repeated interval callbacks emulate a background tab (no wall-clock sleeps).
                page.evaluate("""() => {
                  Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                  document.dispatchEvent(new Event('visibilitychange'));
                }""")
                before = calls.copy()
                page.evaluate("__intervals.forEach(({callback}) => callback())")
                page.wait_for_timeout(100)
                assert calls == before, f"Hidden page still polled: {calls - before}"
                # Advance freshness deadlines without changing performance.now used above.
                page.evaluate("""() => {
                  const now = Date.now; Date.now = () => now() + 360000;
                  Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                  document.dispatchEvent(new Event('visibilitychange'));
                }""")
                wait_js(page, "__uiTest.state.loads.size === 0")
                for endpoint in ("/api/gateways", "/api/events", "/api/account", "/api/countries", "/api/health"):
                    assert calls[endpoint] == before[endpoint] + 1, f"Resume missed {endpoint}"
                # Concurrent reads share one request; no duplicate polling timers after resume.
                before = calls.copy()
                page.evaluate("Promise.all(Array.from({length: 5}, () => __uiTest.loadGateways()))")
                assert calls["/api/gateways"] == before["/api/gateways"] + 1
                page.evaluate("__uiTest.endSession()")
                before = calls.copy()
                page.evaluate("__intervals.forEach(({callback}) => callback()); document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_timeout(100)
                assert calls == before, "Logged-out page still polled"
                expect(page.locator(".gateway-card")).to_have_count(0)
                assert not errors, errors
                browser.close()
                print("Performance regression PASS: keyed cards/focus, lazy views, no-op events/options, search/sort, visibility/resume, dedup/logout, empty/error recovery")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.close()


if __name__ == "__main__":
    main()
