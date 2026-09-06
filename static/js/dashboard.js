    const WINK_COURSE_COLORS = window.WINK_DASHBOARD_DATA.courseColors;

    function busyStretchColor(course) {
      return (WINK_COURSE_COLORS && WINK_COURSE_COLORS[course]) || '#94A3B8';
    }

    document.querySelectorAll('.doc-item[data-course-color]').forEach(item => {
      const color = item.getAttribute('data-course-color');
      item.style.borderLeftColor = color;
      const icon = item.querySelector('.doc-icon');
      if (icon) { icon.style.background = `${color}26`; icon.style.color = color; }
    });

    document.querySelectorAll('#due-week-list .deadline-item[data-course-color]').forEach(item => {
      const color = item.getAttribute('data-course-color');
      item.style.background = `${color}14`;
      item.style.borderColor = `${color}55`;
      item.style.borderLeft = `4px solid ${color}`;
      const dateEl = item.querySelector('.deadline-date');
      if (dateEl) dateEl.style.background = color;
    });


    function toggleProfileEdit(editing) {
      document.querySelectorAll('.profile-edit').forEach(el => el.style.display = editing ? 'flex' : 'none');
      document.getElementById('profile-view-name').style.display = editing ? 'none' : 'block';
      document.getElementById('profile-view-classification').style.display = editing ? 'none' : 'block';
      document.getElementById('profile-view-major').style.display = editing ? 'none' : 'block';
      document.getElementById('profile-view-university').style.display = editing ? 'none' : 'block';
      document.getElementById('profile-edit-actions').style.display = editing ? 'flex' : 'none';
      document.getElementById('profile-edit-btn').style.display = editing ? 'none' : 'inline-block';
      if (!editing) {
        // Reset inputs back to the last-saved values shown in the view mode
        document.getElementById('profile-input-first').value = document.getElementById('profile-view-name').textContent.split(' ')[0] || '';
        document.getElementById('profile-input-last').value = document.getElementById('profile-view-name').textContent.split(' ').slice(1).join(' ') || '';
        document.getElementById('profile-input-university').value = document.getElementById('profile-view-university').textContent.trim();
        document.getElementById('profile-input-classification').value = document.getElementById('profile-view-classification').textContent.trim();
        document.getElementById('profile-input-major').value = document.getElementById('profile-view-major').textContent.trim();
      } else {
        // Clear any leftover search filter so every option is visible again
        const univSelect = document.getElementById('profile-input-university');
        Array.from(univSelect.options).forEach(opt => opt.hidden = false);
      }
    }

    async function resendVerification(btn) {
      const original = btn.textContent;
      btn.textContent = 'Sending…'; btn.disabled = true;
      try {
        const res = await fetch('/resend-verification', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          winkToast(data.already_verified ? 'Your email is already verified.' : 'Verification email sent — check your inbox.');
          btn.textContent = 'Sent ✓';
        } else {
          winkToast(data.error || 'Could not send — please try again.', true);
          btn.textContent = original; btn.disabled = false;
        }
      } catch (e) {
        winkToast('Could not send — please try again.', true);
        btn.textContent = original; btn.disabled = false;
      }
    }

    // A student typically clicks the verification link from an email
    // client in a NEW tab, leaving this dashboard tab open and unaware
    // anything changed. Re-checking status when this tab regains focus
    // (switching back after clicking the link) catches that automatically,
    // so nobody needs to know to hit refresh.
    (function watchVerificationStatus() {
      const statusEl = document.getElementById('email-verify-status');
      if (!statusEl || statusEl.querySelector('.badge')?.textContent.includes('Verified')) return;
      async function checkStatus() {
        try {
          const res = await fetch('/verification-status');
          const data = await res.json();
          if (data.email_verified) {
            statusEl.innerHTML = '<span class="badge" style="background:#dcfce7;color:#166534;margin-left:8px;">✓ Verified</span>';
            winkToast('Your email is now verified!');
            document.removeEventListener('visibilitychange', onVisible);
            window.removeEventListener('focus', onVisible);
          }
        } catch (e) { /* silent — just try again on the next focus/visibility event */ }
      }
      function onVisible() {
        if (document.visibilityState === 'visible') checkStatus();
      }
      document.addEventListener('visibilitychange', onVisible);
      window.addEventListener('focus', onVisible);
    })();

    async function saveProfileEdit() {
      const first_name = document.getElementById('profile-input-first').value.trim();
      const last_name = document.getElementById('profile-input-last').value.trim();
      const classification = document.getElementById('profile-input-classification').value.trim();
      const major = document.getElementById('profile-input-major').value.trim();
      const university = document.getElementById('profile-input-university').value.trim();
      if (!first_name || !last_name || !classification || !major || !university) {
        winkToast('Please fill in every field.', true);
        return;
      }
      try {
        const res = await fetch('/update-profile', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          // preferred_language is intentionally omitted here (not sent as
          // an empty string) — /update-profile treats an omitted field as
          // "leave whatever's already stored untouched," which matters
          // now that this page's UI for editing it is gone; sending ""
          // instead would silently reset a value set elsewhere (e.g. at
          // registration) back to auto-detect.
          body: JSON.stringify({ first_name, last_name, classification, university, major })
        });
        const data = await res.json();
        if (!data.success) { winkToast(data.error || 'Something went wrong.', true); return; }
        document.getElementById('profile-view-name').textContent = `${data.profile.first_name} ${data.profile.last_name}`;
        document.getElementById('profile-view-classification').innerHTML = `<span class="badge">${escapeHtml(data.profile.classification)}</span>`;
        document.getElementById('profile-view-major').textContent = data.profile.major;
        document.getElementById('profile-view-university').textContent = data.profile.university;
        toggleProfileEdit(false);
        winkToast('Profile updated.');
      } catch (e) {
        winkToast('Something went wrong — please try again.', true);
      }
    }

    function openDeleteAccountModal() {
      document.getElementById('delete-account-password').value = '';
      document.getElementById('delete-account-error').style.display = 'none';
      document.getElementById('delete-account-modal').classList.add('open');
    }
    function closeDeleteAccountModal() {
      document.getElementById('delete-account-modal').classList.remove('open');
    }
    async function confirmDeleteAccount() {
      const password = document.getElementById('delete-account-password').value;
      const errorEl = document.getElementById('delete-account-error');
      const btn = document.getElementById('delete-account-confirm-btn');
      if (!password) {
        errorEl.textContent = 'Please enter your password.';
        errorEl.style.display = 'block';
        return;
      }
      btn.disabled = true;
      try {
        const res = await fetch('/account/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'password=' + encodeURIComponent(password)
        });
        const data = await res.json();
        if (data.ok) {
          window.location.href = '/';
        } else {
          errorEl.textContent = data.error || 'Something went wrong.';
          errorEl.style.display = 'block';
          btn.disabled = false;
        }
      } catch (e) {
        errorEl.textContent = 'Something went wrong — please try again.';
        errorEl.style.display = 'block';
        btn.disabled = false;
      }
    }
    // Preselect the student's current university in the edit dropdown
    (function initProfileUniversity() {
      const sel = document.getElementById('profile-input-university');
      const current = document.getElementById('profile-view-university').textContent.trim();
      if (sel && current && current !== 'Not set') sel.value = current;
    })();
    async function rebuildDeadlines(btn) {
      const original = btn.textContent;
      btn.textContent = 'Rebuilding…'; btn.disabled = true;
      try {
        const res = await fetch('/reprocess-deadlines', { method: 'POST' });
        const data = await res.json();
        if (data.error) { winkToast(data.error, true); }
        else {
          winkToast(`Found ${data.deadlines_found} deadline(s) across ${data.documents_processed} document(s).`);
          location.reload();
        }
      } catch (e) {
        winkToast('Something went wrong — please try again.', true);
      }
      btn.textContent = original; btn.disabled = false;
    }

    async function loadDeadlineConflicts() {
      try {
        const res = await fetch('/deadline-conflicts');
        if (!res.ok) return;
        const data = await res.json();
        const conflicts = data.conflicts || [];
        if (!conflicts.length) return; // leave the card hidden — no false alarms
        const card = document.getElementById('conflict-card');
        const body = document.getElementById('conflict-body');

        // Clusters from /deadline-conflicts are grouped by proximity
        // ("close together in time"), not by calendar month, so flatten
        // every cluster's items into one list and regroup by month —
        // a busy stretch spanning a month boundary lands under both
        // months it actually touches.
        const allItems = conflicts.flat();
        const byMonth = {};
        allItems.forEach(item => {
          const monthKey = (item.due_date || '').slice(0, 7); // "YYYY-MM"
          (byMonth[monthKey] ||= []).push(item);
        });
        const monthKeys = Object.keys(byMonth).sort();

        // No inline style attribute with a color substitution here — this
        // app's CSP only allows inline style attributes whose exact value
        // was pre-computed and hashed from the static template files at
        // startup (see csp_hashes.py). A dynamically-built style string
        // interpolating a color variable was never hashed, so the
        // browser silently drops just that attribute while still
        // rendering everything else — which is why colors never showed
        // up even though this code was correct. data-* attributes aren't
        // restricted by style-src-attr, so the color rides along as data
        // and gets applied afterward via direct JS style-PROPERTY
        // assignment (element.style.background = ...), which CSP does
        // not restrict — only the markup style="..." attribute is
        // restricted.
        function itemRowHtml(item) {
          const color = busyStretchColor(item.course);
          return `<div class="deadline-item busy-stretch-item" data-course-color="${color}">
               <div class="deadline-date">${escapeHtml((item.due_date || '').slice(5, 10))}</div>
               <div>
                 <div class="deadline-name">${escapeHtml(item.title)}</div>
                 <div class="deadline-meta busy-stretch-meta">${escapeHtml(item.course)}</div>
               </div>
             </div>`;
        }

        body.innerHTML = monthKeys.map(monthKey => {
          const monthItems = byMonth[monthKey].slice()
            .sort((a, b) => (a.due_date || '').localeCompare(b.due_date || ''));
          const monthLabel = monthKey
            ? new Date(monthKey + '-01T00:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
            : 'Date unknown';

          const byWeek = {};
          monthItems.forEach(item => {
            const day = parseInt((item.due_date || '').slice(8, 10), 10) || 1;
            const weekNum = Math.ceil(day / 7);
            (byWeek[weekNum] ||= []).push(item);
          });
          const weekNums = Object.keys(byWeek).map(Number).sort((a, b) => a - b);

          const weekBlocks = weekNums.map(weekNum => {
            const rows = byWeek[weekNum].map(itemRowHtml).join('');
            return `<div style="margin-bottom:10px;">
                      <div style="font-size:17px;font-weight:700;color:#6b7a99;margin-bottom:4px;">
                        Week ${weekNum}
                      </div>
                      <div class="deadline-list">${rows}</div>
                    </div>`;
          }).join('');

          return `<div style="margin-bottom:18px;">
                    <div style="font-size:13px;font-weight:700;color:#002855;margin-bottom:8px;">
                      ${escapeHtml(monthLabel)}
                    </div>
                    ${weekBlocks}
                  </div>`;
        }).join('');

        // Apply the per-item color via direct style-property assignment
        // (CSP-safe) now that the elements actually exist in the DOM.
        body.querySelectorAll('[data-course-color]').forEach(item => {
          const color = item.dataset.courseColor;
          item.style.borderLeft = `8px solid ${color}`;
          item.style.background = `${color}26`;
          const dateEl = item.querySelector('.deadline-date');
          if (dateEl) dateEl.style.background = color;
          const metaEl = item.querySelector('.busy-stretch-meta');
          if (metaEl) {
            metaEl.style.color = color;
            metaEl.style.fontWeight = '700';
          }
        });

        card.style.display = 'block';
      } catch (e) {  }
    }

    async function loadStudyPlan() {
      const body = document.getElementById('study-plan-body');
      try {
        const res = await fetch('/study-plan?weeks=4');
        if (!res.ok) { body.innerHTML = '<div style="color:#6b7a99;font-size:13px;">Couldn\'t load your study plan.</div>'; return; }
        const data = await res.json();
        const weeks = data.weeks || [];
        // Local date components, not new Date().toISOString() — that
        // converts to UTC first, which shows tomorrow's date for hours
        // every evening in Mountain time (and anywhere west of UTC).
        const now = new Date();
        const pad2 = n => String(n).padStart(2, '0');
        const today = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
        body.innerHTML = weeks.map((w, i) => {
          const label = i === 0 ? 'This week' : `Week of ${w.week_start.slice(5)}`;
          const items = w.deadlines.map(d =>
            `<div class="deadline-item${d.completed ? ' completed' : ''}" data-course-color="${escapeHtml(busyStretchColor(d.course))}" style="text-decoration:none;">
               <div class="deadline-date">${d.due_date.slice(5)}</div>
               <div><div class="deadline-name">${d.completed ? '✓ ' : ''}${escapeHtml(d.title)}</div><div class="deadline-meta">${escapeHtml(d.course)}</div></div>
             </div>`
          ).join('');
          const reviewLine = w.questions_due_for_review > 0
            ? `<div style="font-size:12px;color:#FF8200;font-weight:600;margin-top:6px;">📚 ${w.questions_due_for_review} practice question${w.questions_due_for_review !== 1 ? 's' : ''} due for review</div>`
            : '';
          return `<div style="margin-bottom:16px;">
                    <div style="font-size:12px;font-weight:700;color:#6b7a99;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;">${label}</div>
                    ${items ? `<div class="deadline-list">${items}</div>` : '<div style="font-size:13px;color:#9ca3af;">Nothing due.</div>'}
                    ${reviewLine}
                  </div>`;
        }).join('');

        // Same CSP-safe direct style-property assignment used for the
        // Busy Stretch section above — colors each item by its course.
        body.querySelectorAll('[data-course-color]').forEach(item => {
          const color = item.dataset.courseColor;
          item.style.borderLeft = `4px solid ${color}`;
          const dateEl = item.querySelector('.deadline-date');
          if (dateEl) dateEl.style.background = color;
        });
      } catch (e) {
        body.innerHTML = '<div style="color:#6b7a99;font-size:13px;">Couldn\'t load your study plan.</div>';
      }
    }

    loadDeadlineConflicts();
    loadStudyPlan();
  
