// Claim portal behaviour (served at /claim/<token>). No inline script so the strict CSP holds.
(function () {
  'use strict';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function copy(text, btn) {
    var done = function (ok) {
      if (!btn) return;
      var prev = btn.textContent;
      btn.textContent = ok ? 'Copied!' : 'Copy failed';
      setTimeout(function () { btn.textContent = prev; }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-copy]');
    if (t) copy(t.getAttribute('data-copy'), t);
  });

  var root = document.getElementById('claim-root');
  var btn = document.getElementById('btn-claim');
  var out = document.getElementById('claim-result');
  if (!root || !btn || !out) return;

  btn.addEventListener('click', function () {
    btn.disabled = true;
    btn.textContent = 'Claiming…';
    while (out.firstChild) out.removeChild(out.firstChild);

    fetch('/api/allocations/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ claim_token: root.dataset.token, agent_id: root.dataset.agent || '' })
    })
      .then(function (r) { return r.json().catch(function () { return { success: false, error: 'Unexpected response' }; }); })
      .then(function (data) {
        if (data && data.success && data.api_key) {
          var box = el('div', 'notice notice-ok stack-sm');
          box.appendChild(el('strong', null, 'Key claimed successfully.'));
          box.appendChild(el('p', 'small', 'Store it securely — it will not be shown again on this page.'));
          box.appendChild(el('code', 'code-box', data.api_key));
          var c = el('button', 'btn btn-sm', 'Copy key');
          c.type = 'button';
          c.addEventListener('click', function () { copy(data.api_key, c); });
          box.appendChild(c);
          out.appendChild(box);
          btn.hidden = true;
        } else {
          out.appendChild(el('div', 'notice notice-bad', (data && data.error) || 'Failed to claim key.'));
          btn.disabled = false;
          btn.textContent = 'Claim Allocated Key Now';
        }
      })
      .catch(function (err) {
        out.appendChild(el('div', 'notice notice-bad', 'Network error: ' + err.message));
        btn.disabled = false;
        btn.textContent = 'Claim Allocated Key Now';
      });
  });
})();
