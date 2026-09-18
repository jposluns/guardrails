// Dark/light theme toggle. The initial theme is set by a tiny inline script in each
// page's <head> (so there is no flash); this file provides the toggle and the label.
function toggleTheme(){
  var r=document.documentElement;
  var next=(r.getAttribute('data-theme')==='dark')?'light':'dark';
  r.setAttribute('data-theme',next);
  try{localStorage.setItem('aiqt-theme',next);}catch(e){}
  setThemeLabel(next);
}
function setThemeLabel(t){
  var b=document.getElementById('themebtn');
  if(!b)return;
  b.textContent=(t==='dark')?'☀︎ Light':'☽︎ Dark';
  b.setAttribute('aria-label','Switch to '+((t==='dark')?'light':'dark')+' theme');
}
document.addEventListener('DOMContentLoaded',function(){
  setThemeLabel(document.documentElement.getAttribute('data-theme')||'dark');
});

function toggleNav(){
  var sb=document.getElementById('sidebar');
  var bd=document.getElementById('navbackdrop');
  var btn=document.querySelector('.navtoggle');
  if(!sb)return;
  var open=sb.classList.toggle('open');
  if(bd) bd.classList.toggle('open', open);
  if(btn) btn.setAttribute('aria-expanded', open?'true':'false');
}

// ---- reference-hub tabs: progressive enhancement, inert without a [data-reference-hub] strip ----
document.addEventListener('DOMContentLoaded', function () {
  var hubs = document.querySelectorAll('[data-reference-hub]');
  if (!hubs.length) return;
  document.documentElement.setAttribute('data-tabs-js', '');   // enables the panel-hiding CSS
  hubs.forEach(function (strip) {
    var tabs = [].slice.call(strip.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute('aria-controls')); });
    // Fail safe: malformed markup keeps plain-anchor mode (no hiding).
    if (!tabs.length || panels.some(function (p) { return !p; })) {
      document.documentElement.removeAttribute('data-tabs-js');
      return;
    }
    var allBtn = strip.parentNode.querySelector('[data-show-all]');
    var selected = 0, showAll = false;

    function draw() {
      tabs.forEach(function (t, i) {
        var on = i === selected;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        panels[i].hidden = showAll ? false : !on;
      });
      if (allBtn) {
        allBtn.setAttribute('aria-pressed', String(showAll));
        allBtn.textContent = showAll ? (allBtn.dataset.showoneLabel || 'Return to one file at a time') : (allBtn.dataset.showallLabel || 'Show all files for reading and search');
      }
    }
    function indexForHash() {                              // matches a panel id OR an id inside a panel
      var raw = location.hash.slice(1);
      if (!raw) return -1;
      var ids = [raw];
      try { ids.push(decodeURIComponent(raw)); } catch (e) {}
      var el = null;
      ids.some(function (id) { el = document.getElementById(id); return !!el; });
      if (!el) return -1;
      for (var i = 0; i < panels.length; i++) { if (panels[i] === el || panels[i].contains(el)) return i; }
      return -1;
    }
    function select(i, focus, writeHash) {
      selected = i; showAll = false; draw();
      if (focus) tabs[i].focus();
      if (writeHash && location.hash !== '#' + tabs[i].getAttribute('aria-controls')) {
        try { history.replaceState(null, '', '#' + tabs[i].getAttribute('aria-controls')); }
        catch (e) { location.hash = tabs[i].getAttribute('aria-controls'); }
      }
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;   // let modified clicks open normally
        e.preventDefault(); select(i, false, true);
      });
      tab.addEventListener('keydown', function (e) {
        var n = tabs.length, to = -1;
        if (e.key === 'ArrowRight') to = (i + 1) % n;
        else if (e.key === 'ArrowLeft') to = (i - 1 + n) % n;
        else if (e.key === 'Home') to = 0;
        else if (e.key === 'End') to = n - 1;
        else return;
        e.preventDefault(); select(to, true, true);
      });
    });
    if (allBtn) { allBtn.hidden = false;
      allBtn.addEventListener('click', function () { showAll = !showAll; draw(); }); }
    var start = indexForHash(); selected = start > -1 ? start : 0; draw();
    window.addEventListener('hashchange', function () { var k = indexForHash(); if (k > -1) select(k, false, false); });
  });
});
