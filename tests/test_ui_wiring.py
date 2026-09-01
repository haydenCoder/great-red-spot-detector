"""Web UI wiring regressions — the static contract between index.html, app.js
and style.css.

These are source-level assertions on purpose: the interactive parts (drawer
drag, tab swipe, slider fill) need a real browser, but every one of the bugs
this file pins was findable without one — a helper that was out of scope, an
id the JS reached for that the markup never had, a poll cadence that outran
the server's request budget and froze the page about 20 seconds after load.
"""
from __future__ import annotations

import os
import re
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

HTML = (APP / "templates" / "index.html").read_text(encoding="utf-8")
JS = (APP / "static" / "app.js").read_text(encoding="utf-8")
CSS = (APP / "static" / "style.css").read_text(encoding="utf-8")
SERVER = (APP / "server.py").read_text(encoding="utf-8")


def _closures(src: str):
    """Yield (start, end) char offsets of each top-level ``(function () {`` IIFE.

    Depth starts at 1: the opening brace is already consumed by the pattern.
    """
    out = []
    for m in re.finditer(r"^\(function \(\) \{", src, re.MULTILINE):
        depth, i, n = 1, m.end(), len(src)
        while i < n:
            c = src[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((m.start(), i))
    return out


class TestStaticMarkup(unittest.TestCase):
    def test_no_stale_classes_or_dead_ids(self):
        for stale in ("menu-only-mobile", "time-bar-row", "time-bar-label", "btnCloseDrawer"):
            self.assertNotIn(stale, HTML, f"index.html still references removed UI hook {stale!r}")
            self.assertNotIn(stale, CSS, f"style.css still styles removed UI hook {stale!r}")

    def test_dark_color_scheme_declared(self):
        """Without color-scheme the native date picker renders light-on-dark."""
        self.assertIn('name="color-scheme"', HTML)
        self.assertRegex(CSS, r":root\s*\{[^}]*color-scheme:\s*dark")

    def test_backdrop_does_not_toggle_display(self):
        """`hidden` + an opacity transition cannot animate: it must be class-driven."""
        m = re.search(r'<div class="drawer-backdrop"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertNotIn("hidden", m.group(0), "backdrop must not carry the hidden attribute")
        self.assertRegex(CSS, r"\.drawer-backdrop\.show\s*\{[^}]*opacity:\s*1")
        self.assertNotIn("backdrop.hidden", JS, "setDrawer must not toggle display for the fade")

    def test_assets_share_one_cache_version(self):
        """CSS + JS must be busted with one token, and it must not be a literal.

        The old ?v=6.5.1 pair survived several releases and went stale, which
        is exactly how "my browser still shows the old UI" tickets are born.
        """
        busts = re.findall(r"/static/[\w.]+\?v=\{\{ ui_v or 'dev' \}\}", HTML)
        self.assertEqual(len(busts), 2, f"expected 2 templated asset links, got {busts}")
        self.assertNotRegex(HTML, r"/static/[\w.]+\?v=\d")
        server = (APP / "server.py").read_text(encoding="utf-8")
        self.assertIn("render_template(\"index.html\", ui_v=", server)


class TestDomContract(unittest.TestCase):
    def test_every_js_id_exists_in_markup(self):
        ids = set(re.findall(r'\$\("([\w-]+)"\)', JS)) | set(
            re.findall(r'getElementById\("([\w-]+)"\)', JS)
        )
        self.assertTrue(ids, "no element ids found in app.js — did the parser break?")
        missing = sorted(i for i in ids if f'id="{i}"' not in HTML)
        self.assertEqual(missing, [], f"app.js reaches for ids the markup does not define: {missing}")

    def test_tabs_and_panes_pair_up(self):
        tabs = re.findall(r'data-tab="([\w-]+)"', HTML)
        panes = re.findall(r'id="tab-([\w-]+)"', HTML)
        self.assertEqual(sorted(tabs), sorted(panes), "every tab button needs exactly one pane")
        self.assertEqual(len(tabs), len(set(tabs)), "duplicate data-tab value")

    def test_tab_strip_is_a11y_complete(self):
        """role=tab without aria-controls/tabindex is a tablist in name only."""
        for m in re.finditer(r'<button[^>]*role="tab"[^>]*>', HTML):
            tag = m.group(0)
            self.assertIn("aria-controls=", tag, f"tab button missing aria-controls: {tag}")
            self.assertIn("tabindex=", tag, f"tab button missing roving tabindex: {tag}")
            self.assertIn('id="tabbtn-', tag, f"tab button needs an id for aria-labelledby: {tag}")
        for m in re.finditer(r'<div id="tab-[\w-]+"[^>]*role="tabpanel"[^>]*>', HTML):
            tag = m.group(0)
            self.assertIn("aria-labelledby=", tag, f"tabpanel missing aria-labelledby: {tag}")
            self.assertIn("tabindex=", tag, f"tabpanel needs tabindex=0 to be scrollable by keyboard: {tag}")

    def test_drawer_is_a_focus_target(self):
        m = re.search(r'<section class="panel controls"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertIn('tabindex="-1"', m.group(0), "drawer must be focusable so focus can move into it")
        self.assertIn('id="btnDrawerCloseTop"', HTML)
        self.assertIn('id="edgeZone"', HTML, "swipe-to-open needs its drag strip")


class TestJsScope(unittest.TestCase):
    def test_helpers_are_in_scope_where_used(self):
        """Regression: the Deterioration Lab called esc() from the main IIFE.

        The ReferenceError killed the tips list, the method-survival bars and
        the raw matrix — the panel looked like it had finished but never
        rendered its results.
        """
        for start, end in _closures(JS):
            body = JS[start:end]
            for helper in ("esc",):
                if re.search(rf"\b{helper}\(", body):
                    defined = re.search(rf"(const|function|let)\s+{helper}\b", body)
                    self.assertIsNotNone(
                        defined,
                        f"`{helper}()` used in the closure at offset {start} without a local "
                        "definition — closures in this file do not share scope",
                    )

    def test_deterioration_lab_is_wired(self):
        for hook in ("btnDetRun", "btnDetStop", "detBar", "detProgText", "detMethods", "detTips"):
            self.assertIn(hook, JS)
        # charts measure 0 inside a hidden pane, so a redraw hook is mandatory
        self.assertIn('"grs:tab"', JS)
        self.assertIn("redrawDet", JS)


class TestPollingBudget(unittest.TestCase):
    def test_readonly_polls_get_their_own_budget(self):
        """A live tab fires far more reads than writes; sharing one queue
        throttled the page into 429s and left the UI frozen."""
        import security_hard

        self.assertIn("/api/logs", security_hard.POLL_ENDPOINTS)
        self.assertIn("/api/job", security_hard.POLL_ENDPOINTS)
        self.assertIn("/api/status", security_hard.POLL_ENDPOINTS)
        self.assertIn("/", security_hard.POLL_ENDPOINTS, "loading the page must not eat the work budget")
        self.assertEqual(security_hard.rate_bucket("/api/logs", "GET"), "poll")
        self.assertEqual(security_hard.rate_bucket("/api/status", "GET"), "poll")
        self.assertEqual(security_hard.rate_bucket("/api/deterioration", "GET"), "poll")
        for path in ("/api/process", "/api/upload", "/api/synthetic", "/api/file"):
            self.assertEqual(security_hard.rate_bucket(path, "POST" if path != "/api/file" else "GET"), "mutate")

    def test_budgets_are_separate_queues(self):
        import security_hard

        key = "unit-test-ip"
        for suffix in ("mutate", "poll"):
            security_hard._rl_hits.pop(f"{key}|{suffix}", None)
        # spend the poll budget completely
        self.assertTrue(security_hard.rate_limit_ok(key, max_per_min=2, bucket="poll"))
        self.assertTrue(security_hard.rate_limit_ok(key, max_per_min=2, bucket="poll"))
        self.assertFalse(security_hard.rate_limit_ok(key, max_per_min=2, bucket="poll"))
        # ...and the work budget is still intact, which is the whole point
        self.assertTrue(security_hard.rate_limit_ok(key, max_per_min=2, bucket="mutate"))
        self.assertTrue(security_hard.rate_limit_ok(key, max_per_min=2, bucket="mutate"))
        self.assertFalse(security_hard.rate_limit_ok(key, max_per_min=2, bucket="mutate"))

    def test_ui_polls_one_endpoint_not_three(self):
        self.assertIn("/api/status", JS)
        self.assertNotIn(
            "setInterval(poll, 600)",
            JS,
            "fixed 600ms triple polling is what starved the budget",
        )
        self.assertIn("document.hidden", JS, "polling must stop while the tab is backgrounded")
        self.assertRegex(JS, r"pollMs = Math\.min\(", "polling needs a backoff when the server errors")

    def test_status_endpoint_exists(self):
        server = (APP / "server.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/api/status")', server)
        self.assertIn("rate_bucket", server)


class TestMotionCss(unittest.TestCase):
    def test_slide_tokens_and_gesture_rules_exist(self):
        for token in ("--ease-slide", "--dur-slide", "--drawer-dx"):
            self.assertIn(token, CSS, f"missing motion token {token}")
        self.assertRegex(CSS, r"\.controls\.open,\s*\.controls\.dragging")
        self.assertRegex(CSS, r"\.controls\.dragging\s*\{[^}]*transition:\s*none")
        self.assertRegex(CSS, r"\.tab-ink\s*\{[^}]*transition:")
        self.assertRegex(CSS, r"input\[type=\"range\"\]|\.slider::-webkit-slider-thumb")
        self.assertRegex(CSS, r"\.slider::-moz-range-thumb")

    def test_reduced_motion_covers_the_new_motion(self):
        blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)(?:\n\})", CSS, re.S)
        self.assertTrue(blocks, "no reduced-motion block in style.css")
        body = "\n".join(blocks)
        for sel in (".controls", ".drawer-backdrop", ".tab-ink", ".tabpane.active", ".tabs"):
            self.assertIn(sel, body, f"{sel} still animates under prefers-reduced-motion")



class TestZoomPanAndDrop(unittest.TestCase):
    """Round-2 UI contract: preview zoom/pan and the whole-window file drop."""

    def test_every_zoom_hook_exists(self):
        for fn in ("btnZoomIn", "btnZoomOut", "zoomPct", "zoomHint", "previewWrap"):
            self.assertIn(f'$("{fn}")', JS, f"app.js never touches #{fn}")
            self.assertIn(f'id="{fn}"', HTML, f"#{fn} referenced by JS but not in markup")
        # the styling lives on the shared classes; a renamed class strands the
        # control unstyled, which is the failure mode we keep hitting
        for sel in (".zoom-ctl button", ".zoom-hint", ".zoom-readout",
                    "#previewImg.zoomed", ".preview-wrap.panning"):
            self.assertIn(sel, CSS, f"missing CSS hook {sel}")

    def test_zoomed_image_is_sized_not_transformed(self):
        # transform:scale would not grow the scroll box, so panning silently
        # stops at the edge; the width has to actually change.
        self.assertIn("width: calc(100% * var(--zoom, 1))", CSS)
        self.assertIn("flex: 0 0 auto", CSS)
        self.assertIn('img.style.setProperty("--zoom"', JS)
        self.assertNotIn("scale(zoom", JS.replace("scaleZoom", ""))

    def test_scroll_box_keeps_the_start_edge_reachable(self):
        block = CSS[CSS.index(".preview-wrap {"):]
        block = block[: block.index("}") + 1]
        self.assertIn("overflow: auto", block)
        self.assertIn("align-items: safe center", block)
        self.assertIn("justify-content: safe center", block)

    def test_swipe_only_yields_to_pan_while_zoomed(self):
        # the wrap opts out of tab-swipe dynamically; a static attribute would
        # kill swiping over the image at 100% where there is nothing to pan
        self.assertIn('wrap.toggleAttribute("data-no-swipe", zoomActive())', JS)
        wrap = HTML[HTML.index('id="previewWrap"') - 200: HTML.index('id="previewWrap"') + 200]
        self.assertNotIn("data-no-swipe", wrap)

    def test_wheel_does_not_hijack_the_page_scroll_at_fit(self):
        # the preview lives inside a scrolling column: taking the plain wheel there is
        # how an image viewer becomes unusable, so zooming needs a modifier until the
        # image is actually enlarged
        start = JS.index('$("previewWrap")?.addEventListener("wheel"')
        handler = JS[start: JS.index("}, { passive: false });", start)]
        guard = "if (!(e.ctrlKey || e.metaKey) && !zoomActive()) return;"
        self.assertIn(guard, handler, "plain wheel must not zoom a fitted preview")
        self.assertIn('"wheel"', handler)
        self.assertLess(handler.index(guard), handler.index("e.preventDefault()"),
                        "the guard has to run before the wheel is claimed")
        # preventDefault on a wheel event only works if the listener is non-passive
        self.assertLess(JS.index("passive: false", start) - start, len(handler) + 40,
                        "the wheel listener must be registered { passive: false }")

    def test_zoom_resets_when_a_new_image_loads(self):
        sp = JS[JS.index("function showPreview("):]
        sp = sp[: sp.index("\n  }") + 3]
        self.assertIn("if (zoom !== 1) setZoom(1);", sp)
        self.assertLess(sp.index("img.dataset.src = full"), sp.index("setZoom(1)"))

    def test_hint_repaints_when_the_image_decodes(self):
        # naturalWidth is 0 until the bytes arrive, so the size readout needs the
        # load event — plus `complete` for a cached image that is already there
        sp = JS[JS.index("function showPreview("):]
        sp = sp[: sp.index("\n  }")]
        self.assertIn("img.onload = () => paintZoom();", sp)
        self.assertIn("if (img.complete) paintZoom();", sp)
        self.assertIn("img.naturalWidth", JS[JS.index("function paintZoom()"):JS.index("function setZoom(")])

    def test_window_drop_overlay_is_class_driven(self):
        self.assertIn('id="dropOverlay"', HTML)
        self.assertNotIn('id="dropOverlay" hidden', HTML)
        ov = CSS[CSS.index(".drop-overlay {"):]
        ov = ov[: ov.index("}") + 1]
        for prop in ("opacity: 0", "visibility: hidden", "pointer-events: none"):
            self.assertIn(prop, ov)
        self.assertIn(".drop-overlay.show {", CSS)
        self.assertIn('ov.classList.toggle("show", !!on)', JS)

    def test_window_drop_hands_off_to_zones_and_uses_the_real_uploader(self):
        handler = JS[JS.index('window.addEventListener("drop", (e) => {'):]
        handler = handler[: handler.index("\n  });") + 5]
        self.assertIn('closest(".drop")', handler)
        self.assertIn("if (inZone) return;", handler)      # else one drop uploads twice
        self.assertIn("uploadFile(f)", handler)
        self.assertIn('e.preventDefault()', handler)

    def test_file_drag_guarded_by_types(self):
        # must not hijack text drags or link drags inside the console
        self.assertIn('Array.prototype.includes.call(types, "Files")', JS)

    def test_running_pill_reports_kind_and_elapsed(self):
        aj = JS[JS.index("function applyJobState(j) {"):][: 1600]
        self.assertIn("runT0 = Date.now()", aj)
        self.assertIn('setStatus("run", `${kind} · ${clock}`)', aj)
        self.assertIn('runKey = "";', aj)                    # cleared once idle



class TestOneClickNight(unittest.TestCase):
    """"One click for everything": the CTA, the panels it chains afterwards, the
    Sharpen Lab that had an endpoint but no UI, and tab findability."""

    def test_factory_button_is_a_single_press(self):
        self.assertIn("Run everything", HTML)
        self.assertIn('id="btnFactory"', HTML)
        handler = JS[JS.index('$("btnFactory").addEventListener'):]
        handler = handler[: handler.index("\n  });") + 5]
        self.assertNotIn("confirm(", handler, "one click must not raise a dialog")
        self.assertIn("pendingEverything = true", handler)
        self.assertIn('startJob("/api/factory_night"', handler)
        self.assertIn("factoryHard", handler)
        self.assertIn('id="factoryHard" checked', HTML, "stress suite is part of 'everything'")

    def test_tail_fills_the_panels_the_night_cannot(self):
        tail = JS[JS.index("async function runEverythingTail()"):]
        tail = tail[: tail.index("\n  }\n\n  function CONSOLE_LINE")]
        for step in ("runTransits", "runSessionPlan", "runSharpen", '$("btnDetRun")'):
            self.assertIn(step, tail, f"one-click tail never runs {step}")
        self.assertIn('if (filePath) await step("sharpen"', tail)
        # fired from the result branch, and only for the night itself
        aj = JS[JS.index("function applyJobState(j) {"):][: 2200]
        self.assertIn('pendingEverything && j.result.kind === "factory_night"', aj)
        self.assertIn("pendingEverything = false;", aj)
        self.assertIn("everythingRan = 0;", JS[JS.index("async function startJob("):][: 400],
                      "a second press must be allowed to run the tail again")

    def test_optional_steps_are_folded_away(self):
        start = HTML.index('<details class="steps">')
        end = HTML.index("</details>", start)
        inside = HTML[start:end]
        for btn in ("btnMulti", "btnHard"):
            self.assertIn('id="%s"' % btn, inside, f"{btn} should live inside the disclosure")
        self.assertIn("<summary", inside)
        # …and the buttons the one click drives stay reachable on their own
        for btn in ("btnProcess", "btnSynth", "btnFactory"):
            self.assertNotIn('id="%s"' % btn, inside, f"{btn} must stay outside the disclosure")

    def test_sharpen_lab_is_wired_to_its_endpoint(self):
        for fn in ("sharpMethod", "sharpAmount", "btnSharpen", "sharpOut"):
            self.assertIn('id="%s"' % fn, HTML, f"#{fn} missing from markup")
            self.assertIn('$("%s")' % fn, JS, f"app.js never touches #{fn}")
        self.assertIn('id="sharpLab"', HTML)
        self.assertIn(".sharp-lab {", CSS, "Sharpen Lab row has no styling")
        self.assertIn("showPreview(j.preview", JS, "sharpened result never reaches the preview")
        call = JS[JS.index("async function runSharpen()"):][: 1400]
        self.assertIn('fetch("/api/sharpen"', call)
        self.assertIn("path: filePath", call)
        self.assertIn("method, amount", call)
        self.assertIn('$("btnSharpen").disabled = false', JS)   # only after a real file
        self.assertIn('id="btnSharpen" class="btn ghost" disabled', HTML)

    def test_sharpen_payload_matches_the_server_contract(self):
        route = SERVER[SERVER.index("def api_sharpen()"):]
        route = route[: route.index("@app.route", 10)]
        for key in ('data.get("path")', 'data.get("method")', 'data.get("amount")'):
            self.assertIn(key, route)
        allowed = set(re.findall(r'"(wavelet|unsharp|rl)"', route))
        seg = HTML[HTML.index('id="sharpMethod"'):HTML.index("sharpAmount")]
        in_ui = set(re.findall(r'<option value="(\w+)"', seg))
        self.assertTrue(in_ui, "no sharpen methods in the UI")
        self.assertTrue(in_ui <= allowed, f"UI offers methods the server rejects: {in_ui - allowed}")

    def test_asset_version_follows_the_assets(self):
        """A UI round that never bumps VERSION must still bust the browser cache,
        or the fix the user is waiting for stays invisible to them. Executed from
        source rather than imported, so the check costs no heavy imports."""
        src = (APP / "server.py").read_text(encoding="utf-8")
        start = src.index("def _ui_version(")
        body = src[start: src.index('@app.route("/")', start)]
        self.assertIn('("app.js", "style.css")', body)
        ns: dict = {}
        exec(f"from pathlib import Path\n{body}", ns)
        ui_version = ns["_ui_version"]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app.js").write_text("1", encoding="utf-8")
            (root / "style.css").write_text("1", encoding="utf-8")
            v1 = ui_version("7.0", root)
            self.assertTrue(v1.startswith("7.0."), f"token lost the asset stamp: {v1}")
            os.utime(root / "app.js", (3_000_000_000, 3_000_000_000))
            self.assertNotEqual(v1, ui_version("7.0", root), "touching app.js kept the old token")
            self.assertEqual(ui_version("7.0", root / "absent"), "7.0")   # odd install still renders
        self.assertIn("app.js?v={{ ui_v or 'dev' }}", HTML)
        self.assertIn("style.css?v={{ ui_v or 'dev' }}", HTML)

    def test_deterioration_tab_is_in_the_first_screenful(self):
        tabs = re.findall(r'class="tab[^"]*" id="tabbtn-([a-z-]+)"', HTML)
        self.assertEqual(len(tabs), 11, f"expected 11 tabs, got {tabs}")
        self.assertLess(tabs.index("deterioration"), 5,
                        f"Deterioration Lab is at position {tabs.index('deterioration') + 1} of {len(tabs)}")
        self.assertEqual(tabs[0], "preview")

    def test_number_keys_jump_to_a_tab(self):
        start = JS.index("1…9 and 0 jump")
        h = JS[start: JS.index("});", start) + 3]
        self.assertIn("/^[1-9]$/.test(k)", h)
        self.assertIn('TAB_ORDER[k === "0" ? 9 : parseInt(k, 10) - 1]', h)
        self.assertIn("showTab(name, true)", h)
        for keep in ("input", "select", "textarea", "[contenteditable]", "[role='log']"):
            self.assertIn(keep, h, f"the shortcut must not eat typing in {keep}")
        self.assertIn("e.metaKey || e.ctrlKey || e.altKey", h)
        self.assertIn("press 1-9 or 0", HTML, "the shortcut is invisible without a hint")

class TestBackendIsReachable(unittest.TestCase):
    """"Everything in my code is in this UI", pinned: every Flask route must be
    mentioned by app.js — either fetched directly or handed to startJob — except
    the two the server uses to *build* URLs it already sends to the browser, plus
    one orphan kept as an API surface. A new route with no UI shows up here."""

    # server emits these itself (inside JSON payloads / <img src>) → reachable
    # through a result, and /api/output/* is an external API with no caller.
    SERVER_BUILT = ("/api/file", "/api/output")   # compared after "<" is stripped

    def test_every_route_is_reachable_from_the_page(self):
        routes = sorted(set(re.findall(r'@app\.route\(\s*"([^"]+)"', SERVER)))
        self.assertGreaterEqual(len(routes), 34, "route scan found too few routes — regex drift?")
        orphans = []
        for r in routes:
            if r in ("/", "/favicon.ico", "/static/<path:filename>"):
                continue
            lit = r.split("<")[0].rstrip("/")
            if lit in self.SERVER_BUILT:
                continue
            if lit not in JS:
                orphans.append(r)
        self.assertFalse(orphans, f"routes no page control can reach: {orphans}")

    def test_resolution_table_is_used_by_the_picker(self):
        self.assertIn('fetch("/api/resolutions")', JS)
        self.assertIn('id="resHint"', HTML)
        self.assertIn("$(\"resolution\")?.addEventListener(\"change\", paintResHint)", JS)
        # …and every option the picker offers is a preset the pipeline accepts
        seg = HTML[HTML.index('id="resolution"'): HTML.index("</select>", HTML.index('id="resolution"'))]
        opts = set(re.findall(r'<option value="([\w]+)"', seg))
        self.assertEqual(opts, {"auto", "1080p", "4K", "8K", "16K"})
        for o in opts - {"auto"}:
            self.assertIn(f'"{o}"', SERVER, f"preset {o} is not accepted by the server")


if __name__ == "__main__":
    unittest.main()
