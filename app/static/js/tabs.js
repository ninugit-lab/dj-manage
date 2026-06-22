document.querySelectorAll('[role="tablist"]').forEach(list => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const sel = (i, moveFocus = true) => tabs.forEach((t, j) => {
    const on = i === j;
    t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1;
    const panel = document.getElementById(t.getAttribute('aria-controls'));
    if (panel) panel.hidden = !on;
    if (on && moveFocus) t.focus();
  });
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => sel(i));
    t.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight') { e.preventDefault(); sel((i + 1) % tabs.length); }
      if (e.key === 'ArrowLeft')  { e.preventDefault(); sel((i - 1 + tabs.length) % tabs.length); }
    });
  });
  sel(0, false);
});
