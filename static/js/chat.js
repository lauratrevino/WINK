    // The nonce this page was rendered with (Jinja's csp_nonce(), passed
    // through from templates/chat.html's small inline bridge script as
    // window.WINK_CHAT_DATA.cspNonce) — same value the response's CSP
    // header used for BOTH script-src-elem and style-src-elem (see
    // _set_csp_nonce() / set_security_headers() in wink/__init__.py).
    // NOTE: this file is loaded via <script src>, not inline, so
    // document.currentScript.nonce is empty here — the external tag
    // itself has no nonce attribute (none needed for 'self'-sourced
    // scripts) and doesn't inherit the page's per-request nonce. Reading
    // it from WINK_CHAT_DATA instead is what actually carries the real
    // value through.
    const CSP_NONCE = (window.WINK_CHAT_DATA && window.WINK_CHAT_DATA.cspNonce) || '';

    // Handles broken images from markdown embeds (see formatMessage()'s
    // wink-md-image class below) — 'error' events don't bubble, so this
    // has to be attached with capture:true on an ancestor that exists from
    // page load, rather than on the images themselves (which don't exist
    // yet, and are inserted via innerHTML so they'd have no listener
    // attached anyway). One listener here covers every such image for the
    // life of the page, including ones not inserted yet.
    document.addEventListener('error', function(e) {
      if (e.target && e.target.classList && e.target.classList.contains('wink-md-image')) {
        const wrap = e.target.closest('.wink-embed');
        if (wrap) wrap.style.display = 'none';
      }
    }, true);

    const TEXT_SCALE_STEPS = [0.85, 1, 1.15, 1.3, 1.45, 1.6];
    function applyTextScale(scale) {
      document.documentElement.style.setProperty('--wink-text-scale', scale);
      localStorage.setItem('winkTextScale', String(scale));
    }
    function adjustTextSize(direction) {
      const current = parseFloat(localStorage.getItem('winkTextScale')) || 1;
      let idx = TEXT_SCALE_STEPS.reduce((closest, v, i) =>
        Math.abs(v - current) < Math.abs(TEXT_SCALE_STEPS[closest] - current) ? i : closest, 0);
      idx = Math.max(0, Math.min(TEXT_SCALE_STEPS.length - 1, idx + direction));
      applyTextScale(TEXT_SCALE_STEPS[idx]);
    }
    (function restoreTextScale() {
      const saved = parseFloat(localStorage.getItem('winkTextScale'));
      if (saved) applyTextScale(saved);
    })();

    function readLatestAnswerAloud() {
      const btn = document.getElementById('read-aloud-btn');
      if (window.speechSynthesis.speaking) {
        window.speechSynthesis.cancel();
        btn.textContent = '🔊 Read latest answer';
        return;
      }
      // Excludes #typing-indicator: it's a permanent ".msg wink" element
      // that every real message is inserted BEFORE (see addMessage()'s
      // insertBefore), so it's always last in the DOM. Without this
      // exclusion, this always grabbed the typing indicator's bubble —
      // which only ever contains three empty ".typing-dot" divs, no text —
      // instead of the actual latest answer, which is why this silently
      // "read" nothing no matter what WINK had actually said.
      const winkMessages = document.querySelectorAll('.msg.wink:not(#typing-indicator) .msg-bubble');
      if (winkMessages.length === 0) return;
      const latest = winkMessages[winkMessages.length - 1];
      const utterance = new SpeechSynthesisUtterance(latest.textContent);
      const voice = getSelectedVoice();
      if (voice) utterance.voice = voice;
      // A slightly higher pitch and a touch more pace read as more upbeat
      // and enthusiastic than the flat 1.0/1.0 default, without tipping
      // into sounding sped-up or unnatural. SpeechSynthesisUtterance allows
      // pitch 0–2 and rate 0.1–10; these stay well inside the natural range.
      utterance.pitch = 1.15;
      utterance.rate = 1.05;
      utterance.onend = () => { btn.textContent = '🔊 Read latest answer'; };
      utterance.onerror = () => { btn.textContent = '🔊 Read latest answer'; };
      btn.textContent = '⏹ Stop reading';
      window.speechSynthesis.speak(utterance);
    }

    // The browser/OS supplies whatever voices it has installed — WINK
    // itself has no built-in voice — and the default one many browsers
    // pick first is often a low-quality robotic-sounding option even when
    // much better ones (e.g. "Google US English", or Edge's "* Online
    // (Natural)" voices) are available on the same device. This lets a
    // student pick a better-sounding one once and have it remembered.
    let availableVoices = [];

    function scoreVoice(v) {
      // Higher is better. Prefer English voices, and within those prefer
      // ones whose name suggests a modern/neural engine over a generic
      // legacy one — a rough heuristic since browsers don't expose an
      // actual "quality" field, but names like "Natural", "Neural", or
      // "Google" reliably correlate with the better-sounding options in
      // practice across Chrome, Edge, and Safari. Also biased toward
      // typically female-sounding voice names, since that's the requested
      // default — there's no actual gender field on a SpeechSynthesisVoice,
      // so this is name-matching against the common female voice names
      // shipped by each platform's TTS engine.
      let score = 0;
      if (/^en/i.test(v.lang)) score += 10;
      if (/en-US/i.test(v.lang)) score += 2;
      if (/natural|neural/i.test(v.name)) score += 8;
      if (/google/i.test(v.name)) score += 4;
      if (v.localService) score += 1; // local voices avoid network latency/failures
      if (/female|\b(samantha|zira|jenny|aria|ava|emma|salli|joanna|kendra|kimberly|susan|karen|moira|tessa|fiona|victoria|allison|serena|shelley|nicky)\b/i.test(v.name)) score += 6;
      return score;
    }

    function populateVoiceOptions() {
      availableVoices = window.speechSynthesis.getVoices();
      const select = document.getElementById('voice-select');
      if (!availableVoices.length) { select.style.display = 'none'; return; }

      const sorted = [...availableVoices].sort((a, b) => scoreVoice(b) - scoreVoice(a));
      const saved = localStorage.getItem('winkVoiceURI');
      select.innerHTML = sorted.map(v =>
        `<option value="${escapeHtml(v.voiceURI)}"${v.voiceURI === saved ? ' selected' : ''}>${escapeHtml(v.name)} (${escapeHtml(v.lang)})</option>`
      ).join('');

      // First time on this device: nothing saved yet, so default the
      // dropdown itself to the best-scoring voice rather than leaving it
      // on the browser's own (often lower-quality) first-in-list choice.
      if (!saved && sorted.length) select.value = sorted[0].voiceURI;

      select.style.display = '';
    }

    function getSelectedVoice() {
      const select = document.getElementById('voice-select');
      const chosenURI = select.value;
      return availableVoices.find(v => v.voiceURI === chosenURI) || null;
    }

    function saveVoiceChoice() {
      const select = document.getElementById('voice-select');
      if (select.value) localStorage.setItem('winkVoiceURI', select.value);
    }

    populateVoiceOptions();
    // Chrome (and some others) load the voice list asynchronously — it's
    // frequently empty on the very first call above — so this re-populates
    // once the real list is actually ready.
    if ('onvoiceschanged' in window.speechSynthesis) {
      window.speechSynthesis.onvoiceschanged = populateVoiceOptions;
    }


    const STUDENT_FIRST_NAME = window.WINK_CHAT_DATA.studentFirstName;

    let messages = [];
    let currentConversationId = null;
    let currentTempDoc = null;
    let photoMarkerId = 0;
    let diagramMarkerId = 0;

    const WELCOME_HTML = `<div class="welcome-msg" id="welcome">
      <h2>Let&rsquo;s work on your success&mdash;together!</h2>
      <p class="welcome-sub">I can help you:</p>
      <div class="welcome-cards">
        <div class="welcome-card">
          <div class="welcome-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-4"/></svg></div>
          <div class="welcome-card-title">Plan Your Semester</div>
          <div class="welcome-card-desc">Map out your courses, deadlines, and goals.</div>
        </div>
        <div class="welcome-card">
          <div class="welcome-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg></div>
          <div class="welcome-card-title">Stay on Track</div>
          <div class="welcome-card-desc">Get reminders, prioritize tasks, and build good habits.</div>
        </div>
        <div class="welcome-card">
          <div class="welcome-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z"/></svg></div>
          <div class="welcome-card-title">Understand Your Course</div>
          <div class="welcome-card-desc">Break down topics, review concepts, and practice.</div>
        </div>
        <div class="welcome-card">
          <div class="welcome-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4h12v3a6 6 0 0 1-12 0z"/><path d="M6 5H3a1 1 0 0 0-1 1 5 5 0 0 0 4 5M18 5h3a1 1 0 0 1 1 1 5 5 0 0 1-4 5"/><path d="M12 15v3m-4 3h8"/></svg></div>
          <div class="welcome-card-title">Reach Your Goals</div>
          <div class="welcome-card-desc">Celebrate wins and keep growing.</div>
        </div>
      </div>
      <div class="welcome-prompt-box">
        <span class="welcome-prompt-icon" aria-hidden="true">💬</span>
        <div>
          <div class="welcome-prompt-title">What would you like to work on today?</div>
          <div class="welcome-prompt-sub">Ask me anything or choose a topic above.</div>
        </div>
      </div>
    </div>`;
    const TYPING_HTML = `<div class="msg wink typing" id="typing-indicator">
      <div class="msg-avatar wink-av">🎓</div>
      <div class="msg-bubble">
        <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
      </div>
    </div>`;

    function setPrompt(text) {
      document.getElementById('chat-input').value = text;
      document.getElementById('chat-input').focus();
    }

    function autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }

    function handleKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    }


    // Inline markdown on one already-escaped line.
    function renderInline(t) {
      return t
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*\w])\*(?!\s)([^*\n]+?)\*(?!\w)/g, '$1<em>$2</em>')
        .replace(/(^|[^_\w])_(?!\s)([^_\n]+?)_(?!\w)/g, '$1<em>$2</em>');
    }

    function renderBlocks(src) {
      const lines = src.replace(/\r/g, '').split('\n');
      const out = [];
      let para = [];
      const flush = () => {
        if (para.length) { out.push('<p>' + para.map(renderInline).join('<br>') + '</p>'); para = []; }
      };
      const isRow = l => /^\s*\|.*\|\s*$/.test(l);
      const isDivider = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
      const cells = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => renderInline(c.trim()));
      let i = 0;
      while (i < lines.length) {
        const line = lines[i];
        let m;
        if (!line.trim()) { flush(); i++; continue; }
        // Stashed embed (diagram/map/image) on its own line: emit as-is
        if (/^\s*\u0000EMBED\d+\u0000\s*$/.test(line)) { flush(); out.push(line.trim()); i++; continue; }
        // Fenced code block (non-mermaid; mermaid was stashed earlier)
        if (/^\s*```/.test(line)) {
          flush(); const buf = []; i++;
          while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
          i++; out.push('<pre class="wink-code"><code>' + buf.join('\n') + '</code></pre>'); continue;
        }
        if ((m = line.match(/^\s*(#{1,4})\s+(.+?)\s*#*\s*$/))) {
          flush(); const lvl = m[1].length + 1; // # -> h2 ... #### -> h5
          out.push(`<h${lvl} class="wink-h">${renderInline(m[2])}</h${lvl}>`); i++; continue;
        }
        if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push('<hr class="wink-hr">'); i++; continue; }
        if (isRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
          flush();
          const head = cells(line); i += 2;
          const rows = [];
          while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i++; }
          out.push('<div class="wink-table-wrap"><table class="wink-table"><thead><tr>' +
            head.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>' +
            rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
            '</tbody></table></div>');
          continue;
        }
        if (/^\s*&gt;\s?/.test(line)) {
          flush(); const buf = [];
          while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) { buf.push(renderInline(lines[i].replace(/^\s*&gt;\s?/, ''))); i++; }
          out.push('<blockquote class="wink-quote">' + buf.join('<br>') + '</blockquote>'); continue;
        }
        const ul = /^(\s*)[-*•]\s+(.+)$/, ol = /^(\s*)\d+[.)]\s+(.+)$/;
        if (ul.test(line) || ol.test(line)) {
          flush();
          const ordered = ol.test(line) && !ul.test(line);
          const re = ordered ? ol : ul;
          const items = [];
          while (i < lines.length && (re.test(lines[i]) || (items.length && /^\s{2,}\S/.test(lines[i]) && !ul.test(lines[i]) && !ol.test(lines[i])))) {
            const mm = lines[i].match(re);
            if (mm) items.push({ indent: mm[1].length >= 2, text: renderInline(mm[2]) });
            else items[items.length - 1].text += '<br>' + renderInline(lines[i].trim());
            i++;
          }
          const tag = ordered ? 'ol' : 'ul';
          out.push(`<${tag}>` + items.map(it => `<li${it.indent ? ' class="wink-li-sub"' : ''}>${it.text}</li>`).join('') + `</${tag}>`);
          continue;
        }
        para.push(line); i++;
      }
      flush();
      return out.join('');
    }

    function formatMessage(text) {
      // Convert markdown-ish formatting to HTML
      let html = escapeHtml(text);

      // Anything we render as real markup (maps, images) gets stashed behind a
      // placeholder token first, so later passes (URL linkifier, paragraph
      // wrapping) can't reach inside their src="..." attributes and corrupt them.
      const embeds = [];
      const stash = (snippet) => {
        embeds.push(snippet);
        return `\u0000EMBED${embeds.length - 1}\u0000`;
      };

      // Embedded maps: [[map: some place or address]] -> live Google Maps embed
      // referrerpolicy="no-referrer" (not "no-referrer-when-downgrade", the
      // previous value): a map query necessarily sends the place/address
      // itself to Google as a URL parameter — that's inherent to a live
      // embed and can't be avoided without giving up the embed entirely —
      // but there's no reason to ALSO hand Google the referring WINK page
      // URL on every embed. This stops that second, avoidable leak.
      html = html.replace(/\[\[\s*map:\s*([^\]]+)\]\]/gi, (m, query) => {
        const q = encodeURIComponent(query.trim());
        return stash(`<div class="wink-embed wink-map"><iframe src="https://www.google.com/maps?q=${q}&output=embed" loading="lazy" referrerpolicy="no-referrer" allowfullscreen></iframe></div>`);
      });
      // Photo of a real person/place/notable subject: [[image: Heather Wilson]]
      // Resolved after render via a free Wikipedia lookup — see resolvePhotoMarkers().
      html = html.replace(/\[\[\s*image:\s*([^\]]+)\]\]/gi, (m, query) => {
        const id = `wink-photo-${++photoMarkerId}`;
        return stash(`<div class="wink-embed wink-photo-pending" id="${id}" data-query="${query.trim().replace(/"/g,'&quot;')}"><span class="wink-photo-loading">🔎 Looking for a photo…</span></div>`);
      });
      // Diagrams: ```mermaid fenced code blocks -> an actual rendered
      // diagram via the self-hosted mermaid.js — resolved after insert,
      // same async pattern as resolvePhotoMarkers() (see
      // resolveDiagramMarkers() below). text was already escapeHtml()'d
      // above, so decode entities back to real characters before mermaid
      // parses them, then safely re-escape into the data attribute.
      html = html.replace(/```mermaid\n([\s\S]*?)```/g, (m, code) => {
        const id = `wink-diagram-${++diagramMarkerId}`;
        const raw = code.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
        return stash(`<div class="wink-embed wink-diagram-pending" id="${id}" data-mermaid="${raw.replace(/"/g,'&quot;')}"><span class="wink-photo-loading">📊 Rendering diagram…</span></div>`);
      });
      // Images: standard markdown ![alt](url) -> an actual rendered image.
      // No inline onerror attribute here on purpose — a dynamically-built
      // attribute like that can't be covered by the build-time CSP hash
      // scan (which only sees what's literally in the .html template
      // files), so it would need 'unsafe-inline' to function. Handled
      // instead by a single delegated 'error' listener set up once below.
      html = html.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, (m, alt, url) => {
        return stash(`<div class="wink-embed wink-image"><img src="${url}" alt="${alt.replace(/"/g,'&quot;')}" loading="lazy" class="wink-md-image"></div>`);
      });

      // Source citations: the system prompt instructs the model to name
      // the actual uploaded file it drew an answer from (e.g.
      // "Spring2026Syllabus.docx"), not just say "your documents" —
      // highlight any such filename distinctly so a student can see at a
      // glance which answers are grounded in something they uploaded.
      // Runs after the map/image stashing above so it can't accidentally
      // match inside an already-stashed embed's URL.
      //
      // NOTE (accuracy caveat): this is a text match on the model's own
      // output after the fact — it confirms the model *wrote* a filename,
      // not that the retrieved passage actually came from that file or
      // supports the claim next to it. The title attribute says so
      // directly rather than implying a verified citation; a real
      // passage-level citation system (chunk id + page → the actual
      // excerpt) would be a stronger version of this.
      // NOTE (URL exclusion): \S+ is greedy and URL-unaware, so a
      // filename-shaped tail inside a URL (e.g.
      // "https://example.edu/handouts/notes.pdf") gets captured whole by
      // this pattern — the "://" ends up INSIDE the match itself, not
      // before it, so a lookbehind can't exclude it. Filtering the
      // matched string post-hoc (skip anything containing "://" or
      // starting with "/") is the same approach as the backend's
      // _extract_citation_filenames() in wink/blueprints/chat.py — the
      // two are meant to agree on what counts as a citation.
      html = html.replace(/\b(\S+\.(?:docx|pdf|pptx|xlsx|txt|csv|md|rtf|png|jpe?g|gif|webp|bmp|tiff?|heic|heif|mp4|mov|m4v|webm|avi|mkv))\b/gi, (m, filename) => {
        if (filename.includes('/')) return m;
        return `<cite class="wink-citation" title="Mentioned by name — not an independently verified citation">${filename}</cite>`;
      });

      // Block-level markdown: headings, tables, ordered/unordered lists,
      // horizontal rules, blockquotes, fenced code. Runs on already-escaped
      // text, so it can only ever emit the tags written here.
      html = renderBlocks(html);
      // URLs (anything left over that wasn't part of a stashed embed)
      html = html.replace(/(https?:\/\/[^\s<>"]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');

      // Swap the real markup back in for its placeholder tokens
      html = html.replace(/\u0000EMBED(\d+)\u0000/g, (m, i) => embeds[parseInt(i, 10)]);
      return html;
    }

    // Resolves any [[image: subject]] placeholders inside a container into a
    // real photo, using Wikipedia's free public API (no key, no server call —
    // works entirely client-side). Falls back to hiding the placeholder if
    // the subject has no Wikipedia page or no photo on it.
    async function resolvePhotoMarkers(container) {
      const pending = container.querySelectorAll('.wink-photo-pending');
      for (const el of pending) {
        const query = el.dataset.query;
        try {
          const res = await fetch(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(query)}`, { referrerPolicy: 'no-referrer' });
          if (!res.ok) throw new Error('not found');
          const data = await res.json();
          const src = data.thumbnail && data.thumbnail.source;
          if (!src || !/^https:\/\//.test(src)) throw new Error('no photo available');
          el.classList.remove('wink-photo-pending');
          el.classList.add('wink-photo');
          el.innerHTML = `<img src="${escapeHtml(src)}" alt="${escapeHtml(query)}" loading="lazy">
            <div class="wink-photo-credit">Photo via Wikipedia</div>`;
        } catch (e) {
          el.innerHTML = `<span class="wink-photo-missing">📷 No photo found for "${escapeHtml(query)}"</span>`;
          el.classList.remove('wink-photo-pending');
        }
      }
    }

    let mermaidInitDone = false;
    let _mermaidLoadPromise = null;
    // Loads mermaid.min.js (~3.5MB) on demand the FIRST time a response
    // actually contains a ```mermaid block, instead of on every visit to
    // this page regardless of whether it's ever used. script-src-elem
    // includes 'self' in the CSP (see wink/__init__.py), so a same-origin
    // <script src> injected here is allowed without needing a nonce.
    // Cached in a module-level promise so a second diagram in the same
    // session reuses the same in-flight/completed load rather than
    // injecting the tag again.
    function ensureMermaidLoaded() {
      if (typeof mermaid !== 'undefined') return Promise.resolve();
      if (_mermaidLoadPromise) return _mermaidLoadPromise;
      _mermaidLoadPromise = new Promise((resolve) => {
        const script = document.createElement('script');
        script.src = '/static/mermaid.min.js';
        script.onload = () => resolve();
        script.onerror = () => resolve(); // resolved either way — resolveDiagramMarkers() below still checks typeof mermaid and shows a graceful fallback if it never actually loaded
        document.head.appendChild(script);
      });
      return _mermaidLoadPromise;
    }

    async function resolveDiagramMarkers(container) {
      const pending = container.querySelectorAll('.wink-diagram-pending');
      if (pending.length === 0) return;
      // Kick off the on-demand load (no-op if already loaded/loading),
      // then wait briefly for it — a fast AI response with a diagram can
      // arrive before the library has finished loading.
      await ensureMermaidLoaded();
      if (typeof mermaid === 'undefined') {
        const deadline = Date.now() + 5000;
        while (typeof mermaid === 'undefined' && Date.now() < deadline) {
          await new Promise(r => setTimeout(r, 150));
        }
      }
      if (typeof mermaid === 'undefined') {
        pending.forEach(el => {
          el.innerHTML = '<span class="wink-photo-missing">📊 Diagram rendering isn\'t available right now.</span>';
          el.classList.remove('wink-diagram-pending');
        });
        return;
      }
      if (!mermaidInitDone) {
        // suppressErrorRendering: without this, a syntax error in the
        // AI-generated diagram doesn't throw at all — mermaid.render()
        // resolves normally with ITS OWN big "Syntax error in text /
        // mermaid version ..." SVG (bomb icon included) as the returned
        // svg string, so the catch block below never fires and that error
        // graphic gets inserted straight into the chat. This flag makes a
        // bad diagram actually reject/throw instead, so the existing
        // catch below can show the small, graceful fallback message.
        // UTEP navy/orange instead of mermaid's default gray.
        mermaid.initialize({
          startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
          theme: 'base',
          // Plain SVG text labels (not HTML) so diagrams can also be
          // turned into images for the Word download.
          flowchart: { htmlLabels: false },
          // Always drawn at a fixed readable width; on phones the diagram
          // box scrolls sideways instead of squeezing the chart.
          gantt: { useMaxWidth: false, useWidth: 880, fontSize: 12, barHeight: 22, barGap: 6 },
          themeVariables: {
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif',
            primaryColor: '#e8eef7', primaryTextColor: '#002855', primaryBorderColor: '#002855',
            secondaryColor: '#fff3e6', tertiaryColor: '#f4f6fb', lineColor: '#002855',
            taskBkgColor: '#002855', taskBorderColor: '#002855', taskTextColor: '#ffffff',
            taskTextOutsideColor: '#002855', taskTextLightColor: '#ffffff', taskTextDarkColor: '#002855',
            activeTaskBkgColor: '#FF8200', activeTaskBorderColor: '#cc6800',
            doneTaskBkgColor: '#6b7a99', doneTaskBorderColor: '#6b7a99',
            critBkgColor: '#FF8200', critBorderColor: '#cc6800',
            sectionBkgColor: '#f4f6fb', altSectionBkgColor: '#ffffff', sectionBkgColor2: '#fff3e6',
            gridColor: '#dde3f0', todayLineColor: '#FF8200'
          }
        });
        mermaidInitDone = true;
      }
      for (const el of pending) {
        // Strip any %%{init: ...}%% directive the diagram source might
        // contain — mermaid gives a per-diagram directive priority over
        // the theme set in mermaid.initialize() below, so a diagram that
        // happens to specify (or was copied from a tutorial that
        // specifies) {'theme':'dark'} would render with a black
        // background regardless of this app's own light theme.
        const source = (el.dataset.mermaid || '').replace(/%%\{[\s\S]*?\}%%/g, '');
        try {
          const { svg: rawSvg } = await mermaid.render('wink-mermaid-' + el.id, source);
          // mermaid embeds its own <style> block inside the SVG to size
          // and color everything (task bars, grid lines, section
          // backgrounds, text) — without it, those elements fall back to
          // raw SVG defaults: an unstyled rect defaults to a BLACK fill,
          // and text with no font-size rule renders at a large default
          // size. This page's CSP requires style-src-elem to carry a
          // matching nonce (see wink/__init__.py), and mermaid has no way
          // to know that nonce, so its <style> tag was being silently
          // dropped by the browser — producing exactly that oversized,
          // black-box rendering instead of a normal diagram. Stamping the
          // page's own nonce onto it lets the browser allow it.
          const svg = CSP_NONCE
            ? rawSvg.replace(/<style(\s[^>]*)?>/g, (m, attrs) => `<style nonce="${CSP_NONCE}"${attrs || ''}>`)
            : rawSvg;
          el.innerHTML = svg;
          el.classList.remove('wink-diagram-pending');
          el.classList.add('wink-diagram');
        } catch (e) {
          el.innerHTML = '<span class="wink-photo-missing">📊 Couldn\'t render that diagram.</span>';
          el.classList.remove('wink-diagram-pending');
        }
      }
    }

    function addMessage(role, text) {
      const welcome = document.getElementById('welcome');
      if (welcome) welcome.remove();

      const wrap = document.getElementById('messages');
      const typing = document.getElementById('typing-indicator');

      const div = document.createElement('div');
      div.className = `msg ${role === 'user' ? 'user' : 'wink'}`;

      const avatar = document.createElement('div');
      avatar.className = `msg-avatar ${role === 'user' ? 'user-av' : 'wink-av'}`;
      avatar.textContent = role === 'user' ? (STUDENT_FIRST_NAME[0] || 'U') : '🎓';

      const bubble = document.createElement('div');
      bubble.className = 'msg-bubble';
      bubble.innerHTML = role === 'user' ? escapeHtml(text) : formatMessage(text);

      div.appendChild(avatar);
      div.appendChild(bubble);
      wrap.insertBefore(div, typing);
      wrap.scrollTop = wrap.scrollHeight;
      return bubble;
    }

    // Replies that read like a document (headings, tables, diagrams, or
    // long) get a "Download as Word" button next to the rating buttons.
    function looksLikeDocument(text) {
      return /^#{1,4}\s/m.test(text) || /^\s*\|.*\|\s*$/m.test(text) || /```mermaid/.test(text) || text.length > 1500;
    }

    // Draws each rendered diagram in the bubble onto a canvas and returns
    // PNG data URLs, in order, for the Word export.
    async function diagramsToPng(bubble) {
      const out = [];
      for (const svg of bubble.querySelectorAll('.wink-diagram svg')) {
        try {
          const clone = svg.cloneNode(true);
          const box = svg.getBoundingClientRect();
          const vb = svg.viewBox && svg.viewBox.baseVal;
          const w = Math.max(1, Math.round((vb && vb.width) || box.width));
          const h = Math.max(1, Math.round((vb && vb.height) || box.height));
          clone.setAttribute('width', w); clone.setAttribute('height', h);
          clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
          const xml = new XMLSerializer().serializeToString(clone);
          const img = new Image();
          img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);
          await img.decode();
          const scale = 2;
          const canvas = document.createElement('canvas');
          canvas.width = w * scale; canvas.height = h * scale;
          const ctx = canvas.getContext('2d');
          ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          out.push(canvas.toDataURL('image/png'));
        } catch (e) {
          out.push(null);
        }
      }
      return out;
    }

    // A reply is a slide deck when it has several "## " sections and the
    // student asked for slides/presentation/PowerPoint (or the reply says so).
    function looksLikeDeck(text) {
      return (text.match(/^##\s/gm) || []).length >= 3 && /\b(slide|slides|deck|presentation|powerpoint|pptx)\b/i.test(text);
    }

    async function downloadAsWord(text, bubble, btn, kind) {
      const label = btn.textContent;
      const isDeck = kind === 'pptx';
      btn.disabled = true; btn.textContent = 'Preparing…';
      try {
        const images = await diagramsToPng(bubble);
        const res = await fetch(isDeck ? '/export-pptx' : '/export-docx', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, images })
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.error || (isDeck ? 'Could not create the PowerPoint.' : 'Could not create the Word file.'));
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename="([^"]+)"/);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = m ? m[1] : (isDeck ? 'WINK-slides.pptx' : 'WINK-document.docx');
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        btn.textContent = label;
      } catch (e) {
        btn.textContent = '⚠ Try again';
        alert(e.message);
      } finally {
        btn.disabled = false;
      }
    }

    // ---- Preview before download (Word and PowerPoint) ----
    function richText(el, text) {
      String(text || '').split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/).forEach(part => {
        if (!part) return;
        let node;
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) { node = document.createElement('strong'); node.textContent = part.slice(2, -2); }
        else if (part.startsWith('`') && part.endsWith('`') && part.length > 2) { node = document.createElement('code'); node.textContent = part.slice(1, -1); }
        else if (part.startsWith('*') && part.endsWith('*') && part.length > 2) { node = document.createElement('em'); node.textContent = part.slice(1, -1); }
        else node = document.createTextNode(part);
        el.appendChild(node);
      });
    }

    function closePreview() {
      const o = document.getElementById('preview-overlay');
      if (o) o.remove();
      document.removeEventListener('keydown', previewKeys);
    }
    let previewNav = null;
    function previewKeys(e) {
      if (e.key === 'Escape') closePreview();
      else if (previewNav && e.key === 'ArrowRight') previewNav(1);
      else if (previewNav && e.key === 'ArrowLeft') previewNav(-1);
    }

    function openPreviewShell(titleText, onDownload) {
      closePreview();
      const overlay = document.createElement('div');
      overlay.id = 'preview-overlay'; overlay.className = 'preview-overlay';
      overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
      const box = document.createElement('div'); box.className = 'preview-box';
      const head = document.createElement('div'); head.className = 'preview-head';
      const h = document.createElement('strong'); h.textContent = titleText;
      const dl = document.createElement('button'); dl.type = 'button'; dl.className = 'preview-dl'; dl.textContent = '⬇ Download';
      dl.addEventListener('click', onDownload);
      const x = document.createElement('button'); x.type = 'button'; x.className = 'preview-x'; x.textContent = '✕';
      x.setAttribute('aria-label', 'Close preview'); x.addEventListener('click', closePreview);
      head.appendChild(h); head.appendChild(dl); head.appendChild(x);
      const body = document.createElement('div'); body.className = 'preview-body';
      box.appendChild(head); box.appendChild(body); overlay.appendChild(box);
      overlay.addEventListener('click', e => { if (e.target === overlay) closePreview(); });
      document.body.appendChild(overlay);
      document.addEventListener('keydown', previewKeys);
      previewNav = null;
      return body;
    }

    function previewAsWord(text, bubble, btn) {
      const body = openPreviewShell('Word preview', () => downloadAsWord(text, bubble, btn));
      const page = document.createElement('div'); page.className = 'preview-page';
      const clone = bubble.cloneNode(true);
      clone.querySelectorAll('.msg-feedback').forEach(n => n.remove());
      clone.className = 'preview-page-content';
      page.appendChild(clone); body.appendChild(page);
    }

    async function previewAsDeck(text, bubble, btn) {
      const label = btn.textContent;
      btn.disabled = true; btn.textContent = 'Loading…';
      let slides;
      try {
        const res = await fetch('/preview-pptx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(d.error || 'Could not build the preview.');
        slides = d.slides || [];
      } catch (e) { alert(e.message); return; }
      finally { btn.disabled = false; btn.textContent = label; }
      if (!slides.length) { alert('Nothing to preview.'); return; }
      const diagrams = Array.from(bubble.querySelectorAll('.wink-diagram'));
      let dIdx = 0;
      slides.forEach(s => { s._svg = s.diagram ? (diagrams[dIdx++] || null) : null; });

      const body = openPreviewShell('PowerPoint preview', () => downloadAsWord(text, bubble, btn, 'pptx'));
      const stage = document.createElement('div'); stage.className = 'preview-stage';
      const notes = document.createElement('div'); notes.className = 'preview-notes';
      const nav = document.createElement('div'); nav.className = 'preview-nav';
      const prev = document.createElement('button'); prev.type = 'button'; prev.textContent = '‹ Prev';
      const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next ›';
      const count = document.createElement('span');
      nav.appendChild(prev); nav.appendChild(count); nav.appendChild(next);
      body.appendChild(stage); body.appendChild(nav); body.appendChild(notes);

      let cur = 0;
      function render() {
        const s = slides[cur];
        stage.textContent = '';
        const slide = document.createElement('div');
        slide.className = 'pv-slide' + (s.cover ? ' pv-cover' : '');
        const t = document.createElement('div'); t.className = 'pv-title'; richText(t, s.title); slide.appendChild(t);
        const inner = document.createElement('div'); inner.className = 'pv-body';
        const n = s.bullets.length;
        inner.classList.add(n <= 4 ? 'pv-l' : n <= 6 ? 'pv-m' : n <= 9 ? 'pv-s' : 'pv-xs');
        if (s.table) {
          const tbl = document.createElement('table'); tbl.className = 'pv-table';
          s.table.forEach((row, r) => {
            const tr = document.createElement('tr');
            row.forEach(c => { const cell = document.createElement(r === 0 ? 'th' : 'td'); richText(cell, c); tr.appendChild(cell); });
            tbl.appendChild(tr);
          });
          inner.appendChild(tbl);
        }
        if (s._svg) inner.appendChild(s._svg.cloneNode(true));
        else s.bullets.forEach(b => {
          const li = document.createElement('div');
          li.className = 'pv-bullet' + (b.level ? ' pv-sub' : '');
          const isHead = /^\*\*[^*]+\*\*$/.test(b.text);
          if (!isHead && !s.cover) li.dataset.mark = b.level ? '–' : '•';
          if (isHead) li.classList.add('pv-head');
          richText(li, b.text); inner.appendChild(li);
        });
        slide.appendChild(inner);
        const f = document.createElement('div'); f.className = 'pv-foot'; f.textContent = 'WINK  |  ' + (cur + 1); slide.appendChild(f);
        stage.appendChild(slide);
        count.textContent = (cur + 1) + ' / ' + slides.length;
        prev.disabled = cur === 0; next.disabled = cur === slides.length - 1;
        notes.textContent = s.notes ? 'Speaker notes: ' + s.notes : '';
      }
      previewNav = d => { cur = Math.min(slides.length - 1, Math.max(0, cur + d)); render(); };
      prev.addEventListener('click', () => previewNav(-1));
      next.addEventListener('click', () => previewNav(1));
      render();
    }

    function addFeedbackButtons(container, convId, msgIndex, text) {
      if (!convId && convId !== 0) return;
      const fb = document.createElement('div');
      fb.className = 'msg-feedback';
      const row = document.createElement('div');
      row.className = 'feedback-buttons-row';
      const up = document.createElement('button');
      up.type = 'button'; up.className = 'feedback-btn'; up.setAttribute('aria-label', 'Good answer'); up.textContent = '👍';
      up.addEventListener('click', function() { submitFeedback(convId, msgIndex, 'up', up); });
      const down = document.createElement('button');
      down.type = 'button'; down.className = 'feedback-btn'; down.setAttribute('aria-label', 'Not helpful'); down.textContent = '👎';
      down.addEventListener('click', function() { submitFeedback(convId, msgIndex, 'down', down); });
      row.appendChild(up);
      row.appendChild(down);
      if (text && looksLikeDocument(text)) {
        const word = document.createElement('button');
        word.type = 'button'; word.className = 'feedback-btn word-btn';
        word.setAttribute('aria-label', 'Download this answer as a Word document');
        word.textContent = '⬇ Word';
        word.addEventListener('click', () => downloadAsWord(text, container, word));
        row.appendChild(word);
        const pvw = document.createElement('button');
        pvw.type = 'button'; pvw.className = 'feedback-btn word-btn';
        pvw.setAttribute('aria-label', 'Preview this answer as a Word document');
        pvw.textContent = '👁 Preview';
        pvw.addEventListener('click', () => previewAsWord(text, container, word));
        row.insertBefore(pvw, word);
        if (looksLikeDeck(text)) {
          const ppt = document.createElement('button');
          ppt.type = 'button'; ppt.className = 'feedback-btn word-btn';
          ppt.setAttribute('aria-label', 'Download this answer as a PowerPoint');
          ppt.textContent = '⬇ PowerPoint';
          ppt.addEventListener('click', () => downloadAsWord(text, container, ppt, 'pptx'));
          row.appendChild(ppt);
          const pvp = document.createElement('button');
          pvp.type = 'button'; pvp.className = 'feedback-btn word-btn';
          pvp.setAttribute('aria-label', 'Preview this answer as slides');
          pvp.textContent = '👁 Slides';
          pvp.addEventListener('click', () => previewAsDeck(text, container, ppt));
          row.insertBefore(pvp, ppt);
        }
      }
      const label = document.createElement('div');
      label.className = 'feedback-label';
      label.textContent = 'Please rate the answer';
      fb.appendChild(row);
      fb.appendChild(label);
      container.appendChild(fb);
    }

    async function submitFeedback(convId, msgIndex, rating, btnEl) {
      const group = btnEl.closest('.msg-feedback');
      try {
        const resp = await fetch('/rate-answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conversation_id: convId, message_index: msgIndex, rating: rating }),
        });
        if (resp.ok) {
          group.innerHTML = '<span class="feedback-thanks">Thanks for the feedback.</span>';
        }
      } catch (e) {  }
    }

    function setToolbarEnabled(enabled) {
      document.getElementById('export-btn').disabled = !enabled;
      document.getElementById('share-btn').disabled = !enabled;
    }

    function startNewChat() {
      closeMobileSidebar();
      messages = [];
      currentConversationId = null;
      currentTempDoc = null;
      document.getElementById('messages').innerHTML = WELCOME_HTML + TYPING_HTML;
      setToolbarEnabled(false);
      highlightActiveConversation(null);
      document.getElementById('chat-input').focus();
    }

    function highlightActiveConversation(id) {
      document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', String(el.dataset.id) === String(id));
      });
    }

    let allConversationsCache = [];

    async function loadConversationList() {
      try {
        const res = await fetch('/conversations');
        const data = await res.json();
        allConversationsCache = data.conversations || [];
        const list = document.getElementById('conv-list');
        const convs = allConversationsCache.slice(0, 3);
        if (convs.length === 0) {
          list.innerHTML = '<div class="conv-empty">No past chats yet</div>';
          return;
        }
        list.innerHTML = '';
        convs.forEach(c => {
          const item = document.createElement('div');
          item.className = 'conv-item';
          item.dataset.id = c.id;
          item.innerHTML = `<span class="conv-title">${escapeHtml(c.title || 'Conversation')}</span>
                             <button class="conv-delete" title="Delete" aria-label="Delete conversation">🗑</button>`;
          item.querySelector('.conv-title').addEventListener('click', () => loadConversation(c.id));
          item.querySelector('.conv-delete').addEventListener('click', (e) => {
            e.stopPropagation(); deleteConversation(c.id);
          });
          list.appendChild(item);
        });
        highlightActiveConversation(currentConversationId);
      } catch (e) {
        console.error('loadConversationList error', e);
      }
    }

    function openAllConversations() {
      let overlay = document.getElementById('wink-allconvs-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'wink-allconvs-overlay';
        overlay.className = 'wink-modal-overlay';
        overlay.innerHTML = `
          <div class="wink-modal-card" role="dialog" aria-modal="true" aria-labelledby="ac-title" style="max-width:420px;max-height:70vh;display:flex;flex-direction:column;">
            <h3 id="ac-title">All Conversations</h3>
            <input type="text" id="ac-search" placeholder="Search by title…" style="width:100%;padding:9px 12px;border:1.5px solid #dde3f0;border-radius:6px;font-size:14px;margin-bottom:12px;" aria-label="Search conversations">
            <div id="ac-list" style="overflow-y:auto;flex:1;"></div>
            <div class="wink-modal-actions" style="margin-top:14px;">
              <button class="wink-modal-btn secondary" id="ac-close">Close</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);
        overlay.querySelector('#ac-close').onclick = () => overlay.classList.remove('open');
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('open'); });
        overlay.querySelector('#ac-search').addEventListener('input', (e) => renderAllConversations(e.target.value));
      }
      overlay.querySelector('#ac-search').value = '';
      renderAllConversations('');
      overlay.classList.add('open');
    }

    function renderAllConversations(query) {
      const listEl = document.getElementById('ac-list');
      const q = query.trim().toLowerCase();
      const filtered = allConversationsCache.filter(c => (c.title || 'Conversation').toLowerCase().includes(q));
      if (filtered.length === 0) {
        listEl.innerHTML = '<div class="conv-empty" style="padding:16px 0;">No conversations match.</div>';
        return;
      }
      listEl.innerHTML = filtered.map(c => `
        <div class="conv-item" style="color:#002855;" data-id="${c.id}">
          <span class="conv-title" style="cursor:pointer;">${escapeHtml(c.title || 'Conversation')}</span>
          <button class="conv-delete" title="Delete" aria-label="Delete conversation">🗑</button>
        </div>`).join('');
      listEl.querySelectorAll('.conv-item').forEach(item => {
        const id = parseInt(item.dataset.id, 10);
        item.querySelector('.conv-title').addEventListener('click', () => {
          loadConversation(id);
          document.getElementById('wink-allconvs-overlay').classList.remove('open');
        });
        item.querySelector('.conv-delete').addEventListener('click', async (e) => {
          e.stopPropagation();
          await deleteConversation(id);
          renderAllConversations(document.getElementById('ac-search').value);
        });
      });
    }

    async function loadConversation(id) {
      closeMobileSidebar();
      try {
        const res = await fetch(`/conversations/${id}`);
        if (!res.ok) return;
        const data = await res.json();
        messages = (data.messages || []).map(m => ({ role: m.role, content: m.content }));
        currentConversationId = id;
        currentTempDoc = null;
        document.getElementById('messages').innerHTML = TYPING_HTML;
        messages.forEach((m, i) => {
          const bubble = addMessage(m.role, m.content);
          if (m.role !== 'user' && bubble) {
            resolvePhotoMarkers(bubble);
            resolveDiagramMarkers(bubble);
            addFeedbackButtons(bubble, currentConversationId, i, m.content);
          }
        });
        setToolbarEnabled(true);
        highlightActiveConversation(id);
      } catch (e) {
        console.error('loadConversation error', e);
      }
    }

    async function deleteConversation(id) {
      const ok = await winkConfirm({
        title: 'Delete this conversation?',
        message: 'This can\'t be undone — the conversation and its messages will be permanently removed.',
        confirmLabel: 'Delete', danger: true
      });
      if (!ok) return;
      await fetch(`/conversations/${id}/delete`, { method: 'POST' });
      if (currentConversationId === id) startNewChat();
      loadConversationList();
      winkToast('Conversation deleted.');
    }

    function toggleMobileSidebar() {
      document.querySelector('.chat-sidebar').classList.toggle('mobile-open');
      document.getElementById('mobile-sidebar-backdrop').classList.toggle('show');
    }
    function closeMobileSidebar() {
      document.querySelector('.chat-sidebar').classList.remove('mobile-open');
      document.getElementById('mobile-sidebar-backdrop').classList.remove('show');
    }

    function exportConversation() {
      if (!currentConversationId) return;
      window.open(`/conversations/${currentConversationId}/export`, '_blank');
    }

    async function shareConversation() {
      if (!currentConversationId) return;
      try {
        const res = await fetch(`/conversations/${currentConversationId}/share`, { method: 'POST' });
        const data = await res.json();
        if (data.share_url) {
          openShareModal(data.share_url, currentConversationId);
        } else {
          alert(data.error || 'Could not create share link.');
        }
      } catch (e) {
        alert('Could not create share link.');
      }
    }

    function openShareModal(shareUrl, convId) {
      document.getElementById('wink-modal-body').innerHTML = `
        <h2>Share This Conversation</h2>
        <p>Anyone with this link can view this conversation, until you stop sharing it.</p>
        <p style="word-break:break-all;background:#f4f6fb;border:1px solid #dde3f0;border-radius:8px;padding:10px 12px;font-size:12px;color:#002855;">${shareUrl}</p>
        <div class="wink-modal-actions">
          <button type="button" class="wink-modal-btn danger" id="share-stop-btn">Stop Sharing</button>
          <button type="button" class="wink-modal-btn primary" id="share-copy-btn">Copy Link</button>
        </div>
      `;
      document.getElementById('wink-modal').classList.add('open');
      document.getElementById('share-copy-btn').onclick = async () => {
        await navigator.clipboard.writeText(shareUrl).catch(() => {});
        const btn = document.getElementById('share-copy-btn');
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy Link'; }, 1500);
      };
      document.getElementById('share-stop-btn').onclick = async () => {
        try {
          const res = await fetch(`/conversations/${convId}/unshare`, { method: 'POST' });
          const data = await res.json();
          if (data.ok) {
            closeWinkModal();
            winkToast('This conversation is no longer shared.');
          } else {
            alert(data.error || 'Could not stop sharing.');
          }
        } catch (e) {
          alert('Could not stop sharing.');
        }
      };
    }

    function startRateLimitCountdown(seconds) {
      const sendBtn = document.getElementById('send-btn');
      const input = document.getElementById('chat-input');
      let remaining = Math.max(1, Math.ceil(seconds));
      sendBtn.disabled = true;
      input.disabled = true;
      const originalPlaceholder = input.placeholder;
      const tick = () => {
        input.placeholder = `Please wait ${remaining}s before asking again…`;
        if (remaining <= 0) {
          clearInterval(timer);
          input.placeholder = originalPlaceholder;
          input.disabled = false;
          sendBtn.disabled = false;
          input.focus();
          return;
        }
        remaining -= 1;
      };
      tick();
      const timer = setInterval(tick, 1000);
    }

    async function sendMessage() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;

      input.value = '';
      input.style.height = 'auto';
      document.getElementById('send-btn').disabled = true;

      messages.push({ role: 'user', content: text });
      addMessage('user', text);

      const typing = document.getElementById('typing-indicator');
      typing.style.display = 'flex';
      document.getElementById('messages').scrollTop = 99999;

      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: messages.slice(-16), conversation_id: currentConversationId, temp_doc: currentTempDoc })
        });

        if (!res.ok || !res.body) {
          const data = await res.json().catch(() => ({}));
          typing.style.display = 'none';
          if (res.status === 429) {
            addMessage('wink', '⏳ ' + (data.error || "You're asking questions faster than I can keep up."));
            startRateLimitCountdown(data.retry_after || 15);
          } else {
            addMessage('wink', '❌ ' + (data.error || 'Something went wrong. Please click + New Chat and ask again. That usually fixes it.'));
            document.getElementById('send-btn').disabled = false;
          }
          return;
        }

        const returnedConvId = res.headers.get('X-Conversation-Id');
        const isNewConversation = returnedConvId && !currentConversationId;
        if (returnedConvId) currentConversationId = returnedConvId;

        typing.style.display = 'none';

        const wrap = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = 'msg wink';
        const avatar = document.createElement('div');
        avatar.className = 'msg-avatar wink-av';
        avatar.textContent = '🎓';
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble';
        div.appendChild(avatar);
        div.appendChild(bubble);
        wrap.insertBefore(div, typing);

        div.scrollIntoView({ behavior: 'smooth', block: 'start' });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          fullText += decoder.decode(value, { stream: true });
          bubble.innerHTML = formatMessage(fullText);
        }

        messages.push({ role: 'assistant', content: fullText });
        resolvePhotoMarkers(bubble);
        resolveDiagramMarkers(bubble);
        addFeedbackButtons(bubble, currentConversationId, messages.length - 1, fullText);
        setToolbarEnabled(true);
        if (isNewConversation) loadConversationList();

      } catch(e) {
        typing.style.display = 'none';
        addMessage('wink', '\u274c Connection error. Please click + New Chat and ask again. That usually fixes it.');
      }
      document.getElementById('send-btn').disabled = false;
      document.getElementById('chat-input').focus();
    }

    function closeWinkModal() {
      document.getElementById('wink-modal').classList.remove('open');
    }

    function winkUploadChoice(fileName) {
      return new Promise(resolve => {
        let overlay = document.getElementById('wink-upload-overlay');
        if (!overlay) {
          overlay = document.createElement('div');
          overlay.id = 'wink-upload-overlay';
          overlay.className = 'wink-modal-overlay';
          overlay.innerHTML = `
            <div class="wink-modal-card" role="dialog" aria-modal="true" aria-labelledby="wu-title">
              <h3 id="wu-title">Add <span id="wu-filename"></span></h3>
              <div id="wu-step-choice">
                <p>Save this permanently so you (and WINK) can come back to it later, or just use it for this one conversation?</p>
                <div class="wink-modal-actions" style="justify-content:stretch;flex-direction:column;">
                  <button class="wink-modal-btn primary" id="wu-choose-permanent" style="width:100%;margin-bottom:8px;">📌 Save Permanently</button>
                  <button class="wink-modal-btn secondary" id="wu-choose-temp" style="width:100%;margin-bottom:8px;">💬 Just This Conversation</button>
                </div>
                <p style="font-size:12px;color:#6b7a99;margin-bottom:0;">Permanent uploads count toward your 20-document limit and show up in My Documents. This-conversation-only uploads don't count toward that limit and disappear when you leave this chat.</p>
                <div class="wink-modal-actions" style="margin-top:14px;">
                  <button class="wink-modal-btn secondary" id="wu-cancel-1">Cancel</button>
                </div>
              </div>
              <div id="wu-step-details" style="display:none;">
                <p style="margin-bottom:10px;">Which course is this for?</p>
                <input type="text" id="wu-course" placeholder="e.g., MIS 4310" style="width:100%;padding:9px 12px;border:1.5px solid #dde3f0;border-radius:6px;font-size:14px;margin-bottom:10px;" aria-label="Course name">
                <input type="text" id="wu-crn" placeholder="CRN#" style="width:100%;padding:9px 12px;border:1.5px solid #dde3f0;border-radius:6px;font-size:14px;margin-bottom:6px;" aria-label="CRN number">
                <div id="wu-error" style="color:#b91c1c;font-size:12px;margin-bottom:8px;"></div>
                <div class="wink-modal-actions">
                  <button class="wink-modal-btn secondary" id="wu-back">← Back</button>
                  <button class="wink-modal-btn primary" id="wu-submit">Upload</button>
                </div>
              </div>
            </div>`;
          document.body.appendChild(overlay);
        }
        overlay.querySelector('#wu-filename').textContent = `"${fileName}"`;
        const stepChoice = overlay.querySelector('#wu-step-choice');
        const stepDetails = overlay.querySelector('#wu-step-details');
        const courseInput = overlay.querySelector('#wu-course');
        const crnInput = overlay.querySelector('#wu-crn');
        const errorEl = overlay.querySelector('#wu-error');
        stepChoice.style.display = 'block';
        stepDetails.style.display = 'none';
        courseInput.value = ''; crnInput.value = ''; errorEl.textContent = '';

        function close(result) { overlay.classList.remove('open'); resolve(result); }

        overlay.querySelector('#wu-choose-temp').onclick = () => close({ permanent: false });
        overlay.querySelector('#wu-cancel-1').onclick = () => close(null);
        overlay.querySelector('#wu-choose-permanent').onclick = () => {
          stepChoice.style.display = 'none';
          stepDetails.style.display = 'block';
          courseInput.focus();
        };
        overlay.querySelector('#wu-back').onclick = () => {
          stepDetails.style.display = 'none';
          stepChoice.style.display = 'block';
        };
        overlay.querySelector('#wu-submit').onclick = () => {
          const course = courseInput.value.trim();
          const crn = crnInput.value.trim();
          if (!course || !crn) {
            errorEl.textContent = 'Please fill in both the course name and CRN#.';
            return;
          }
          close({ permanent: true, course, crn });
        };
        overlay.addEventListener('click', function selfClose(e) {
          if (e.target === overlay) { close(null); }
        }, { once: true });
        overlay.classList.add('open');
      });
    }

    async function handleChatFileUpload(event) {
      const file = event.target.files[0];
      event.target.value = '';
      if (!file) return;

      const choice = await winkUploadChoice(file.name);
      if (!choice) return; // cancelled

      const keepPermanently = choice.permanent;
      const course = choice.course || null;
      const crn = choice.crn || null;

      const uploadBtn = document.getElementById('upload-btn');
      uploadBtn.textContent = '…';
      uploadBtn.disabled = true;

      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('temporary', keepPermanently ? 'false' : 'true');
        if (keepPermanently) {
          formData.append('course', course);
          formData.append('crn', crn);
        }
        if (currentConversationId) formData.append('conversation_id', currentConversationId);
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (res.status === 429) {
          winkToast(data.error || 'Too many uploads in a row — please wait a moment.', true);
          if (data.retry_after) startUploadCountdown(data.retry_after);
        } else if (data.success) {
          if (keepPermanently) {
            addMessage('wink', `📎 Got it — **${file.name}** is uploaded for **${course} (CRN ${crn})** and ready to reference.`);
          } else {
            currentTempDoc = { name: data.name || file.name, content: data.content || '' };
            addMessage('wink', `📎 Got it — **${file.name}** is loaded for this conversation only, so ask away. It won't be saved after this chat — if you'd like to keep it for later, you can upload it permanently anytime from **My Documents**.`);
          }
        } else {
          winkToast(data.error || 'Upload failed — please try again.', true);
        }
      } catch (e) {
        winkToast('Upload failed — please try again.', true);
      }
      uploadBtn.textContent = '＋';
      uploadBtn.disabled = false;
    }

    function startUploadCountdown(seconds) {
      const uploadBtn = document.getElementById('upload-btn');
      let remaining = Math.max(1, Math.ceil(seconds));
      uploadBtn.disabled = true;
      const tick = () => {
        uploadBtn.textContent = remaining;
        if (remaining <= 0) {
          clearInterval(timer);
          uploadBtn.textContent = '＋';
          uploadBtn.disabled = false;
          return;
        }
        remaining -= 1;
      };
      tick();
      const timer = setInterval(tick, 1000);
    }

    let winkRecognition = null;
    let winkListening = false;
    function toggleVoiceInput() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        alert("Voice input isn't supported in this browser — try Chrome or Edge.");
        return;
      }
      const micBtn = document.getElementById('mic-btn');
      if (winkListening) {
        winkRecognition && winkRecognition.stop();
        return;
      }
      winkRecognition = new SpeechRecognition();
      winkRecognition.lang = 'en-US';
      winkRecognition.interimResults = false;
      winkRecognition.maxAlternatives = 1;

      winkRecognition.onstart = () => {
        winkListening = true;
        micBtn.classList.add('recording');
        micBtn.title = 'Listening… click to stop';
      };
      winkRecognition.onresult = (e) => {
        const transcript = e.results[0][0].transcript;
        const input = document.getElementById('chat-input');
        input.value = (input.value ? input.value + ' ' : '') + transcript;
        autoResize(input);
        input.focus();
      };
      winkRecognition.onerror = () => {
        micBtn.classList.remove('recording');
        micBtn.title = 'Speak your question';
        winkListening = false;
      };
      winkRecognition.onend = () => {
        micBtn.classList.remove('recording');
        micBtn.title = 'Speak your question';
        winkListening = false;
      };
      winkRecognition.start();
    }

    loadConversationList();
  
