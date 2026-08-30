/* Minimal vanilla JS - no frameworks, kooora style.
   Safari-safe: accordions and tabs use plain buttons (no <details>).
   1. Convert kickoff times (stored UTC) to the visitor's local timezone.
   2. Date picker navigation.
   3. Accordion toggles (competition groups, bench lists).
   4. Match detail tabs (Events / Lineups / Stats).
*/
(function () {
  "use strict";

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function each(list, fn) { return Array.prototype.forEach.call(list, fn); }

  // --- Local time conversion -------------------------------------------
  each(document.querySelectorAll(".tt[data-utc]"), function (el) {
    var iso = el.getAttribute("data-utc");
    if (!iso) return;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return;
    el.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (el.hasAttribute("data-full")) el.title = d.toLocaleString();
  });

  // --- Date picker ------------------------------------------------------
  var dp = document.getElementById("datepick");
  if (dp) {
    dp.addEventListener("change", function () {
      if (!dp.value) return;
      var base = dp.getAttribute("data-base") || "/day/";
      var qs = dp.getAttribute("data-qs") || "";
      window.location.href = base + dp.value + qs;
    });
  }

  // --- Accordions (Safari-safe <details> replacement) --------------------
  each(document.querySelectorAll("[data-acc-btn]"), function (btn) {
    btn.addEventListener("click", function () {
      var host = btn.closest("[data-acc]");
      if (!host) return;
      var isOpen = host.classList.toggle("open");
      btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  });

  // --- Match detail tabs --------------------------------------------------
  var tabs = document.querySelectorAll(".tab[data-tab]");
  if (tabs.length) {
    each(tabs, function (tab) {
      tab.addEventListener("click", function () {
        each(tabs, function (t) {
          t.classList.remove("active");
          t.setAttribute("aria-selected", "false");
        });
        tab.classList.add("active");
        tab.setAttribute("aria-selected", "true");
        each(document.querySelectorAll(".tab-panel"), function (p) {
          p.classList.remove("active");
        });
        var panel = document.getElementById("panel-" + tab.getAttribute("data-tab"));
        if (panel) panel.classList.add("active");
      });
    });
  }

  // --- Crest fallback safety net (covers cached broken images) -----------
  each(document.querySelectorAll("img.crest, img.crest-s, img.sb-crest-img"), function (img) {
    if (img.complete && img.naturalWidth === 0 && !img.classList.contains("is-fallback")) {
      img.onerror = null;
      img.src = "/static/crest.svg";
      img.classList.add("is-fallback");
    }
  });
})();
