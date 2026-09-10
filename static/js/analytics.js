    let analyticsData = null;

    function switchTab(name) {
      document.querySelectorAll('.tab-btn').forEach((b, i) => {
        const tabs = ['students','demo','activity','conversations','deadlines','distributions','insights','general-docs'];
        b.classList.toggle('active', tabs[i] === name);
      });
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
    }

    const eventIcons = {
      account_created: '🆕', login: '🔐', page_view: '👁',
      file_uploaded: '📁', question_asked: '❓', answer_given: '💬',
      file_deleted: '🗑'
    };

    function formatTimeSpent(minutes) {
      const total = Math.round(Number(minutes) || 0);
      if (total <= 0) return '—';
      const hrs = Math.floor(total / 60);
      const mins = total % 60;
      return hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
    }

    function formatTokenCount(tokens) {
      const n = Math.round(Number(tokens) || 0);
      return n > 0 ? n.toLocaleString() : '—';
    }
    function formatCostUsd(usd) {
      const n = Number(usd) || 0;
      if (n === 0) return '$0.00';

      return n < 1 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
    }

    let currentStudents = [];
    let sortState = { field: null, dir: 'asc' };

    function applySort(students) {
      if (!sortState.field) return students;
      const { field, dir } = sortState;
      const th = document.querySelector(`th[data-sort="${field}"]`);
      const type = th ? th.dataset.type : 'str';
      const getVal = (s) => {
        if (field === 'name') return `${s.first_name || ''} ${s.last_name || ''}`.trim().toLowerCase();
        if (field === 'status') return s.is_active ? 'active' : 'suspended';
        const v = s[field];
        if (type === 'str') return String(v || '').toLowerCase();
        if (type === 'num') return Number(v) || 0;
        if (type === 'date') return new Date(v || 0).getTime() || 0;
        return v;
      };
      const sorted = [...students].sort((a, b) => {
        const av = getVal(a), bv = getVal(b);
        if (av < bv) return dir === 'asc' ? -1 : 1;
        if (av > bv) return dir === 'asc' ? 1 : -1;
        return 0;
      });
      return sorted;
    }

    function renderStudentsTable(students) {
      document.getElementById('student-count').textContent = students.length + ' students';
      const tbody = document.getElementById('students-tbody');
      if (students.length === 0) {
        tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;padding:32px;color:#6b7a99;">No students yet.</td></tr>';
        return;
      }
      tbody.innerHTML = students.map(s => `
        <tr>
          <td><strong>${escapeHtml(s.first_name)} ${escapeHtml(s.last_name)}</strong></td>
          <td style="color:#6b7a99;">${escapeHtml(s.email)}</td>
          <td>${escapeHtml(s.university)}</td>
          <td><span class="badge badge-navy">${escapeHtml(s.classification)}</span></td>
          <td>${escapeHtml(s.major)}</td>
          <td>${s.first_generation ? '<span class="badge badge-orange">Yes</span>' : '<span style="color:#6b7a99;">No</span>'}</td>
          <td style="color:#6b7a99;">${escapeHtml(s.joined)}</td>
          <td>${s.sessions}</td>
          <td><span class="badge badge-orange">${s.questions}</span></td>
          <td>${s.uploads}</td>
          <td style="color:#6b7a99;">${formatTimeSpent(s.time_spent_minutes)}</td>
          <td style="color:#6b7a99;">${formatTokenCount(s.total_tokens)}</td>
          <td style="color:#6b7a99;">${formatCostUsd(s.estimated_cost_usd)}</td>
          <td>${renderStudentStatusCell(s)}</td>
        </tr>`).join('');
      // Names are attached via dataset + addEventListener rather than baked into an
      // inline onclick string — untrusted text inside an inline event-handler
      // attribute is unsafe even when HTML-escaped, since the browser HTML-decodes
      // the attribute before handing it to the JS engine, undoing the escaping.
      tbody.querySelectorAll('.student-toggle-btn').forEach((btn, idx) => {
        const student = students[idx];
        btn.addEventListener('click', () => {
          toggleStudentActive(
            parseInt(btn.dataset.id, 10),
            `${student.first_name} ${student.last_name}`,
            btn.dataset.active === 'true'
          );
        });
      });
      tbody.querySelectorAll('.student-anonymize-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          anonymizeStudent(parseInt(btn.dataset.id, 10));
        });
      });
      tbody.querySelectorAll('.student-verify-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          manuallyVerifyStudent(parseInt(btn.dataset.id, 10));
        });
      });
      tbody.querySelectorAll('.student-delete-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          deleteStudent(parseInt(btn.dataset.id, 10), btn.dataset.email);
        });
      });
    }

    function renderStudentStatusCell(s) {
      if (s.anonymized_at) {
        return `<span class="badge" style="background:#eef0f6;color:#6b7a99;">Anonymized</span>`;
      }
      if (s.account_deleted_at) {
        return `<span class="badge" style="background:#fef2f2;color:#b91c1c;">Deleted</span>
                <button class="tab-btn student-anonymize-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}">Anonymize</button>
                <button class="tab-btn student-delete-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;color:#b91c1c;border-color:#f3c6c6;" data-id="${s.id}" data-email="${escapeHtml(s.email)}">Delete</button>`;
      }
      // Safety-net for when verification email genuinely doesn't reach a
      // student (SMTP/SES issues, a school spam filter, etc.) — lets an
      // admin unblock their own real account without touching anything
      // else about it. Only shown pre-verification; once verified there's
      // nothing left to do here, so the button disappears on its own.
      const verifyBtn = s.email_verified ? '' :
        `<button class="tab-btn student-verify-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}">Verify Email</button>`;
      if (s.is_active) {
        return `<span class="badge badge-active">Active</span>
                <button class="tab-btn student-toggle-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}" data-active="true">Suspend</button>
                ${verifyBtn}
                <button class="tab-btn student-anonymize-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}">Anonymize</button>
                <button class="tab-btn student-delete-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;color:#b91c1c;border-color:#f3c6c6;" data-id="${s.id}" data-email="${escapeHtml(s.email)}">Delete</button>`;
      }
      return `<span class="badge" style="background:#fef2f2;color:#b91c1c;">Suspended</span>
              <button class="tab-btn student-toggle-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}" data-active="false">Reactivate</button>
              ${verifyBtn}
              <button class="tab-btn student-anonymize-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;" data-id="${s.id}">Anonymize</button>
              <button class="tab-btn student-delete-btn" style="padding:4px 10px;font-size:11px;margin-left:6px;color:#b91c1c;border-color:#f3c6c6;" data-id="${s.id}" data-email="${escapeHtml(s.email)}">Delete</button>`;
    }

    async function manuallyVerifyStudent(studentId) {
      const ok = await winkConfirm({
        title: 'Manually verify this student\'s email?',
        message: "Use this only if they genuinely didn't receive the verification email " +
          "(and you've confirmed it's really their address). This unblocks chat, uploads, " +
          "and practice questions for their account immediately, without them clicking a link.",
        confirmLabel: 'Verify'
      });
      if (!ok) return;
      try {
        const res = await fetch('/manually-verify-student', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: studentId })
        });
        const data = await res.json();
        if (!data.success) { winkToast(data.error || 'Something went wrong.', true); return; }
        winkToast(data.already_verified ? 'Already verified.' : 'Email verified.');
        loadData();
      } catch (e) {
        winkToast('Something went wrong — please try again.', true);
      }
    }

    async function anonymizeStudent(studentId) {
      const ok = await winkConfirm({
        title: 'Anonymize this student?',
        message: "This is IRREVERSIBLE — their name/email will be replaced with an " +
          "untraceable label, and they won't be able to log in again. Their " +
          "conversations, documents, and research data stay intact for analysis, " +
          "just no longer tied to an identifiable name.",
        confirmLabel: 'Anonymize',
        danger: true
      });
      if (!ok) return;
      try {
        const res = await fetch('/anonymize-student', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: studentId })
        });
        const data = await res.json();
        if (!data.success) { winkToast(data.error || 'Something went wrong.', true); return; }
        winkToast(`Anonymized as ${data.label}.`);
        loadData();
      } catch (e) {
        winkToast('Something went wrong — please try again.', true);
      }
    }

    async function deleteStudent(studentId, email) {
      const ok = await winkConfirm({
        title: 'Permanently delete this student?',
        message: `This is IRREVERSIBLE and different from Anonymize — the entire row ` +
          `for ${email} is erased, along with their conversations, documents, deadlines, ` +
          `grades, and research data. Only use this for test/junk accounts so the email ` +
          `can be reused to register — never for a real pilot participant (use Anonymize ` +
          `for those, to honor the data retention consent they agreed to).`,
        confirmLabel: 'Continue',
        danger: true
      });
      if (!ok) return;
      const typed = prompt(`Type this student's exact email address to confirm permanent deletion:\n${email}`);
      if (typed === null) return;
      if (typed.trim().toLowerCase() !== email.trim().toLowerCase()) {
        winkToast('Email did not match — nothing was deleted.', true);
        return;
      }
      try {
        const res = await fetch('/delete-student', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: studentId, confirm_email: typed.trim() })
        });
        const data = await res.json();
        if (!data.success) { winkToast(data.error || 'Something went wrong.', true); return; }
        winkToast(`Deleted ${data.email} — that email can be used to register again.`);
        loadData();
      } catch (e) {
        winkToast('Something went wrong — please try again.', true);
      }
    }

    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
          const field = th.dataset.sort;
          if (sortState.field === field) {
            sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
          } else {
            sortState = { field, dir: 'asc' };
          }
          document.querySelectorAll('th.sortable').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
          th.classList.add(sortState.dir === 'asc' ? 'sort-asc' : 'sort-desc');
          renderStudentsTable(applySort(currentStudents));
        });
      });
    });

    async function loadData() {
      try {
        const res = await fetch('/analytics-data-full');
        const d = await res.json();
        if (d.error) { winkToast('Error: ' + d.error, true); return; }
        analyticsData = d;

        const cards = document.querySelectorAll('.stat-card');
        const vals = [d.total_students, d.total_sessions, d.total_questions, d.total_uploads, d.total_deadlines, formatCostUsd(d.total_estimated_cost_usd)];
        cards.forEach((c, i) => {
          c.querySelector('.stat-loading') && (c.innerHTML = `<div class="stat-value">${vals[i]}</div><div class="stat-label">${c.querySelector('.stat-label').textContent}</div>`);
          c.querySelector('.stat-value') && (c.querySelector('.stat-value').textContent = vals[i]);
        });

        const sr = document.getElementById('stats-row');
        const labels = ['Total Students','Total Sessions','Questions Asked','Files Uploaded','Upcoming Deadlines','Est. AI Cost (Pilot)'];
        const tops = ['','navy-top','','green-top','navy-top',''];
        sr.innerHTML = vals.map((v,i) => `<div class="stat-card ${tops[i]}"><div class="stat-value">${v}</div><div class="stat-label">${labels[i]}</div></div>`).join('');

        // Students table
        currentStudents = d.students;
        renderStudentsTable(applySort(currentStudents));

        // Activity feed
        const feed = document.getElementById('event-feed');
        feed.innerHTML = d.recent.map(e => {
          const icon = eventIcons[e.event_type] || '📌';
          const name = e.first_name ? `${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}` : 'Unknown';
          let detail = escapeHtml(e.event_type.replace(/_/g,' '));
          if (e.payload && e.payload.q) detail = '"' + escapeHtml(e.payload.q.substring(0,80)) + (e.payload.q.length > 80 ? '…' : '') + '"';
          else if (e.payload && e.payload.page) detail = 'viewed ' + escapeHtml(e.payload.page);
          else if (e.payload && e.payload.name) detail = escapeHtml(e.payload.name);
          return `<div class="event-item">
            <div class="event-icon">${icon}</div>
            <div class="event-body">
              <div class="event-name">${name}</div>
              <div class="event-detail">${detail}</div>
            </div>
            <div class="event-time">${escapeHtml(e.ts)}</div>
          </div>`;
        }).join('') || '<div style="color:#6b7a99;font-size:13px;padding:12px;">No events yet.</div>';

        // Conversations
        const cb = document.getElementById('convos-body');
        if (!d.conversations || d.conversations.length === 0) {
          cb.innerHTML = '<div style="color:#6b7a99;font-size:14px;text-align:center;padding:32px;">No conversations yet.</div>';
        } else {
          cb.innerHTML = d.conversations.map((c, i) => `
            <div class="convo-item">
              <div class="convo-header" data-convo-index="${i}">
                <div>
                  <div style="font-size:11px;color:#6b7a99;margin-bottom:3px;">${escapeHtml(c.first_name)} ${escapeHtml(c.last_name)} · ${escapeHtml(c.ts)}</div>
                  <div class="convo-q">❓ ${escapeHtml(c.question.substring(0,120))}${c.question.length > 120 ? '…' : ''}</div>
                </div>
                <span style="font-size:12px;color:#6b7a99;flex-shrink:0;margin-left:12px;">▼</span>
              </div>
              <div class="convo-body" id="convo-body-${i}">
                <div class="convo-answer">${c.answer ? escapeHtml(c.answer).replace(/\n/g,'<br>') : 'No answer recorded.'}</div>
              </div>
            </div>`).join('');
        }

        // Deadlines
        const dtbody = document.getElementById('deadlines-tbody');
        if (!d.upcoming_deadlines || d.upcoming_deadlines.length === 0) {
          dtbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:32px;color:#6b7a99;">No upcoming deadlines found yet.</td></tr>';
        } else {
          dtbody.innerHTML = d.upcoming_deadlines.map(dl => `
            <tr>
              <td><span class="badge badge-orange">${escapeHtml(dl.due_date)}</span></td>
              <td><strong>${escapeHtml(dl.title)}</strong></td>
              <td>${escapeHtml(dl.course)}</td>
              <td style="color:#6b7a99;">${escapeHtml(dl.first_name)} ${escapeHtml(dl.last_name)}</td>
            </tr>`).join('');
        }

        // Bar charts
        function renderBars(containerId, items, maxN, orange) {
          const max = Math.max(...items.map(x => x.n), 1);
          const container = document.getElementById(containerId);
          container.innerHTML = items.slice(0, maxN).map(item => `
            <div class="bar-row">
              <div class="bar-label" title="${item.major || item.classification}">${(item.major || item.classification).substring(0,20)}</div>
              <div class="bar-track"><div class="bar-fill ${orange?'orange':''}" data-w="${Math.round(item.n/max*100)}%"></div></div>
              <div class="bar-val">${item.n}</div>
            </div>`).join('');
          applyPendingStyles(container);
        }
        renderBars('class-chart', d.by_class, 8, false);
        renderBars('major-chart', d.by_major, 12, true);

        renderMiniStats(d);
        renderDemoStats(d);
        renderUniversityTable(d);
        renderCourseChart(d.by_course);
        renderHeatmap(d.usage_heatmap);
        renderDeadlineSpikes(d.deadline_spikes);
        renderUploadMix(d.upload_mix);
        renderAnswerFeedback(d.answer_feedback);
        renderCommonQuestions(d.common_questions);
        renderStaleDocs(d.stale_global_docs);

      } catch(e) {
        console.error('Analytics load error:', e);
        winkToast('Failed to load analytics data.', true);
      }
    }

    function renderMiniStats(d) {
      const box = document.getElementById('insight-mini-stats');
      if (!box) return;
      const fmt = v => (v === null || v === undefined) ? '—' : v;
      const items = [
        [fmt(d.avg_session_minutes) + (d.avg_session_minutes ? ' min' : ''), 'Avg Session'],
        [d.total_sessions ? Math.round((d.total_questions / d.total_sessions) * 10) / 10 : '—', 'Questions / Session'],
        [fmt(d.retention_pct) + (d.retention_pct !== undefined ? '%' : ''), 'Retention (2+ weeks)'],
        [d.avg_minutes_to_first_question === null ? 'n/a' : fmt(d.avg_minutes_to_first_question) + ' min', 'Time to First Question'],
        [fmt(d.general_doc_availability_pct) + '%', 'Questions w/ General Docs'],
      ];
      box.innerHTML = items.map(([val, label]) => `
        <div class="mini-stat"><div class="mini-stat-value">${val}</div><div class="mini-stat-label">${label}</div></div>
      `).join('');
    }

    // Duration comes from the backend in whole seconds (see
    // services/analytics.py's get_demo_usage_stats) — format for display.
    function formatDemoDuration(seconds) {
      const s = Math.round(Number(seconds) || 0);
      if (s <= 0) return '—';
      const mins = Math.floor(s / 60);
      if (mins < 1) return `${s}s`;
      const hrs = Math.floor(mins / 60);
      return hrs > 0 ? `${hrs}h ${mins % 60}m` : `${mins}m`;
    }

    function renderDemoStats(d) {
      const box = document.getElementById('demo-mini-stats');
      if (!box) return;
      const dm = d.demo_usage || {};
      const items = [
        [dm.total_sessions || 0, 'Total Demos Started'],
        [dm.active_now || 0, 'Active Right Now'],
        [formatDemoDuration(dm.avg_duration_seconds), 'Avg Duration'],
        [formatDemoDuration(dm.max_duration_seconds), 'Longest Session'],
        [dm.total_questions_asked || 0, 'Total Questions Asked'],
        [dm.total_uploads || 0, 'Total Uploads'],
        [formatTokenCount(dm.total_tokens), 'Total Tokens'],
        [formatCostUsd(dm.total_estimated_cost_usd), 'Est. Cost'],
      ];
      box.innerHTML = items.map(([val, label]) => `
        <div class="mini-stat"><div class="mini-stat-value">${val}</div><div class="mini-stat-label">${label}</div></div>
      `).join('');
      renderDemoSessionsTable(d.demo_sessions || []);
    }

    function renderDemoSessionsTable(sessions) {
      const tbody = document.getElementById('demo-sessions-tbody');
      if (!tbody) return;
      if (!sessions.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="color:#6b7a99;">No demo sessions yet.</td></tr>';
        return;
      }
      tbody.innerHTML = sessions.map(s => `
        <tr>
          <td>${escapeHtml(s.started)}</td>
          <td>${formatDemoDuration(s.duration_seconds)}</td>
          <td><span class="badge badge-orange">${s.questions_asked}</span></td>
          <td>${s.uploads}</td>
          <td style="color:#6b7a99;">${formatTokenCount(s.total_tokens)}</td>
          <td style="color:#6b7a99;">${formatCostUsd(s.estimated_cost_usd)}</td>
          <td>${s.is_active
            ? '<span class="badge badge-active">Active</span>'
            : `<span style="color:#6b7a99;">${escapeHtml(s.ended_reason)}</span>`}</td>
          <td>${s.student_id && s.questions_asked > 0
            ? `<button class="tab-btn demo-view-btn" style="padding:4px 10px;font-size:11px;" data-sid="${s.student_id}">View</button>`
            : ''}</td>
        </tr>`).join('');
      tbody.querySelectorAll('.demo-view-btn').forEach(btn => {
        btn.addEventListener('click', () => viewDemoConversation(parseInt(btn.dataset.sid, 10)));
      });
    }

    async function viewDemoConversation(sid) {
      // Built with CSS classes (defined in analytics.css) rather than
      // inline style="" attributes: this page's CSP only allows the
      // specific inline-style hashes computed from templates/*.html at
      // startup (see wink/csp_hashes.py), so a style="" attribute written
      // here in JS can never be on that allowlist and gets silently
      // dropped by the browser -- which is why this popup used to render
      // with no background or positioning at all once it had enough
      // content to need its max-height/overflow rules.
      let overlay = document.getElementById('demo-convo-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'demo-convo-overlay';
        overlay.className = 'demo-convo-overlay';
        overlay.innerHTML = `<div class="demo-convo-card">
          <div class="demo-convo-header">
            <h3>Demo session detail</h3>
            <button id="demo-convo-close" class="demo-convo-close">✕</button>
          </div>
          <div id="demo-convo-body" class="demo-convo-body"></div>
        </div>`;
        document.body.appendChild(overlay);
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('open'); });
        overlay.querySelector('#demo-convo-close').addEventListener('click', () => overlay.classList.remove('open'));
      }
      overlay.classList.add('open');
      const body = overlay.querySelector('#demo-convo-body');
      body.innerHTML = '<p class="demo-convo-loading">Loading…</p>';
      try {
        const res = await fetch(`/student-conversations/${sid}`);
        const data = await res.json();
        const convos = data.conversations || [];
        const pages = data.pages || [];

        const ratingBadge = r => r === 'up'
          ? '<span class="demo-rating-up">👍 helpful</span>'
          : r === 'down' ? '<span class="demo-rating-down">👎 not helpful</span>' : '';

        const pagesHtml = pages.length ? `
          <div class="demo-pages-section">
            <div class="demo-pages-title">Pages visited</div>
            <table>
              <thead><tr>
                <th>Page</th>
                <th>Visits</th>
                <th>Time spent</th>
              </tr></thead>
              <tbody>${pages.map(p => `
                <tr>
                  <td>${escapeHtml(p.page)}</td>
                  <td>${p.visits}</td>
                  <td>${p.minutes > 0 ? p.minutes + ' min' : '—'}</td>
                </tr>`).join('')}</tbody>
            </table>
          </div>` : '';

        if (!convos.length) {
          body.innerHTML = pagesHtml +
            '<p class="demo-convo-empty">No conversation recorded for this demo.</p>';
          return;
        }
        const convosHtml = convos.map(c => `
          <div class="demo-convo-exchange">
            <div class="demo-convo-meta">${escapeHtml(c.ts)}${c.rating ? ' · ' + ratingBadge(c.rating) : ''}</div>
            <div class="demo-convo-question"><strong>Student:</strong> ${escapeHtml(c.question)}</div>
            <div class="demo-convo-answer"><strong>WINK:</strong> ${escapeHtml(c.answer)}</div>
          </div>`).join('');
        body.innerHTML = pagesHtml + convosHtml;
      } catch (e) {
        body.innerHTML = '<p class="demo-convo-error">Something went wrong loading this conversation.</p>';
      }
    }

    function renderUniversityTable(d) {
      const tbody = document.getElementById('university-tbody');
      if (!tbody) return;
      const rows = d.by_university || [];
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:#6b7a99;">No data yet.</td></tr>';
        return;
      }
      const durByUni = d.avg_session_minutes_by_university || {};
      tbody.innerHTML = rows.map(r => `
        <tr>
          <td><strong>${escapeHtml(r.university)}</strong></td>
          <td>${r.students}</td>
          <td>${r.sessions}</td>
          <td><span class="badge badge-orange">${r.questions}</span></td>
          <td>${r.uploads}</td>
          <td>${durByUni[r.university] !== undefined ? durByUni[r.university] + ' min' : '—'}</td>
        </tr>`).join('');
    }

    function renderCourseChart(items) {
      const el = document.getElementById('course-chart');
      if (!el) return;
      const filtered = (items || []).filter(x => x.course && x.course.trim());
      if (filtered.length === 0) { el.innerHTML = '<div style="color:#6b7a99;font-size:13px;">No documents uploaded yet.</div>'; return; }
      const max = Math.max(...filtered.map(x => x.n), 1);
      el.innerHTML = filtered.slice(0, 12).map(item => `
        <div class="bar-row">
          <div class="bar-label" title="${escapeHtml(item.course)}">${escapeHtml(item.course).substring(0,20)}</div>
          <div class="bar-track"><div class="bar-fill orange" data-w="${Math.round(item.n/max*100)}%"></div></div>
          <div class="bar-val">${item.n}</div>
        </div>`).join('');
      applyPendingStyles(el);
    }

    function renderHeatmap(grid) {
      const el = document.getElementById('heatmap-container');
      if (!el) return;
      if (!grid || grid.length === 0) { el.innerHTML = '<div style="color:#6b7a99;font-size:13px;">No question activity yet.</div>'; return; }
      const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
      const max = Math.max(...grid.flat(), 1);
      const colorFor = n => {
        if (n === 0) return '#eef0f6';
        const t = n / max;
        const r = Math.round(255 - t * (255-255));
        return `rgba(255,130,0,${(0.15 + t * 0.85).toFixed(2)})`;
      };
      let html = '<table class="heatmap-table"><thead><tr><th></th>';
      for (let h = 0; h < 24; h++) html += `<th>${h}</th>`;
      html += '</tr></thead><tbody>';
      for (let d = 0; d < 7; d++) {
        html += `<tr><td class="heatmap-row-label">${days[d]}</td>`;
        for (let h = 0; h < 24; h++) {
          const n = grid[d][h] || 0;
          html += `<td data-bg="${colorFor(n)}" title="${days[d]} ${h}:00 — ${n} question${n!==1?'s':''}">${n || ''}</td>`;
        }
        html += '</tr>';
      }
      html += '</tbody></table>';
      el.innerHTML = html;
      applyPendingStyles(el);
    }

    function renderDeadlineSpikes(spikes) {
      const tbody = document.getElementById('deadline-spikes-tbody');
      if (!tbody) return;
      if (!spikes || spikes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:#6b7a99;">No deadline data yet.</td></tr>';
        return;
      }
      tbody.innerHTML = spikes.slice().reverse().map(s => `
        <tr>
          <td><span class="badge badge-orange">${s.due_date}</span></td>
          <td>${s.deadlines_due}</td>
          <td>${s.questions_same_day}</td>
          <td>${s.questions_prior_3_days}</td>
        </tr>`).join('');
    }

    function renderUploadMix(mix) {
      const el = document.getElementById('upload-mix-container');
      if (!el) return;
      if (!mix) { el.innerHTML = '<div style="color:#6b7a99;font-size:13px;">No uploads yet.</div>'; return; }
      const total = (mix.permanent||0) + (mix.temporary||0) + (mix.global||0);
      const rows = [
        ['#002855', 'Permanent (student uploads)', mix.permanent||0],
        ['#FF8200', 'Temporary (this-conversation-only)', mix.temporary||0],
        ['#166534', 'Global (admin knowledge base)', mix.global||0],
      ];
      el.innerHTML = rows.map(([color, label, n]) => `
        <div class="upload-mix-row">
          <div class="upload-mix-swatch" data-bg="${color}"></div>
          <div style="flex:1;font-size:13px;color:#444;">${label}</div>
          <div style="font-weight:700;color:#002855;">${n}</div>
          <div style="width:50px;text-align:right;color:#6b7a99;font-size:12px;">${total ? Math.round(n/total*100) : 0}%</div>
        </div>`).join('');
      applyPendingStyles(el);
    }

    function renderAnswerFeedback(feedback) {
      const el = document.getElementById('answer-feedback-container');
      if (!el) return;
      if (!feedback || (feedback.up === 0 && feedback.down === 0)) {
        el.innerHTML = '<div style="color:#6b7a99;font-size:13px;">No feedback recorded yet — students can rate answers with the 👍/👎 buttons in chat.</div>';
        return;
      }
      const total = feedback.up + feedback.down;
      const pct = feedback.positive_pct;
      const rows = [
        ['#166534', '👍 Helpful', feedback.up],
        ['#dc2626', '👎 Not helpful', feedback.down],
      ];
      let html = rows.map(([color, label, n]) => `
        <div class="upload-mix-row">
          <div class="upload-mix-swatch" data-bg="${color}"></div>
          <div style="flex:1;font-size:13px;color:#444;">${label}</div>
          <div style="font-weight:700;color:#002855;">${n}</div>
          <div style="width:50px;text-align:right;color:#6b7a99;font-size:12px;">${total ? Math.round(n/total*100) : 0}%</div>
        </div>`).join('');
      html += `<div style="margin-top:12px;padding-top:12px;border-top:1px solid #eef0f6;font-size:13px;color:#444;">
        <strong style="color:#002855;">${pct !== null ? pct + '%' : '—'}</strong> of rated answers were marked helpful (${total} total ratings).
      </div>`;
      el.innerHTML = html;
      applyPendingStyles(el);
    }

    function renderCommonQuestions(items) {
      const el = document.getElementById('common-questions-container');
      if (!el) return;
      if (!items || items.length === 0) {
        el.innerHTML = '<div style="color:#6b7a99;font-size:13px;">No question has been asked by 2 or more different students in the past week yet.</div>';
        return;
      }
      el.innerHTML = items.map(item => `
        <div style="display:flex;gap:14px;align-items:flex-start;padding:10px 0;border-bottom:1px solid #eef0f6;">
          <div style="flex-shrink:0;min-width:78px;text-align:center;font-weight:700;color:#002855;font-size:13px;background:#eef4ff;border-radius:6px;padding:4px 6px;">${item.n_students} students</div>
          <div>
            <div style="font-size:13px;font-weight:600;color:#002855;">${escapeHtml(item.question)}</div>
            <div style="font-size:11px;color:#6b7a99;margin-top:2px;">Asked ${item.n} time${item.n !== 1 ? 's' : ''} total</div>
          </div>
        </div>`).join('');
    }

    function renderStaleDocs(docs) {
      const tbody = document.getElementById('stale-docs-tbody');
      if (!tbody) return;
      if (!docs || docs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:#6b7a99;">Nothing stale — all general reference docs are recent.</td></tr>';
        return;
      }
      tbody.innerHTML = docs.map(d => `
        <tr>
          <td>${escapeHtml(d.university || 'Not set')}</td>
          <td>${escapeHtml(d.label || 'General')}</td>
          <td style="font-weight:600;color:#002855;">${escapeHtml(d.orig_name)}</td>
          <td style="color:#6b7a99;">${d.uploaded_at}</td>
        </tr>`).join('');
    }

    async function toggleStudentActive(studentId, studentName, isSuspending) {
      const ok = await winkConfirm({
        title: isSuspending ? `Suspend ${studentName}?` : `Reactivate ${studentName}?`,
        message: isSuspending
          ? `${studentName} will be immediately logged out and won't be able to log back in until reactivated.`
          : `${studentName} will be able to log in again immediately.`,
        confirmLabel: isSuspending ? 'Suspend' : 'Reactivate',
        danger: isSuspending
      });
      if (!ok) return;
      try {
        const res = await fetch('/toggle-student-active', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: studentId })
        });
        const data = await res.json();
        if (!data.success) { winkToast(data.error || 'Could not update status.', true); return; }
        winkToast(isSuspending ? `${studentName} has been suspended.` : `${studentName} has been reactivated.`);
        loadData();
      } catch (e) {
        winkToast('Could not update status — please try again.', true);
      }
    }

    // Delegated: convo-header no longer carries a per-item inline onclick
    // (its index changes every render, so it can't be CSP-hash-allowlisted).
    document.addEventListener('click', function(e) {
      const header = e.target.closest('.convo-header');
      if (header && header.dataset.convoIndex !== undefined) {
        const body = document.getElementById('convo-body-' + header.dataset.convoIndex);
        if (body) body.classList.toggle('open');
      }
    });

    // ── General reference documents ─────────────────────────────

    // Applies width/background set via data-w/data-bg placeholders using the
    // DOM style property directly — never restricted by CSP, unlike setting
    // the style="..." HTML attribute itself. Used anywhere a value (a bar's
    // percentage width, a heatmap cell's color) varies per row and so can't
    // be a fixed, CSP-hash-allowlisted inline style string.
    function applyPendingStyles(root) {
      root.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w; el.removeAttribute('data-w'); });
      root.querySelectorAll('[data-bg]').forEach(el => { el.style.background = el.dataset.bg; el.removeAttribute('data-bg'); });
    }

    let gdSelectedFile = null;
    let gdSelectedUniversity = '';
    const gdDropZone = document.getElementById('gd-drop-zone');

    function handleGdFileSelect(file) {
      gdSelectedFile = file || null;
      document.getElementById('gd-drop-zone-filename').textContent = gdSelectedFile ? gdSelectedFile.name : '';
      document.getElementById('gd-upload-btn').disabled = !(gdSelectedFile && gdSelectedUniversity);
    }

    function onGdUniversityChange() {
      gdSelectedUniversity = document.getElementById('gd-university-select').value;
      document.getElementById('gd-upload-btn').disabled = !(gdSelectedFile && gdSelectedUniversity);
      loadGdDocs();
    }

    if (gdDropZone) {
      gdDropZone.addEventListener('dragover', e => { e.preventDefault(); gdDropZone.classList.add('drag-over'); });
      gdDropZone.addEventListener('dragleave', () => gdDropZone.classList.remove('drag-over'));
      gdDropZone.addEventListener('drop', e => {
        e.preventDefault(); gdDropZone.classList.remove('drag-over');
        if (e.dataTransfer.files[0]) handleGdFileSelect(e.dataTransfer.files[0]);
      });
    }

    async function uploadGdDoc() {
      if (!gdSelectedFile || !gdSelectedUniversity) return;
      const label = document.getElementById('gd-label-input').value.trim() || 'General';
      const btn = document.getElementById('gd-upload-btn');
      const msgEl = document.getElementById('gd-form-msg');
      btn.disabled = true; btn.textContent = 'Uploading…';
      msgEl.innerHTML = '';

      try {
        const formData = new FormData();
        formData.append('file', gdSelectedFile);
        formData.append('label', label);
        formData.append('university', gdSelectedUniversity);
        const res = await fetch('/upload-global', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
          msgEl.innerHTML = `<span style="color:#166534;">Uploaded "${escapeHtml(gdSelectedFile.name)}" for ${escapeHtml(gdSelectedUniversity)} (${data.chars_extracted} characters extracted).</span>`;
          document.getElementById('gd-label-input').value = '';
          document.getElementById('gd-file-input').value = '';
          handleGdFileSelect(null);
          loadGdDocs();
        } else {
          msgEl.innerHTML = `<span style="color:#b91c1c;">${escapeHtml(data.error || 'Upload failed.')}</span>`;
        }
      } catch (e) {
        msgEl.innerHTML = '<span style="color:#b91c1c;">Upload failed — please try again.</span>';
      }
      btn.textContent = 'Upload'; btn.disabled = !(gdSelectedFile && gdSelectedUniversity);
    }

    document.addEventListener('click', function(e) {
      const gdBtn = e.target.closest('.btn-delete-gd');
      if (gdBtn && gdBtn.dataset.gdDocId) deleteGdDoc(parseInt(gdBtn.dataset.gdDocId, 10));
    });

    async function deleteGdDoc(id) {
      const ok = await winkConfirm({
        title: 'Remove this general document?',
        message: 'It will no longer be available to any student at this university.',
        confirmLabel: 'Remove', danger: true
      });
      if (!ok) return;
      try {
        const res = await fetch('/delete-global-document', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_id: id, university: gdSelectedUniversity })
        });
        const data = await res.json();
        if (data.success) { loadGdDocs(); winkToast('Document removed.'); }
        else winkToast(data.error || 'Could not delete.', true);
      } catch (e) {
        winkToast('Could not delete — please try again.', true);
      }
    }

    async function loadGdDocs() {
      const tbody = document.getElementById('gd-tbody');
      const scopeText = document.getElementById('gd-scope-text');
      if (!tbody) return;
      if (!gdSelectedUniversity) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#6b7a99;">Select a university above to view its knowledge base.</td></tr>';
        document.getElementById('gd-count-text').textContent = '0 files';
        if (scopeText) scopeText.textContent = '';
        return;
      }
      if (scopeText) scopeText.textContent = gdSelectedUniversity;
      try {
        const res = await fetch('/global-documents?university=' + encodeURIComponent(gdSelectedUniversity));
        const data = await res.json();
        const docs = data.docs || [];
        document.getElementById('gd-count-text').textContent = `${docs.length} file${docs.length !== 1 ? 's' : ''}`;
        if (docs.length === 0) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#6b7a99;">No general documents uploaded yet for this university.</td></tr>';
          return;
        }
        tbody.innerHTML = docs.map(d => `
          <tr>
            <td>${escapeHtml(d.course || 'General')}</td>
            <td style="font-weight:600;color:#002855;">${escapeHtml(d.orig_name)}</td>
            <td>${(d.size_bytes / 1024).toFixed(1)} KB</td>
            <td>${d.uploaded_at ? new Date(d.uploaded_at).toLocaleDateString() : ''}</td>
            <td><button class="btn-delete-gd" data-gd-doc-id="${d.id}" aria-label="Delete ${escapeHtml(d.orig_name)}">🗑</button></td>
          </tr>
        `).join('');
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#6b7a99;">Could not load documents.</td></tr>';
      }
    }

    // Default to UTEP since that's WINK's original/primary school — admins

    (function initGdUniversity() {
      const sel = document.getElementById('gd-university-select');
      if (sel) {
        sel.value = 'University of Texas at El Paso';
        gdSelectedUniversity = sel.value;
      }
    })();
    loadGdDocs();
    loadData();
  
