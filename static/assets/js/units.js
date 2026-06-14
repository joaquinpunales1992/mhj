/*
 * Site-wide units & currency preference.
 *
 * Prices render server-side in USD with a data-yen="<yen>" attribute; areas
 * render in m² with data-m2="<m2>" (and data-orig="<original text>"). This
 * script reformats every tagged element to the visitor's chosen currency /
 * area unit, remembered in localStorage. No JS → the server-rendered USD / m²
 * values stand.
 *
 * Exchange rates are JPY -> currency. They're approximate and intentionally
 * kept here as the single source of truth so they're trivial to update (or
 * later replace with a daily fetch).
 */
(function () {
  "use strict";

  // JPY -> currency. USD matches the server-side rate (inventory/utils.py) so
  // the price doesn't shift when JS upgrades the server-rendered USD value.
  var RATES = { USD: 0.007, EUR: 0.0062, AUD: 0.0101, JPY: 1 };
  var SYMBOL = { USD: "$", EUR: "€", AUD: "A$", JPY: "¥" };
  var M2_TO_FT2 = 10.7639;

  var CUR_KEY = "mhj_currency";
  var UNIT_KEY = "mhj_unit";

  function getCurrency() {
    var c = localStorage.getItem(CUR_KEY);
    return RATES.hasOwnProperty(c) ? c : "USD";
  }
  function getUnit() {
    var u = localStorage.getItem(UNIT_KEY);
    // Default to ft² unless the visitor has explicitly chosen m².
    return u === "m2" ? "m2" : "ft2";
  }

  function fmtInt(n) {
    return Math.round(n).toLocaleString("en-US");
  }

  function formatMoney(yen, cur) {
    return SYMBOL[cur] + fmtInt(yen * RATES[cur]);
  }

  function formatArea(m2, unit) {
    if (unit === "ft2") return fmtInt(m2 * M2_TO_FT2) + " ft²";
    // Trim a trailing .0 but keep real decimals (e.g. 103.24).
    var v = Math.round(m2 * 100) / 100;
    return v.toLocaleString("en-US") + " m²";
  }

  // "per m²" price (yen) -> chosen currency + chosen unit, e.g. "$1,644/m²".
  function formatPerArea(yenPerM2, cur, unit) {
    var perUnitYen = unit === "ft2" ? yenPerM2 / M2_TO_FT2 : yenPerM2;
    var label = unit === "ft2" ? "/ft²" : "/m²";
    return SYMBOL[cur] + fmtInt(perUnitYen * RATES[cur]) + label;
  }

  function apply() {
    var cur = getCurrency();
    var unit = getUnit();

    var i, el;

    var prices = document.querySelectorAll("[data-yen]");
    for (i = 0; i < prices.length; i++) {
      el = prices[i];
      el.textContent = formatMoney(parseFloat(el.getAttribute("data-yen")), cur);
    }

    var areas = document.querySelectorAll("[data-m2]");
    for (i = 0; i < areas.length; i++) {
      el = areas[i];
      var m2 = parseFloat(el.getAttribute("data-m2"));
      if (unit === "m2" && el.getAttribute("data-orig")) {
        el.textContent = el.getAttribute("data-orig"); // keep "(public book)" etc.
      } else {
        el.textContent = formatArea(m2, unit);
      }
    }

    var perAreas = document.querySelectorAll("[data-yen-per-m2]");
    for (i = 0; i < perAreas.length; i++) {
      el = perAreas[i];
      el.textContent = formatPerArea(
        parseFloat(el.getAttribute("data-yen-per-m2")),
        cur,
        unit
      );
    }

    // Static "m²" labels in headings/copy follow the chosen unit.
    var unitLabel = unit === "ft2" ? "ft²" : "m²";
    var labels = document.querySelectorAll(".js-unit-label");
    for (i = 0; i < labels.length; i++) labels[i].textContent = unitLabel;

    // Reflect the current choice on any selectors on the page.
    var curSel = document.querySelectorAll("[data-mhj-currency]");
    for (i = 0; i < curSel.length; i++) curSel[i].value = cur;
    var unitSel = document.querySelectorAll("[data-mhj-unit]");
    for (i = 0; i < unitSel.length; i++) unitSel[i].value = unit;
  }

  function wire() {
    document.addEventListener("change", function (e) {
      var t = e.target;
      if (t && t.hasAttribute && t.hasAttribute("data-mhj-currency")) {
        localStorage.setItem(CUR_KEY, t.value);
        apply();
      } else if (t && t.hasAttribute && t.hasAttribute("data-mhj-unit")) {
        localStorage.setItem(UNIT_KEY, t.value);
        apply();
      }
    });
    apply();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
