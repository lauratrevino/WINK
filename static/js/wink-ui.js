// Shared UI helpers used across multiple pages. Previously each page kept
// its own copy of these pasted inline — escapeHtml alone existed as five
// slightly different versions across nine templates. Consolidated here so
// there's exactly one implementation to read, fix, or extend. Any page
// using these must load this file (and, for winkToast/winkConfirm, must
// already style .wink-toast / .wink-modal-overlay in its own CSS — this
// file only supplies behavior, not appearance).

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function winkToast(msg, isError = false) {
  let t = document.getElementById('wink-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'wink-toast';
    t.className = 'wink-toast';
    t.setAttribute('role', 'status');
    t.setAttribute('aria-live', 'polite');
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = 'wink-toast show' + (isError ? ' error' : '');
  clearTimeout(t._hideTimer);
  t._hideTimer = setTimeout(() => t.classList.remove('show'), 3200);
}

function winkConfirm({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
  return new Promise(resolve => {
    let overlay = document.getElementById('wink-modal-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'wink-modal-overlay';
      overlay.className = 'wink-modal-overlay';
      overlay.innerHTML = `<div class="wink-modal-card" role="dialog" aria-modal="true" aria-labelledby="wink-modal-title">
        <h3 id="wink-modal-title"></h3>
        <p id="wink-modal-message"></p>
        <div class="wink-modal-actions">
          <button class="wink-modal-btn secondary" id="wink-modal-cancel"></button>
          <button class="wink-modal-btn primary" id="wink-modal-confirm"></button>
        </div>
      </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
    }
    document.getElementById('wink-modal-title').textContent = title;
    document.getElementById('wink-modal-message').textContent = message;
    const confirmBtn = document.getElementById('wink-modal-confirm');
    const cancelBtn = document.getElementById('wink-modal-cancel');
    confirmBtn.textContent = confirmLabel;
    cancelBtn.textContent = cancelLabel;
    confirmBtn.className = 'wink-modal-btn ' + (danger ? 'danger' : 'primary');
    overlay.classList.add('open');
    function finish(result) {
      overlay.classList.remove('open');
      confirmBtn.onclick = null; cancelBtn.onclick = null;
      resolve(result);
    }
    confirmBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
  });
}

function filterUniversityOptions(term, selectId) {
  const select = document.getElementById(selectId);
  const q = term.trim().toLowerCase();
  Array.from(select.options).forEach(opt => {
    if (!opt.value) return;
    opt.hidden = !opt.text.toLowerCase().includes(q);
  });
}
