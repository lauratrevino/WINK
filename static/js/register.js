    (function setupPasswordToggle() {
      const btn = document.getElementById('toggle-password-btn');
      const input = document.getElementById('password');
      const iconVisible = document.getElementById('eye-icon-visible');
      const iconHidden = document.getElementById('eye-icon-hidden');
      if (!btn || !input) return;
      btn.addEventListener('click', () => {
        const showing = input.type === 'text';
        input.type = showing ? 'password' : 'text';
        if (iconVisible && iconHidden) {
          iconVisible.style.display = showing ? '' : 'none';
          iconHidden.style.display = showing ? 'none' : '';
        }
        btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
      });
    })();

    (function captureTimezone() {
      // Populated immediately on page load rather than at submit time —
      // simpler, and it means the value is already sitting in the form
      // by the time any submit path runs, with nothing else that needs
      // to remember to set it. Falls back to leaving the field blank
      // (never throws) if the browser genuinely can't report one; the
      // server already treats a missing/invalid timezone as "unknown"
      // and falls back to Mountain Time itself.
      const field = document.getElementById('register-timezone-field');
      if (!field) return;
      try {
        field.value = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      } catch (e) {
        field.value = '';
      }
    })();

    (function setupUniversityCombobox() {
      const input = document.getElementById('university-input');
      const listbox = document.getElementById('university-listbox');
      const otherGroup = document.getElementById('other_university_group');
      const otherInput = document.getElementById('other_university_name');
      const data = (window.WINK_REGISTER_DATA && window.WINK_REGISTER_DATA.universities) || [];
      if (!input || !listbox || !otherGroup || !otherInput) return;

      const MAX_RESULTS = 50;
      let activeIndex = -1;   // index into the CURRENTLY RENDERED <li> options, not into `data`
      let options = [];       // the <li> elements currently rendered

      function syncOtherField() {
        const isOther = input.value === 'Other';
        otherGroup.style.display = isOther ? '' : 'none';
        otherInput.required = isOther;
        if (!isOther) otherInput.value = '';
      }

      function closeListbox() {
        listbox.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-activedescendant', '');
        activeIndex = -1;
        options = [];
      }

      function setActive(index) {
        if (activeIndex >= 0 && options[activeIndex]) {
          options[activeIndex].classList.remove('is-active');
        }
        activeIndex = index;
        if (activeIndex >= 0 && options[activeIndex]) {
          const opt = options[activeIndex];
          opt.classList.add('is-active');
          if (opt.scrollIntoView) opt.scrollIntoView({ block: 'nearest' });
          input.setAttribute('aria-activedescendant', opt.id);
        } else {
          input.setAttribute('aria-activedescendant', '');
        }
      }

      function selectValue(value) {
        input.value = value;
        syncOtherField();
        closeListbox();
      }

      function renderOptions() {
        const query = input.value.trim().toLowerCase();
        listbox.textContent = ''; // clear via DOM, not innerHTML — nothing untrusted here anyway,
                                   // but this avoids ever re-parsing university names as markup
        options = [];

        if (!query) { closeListbox(); return; }

        const matches = data.filter(function(name) {
          return name.toLowerCase().indexOf(query) !== -1;
        }).slice(0, MAX_RESULTS);

        if (matches.length === 0) {
          const empty = document.createElement('li');
          empty.className = 'uni-combobox-empty';
          empty.setAttribute('role', 'presentation');
          empty.textContent = 'No matches — check the spelling, or type "Other" if your school isn\'t listed.';
          listbox.appendChild(empty);
          listbox.hidden = false;
          input.setAttribute('aria-expanded', 'true');
          return;
        }

        matches.forEach(function(name, i) {
          const li = document.createElement('li');
          li.id = 'uni-opt-' + i;
          li.className = 'uni-combobox-option';
          li.setAttribute('role', 'option');
          li.setAttribute('aria-selected', String(name === input.value));
          li.textContent = name;
          // mousedown (not click) + preventDefault keeps focus on the
          // input the whole time — otherwise the input's blur handler
          // would close the listbox before the click ever registers.
          li.addEventListener('mousedown', function(e) {
            e.preventDefault();
            selectValue(name);
          });
          listbox.appendChild(li);
          options.push(li);
        });

        listbox.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        setActive(0); // auto-highlight the top match so Enter is immediately useful
      }

      input.addEventListener('input', renderOptions);

      input.addEventListener('keydown', function(e) {
        if (listbox.hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
          renderOptions();
          return;
        }
        if (listbox.hidden) return;

        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (options.length) setActive(Math.min(activeIndex + 1, options.length - 1));
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (options.length) setActive(Math.max(activeIndex - 1, 0));
        } else if (e.key === 'Enter') {
          if (activeIndex >= 0 && options[activeIndex]) {
            e.preventDefault();
            selectValue(options[activeIndex].textContent);
          }
        } else if (e.key === 'Escape') {
          closeListbox();
        }
      });

      // Clicking anywhere outside closes the list (mousedown-preventDefault
      // above already handles clicks on the options themselves).
      document.addEventListener('mousedown', function(e) {
        if (e.target !== input && !listbox.contains(e.target)) closeListbox();
      });

      input.addEventListener('blur', function() {
        // A tiny delay so an in-progress option mousedown (which calls
        // selectValue synchronously before blur fires) isn't undone by
        // this — belt-and-suspenders alongside preventDefault above.
        setTimeout(closeListbox, 0);
      });

      syncOtherField(); // in case the browser restored a previous value on reload
    })();

    (function setupTermsGate() {
      const form = document.getElementById('register-form');
      const overlay = document.getElementById('terms-overlay');
      const termsCheck = document.getElementById('terms-check');
      const researchCheck = document.getElementById('research-check');
      const agreeBtn = document.getElementById('terms-agree');
      const cancelBtn = document.getElementById('terms-cancel');
      if (!form || !overlay || !termsCheck || !researchCheck || !agreeBtn) return;

      let approved = false;
      let submitting = false;
      const agreeBtnDefaultText = agreeBtn.textContent;

      function updateAgreeButton() {
        if (submitting) return; // don't re-enable mid-submit
        agreeBtn.disabled = !(termsCheck.checked && researchCheck.checked);
      }

      termsCheck.addEventListener('change', updateAgreeButton);
      researchCheck.addEventListener('change', updateAgreeButton);

      form.addEventListener('submit', function(e) {
        if (approved) return;
        e.preventDefault();
        if (!form.reportValidity()) return;
        overlay.classList.add('open');
        termsCheck.focus();
      });

      agreeBtn.addEventListener('click', function() {
        if (submitting) return; // guard against double-clicks firing two submissions
        if (!(termsCheck.checked && researchCheck.checked)) return;

        submitting = true;
        approved = true;
        agreeBtn.disabled = true;
        agreeBtn.textContent = 'Creating your account…';
        overlay.classList.remove('open');

        const verifyMsg = document.getElementById('verify-email-msg');

        // Submitted via fetch (not a plain form POST/redirect) so we can show
        // the "check your email to verify" confirmation right here, then move
        // to the Documents page ourselves — instead of a full-page navigation
        // that can flash an intermediate page while the browser follows the
        // server's redirect chain.
        fetch(form.action, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: new FormData(form)
        })
          .then(function(res) {
            return res.json().then(function(data) { return { ok: res.ok, data: data }; });
          })
          .then(function(result) {
            if (result.ok && result.data && result.data.success) {
              if (verifyMsg) {
                verifyMsg.textContent = '✅ Account created! We just sent a verification link to '
                  + (result.data.email || 'your email address') + ' — validate it whenever you '
                  + 'get a chance. Taking you to your Documents now…';
                verifyMsg.style.display = 'block';
              }
              window.location.href = result.data.redirect || '/documents';
              return;
            }
            throw new Error((result.data && result.data.error) || 'Something went wrong submitting your information. Please try again.');
          })
          .catch(function(err) {
            submitting = false;
            approved = false;
            agreeBtn.disabled = false;
            agreeBtn.textContent = agreeBtnDefaultText;
            alert(err.message || 'Something went wrong submitting your information. Please try again, or use a different browser if this keeps happening.');
          });

        // Safety net: if the page hasn't navigated away within a few
        // seconds (e.g. a network stall), restore the button instead of
        // leaving the student staring at a permanently disabled,
        // "Creating your account…" button with no way to retry.
        setTimeout(function() {
          if (submitting) {
            submitting = false;
            approved = false;
            agreeBtn.disabled = !(termsCheck.checked && researchCheck.checked);
            agreeBtn.textContent = agreeBtnDefaultText;
          }
        }, 8000);
      });

      cancelBtn.addEventListener('click', function() {
        overlay.classList.remove('open');
      });
    })();

  
