    const WINK_COURSE_COLORS = window.WINK_DOCUMENTS_DATA.courseColors;

    async function refreshCourseColorsAndDropdown() {
      try {
        const res = await fetch('/course-colors');
        const data = await res.json();
        if (!data || data.error) return;
        Object.assign(WINK_COURSE_COLORS, data.colors || {});
        applyCourseColors();
        const list = document.getElementById('course-list');
        const existing = new Set(Array.from(list.options).map(o => o.value.toLowerCase()));
        (data.courses || []).forEach(c => {
          if (!existing.has(c.toLowerCase())) {
            const opt = document.createElement('option');
            opt.value = c;
            list.appendChild(opt);
          }
        });
      } catch (e) {

      }
    }

    function applyCourseColors(root) {
      (root || document).querySelectorAll('.course-section').forEach(section => {
        const h3 = section.querySelector('.course-section-header h3');
        if (!h3) return;
        const courseName = h3.textContent.replace(/\s*\(CRN[^)]*\)\s*$/i, '').trim();
        const color = winkColorForCourse(courseName, WINK_COURSE_COLORS);
        section.style.borderLeft = `4px solid ${color}`;
        section.style.paddingLeft = '12px';
        h3.style.color = color;
        const badge = section.querySelector('.course-count-badge');
        if (badge) { badge.style.background = color; badge.style.color = '#fff'; }

        section.querySelectorAll('.doc-file-icon').forEach(icon => {
          icon.style.color = color;
          icon.style.background = `${color}1f`;
          icon.style.display = 'inline-flex';
          icon.style.alignItems = 'center';
          icon.style.justifyContent = 'center';
          icon.style.width = '26px';
          icon.style.height = '26px';
          icon.style.borderRadius = '6px';
          icon.style.flexShrink = '0';
        });
      });
    }


    const MAX_DOCS = window.WINK_DOCUMENTS_DATA.maxDocs;
    let currentDocCount = window.WINK_DOCUMENTS_DATA.currentDocCount;

    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const courseInput = document.getElementById('course-input');
    const crnInput = document.getElementById('crn-input');
    const selectedFileWrap = document.getElementById('selected-file-single');
    const selectedFileName = document.getElementById('selected-file-name');
    const selectedFileClear = document.getElementById('selected-file-clear');
    const crnAutofillHint = document.getElementById('crn-autofill-hint');

    const DOCS_FOR_CRN_MAP = window.WINK_DOCUMENTS_DATA.docsForCrnMap;
    const COURSE_CRN_MAP = {};
    DOCS_FOR_CRN_MAP.forEach(d => {
      if (d.course && d.crn) COURSE_CRN_MAP[d.course.trim().toLowerCase()] = d.crn;
    });

    function tryAutofillCrn() {
      const match = COURSE_CRN_MAP[courseInput.value.trim().toLowerCase()];
      if (match) {
        crnInput.value = match;
        crnAutofillHint.style.display = 'inline';
      } else {
        crnAutofillHint.style.display = 'none';
      }
    }
    courseInput.addEventListener('input', tryAutofillCrn);
    courseInput.addEventListener('change', tryAutofillCrn);
    // Safari doesn't reliably fire input/change when a datalist option is
    // clicked (a known long-standing quirk) — blur is the fallback that
    // always fires once the field loses focus, so autofill still happens.
    courseInput.addEventListener('blur', tryAutofillCrn);

    function setSelectedFile(file) {
      const progressWrap = document.getElementById('upload-progress-wrap');
      const progressFill = document.getElementById('upload-progress-fill');
      const progressText = document.getElementById('upload-progress-text');
      const doneSticker = document.getElementById('upload-done-sticker');
      if (file) {
        selectedFileName.textContent = file.name;
        selectedFileWrap.style.display = 'flex';
        dropZone.style.display = 'none';

        progressWrap.style.display = 'block';
        progressFill.style.width = '0%';
        progressFill.classList.remove('is-done');
        doneSticker.classList.remove('show');
        progressText.textContent = `${file.name} ready to upload`;
      } else {
        selectedFileWrap.style.display = 'none';
        dropZone.style.display = 'block';
        progressWrap.style.display = 'none';
      }
    }

    fileInput.addEventListener('change', () => setSelectedFile(fileInput.files[0] || null));
    selectedFileClear.addEventListener('click', () => {
      fileInput.value = '';
      setSelectedFile(null);
    });

    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));

    dropZone.addEventListener('drop', e => {
      e.preventDefault(); dropZone.classList.remove('drag-over');
      if (isAtLimit()) return;
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        setSelectedFile(fileInput.files[0]);
      }
    });

    function isAtLimit() { return currentDocCount >= MAX_DOCS; }

    function updateLimitUI() {
      document.getElementById('docs-count-text').textContent = currentDocCount + ' file' + (currentDocCount !== 1 ? 's' : '');
      document.getElementById('hero-docs-used').textContent = currentDocCount + ' of ' + MAX_DOCS;
      document.getElementById('hero-slots-remaining').textContent = Math.max(0, MAX_DOCS - currentDocCount);
      document.getElementById('limit-banner').style.display = isAtLimit() ? 'flex' : 'none';
      document.getElementById('btn-upload').disabled = isAtLimit();
      dropZone.classList.toggle('disabled', isAtLimit());
    }

    function uploadFiles() {
      const status = document.getElementById('upload-status');
      const file = fileInput.files[0];
      const course = courseInput.value.trim();
      const crn = crnInput.value.trim();
      const docType = document.getElementById('doc-type-input').value;

      if (!course) {
        status.innerHTML = '<span class="status-error">Please enter a course name.</span>';
        return;
      }
      if (!crn) {
        status.innerHTML = '<span class="status-error">Please enter a CRN#.</span>';
        return;
      }
      if (!file) {
        status.innerHTML = '<span class="status-error">Please select a file.</span>';
        return;
      }
      if (isAtLimit()) {
        status.innerHTML = `<span class="status-error">You're at the ${MAX_DOCS}-document limit — delete something first.</span>`;
        return;
      }

      const progressWrap = document.getElementById('upload-progress-wrap');
      const progressFill = document.getElementById('upload-progress-fill');
      const progressText = document.getElementById('upload-progress-text');
      const doneSticker = document.getElementById('upload-done-sticker');
      const firstUploadNote = document.getElementById('first-upload-note');
      firstUploadNote.style.display = (currentDocCount === 0) ? 'block' : 'none';
      progressWrap.style.display = 'block';
      progressFill.style.width = '0%';
      progressFill.classList.remove('is-done');
      doneSticker.classList.remove('show');
      status.innerHTML = `<span class="status-info">⏳ Uploading ${escapeHtml(file.name)}…</span>`;

      const fd = new FormData();
      fd.append('file', file); fd.append('course', course); fd.append('crn', crn); fd.append('doc_type', docType);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');
      const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
      if (token) xhr.setRequestHeader('X-CSRFToken', token);

      xhr.upload.addEventListener('progress', e => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded / e.total) * 100);
        progressFill.style.width = `${pct}%`;
        progressText.innerHTML = `${escapeHtml(file.name)} <span class="pct">${pct}%</span>`;
      });

      xhr.onload = () => {
        let data;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        firstUploadNote.style.display = 'none';
        if (data && !data.error) {
          currentDocCount = data.docs.length;
          COURSE_CRN_MAP[course.toLowerCase()] = crn;
          updateLimitUI();
          status.innerHTML = `<span class="status-success">✅ Uploaded ${escapeHtml(file.name)}.</span>`;
          progressText.innerHTML = `Uploaded ${escapeHtml(file.name)}. <span class="pct">100%</span>`;
          progressFill.classList.add('is-done');
          doneSticker.classList.add('show');
          fileInput.value = '';
          setSelectedFile(null);
          refreshLibrary(data.docs);
          refreshCourseColorsAndDropdown();
        } else {
          status.innerHTML = `<span class="status-error">${escapeHtml((data && data.error) || 'Upload failed — please try again.')}</span>`;
        }
        setTimeout(() => { progressWrap.style.display = 'none'; progressFill.classList.remove('is-done'); doneSticker.classList.remove('show'); }, 1600);
      };
      xhr.onerror = () => {
        status.innerHTML = '<span class="status-error">Upload failed — please try again.</span>';
        setTimeout(() => { progressWrap.style.display = 'none'; }, 1600);
      };
      xhr.send(fd);
    }

    document.addEventListener('click', function(e) {
      const btn = e.target.closest('.btn-delete');
      if (btn && btn.dataset.docId) deleteDoc(parseInt(btn.dataset.docId, 10));
    });

    document.addEventListener('click', function(e) {
      const btn = e.target.closest('.btn-download');
      if (btn && btn.dataset.docUrl) downloadDoc(btn.dataset.docUrl, btn.dataset.docName || 'document');
    });

    async function downloadDoc(url, filename) {
      const wrap = document.getElementById('download-progress-wrap');
      const fill = document.getElementById('download-progress-fill');
      const label = document.getElementById('download-progress-label');
      wrap.style.display = 'block';
      fill.style.width = '0%';
      fill.classList.remove('is-done');
      label.innerHTML = `Downloading ${escapeHtml(filename)}… <span class="pct">0%</span>`;
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error('Download failed');
        const totalStr = res.headers.get('Content-Length');
        const total = totalStr ? parseInt(totalStr, 10) : 0;
        const reader = res.body.getReader();
        const chunks = [];
        let received = 0;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          if (total) {
            const pct = Math.round((received / total) * 100);
            fill.style.width = `${pct}%`;
            label.innerHTML = `Downloading ${escapeHtml(filename)}… <span class="pct">${pct}%</span>`;
          } else {
            fill.style.width = '100%';
            label.innerHTML = `Downloading ${escapeHtml(filename)}… <span class="pct">${(received / 1024).toFixed(0)} KB</span>`;
          }
        }
        const blob = new Blob(chunks);
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(blobUrl);
        fill.style.width = '100%';
        fill.classList.add('is-done');
        label.innerHTML = `✅ Downloaded ${escapeHtml(filename)}. <span class="pct">100%</span>`;
      } catch (e) {
        label.textContent = `Could not download ${filename}.`;
        winkToast('Download failed — please try again.', true);
      } finally {
        setTimeout(() => { wrap.style.display = 'none'; fill.classList.remove('is-done'); }, 1600);
      }
    }

    async function deleteDoc(id) {
      const ok = await winkConfirm({
        title: 'Delete this document?',
        message: 'It will be removed from your uploads and WINK will no longer be able to reference it.',
        confirmLabel: 'Delete', danger: true
      });
      if (!ok) return;
      try {
        const res = await fetch('/delete-file', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({doc_id: id}) });
        const data = await res.json();
        if (data.success) {
          currentDocCount = data.docs.length;
          updateLimitUI();
          refreshLibrary(data.docs);
          winkToast('Document deleted.');
        } else {
          winkToast(data.error || 'Could not delete — please try again.', true);
        }
      } catch (e) {
        winkToast('Could not delete — please try again.', true);
      }
    }

    const DOC_FILE_ICON_SVG = '<svg class="doc-file-icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" xmlns="http://www.w3.org/2000/svg">'
      + '<path d="M6 2a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6H6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>'
      + '<path d="M14 2v6h6" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';

    function fileIcon(name) {

      return DOC_FILE_ICON_SVG;
    }


    function groupDocsByCourse(docs) {
      const groups = {};
      docs.forEach(d => {
        const course = (d.course || '').trim() || 'General';
        const crn = (d.crn || '').trim();
        const label = crn ? `${course} (CRN ${crn})` : course;
        (groups[label] = groups[label] || []).push(d);
      });
      const ordered = Object.keys(groups).sort();
      return ordered.map(label => [label, groups[label]]);
    }

    function refreshLibrary(docs) {
      const wrap = document.getElementById('library-wrap');

      if (docs.length === 0) {
        wrap.innerHTML = `<div class="empty-state" id="empty-state"><span class="empty-icon">📭</span><h3>No documents yet</h3><p>Add a course name and CRN# above, then upload your first file — WINK will read it and answer questions from its content.</p></div>`;
        return;
      }

      const grouped = groupDocsByCourse(docs);
      let html = '';
      grouped.forEach(([course, courseDocs]) => {
        html += `<div class="course-section">
          <div class="course-section-header">
            <h3>${escapeHtml(course)}</h3>
            <span class="course-count-badge">${courseDocs.length}</span>
          </div>
          <div class="card docs-card">
            <table>
              <thead><tr><th>File</th><th>Size</th><th>Uploaded</th><th></th></tr></thead>
              <tbody>`;
        courseDocs.forEach(d => {
          const uploadedDate = d.uploaded_at
            ? new Date(d.uploaded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
            : '—';
          html += `<tr id="doc-row-${d.id}">
            <td><div class="doc-name-cell"><span class="doc-file-icon">${fileIcon(d.orig_name)}</span><a class="doc-name-text" href="/documents/${d.id}/file" target="_blank" rel="noopener">${escapeHtml(d.orig_name)}</a>${d.chunking_failed ? '<span class="badge" title="WINK couldn\'t finish processing this file for smarter search — it may not show up in answers about your other courses\' content. Try deleting and re-uploading it." style="background:#fef2f2;color:#b91c1c;margin-left:6px;font-size:10px;">⚠ Processing issue</span>' : ''}</div></td>
            <td>${(d.size_bytes/1024).toFixed(1)} KB</td>
            <td>${uploadedDate}</td>
            <td>
              <button class="btn-download" data-doc-url="/documents/${d.id}/file" data-doc-name="${escapeHtml(d.orig_name)}" aria-label="Download ${escapeHtml(d.orig_name)}">⬇ Download</button>
              <button class="btn-delete" data-doc-id="${d.id}" aria-label="Delete ${escapeHtml(d.orig_name)}">Delete</button>
            </td>
          </tr>`;
        });
        html += `</tbody></table></div></div>`;
      });
      wrap.innerHTML = html;
      applyCourseColors(wrap);
      filterDocLibrary(document.getElementById('doc-search')?.value || '');
    }

    function filterDocLibrary(query) {
      const q = query.trim().toLowerCase();
      const sections = document.querySelectorAll('#library-wrap .course-section');
      let anyVisible = false;
      sections.forEach(section => {
        const courseName = (section.querySelector('h3')?.textContent || '').toLowerCase();
        const rows = section.querySelectorAll('tbody tr');
        let sectionHasMatch = false;
        rows.forEach(row => {
          const filename = (row.querySelector('.doc-name-text')?.textContent || '').toLowerCase();
          const matches = !q || courseName.includes(q) || filename.includes(q);
          row.style.display = matches ? '' : 'none';
          if (matches) sectionHasMatch = true;
        });
        section.style.display = sectionHasMatch ? '' : 'none';
        if (sectionHasMatch) anyVisible = true;
      });
      const emptyMsg = document.getElementById('doc-search-empty');
      if (emptyMsg) emptyMsg.style.display = (q && !anyVisible && sections.length > 0) ? 'block' : 'none';
    }

    updateLimitUI();
    applyCourseColors();

    (function highlightLinkedDocument() {
      if (!location.hash) return;
      const row = document.querySelector(location.hash);
      if (!row) return;
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row.style.transition = 'background-color 0.4s ease';
      row.style.backgroundColor = '#fff3e6';
      setTimeout(() => { row.style.backgroundColor = ''; }, 2200);
    })();
  
