/**
 * scroll-reset.js
 *
 * WINK reuses the same modal/overlay pattern everywhere (elements that
 * become visible via a `.open` class, or by their `style.display` being
 * set to 'block'/'flex'). Browsers can leave a previously-scrolled
 * container at its last scroll position when it's reopened, which looks
 * like the popup "jumping" straight into the middle of its content.
 *
 * This watches the whole page for that open pattern and snaps any
 * scrollable container inside the newly-opened element back to the top,
 * so every modal/dropdown/panel always opens scrolled to the top —
 * without having to hand-wire scrollTop = 0 into each individual
 * open___Modal() function across every page.
 */
(function () {
  function resetScrollables(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.scrollHeight > root.clientHeight) root.scrollTop = 0;
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var style = window.getComputedStyle(el);
      if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
        el.scrollTop = 0;
      }
    }
  }

  function handleMutation(target) {
    window.requestAnimationFrame(function () {
      resetScrollables(target);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if (m.type !== 'attributes') continue;
        var el = m.target;
        if (m.attributeName === 'class' && el.classList && el.classList.contains('open')) {
          handleMutation(el);
        } else if (m.attributeName === 'style') {
          var display = el.style && el.style.display;
          if (display === 'block' || display === 'flex') {
            handleMutation(el);
          }
        }
      }
    });

    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['class', 'style'],
      subtree: true,
    });
  });
})();
