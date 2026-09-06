    // Scores are scratch-pad "what-if" numbers, not the real grading

    const SCORES_KEY_PREFIX = 'wink-grades-scores:' + window.WINK_GRADES_DATA.studentId + ':';

    function loadScores(course) {
      try {
        const raw = localStorage.getItem(SCORES_KEY_PREFIX + course.toLowerCase());
        return raw ? JSON.parse(raw) : {};
      } catch (e) { return {}; }
    }

    function saveScores(course, scoresByRowId) {
      try {
        localStorage.setItem(SCORES_KEY_PREFIX + course.toLowerCase(), JSON.stringify(scoresByRowId));
      } catch (e) {  }
    }

    let rowSeq = 0;
    let currentCourse = '';
    let saveTimer = null;
    let mode = 'actual'; // 'actual' (autosaved, real grade) or 'whatif' (sandbox, never saved)
    let actualScoresByCategory = {};   // mirrors what's saved for real
    let whatIfScoresByCategory = {};   // hypothetical scratch pad, never persisted

    function currentCourseName() {
      return document.getElementById('course-input').value.trim();
    }

    function addRow(category, weight, score) {
      document.getElementById('weights-card').style.display = 'block';
      document.getElementById('no-course-state').style.display = 'none';
      const tbody = document.getElementById('weights-tbody');
      const id = 'row-' + (++rowSeq);
      const tr = document.createElement('tr');
      tr.dataset.rowId = id;
      tr.innerHTML = `
        <td><input type="text" class="category-input" value="${escapeHtml(category || '')}" placeholder="e.g. Homework" aria-label="Category name"></td>
        <td><div class="weight-input-wrap"><input type="number" class="weight-input" value="${weight !== '' && weight != null ? weight : ''}" min="0" max="100" step="0.1" aria-label="Weight percent"><span class="input-suffix">%</span></div></td>
        <td><input type="number" class="score-input" value="${score !== '' && score != null ? score : ''}" min="0" max="100" step="0.1" aria-label="Your score percent"></td>
        <td><button type="button" class="btn-danger" title="Remove category" aria-label="Remove category">✕</button></td>`;
      tbody.appendChild(tr);
      tr.querySelector('.category-input').addEventListener('input', onWeightsChanged);
      tr.querySelector('.weight-input').addEventListener('input', onWeightsChanged);
      tr.querySelector('.score-input').addEventListener('input', onScoreChanged);
      // Attached here rather than as an inline onclick attribute with an
      // interpolated id — this app's CSP only allows inline event-handler
      // attributes whose exact value was pre-hashed from the static
      // template files at startup (see csp_hashes.py). A dynamically-built
      // attribute value can never be pre-hashed, so the browser silently
      // drops it and the button does nothing. addEventListener isn't
      // subject to that restriction.
      tr.querySelector('.btn-danger').addEventListener('click', () => removeRow(id));
      recompute();
    }

    function removeRow(id) {
      const tr = document.querySelector(`tr[data-row-id="${id}"]`);
      if (tr) tr.remove();
      onWeightsChanged();
    }

    function clearTable() {
      document.getElementById('weights-tbody').innerHTML = '';
    }

    function getRows() {
      return [...document.querySelectorAll('#weights-tbody tr')].map(tr => ({
        id: tr.dataset.rowId,
        category: tr.querySelector('.category-input').value.trim(),
        weight: parseFloat(tr.querySelector('.weight-input').value),
        score: tr.querySelector('.score-input').value === '' ? null : parseFloat(tr.querySelector('.score-input').value),
      }));
    }

    function onScoreChanged() {
      if (mode === 'actual') {
        syncActualFromRows();
        const course = currentCourseName();
        if (course) saveScores(course, actualScoresByCategory);
      } else {
        whatIfScoresByCategory = {};
        getRows().forEach(r => { if (r.score !== null && !isNaN(r.score)) whatIfScoresByCategory[r.category] = r.score; });
      }
      recompute();
    }

    function syncActualFromRows() {
      actualScoresByCategory = {};
      getRows().forEach(r => { if (r.score !== null && !isNaN(r.score)) actualScoresByCategory[r.category] = r.score; });
    }

    function applyScoresToInputs(scoresByCategory) {
      document.querySelectorAll('#weights-tbody tr').forEach(tr => {
        const cat = tr.querySelector('.category-input').value.trim();
        const scoreInput = tr.querySelector('.score-input');
        const val = scoresByCategory[cat];
        scoreInput.value = (val !== undefined && val !== null) ? val : '';
      });
    }

    function setMode(newMode) {
      if (newMode === mode) return;
      if (newMode === 'whatif') {
        syncActualFromRows();
        whatIfScoresByCategory = { ...actualScoresByCategory };
        applyScoresToInputs(whatIfScoresByCategory);
      } else {
        applyScoresToInputs(actualScoresByCategory);
      }
      mode = newMode;
      updateModeUI();
      recompute();
    }

    function resetWhatIf() {
      whatIfScoresByCategory = { ...actualScoresByCategory };
      applyScoresToInputs(whatIfScoresByCategory);
      recompute();
    }

    function updateModeUI() {
      const inWhatIf = mode === 'whatif';
      document.getElementById('mode-actual-btn').classList.toggle('active', !inWhatIf);
      document.getElementById('mode-whatif-btn').classList.toggle('active', inWhatIf);
      document.getElementById('reset-whatif-btn').style.display = inWhatIf ? 'inline-block' : 'none';
      document.getElementById('mode-note').textContent = inWhatIf
        ? 'What-If Scenario: test hypothetical scores freely — nothing here is saved, so your real scores are safe.'
        : "Actual Scores autosave and drive your real Current Grade. Switch to What-If Scenario to test hypothetical scores — nothing there gets saved, so you can't lose your real data.";
      document.getElementById('current-grade-title').textContent = inWhatIf ? '3. What-if grade preview' : '3. Current grade';
      document.getElementById('current-grade-label').textContent = inWhatIf
        ? 'Based on your what-if scores:'
        : "Based on the categories you've entered a score for:";
      document.querySelectorAll('.category-input, .weight-input').forEach(el => el.disabled = inWhatIf);
      const addBtn = document.getElementById('add-row-btn');
      if (addBtn) addBtn.disabled = inWhatIf;
      // Course selection stays enabled even in What-If mode — loadCourse()
      // already resets back to Actual mode for whichever course gets
      // selected, so there's no need to force the student to back out of
      // What-If first just to look at a different class.
      document.getElementById('extract-btn').disabled = inWhatIf || !currentCourseName();
    }

    function onWeightsChanged() {
      recompute();
      scheduleSaveWeights();
    }

    function scheduleSaveWeights() {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveWeightsNow, 700);
    }

    async function saveWeightsNow() {
      const course = currentCourseName();
      if (!course) return;
      const weights = getRows()
        .filter(r => r.category && !isNaN(r.weight) && r.weight > 0)
        .map(r => ({ category: r.category, weight: r.weight }));
      try {
        await fetch('/save-grading-weights', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ course, weights }),
        });
      } catch (e) {  }
    }

    function recompute() {
      const rows = getRows().filter(r => r.category && !isNaN(r.weight) && r.weight > 0);
      const totalWeight = rows.reduce((sum, r) => sum + r.weight, 0);

      document.getElementById('weights-empty').style.display = rows.length ? 'none' : 'block';
      const totalLabel = document.getElementById('weight-total-label');
      if (rows.length) {
        const diff = totalWeight - 100;
        const off = Math.abs(diff) > 0.5;
        let note = '';
        if (off) {
          note = diff > 0
            ? ` — that's over 100%, double-check your categories.`
            : ` — that's under 100%, double-check your categories.`;
        }
        totalLabel.innerHTML = `Total weight: <span class="${off ? 'weight-off' : ''}">${totalWeight.toFixed(1)}%</span>${note}`;
      } else {
        totalLabel.textContent = '';
      }

      const scored = rows.filter(r => r.score !== null && !isNaN(r.score));
      const currentCard = document.getElementById('current-grade-card');
      const targetCard = document.getElementById('target-card');
      if (rows.length === 0) {
        currentCard.style.display = 'none';
        targetCard.style.display = 'none';
        return;
      }
      currentCard.style.display = 'block';
      // "What do I need on the rest?" only makes sense against your real,
      // saved scores — hide it while in What-If Scenario mode so it
      // doesn't solve against hypothetical numbers.
      targetCard.style.display = (mode === 'actual') ? 'block' : 'none';

      if (scored.length === 0) {
        document.getElementById('current-grade-value').textContent = '—';
        document.getElementById('current-grade-sub').textContent = 'Enter a score for at least one category.';
        document.getElementById('current-grade-formula').textContent = '';
      } else {
        const scoredWeight = scored.reduce((s, r) => s + r.weight, 0);
        const weighted = scored.reduce((s, r) => s + r.weight * r.score, 0);
        const currentGrade = scoredWeight > 0 ? weighted / scoredWeight : 0;
        document.getElementById('current-grade-value').textContent = currentGrade.toFixed(2) + '%';
        const remaining = rows.length - scored.length;
        document.getElementById('current-grade-sub').textContent = remaining > 0
          ? `Based on ${scoredWeight.toFixed(1)}% of the total weight — ${remaining} categor${remaining === 1 ? 'y' : 'ies'} still blank.`
          : 'Every category has a score entered — this is your full grade.';

        const terms = scored.map(r => `${r.score}×${r.weight}%`).join(' + ');
        document.getElementById('current-grade-formula').textContent =
          `Formula: (${terms}) ÷ ${scoredWeight.toFixed(1)}% = ${currentGrade.toFixed(2)}%`;
      }
    }

    function calculateTarget() {
      const target = parseFloat(document.getElementById('target-input').value);
      const resultEl = document.getElementById('target-result');
      if (isNaN(target)) {
        resultEl.className = 'target-result warn';
        resultEl.textContent = 'Enter a target grade first.';
        return;
      }
      const rows = getRows().filter(r => r.category && !isNaN(r.weight) && r.weight > 0);
      const totalWeight = rows.reduce((s, r) => s + r.weight, 0);
      if (totalWeight === 0) {
        resultEl.className = 'target-result warn';
        resultEl.textContent = 'Add at least one weighted category first.';
        return;
      }
      const scored = rows.filter(r => r.score !== null && !isNaN(r.score));
      const remaining = rows.filter(r => r.score === null || isNaN(r.score));
      const knownWeighted = scored.reduce((s, r) => s + r.weight * r.score, 0);
      const scoredWeight = scored.reduce((s, r) => s + r.weight, 0);
      const remainingWeight = remaining.reduce((s, r) => s + r.weight, 0);
      // Points already locked in, as a share of the FULL 100%-weight grade —
      // e.g. a 90 on a 20%-weight category has banked 18 of your final 100
      // possible points, regardless of what anything else scores.
      const bankedPoints = knownWeighted / 100;

      if (remainingWeight === 0) {
        const finalGrade = totalWeight > 0 ? knownWeighted / totalWeight : 0;
        resultEl.className = finalGrade >= target ? 'target-result' : 'target-result bad';
        resultEl.innerHTML = `Every category already has a score, so there's nothing left to solve for — your final grade is <strong>${finalGrade.toFixed(2)}%</strong>.`;
        return;
      }

      const needed = (target * totalWeight - knownWeighted) / remainingWeight;
      const remainingLabel = `${remaining.length} categor${remaining.length === 1 ? 'y' : 'ies'}`;
      resultEl.className = needed > 100 ? 'target-result bad' : 'target-result';
      if (needed > 100) {
        resultEl.innerHTML =
          `<strong>Not reachable from here.</strong> You've banked ${bankedPoints.toFixed(1)} out of your final 100 points ` +
          `from the ${scored.length} categor${scored.length === 1 ? 'y' : 'ies'} you've scored so far (${scoredWeight.toFixed(1)}% of your grade). ` +
          `That leaves ${remainingWeight.toFixed(1)}% of your grade riding on the ${remainingLabel} you haven't scored yet — ` +
          `and to reach ${target}% overall, you'd need to average <strong>${needed.toFixed(2)} points</strong> (out of 100) across ` +
          `those, which is over 100. A ${target}% overall isn't possible anymore given your current scores — figure out the highest overall grade actually within reach instead.`;
      } else if (needed < 0) {
        resultEl.innerHTML =
          `<strong>Already locked in.</strong> You've banked ${bankedPoints.toFixed(1)} out of your final 100 points ` +
          `from the ${scored.length} categor${scored.length === 1 ? 'y' : 'ies'} you've scored so far — that alone guarantees at least ${target}% overall, ` +
          `so even a 0 on everything else in the ${remainingLabel} left (${remainingWeight.toFixed(1)}% of your grade) won't drop you below it.`;
      } else {
        resultEl.innerHTML =
          `You've banked ${bankedPoints.toFixed(1)} out of your final 100 points so far, from the ${scored.length} ` +
          `categor${scored.length === 1 ? 'y' : 'ies'} you've already scored (${scoredWeight.toFixed(1)}% of your grade). ` +
          `That leaves ${remainingWeight.toFixed(1)}% of your grade riding on the ${remainingLabel} you haven't scored yet. ` +
          `To land at ${target}% overall, you need to average <strong>${needed.toFixed(2)} points</strong> (out of 100) across ` +
          `just those remaining categories — not across everything, just what's left.`;
      }
    }

    function showExtractMessage(text) {
      const el = document.getElementById('extract-msg');
      el.textContent = text;
      el.classList.add('show');
    }

    function hideExtractMessage() {
      document.getElementById('extract-msg').classList.remove('show');
    }

    async function loadCourse(course) {
      currentCourse = course;
      clearTable();
      hideExtractMessage();
      if (!course) {
        document.getElementById('weights-card').style.display = 'none';
        document.getElementById('current-grade-card').style.display = 'none';
        document.getElementById('target-card').style.display = 'none';
        document.getElementById('no-course-state').style.display = 'block';
        return;
      }
      let weights = [];
      try {
        const res = await fetch(`/grading-weights?course=${encodeURIComponent(course)}`);
        const data = await res.json();
        weights = data.weights || [];
      } catch (e) { console.error('grading-weights fetch error', e); }

      if (weights.length === 0) {
        document.getElementById('weights-card').style.display = 'none';
        document.getElementById('current-grade-card').style.display = 'none';
        document.getElementById('target-card').style.display = 'none';
        document.getElementById('no-course-state').style.display = 'block';
        return;
      }
      const scores = loadScores(course);
      weights.forEach(w => addRow(w.category, w.weight, scores[w.category] ?? ''));
      mode = 'actual';
      syncActualFromRows();
      whatIfScoresByCategory = { ...actualScoresByCategory };
      updateModeUI();
    }

    async function extractWeights() {
      const course = currentCourseName();
      if (!course) { alert('Enter a course first.'); return; }
      const btn = document.getElementById('extract-btn');
      btn.disabled = true;
      const originalLabel = btn.textContent;
      btn.textContent = 'Reading your course document…';
      hideExtractMessage();
      try {
        const res = await fetch('/extract-grading-weights', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ course }),
        });
        const data = await res.json();
        if (!res.ok) {
          showExtractMessage(data.error || 'Something went wrong extracting weights.');
          if (data.retry_after) btn.textContent = `Wait ${Math.ceil(data.retry_after)}s…`;
          return;
        }
        if (data.message) showExtractMessage(data.message);
        currentCourse = course;
        clearTable();
        const scores = loadScores(course);
        (data.weights || []).forEach(w => addRow(w.category, w.weight, scores[w.category] ?? ''));
        document.getElementById('weights-card').style.display = 'block';
        document.getElementById('no-course-state').style.display = 'none';
        mode = 'actual';
        syncActualFromRows();
        whatIfScoresByCategory = { ...actualScoresByCategory };
        updateModeUI();
      } catch (e) {
        console.error('extractWeights error', e);
        showExtractMessage('Network error — please try again.');
      } finally {
        btn.disabled = false;
        btn.textContent = originalLabel;
      }
    }

    document.getElementById('course-input').addEventListener('change', (e) => loadCourse(e.target.value.trim()));
