// Health page > Blocked Email Addresses (admin only). Lists addresses on
// the SES suppression list and lets the admin remove one.
(function () {
  const body = document.getElementById('sup-body');
  if (!body) return;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasonLabel = r => (r || '').toLowerCase().includes('complaint') ? 'Marked as spam'
                        : (r || '').toLowerCase().includes('bounce') ? 'Bounced' : (r || 'Blocked');
  async function load() {
    try {
      const res = await fetch('/email-suppressions');
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Could not load the list.');
      const rows = data.suppressions || [];
      if (!rows.length) { body.innerHTML = '<div class="sup-empty">No blocked addresses. Every student can receive email. ✅</div>'; return; }
      body.innerHTML = rows.map(r => `
        <div class="sup-row">
          <span class="sup-email">${esc(r.email)}</span>
          <span class="sup-reason">${esc(reasonLabel(r.reason))}${r.created_at ? ' · ' + esc(new Date(r.created_at + 'Z').toLocaleDateString()) : ''}</span>
          <button type="button" class="sup-btn" data-email="${esc(r.email)}">Remove</button>
        </div>`).join('') + '<div class="sup-msg" id="sup-msg"></div>';
      body.querySelectorAll('.sup-btn').forEach(btn => btn.addEventListener('click', () => remove(btn)));
    } catch (e) {
      body.innerHTML = `<div class="sup-empty">${esc(e.message)}</div>`;
    }
  }
  async function remove(btn) {
    const email = btn.dataset.email;
    if (!confirm(`Start sending email to ${email} again?`)) return;
    btn.disabled = true;
    try {
      const res = await fetch('/email-suppressions/remove', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) throw new Error(data.error || 'Could not remove that address.');
      await load();
    } catch (e) {
      btn.disabled = false;
      const msg = document.getElementById('sup-msg');
      if (msg) msg.textContent = e.message;
    }
  }
  load();
})();
