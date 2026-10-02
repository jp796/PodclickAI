/**
 * podclick-nav.js — the ONE PodClick app shell.
 *
 * Every page loads:  <script src="/static/podclick-nav.js?v=…" defer></script>
 * and nothing else renders site navigation. Pages keep only page-LOCAL
 * sub-tabs (class .pc-subtabs), never a second copy of the global menu.
 *
 * Layout (styles live in /podclick-design.css, the single source of truth):
 *  - >= 1024px : fixed left sidebar, body gets padding-left
 *  - <  1024px : sticky top bar (brand · Upload · menu) + slide-in drawer
 *
 * Behaviour:
 *  - Skips /onboarding (full-screen first-run flow)
 *  - First-run gate: redirects to /onboarding until setup is complete
 *  - Marks the current page (aria-current="page")
 *  - Primary "Upload episode" CTA ([data-pc-upload]) opens a file picker +
 *    dialog that POSTs /api/projects/from-upload and redirects to
 *    /project/{id}. Fallback href /studio#upload scrolls to the studio tray
 *    (and dismisses the camera check) when JS handling is bypassed.
 *  - Ensures /podclick-design.css is loaded even if a page forgot the <link>
 */
(function () {
  'use strict';

  if (window.location.pathname.startsWith('/onboarding')) return;

  /* ── First-run gate ─────────────────────────────────────────────────────────
   * The onboarding redirect used to live in walkthrough.html ALONE, so a new user
   * landing on /projects, /studio, /calendar, /youtube-studio, /blueprint or
   * anywhere else skipped setup entirely and hit empty-Foundation errors - the
   * generators 422 with foundation_not_ready and nothing explains why.
   *
   * It belongs here because this script is the one thing every page loads, so
   * one gate covers all of them instead of fifteen copies drifting apart.
   *
   * Deliberately non-blocking: an unreachable API must never lock someone out of
   * their own studio, and the sessionStorage latch means even a broken response
   * cannot produce a redirect loop.
   */
  (function firstRunGate() {
    try {
      if (sessionStorage.getItem('podclick_onboarding_checked') === '1') return;
    } catch (e) { return; }   // storage blocked - skip rather than risk a loop
    fetch('/api/onboarding/state')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (state) {
        try { sessionStorage.setItem('podclick_onboarding_checked', '1'); } catch (e) {}
        if (state && !state.completed_at) {
          window.location.href = '/onboarding';
        }
      })
      .catch(function () {
        try { sessionStorage.setItem('podclick_onboarding_checked', '1'); } catch (e) {}
      });
  })();

  /* ── Information architecture ──────────────────────────────────────────────
   * Media/content manager for a real-estate agent who is also an influencer.
   * `match` lists extra path prefixes that light up the item.
   * `soon: true` = route not built yet; rendered with a SOON badge.
   */
  var UPLOAD_HREF = '/studio#upload';

  var ICONS = {
    home:    '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    podcast: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0"/><path d="M12 18v3"/>',
    studio:  '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/>',
    social:  '<path d="M4 5h16v11H8l-4 4z"/>',
    video:   '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/>',
    brand:   '<path d="M12 3l2.6 5.6 6.1.6-4.6 4.1 1.3 6L12 16.3 6.6 19.3l1.3-6L3.3 9.2l6.1-.6z"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    jobsite: '<path d="M3 21h18"/><path d="M5 21V9l7-5 7 5v12"/><path d="M10 21v-6h4v6"/>',
    scout:   '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5"/>',
    agents:  '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M15 15c3 0 6 2 6 5"/>',
    found:   '<path d="M3 20h18"/><path d="M5 20v-5h14v5"/><path d="M8 15v-4h8v4"/>',
    blueprint:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
    permit:  '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    upload:  '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>',
    menu:    '<path d="M4 7h16M4 12h16M4 17h16"/>',
    close:   '<path d="M6 6l12 12M18 6L6 18"/>'
  };

  var NAV = [
    { section: 'Home', items: [
      { href: '/walkthrough',    label: 'Walk-through', icon: 'home' }
    ]},
    { section: 'Create', items: [
      { href: '/',               label: 'Podcast',      icon: 'podcast', exact: true },
      { href: '/studio',         label: 'Studio',       icon: 'studio' },
      { href: '/social-studio',  label: 'Social',       icon: 'social' },
      { href: '/vsl-editor',     label: 'Video / VSL',  icon: 'video' },
      { href: '/brand-studio',   label: 'Brand',        icon: 'brand' }
    ]},
    { section: 'Plan & publish', items: [
      { href: '/calendar',       label: 'Calendar',     icon: 'calendar' },
      { href: '/projects',       label: 'Job Site',     icon: 'jobsite', match: ['/project/', '/editor/'] }
    ]},
    { section: 'Grow', items: [
      { href: '/youtube-studio', label: 'Scout',        icon: 'scout' },
      { href: '/agents',         label: 'Agents',       icon: 'agents', soon: true }
    ]},
    { section: 'Foundation', items: [
      { href: '/foundation',     label: 'Foundation',   icon: 'found' },
      { href: '/blueprint',      label: 'Blueprint',    icon: 'blueprint' },
      { href: '/permit',         label: 'Permit',       icon: 'permit' }
    ]}
  ];

  var path = window.location.pathname.replace(/\/+$/, '') || '/';

  function isActive(item) {
    if (item.exact) return path === item.href;
    if (path === item.href || path.indexOf(item.href + '/') === 0) return true;
    return (item.match || []).some(function (p) { return path.indexOf(p) === 0; });
  }

  function svg(name, cls) {
    return '<svg class="' + (cls || 'pc-ico') + '" viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
      'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
      (ICONS[name] || '') + '</svg>';
  }

  /* Make sure the design tokens are present on every page. */
  function ensureDesignCss() {
    if (document.querySelector('link[href*="podclick-design.css"]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = '/podclick-design.css?v=20261001-2';
    document.head.insertBefore(l, document.head.firstChild);
  }

  function buildShell() {
    var groups = NAV.map(function (g) {
      var links = g.items.map(function (item) {
        var active = isActive(item);
        var attrs = 'href="' + item.href + '" class="pc-link' + (active ? ' pc-active' : '') + (item.soon ? ' pc-soon' : '') + '"';
        if (active) attrs += ' aria-current="page"';
        if (item.soon) attrs += ' title="Agents hub is being built — coming soon"';
        return '<a ' + attrs + '>' + svg(item.icon) + '<span class="pc-link-label">' + item.label + '</span>' +
          (item.soon ? '<span class="pc-badge">Soon</span>' : '') + '</a>';
      }).join('');
      return '<div class="pc-group" role="group" aria-label="' + g.section + '">' +
        '<div class="pc-group-label">' + g.section + '</div>' + links + '</div>';
    }).join('');

    var nav = document.createElement('nav');
    nav.id = 'pc-shell';
    nav.className = 'pc-shell';
    nav.setAttribute('aria-label', 'PodClick');
    nav.innerHTML =
      '<div class="pc-shell-bar">' +
        '<a href="/walkthrough" class="pc-brand" aria-label="PodClick home">Pod<em>Click</em></a>' +
        '<a href="' + UPLOAD_HREF + '" class="pc-bar-cta" data-pc-upload>' + svg('upload') + '<span>Upload</span></a>' +
        '<button type="button" class="pc-burger" aria-expanded="false" aria-controls="pc-shell-menu" aria-label="Open menu">' +
          svg('menu', 'pc-ico pc-ico-menu') + svg('close', 'pc-ico pc-ico-close') +
        '</button>' +
      '</div>' +
      '<div class="pc-shell-menu" id="pc-shell-menu">' +
        '<a href="' + UPLOAD_HREF + '" class="pc-cta" data-pc-upload>' + svg('upload') + '<span>Upload episode</span></a>' +
        groups +
        '<div class="pc-shell-foot">Your content.<br>Your voice.</div>' +
      '</div>';

    var scrim = document.createElement('div');
    scrim.className = 'pc-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    return { nav: nav, scrim: scrim };
  }

  function wireDrawer(nav, scrim) {
    var burger = nav.querySelector('.pc-burger');
    var mq = window.matchMedia('(max-width: 1023.98px)');
    function setOpen(open) {
      nav.classList.toggle('pc-open', open);
      scrim.classList.toggle('pc-open', open);
      document.documentElement.classList.toggle('pc-drawer-open', open);
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      if (open) {
        var first = nav.querySelector('.pc-shell-menu a');
        if (first) first.focus();
      }
    }
    burger.addEventListener('click', function () { setOpen(!nav.classList.contains('pc-open')); });
    scrim.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('pc-open')) { setOpen(false); burger.focus(); }
    });
    nav.querySelectorAll('.pc-shell-menu a').forEach(function (a) {
      a.addEventListener('click', function () { setOpen(false); });
    });
    var onChange = function () { if (!mq.matches) setOpen(false); };
    if (mq.addEventListener) mq.addEventListener('change', onChange); else mq.addListener(onChange);
  }

  /* /studio#upload — jump straight to the pre-recorded upload tray.
   * The studio opens a full-screen camera check on load; an upload does not
   * need a camera, so dismiss it via the page's own dcSkip() and stop any
   * stream the async device check opens afterwards. */
  function focusStudioUpload() {
    if (path !== '/studio' || window.location.hash !== '#upload') return;
    var tray = document.getElementById('upload-tray');
    if (!tray) return;
    try {
      var dc = document.getElementById('device-check');
      if (dc && dc.style.display !== 'none' && typeof window.dcSkip === 'function') window.dcSkip();
    } catch (e) { /* never block navigation on page internals */ }
    var ticks = 0;
    var t = setInterval(function () {
      ticks++;
      try {
        /* global _dcStream */
        if (typeof _dcStream !== 'undefined' && _dcStream) {
          _dcStream.getTracks().forEach(function (tr) { tr.stop(); });
          _dcStream = null; // eslint-disable-line no-global-assign
        }
      } catch (e) {}
      if (ticks > 16) clearInterval(t);
    }, 250);
    tray.scrollIntoView({ behavior: 'smooth', block: 'start' });
    tray.classList.remove('pc-flash');
    void tray.offsetWidth;
    tray.classList.add('pc-flash');
    var input = document.getElementById('upload-drop-zone');
    if (input) { input.setAttribute('tabindex', '0'); input.focus({ preventScroll: true }); }
  }

  /* ── Upload episode (shared) ────────────────────────────────────────────────
   * Any element with [data-pc-upload] opens a file picker + small dialog that
   * POSTs /api/projects/from-upload (multipart: file, title) and redirects to
   * /project/{id}, where transcription is already running. The element's href
   * (/studio#upload) is the no-JS fallback. Accepted types mirror the server
   * whitelist in main.py create_project_from_upload. */
  var UPLOAD_ACCEPT = '.mp4,.mov,.webm,.mkv,.mp3,.m4a,.wav,.flac,.aac';
  var uploadXhr = null;

  function fmtSize(b) {
    return b > 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB';
  }

  function openUploadDialog(file) {
    var old = document.getElementById('pc-upload-dialog');
    if (old) old.remove();
    var d = document.createElement('div');
    d.id = 'pc-upload-dialog';
    d.className = 'pc-upload-backdrop';
    d.innerHTML =
      '<div class="pc-upload-box" role="dialog" aria-modal="true" aria-labelledby="pc-upload-h">' +
        '<p class="pc-upload-eyebrow">New build</p>' +
        '<h2 id="pc-upload-h">Upload episode</h2>' +
        '<p class="pc-upload-file"></p>' +
        '<label for="pc-upload-title">What’s this build?</label>' +
        '<input id="pc-upload-title" type="text" placeholder="Leave blank to auto-name from the transcript">' +
        '<div class="pc-upload-track" hidden><div class="pc-upload-fill"></div></div>' +
        '<p class="pc-upload-status" role="status"></p>' +
        '<div class="pc-upload-actions">' +
          '<button type="button" class="pc-upload-cancel">Cancel</button>' +
          '<button type="button" class="pc-upload-go">Upload &amp; continue</button>' +
        '</div>' +
      '</div>';
    d.querySelector('.pc-upload-file').textContent = file.name + ' · ' + fmtSize(file.size);
    document.body.appendChild(d);
    var go = d.querySelector('.pc-upload-go');
    var cancel = d.querySelector('.pc-upload-cancel');
    var status = d.querySelector('.pc-upload-status');
    var track = d.querySelector('.pc-upload-track');
    var fill = d.querySelector('.pc-upload-fill');
    var title = d.querySelector('#pc-upload-title');
    title.focus();

    function close() {
      if (uploadXhr) { uploadXhr.abort(); uploadXhr = null; }
      d.remove();
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    cancel.addEventListener('click', close);
    d.addEventListener('click', function (e) { if (e.target === d) close(); });
    title.addEventListener('keydown', function (e) { if (e.key === 'Enter') go.click(); });

    go.addEventListener('click', function () {
      go.disabled = true; title.disabled = true;
      track.hidden = false; status.textContent = 'Uploading…';
      var form = new FormData();
      form.append('file', file);
      form.append('title', title.value.trim());
      var xhr = uploadXhr = new XMLHttpRequest();
      xhr.open('POST', '/api/projects/from-upload');
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var pct = Math.round(e.loaded / e.total * 100);
        fill.style.width = pct + '%';
        status.textContent = pct < 100 ? 'Uploading… ' + pct + '%' : 'Pouring it into the pipeline…';
      };
      xhr.onload = function () {
        uploadXhr = null;
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) {}
        if (xhr.status >= 200 && xhr.status < 300 && data.project_id) {
          status.textContent = 'Uploaded. Opening your build…';
          window.location.href = '/project/' + encodeURIComponent(data.project_id);
        } else {
          status.textContent = data.error || ('Upload failed (' + xhr.status + ').');
          status.classList.add('pc-upload-err');
          go.disabled = false; title.disabled = false; go.textContent = 'Try again';
        }
      };
      xhr.onerror = function () {
        uploadXhr = null;
        status.textContent = 'Network error — the upload did not reach the server.';
        status.classList.add('pc-upload-err');
        go.disabled = false; title.disabled = false; go.textContent = 'Try again';
      };
      xhr.send(form);
    });
  }

  function startUpload() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = UPLOAD_ACCEPT;
    input.style.display = 'none';
    input.addEventListener('change', function () {
      var f = input.files && input.files[0];
      input.remove();
      if (f) openUploadDialog(f);
    });
    document.body.appendChild(input);
    input.click();
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-pc-upload]');
    if (!t || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    startUpload();
  });
  window.PodClickUpload = { open: startUpload };

  function inject() {
    if (document.getElementById('pc-shell')) return; // already injected
    ensureDesignCss();
    var built = buildShell();
    document.body.insertBefore(built.scrim, document.body.firstChild);
    document.body.insertBefore(built.nav, document.body.firstChild);
    document.body.classList.add('pc-shell-on');
    wireDrawer(built.nav, built.scrim);
    focusStudioUpload();
    window.addEventListener('hashchange', focusStudioUpload);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
