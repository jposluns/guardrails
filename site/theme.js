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
