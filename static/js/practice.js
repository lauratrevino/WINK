    const QTYPE_NOTES = {
      flashcard: 'Short front/back cards for quick recall — self-graded.',
      review: 'Open-ended short-answer and multiple-choice questions — self-graded.',
      quiz: 'Multiple choice, auto-graded — pick an answer and see your score with an explanation immediately.',
      summary: 'A study summary of your material — not a question set, nothing to grade.',
      assessment_quiz: 'Determines how much you already know about this course, then builds a personalized study plan based on your current knowledge.',
    };

    function onQtypeChange() {
      const qtype = document.getElementById('qtype-input').value;
      const note = document.getElementById('qtype-note');
      const countGroup = document.getElementById('count-group');
      countGroup.style.display = (qtype === 'summary') ? 'none' : '';
      const text = QTYPE_NOTES[qtype] || '';
      note.textContent = text;
      note.style.display = text ? 'block' : 'none';
    }
    document.getElementById('course-input').addEventListener('input', onQtypeChange);
    onQtypeChange();

    let tempHandoutContent = null;

    async function attachTempHandout() {
      const input = document.getElementById('handout-input');
      const status = document.getElementById('attach-status');
      const file = input.files[0];
      if (!file) return;
      status.textContent = 'Reading ' + file.name + '…';
      const formData = new FormData();
      formData.append('file', file);
      formData.append('temporary', 'true');
      try {
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          status.textContent = data.error || 'Could not read that file.';
          tempHandoutContent = null;
          return;
        }
        tempHandoutContent = data.content;
        status.textContent = '✓ ' + file.name + ' attached for this session (not saved).';
      } catch (e) {
        status.textContent = 'Could not read that file.';
        tempHandoutContent = null;
      }
    }

    function renderQuestionCard(q) {
      const wrap = document.createElement('div');
      wrap.className = 'question-card';
      wrap.dataset.qid = q.id || '';
      const answerHtml = q.explanation
        ? `<div>${escapeHtml(q.answer)}</div><div class="q-explanation">${escapeHtml(q.explanation)}</div>`
        : `<div>${escapeHtml(q.answer)}</div>`;
      wrap.innerHTML = `
        <div class="q-text">${escapeHtml(q.question)}<span class="grade-badge"></span></div>
        <div class="q-self-check-hint">Think through your own answer, then reveal it below to check yourself.</div>
        <div class="q-answer" id="answer-${escapeHtml(String(q.id || Math.random()))}">${answerHtml}</div>
        <div class="q-actions"></div>`;

      const actions = wrap.querySelector('.q-actions');
      const revealBtn = document.createElement('button');
      revealBtn.className = 'btn btn-ghost btn-sm';
      revealBtn.textContent = 'Reveal answer';
      revealBtn.addEventListener('click', function() {
        wrap.querySelector('.q-answer').classList.toggle('shown');
      });
      actions.appendChild(revealBtn);

      if (q.id) {
        const correctBtn = document.createElement('button');
        correctBtn.className = 'btn btn-correct btn-sm';
        correctBtn.textContent = 'Got it right';
        correctBtn.addEventListener('click', function() { gradeQuestion(q.id, true, correctBtn); });
        const incorrectBtn = document.createElement('button');
        incorrectBtn.className = 'btn btn-incorrect btn-sm';
        incorrectBtn.textContent = 'Got it wrong';
        incorrectBtn.addEventListener('click', function() { gradeQuestion(q.id, false, incorrectBtn); });
        actions.appendChild(correctBtn);
        actions.appendChild(incorrectBtn);
      }
      return wrap;
    }

    function renderFlashcard(q) {
      const wrap = document.createElement('div');
      wrap.className = 'flashcard-wrap';
      wrap.dataset.qid = q.id || '';

      const card = document.createElement('div');
      card.className = 'flashcard';
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', 'Flip flashcard');
      card.innerHTML = `
        <div class="flashcard-inner">
          <div class="flashcard-face flashcard-front">
            <span class="flashcard-label">Question</span>
            <div class="flashcard-text">${escapeHtml(q.question)}</div>
            <span class="flashcard-hint">Tap to flip</span>
          </div>
          <div class="flashcard-face flashcard-back">
            <span class="flashcard-label">Answer</span>
            <div class="flashcard-text">${escapeHtml(q.answer)}${q.explanation ? `<div class="q-explanation" style="margin-top:8px;font-weight:400;font-size:13px;">${escapeHtml(q.explanation)}</div>` : ''}</div>
            <span class="flashcard-hint">Tap to flip back</span>
          </div>
        </div>`;
      const flip = () => card.classList.toggle('flipped');
      card.addEventListener('click', flip);
      card.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); }
      });
      wrap.appendChild(card);

      if (q.id) {
        const actions = document.createElement('div');
        actions.className = 'flashcard-actions';
        const correctBtn = document.createElement('button');
        correctBtn.className = 'btn btn-correct btn-sm';
        correctBtn.textContent = 'Got it right';
        correctBtn.addEventListener('click', function() { gradeQuestion(q.id, true, correctBtn); });
        const incorrectBtn = document.createElement('button');
        incorrectBtn.className = 'btn btn-incorrect btn-sm';
        incorrectBtn.textContent = 'Got it wrong';
        incorrectBtn.addEventListener('click', function() { gradeQuestion(q.id, false, incorrectBtn); });
        actions.appendChild(correctBtn);
        actions.appendChild(incorrectBtn);
        wrap.appendChild(actions);
      }
      return wrap;
    }

    function printSection(resultsId) {
      const source = document.getElementById(resultsId);
      if (!source || !source.innerHTML.trim()) return;
      const printArea = document.getElementById('print-area');
      printArea.innerHTML = source.innerHTML;
      window.print();
    }

    let quizTally = { correct: 0, total: 0 };
    // Only set for a freshly-generated Assessment Quiz session (not the spaced-
    // repetition review queue, which can mix questions from different courses/
    // sessions and wouldn't represent "how you just did" for one topic).
    let assessmentQuizTracking = null;

    function updateQuizTallyDisplay() {
      const el = document.getElementById('quiz-tally');
      if (!el) return;
      el.textContent = `Score: ${quizTally.correct}/${quizTally.total}`;
    }

    function renderQuizCard(q) {
      const wrap = document.createElement('div');
      wrap.className = 'question-card quiz-card';
      wrap.dataset.qid = q.id || '';
      const optionsHtml = (q.options || []).map((opt, i) =>
        `<button class="quiz-option" data-idx="${i}">${escapeHtml(opt)}</button>`
      ).join('');
      wrap.innerHTML = `
        <div class="q-text">${escapeHtml(q.question)}<span class="grade-badge"></span></div>
        <div class="quiz-options">${optionsHtml}</div>
        <div class="quiz-explanation"></div>`;

      wrap.querySelectorAll('.quiz-option').forEach(btn => {
        btn.addEventListener('click', () => submitQuizAnswer(q.id, parseInt(btn.dataset.idx, 10), wrap, q));
      });
      return wrap;
    }

    async function submitQuizAnswer(questionId, selectedIndex, cardEl, q) {
      if (!questionId) return;
      const buttons = cardEl.querySelectorAll('.quiz-option');
      buttons.forEach(b => b.disabled = true);
      try {
        const resp = await fetch('/grade-quiz-answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question_id: questionId, selected_index: selectedIndex }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          buttons.forEach(b => b.disabled = false);
          return;
        }
        buttons.forEach((b, i) => {
          if (i === data.correct_index) b.classList.add('correct');
          else if (i === selectedIndex && !data.correct) b.classList.add('incorrect');
        });
        const badge = cardEl.querySelector('.grade-badge');
        badge.innerHTML = data.correct
          ? '<span class="badge-graded badge-correct">Correct</span>'
          : '<span class="badge-graded badge-incorrect">Missed — see why below</span>';
        if (data.explanation) {
          const expl = cardEl.querySelector('.quiz-explanation');
          expl.textContent = data.explanation;
          expl.classList.add('shown');
        }
        quizTally.total += 1;
        if (data.correct) quizTally.correct += 1;
        updateQuizTallyDisplay();

        if (assessmentQuizTracking) {
          assessmentQuizTracking.results.push({
            question: (q && q.question) || '',
            correct: !!data.correct,
          });
          if (assessmentQuizTracking.results.length >= assessmentQuizTracking.total) {
            generateStudyPlan();
          }
        }
      } catch (e) {
        buttons.forEach(b => b.disabled = false);
      }
    }

    function renderSummaryMarkdown(text) {
      const lines = escapeHtml(text).split('\n');
      let html = '';
      let inList = false;
      for (let raw of lines) {
        const line = raw.trim();
        const bolded = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        if (line.startsWith('## ')) {
          if (inList) { html += '</ul>'; inList = false; }
          html += `<h3>${bolded.slice(3)}</h3>`;
        } else if (line.startsWith('- ') || line.startsWith('* ')) {
          if (!inList) { html += '<ul>'; inList = true; }
          html += `<li>${bolded.slice(2)}</li>`;
        } else if (line === '') {
          if (inList) { html += '</ul>'; inList = false; }
        } else {
          if (inList) { html += '</ul>'; inList = false; }
          html += `<p>${bolded}</p>`;
        }
      }
      if (inList) html += '</ul>';
      return html;
    }

    async function generateStudyPlan() {
      if (!assessmentQuizTracking) return;
      const { course, results, material } = assessmentQuizTracking;
      const container = document.getElementById('generate-results');
      const planWrap = document.createElement('div');
      planWrap.id = 'study-plan-wrap';
      planWrap.className = 'card';
      planWrap.style.marginTop = '16px';
      planWrap.innerHTML = '<h2>Your Personalized Study Plan</h2><div class="loading-spinner">Building your study plan…</div>';
      container.appendChild(planWrap);
      try {
        const resp = await fetch('/generate-study-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ course: course, results: results, material: material }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.plan) {
          planWrap.innerHTML = '<h2>Your Personalized Study Plan</h2><div class="empty-state">Couldn\'t generate a study plan this time — your quiz results above are still saved.</div>';
          return;
        }
        planWrap.innerHTML = '<h2>Your Personalized Study Plan</h2>' + renderSummaryMarkdown(data.plan);
      } catch (e) {
        planWrap.innerHTML = '<h2>Your Personalized Study Plan</h2><div class="empty-state">Couldn\'t generate a study plan this time — your quiz results above are still saved.</div>';
      }
    }

    async function generatePractice() {
      const course = document.getElementById('course-input').value.trim();
      const qtype = document.getElementById('qtype-input').value;
      const count = parseInt(document.getElementById('count-input').value, 10) || 8;
      const errorBox = document.getElementById('generate-error');
      const loading = document.getElementById('generate-loading');
      const results = document.getElementById('generate-results');
      const btn = document.getElementById('generate-btn');
      errorBox.style.display = 'none';
      results.innerHTML = '';
      document.getElementById('generate-print-row').style.display = 'none';
      assessmentQuizTracking = null;
      if (!course) {
        errorBox.textContent = 'Please enter a course.';
        errorBox.style.display = 'block';
        return;
      }
      btn.disabled = true;
      loading.style.display = 'block';
      try {
        const body = { course: course, count: count, qtype: qtype };
        if (tempHandoutContent) body.temp_material = tempHandoutContent;
        const resp = await fetch('/generate-practice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
          errorBox.textContent = data.error || 'Something went wrong. Please try again.';
          errorBox.style.display = 'block';
          return;
        }
        if (qtype === 'summary') {
          if (!data.summary) {
            results.innerHTML = '<div class="empty-state">No summary was generated — try a different course or attach more material.</div>';
            return;
          }
          const block = document.createElement('div');
          block.className = 'summary-block';
          block.innerHTML = renderSummaryMarkdown(data.summary);
          results.appendChild(block);
          document.getElementById('generate-print-row').style.display = 'flex';
          return;
        }
        if (!data.questions || !data.questions.length) {
          results.innerHTML = '<div class="empty-state">No questions were generated — try a different course or attach more material.</div>';
          return;
        }
        const isQuiz = (qtype === 'quiz' || qtype === 'assessment_quiz');
        const isFlashcard = (qtype === 'flashcard');
        if (isQuiz) {
          quizTally = { correct: 0, total: 0 };
          const tallyEl = document.createElement('div');
          tallyEl.id = 'quiz-tally';
          tallyEl.className = 'quiz-score-tally';
          tallyEl.textContent = `Score: 0/${data.questions.length}`;
          results.appendChild(tallyEl);
        }
        if (qtype === 'assessment_quiz') {
          assessmentQuizTracking = { course: course, results: [], total: data.questions.length, material: tempHandoutContent || '' };
        }
        if (isFlashcard) {
          const grid = document.createElement('div');
          grid.className = 'flashcard-grid';
          data.questions.forEach(q => grid.appendChild(renderFlashcard(q)));
          results.appendChild(grid);
        } else {
          data.questions.forEach(q => results.appendChild(isQuiz ? renderQuizCard(q) : renderQuestionCard(q)));
        }
        document.getElementById('generate-print-row').style.display = 'flex';
      } catch (e) {
        errorBox.textContent = 'Something went wrong. Please try again.';
        errorBox.style.display = 'block';
      } finally {
        btn.disabled = false;
        loading.style.display = 'none';
      }
    }

    async function gradeQuestion(questionId, correct, btnEl) {
      const card = btnEl.closest('.question-card') || btnEl.closest('.flashcard-wrap');
      try {
        const resp = await fetch('/practice-attempt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question_id: questionId, correct: correct }),
        });
        if (!resp.ok) return;
        card.classList.add('graded');
        const badge = card.querySelector('.grade-badge');
        if (badge) {
          badge.innerHTML = correct
            ? '<span class="badge-graded badge-correct">Correct</span>'
            : '<span class="badge-graded badge-incorrect">Review again soon</span>';
        }
        card.querySelectorAll('.btn-correct, .btn-incorrect').forEach(b => b.disabled = true);
      } catch (e) {  }
    }

    async function loadReview() {
      const loading = document.getElementById('review-loading');
      const results = document.getElementById('review-results');
      loading.style.display = 'block';
      results.innerHTML = '';
      document.getElementById('review-print-row').style.display = 'none';
      // The review queue can mix questions from different courses and sessions,
      // so it never counts toward a single-session study plan.
      assessmentQuizTracking = null;
      try {
        const resp = await fetch('/practice-review');
        const data = await resp.json();
        loading.style.display = 'none';
        if (!data.questions || !data.questions.length) {
          results.innerHTML = '<div class="empty-state">Nothing due for review right now — generate some study materials above, or check back later.</div>';
          return;
        }

        const quizQuestions = data.questions.filter(q => q.qtype === 'quiz' || q.qtype === 'assessment_quiz');
        if (quizQuestions.length) {
          quizTally = { correct: 0, total: 0 };
          const tallyEl = document.createElement('div');
          tallyEl.id = 'quiz-tally';
          tallyEl.className = 'quiz-score-tally';
          tallyEl.textContent = `Score: 0/${quizQuestions.length}`;
          results.appendChild(tallyEl);
        }
        const flashcardGrid = document.createElement('div');
        flashcardGrid.className = 'flashcard-grid';
        let hasFlashcards = false;
        data.questions.forEach(q => {
          const isQuiz = (q.qtype === 'quiz' || q.qtype === 'assessment_quiz');
          if (q.qtype === 'flashcard') {
            hasFlashcards = true;
            flashcardGrid.appendChild(renderFlashcard(q));
          } else {
            results.appendChild(isQuiz ? renderQuizCard(q) : renderQuestionCard(q));
          }
        });
        if (hasFlashcards) results.appendChild(flashcardGrid);
        document.getElementById('review-print-row').style.display = 'flex';
      } catch (e) {
        loading.style.display = 'none';
        results.innerHTML = '<div class="empty-state">Couldn\'t load your review queue — please refresh the page.</div>';
      }
    }

    loadReview();
  
