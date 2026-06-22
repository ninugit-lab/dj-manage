export function livePoll(selector, url, ms) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.setAttribute('aria-live', 'polite');
  el.setAttribute('aria-atomic', 'false');
  const tick = () => fetch(url).then(r => r.text()).then(html => { el.innerHTML = html; });
  setInterval(tick, ms);
}
