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

    (function setupOtherUniversityToggle() {
      const select = document.getElementById('university');
      const group = document.getElementById('other_university_group');
      const input = document.getElementById('other_university_name');
      if (!select || !group || !input) return;

      function sync() {
        const isOther = select.value === 'Other';
        group.style.display = isOther ? '' : 'none';
        input.required = isOther;
        if (!isOther) input.value = '';
      }
      select.addEventListener('change', sync);
      sync(); // in case the browser restored a previous value on reload
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

  
