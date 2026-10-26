// CMDM site: term search, IRI copy, active section in the sidebar.
(function () {
  "use strict";

  // --- search -------------------------------------------------------
  var input = document.getElementById("q");
  var count = document.querySelector(".search-count");
  var terms = Array.prototype.slice.call(document.querySelectorAll(".term[data-search]"));
  var groups = Array.prototype.slice.call(document.querySelectorAll(".kind-group, .module"));

  function applyFilter() {
    var q = (input.value || "").trim().toLowerCase();
    var shown = 0;
    terms.forEach(function (el) {
      var hit = !q || el.getAttribute("data-search").indexOf(q) !== -1 || el.id.toLowerCase().indexOf(q) !== -1;
      el.classList.toggle("is-hidden", !hit);
      if (hit) shown++;
    });
    groups.forEach(function (g) {
      g.classList.toggle("is-hidden", !!q && !g.querySelector(".term:not(.is-hidden)"));
    });
    if (count) count.textContent = q ? shown + "건 일치" : "";
    try {
      var url = new URL(window.location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      history.replaceState(null, "", url);
    } catch (e) { /* ignore */ }
  }

  if (input) {
    try {
      var initial = new URL(window.location.href).searchParams.get("q");
      if (initial) input.value = initial;
    } catch (e) { /* ignore */ }
    input.addEventListener("input", applyFilter);
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { input.value = ""; applyFilter(); }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "/" && document.activeElement !== input) { ev.preventDefault(); input.focus(); }
    });
    if (input.value) applyFilter();
  }

  // Clear the filter when following an in-page link to a hidden term.
  window.addEventListener("hashchange", function () {
    var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && target.closest(".is-hidden") && input) {
      input.value = ""; applyFilter(); target.scrollIntoView();
    }
  });

  // --- copy IRI -----------------------------------------------------
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".copy");
    if (!btn) return;
    var text = btn.getAttribute("data-copy");
    var done = function () {
      var old = btn.textContent;
      btn.textContent = "복사됨"; btn.classList.add("done");
      setTimeout(function () { btn.textContent = old; btn.classList.remove("done"); }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { window.prompt("IRI", text); });
    } else {
      window.prompt("IRI", text);
    }
  });

  // --- active section in sidebar -----------------------------------
  var links = Array.prototype.slice.call(document.querySelectorAll(".toc a[href^='#']"));
  if (links.length && "IntersectionObserver" in window) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting && byId[en.target.id]) {
          links.forEach(function (a) { a.classList.remove("is-active"); });
          byId[en.target.id].classList.add("is-active");
        }
      });
    }, { rootMargin: "-80px 0px -70% 0px" });
    Object.keys(byId).forEach(function (id) {
      var sec = document.getElementById(id);
      if (sec) obs.observe(sec);
    });
  }
})();
