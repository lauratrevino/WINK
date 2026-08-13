













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
