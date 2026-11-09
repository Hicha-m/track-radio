"""Public deployed-player audio decoding check; no recording, screenshots or private data."""

import json

from playwright.sync_api import sync_playwright


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome")
        context = browser.new_context()
        # Covers do not participate in audio decoding.
        context.route("**/api/stations/*/artwork", lambda route: route.abort())
        page = context.new_page()
        page.goto("https://radio.hicham.dpdns.org/radios", wait_until="domcontentloaded")
        for key in ("francais", "klassiks", "hit_us", "nocturne"):
            try:
                button = page.locator(f'.station-play[data-url$="/{key}_aac_128k"]')
                button.click(timeout=15000)
                page.wait_for_function(
                    "() => { const a = document.querySelector('#radio-player'); "
                    "return !a.paused && !a.muted && !a.error && a.readyState >= 3 "
                    "&& a.currentTime > 2; }",
                    timeout=25000,
                )
                results.append({"radio": key, "decoded_and_playing": True})
            except Exception as error:
                results.append(
                    {"radio": key, "decoded_and_playing": False, "error": type(error).__name__}
                )
            page.evaluate("window.RadioPlayer.pause()")
        context.close()
        browser.close()
    print("Skyrock browser listening: " + json.dumps(results, sort_keys=True))
    if not all(item["decoded_and_playing"] for item in results):
        raise SystemExit("At least one live stream could not be verified from this network")


if __name__ == "__main__":
    main()
