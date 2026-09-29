// Accept-updated-terms page: enable the button only once the box is checked.
(function () {
  const box = document.getElementById('at-agree');
  const btn = document.getElementById('at-submit');
  if (!box || !btn) return;
  const sync = () => { btn.disabled = !box.checked; };
  box.addEventListener('change', sync);
  sync();
})();
