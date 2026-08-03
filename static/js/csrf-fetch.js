/*
  Attaches the page's CSRF token (from <meta name="csrf-token">, which
  each page still sets itself from Flask's csrf_token()) to every
  state-changing fetch() call automatically. Extracted from a
  byte-for-byte identical inline <script> that was duplicated across 7
  pages (analytics, calendar, chat, dashboard, documents, grades,
  practice) — same fix as static/css/nav.css, for the JS side of the
  same duplication.

  Requires the including page to still have its own
  <meta name="csrf-token" content="{{ csrf_token() }}"> tag — this file
  only reads that value, it doesn't generate it (each render needs its
  own per-request token from Flask).
*/
(function() {
  const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    init = init || {};
    const method = (init.method || 'GET').toUpperCase();
    if (token && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      const headers = new Headers(init.headers || {});
      if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
      init.headers = headers;
    }
    return originalFetch(input, init);
  };
})();
