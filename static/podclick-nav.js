/**
 * podclick-nav.js — Shared platform navigation
 * Add ONE line to every page <head>: <script src="/static/podclick-nav.js" defer></script>
 *
 * Behaviour:
 *  - Replaces any element with class .topbar or #pc-nav-host with the canonical nav
 *  - If no .topbar exists, prepends nav to <body>
 *  - Skips /onboarding (full-screen flow)
 *  - Sets .active on current page's link
 *  - Preserves any .topbar-extra content (e.g. Brick badge on walkthrough)
 */
(function () {
  'use strict';

  if (window.location.pathname.startsWith('/onboarding')) return;

  const NAV_ITEMS = [
    { href: '/walkthrough',    label: 'Walk-through' },
    { href: '/foundation',     label: 'Foundation'   },
    { href: '/projects',       label: 'Job Site'     },
    { href: '/project/',       label: null            },  // hidden — matched for active only
    { href: '/studio',         label: 'Studio'       },
    { href: '/calendar',       label: 'Calendar'     },
    { href: '/social-studio',  label: 'Social'       },
    { href: '/youtube-studio', label: 'Scout'        },
    { href: '/blueprint',      label: 'Blueprint'    },
    { href: '/brand-studio',   label: 'Brand'        },
    { href: '/vsl-editor',     label: 'VSL'          },
    { href: '/permit',         label: 'Permit'       },
    { href: '/editor/',        label: null            },  // hidden — matched for active only
  ];

  const path = window.location.pathname;

  // ── Styles ────────────────────────────────────────────────────────────────
  if (!document.getElementById('pc-nav-css')) {
    const s = document.createElement('style');
    s.id = 'pc-nav-css';
    s.textContent = `
      .pc-topbar {
        display: flex; align-items: center; gap: 12px;
        padding: 11px 22px; border-bottom: 1px solid #272420;
        background: #111009; position: sticky; top: 0; z-index: 950;
        font-family: 'Inter', system-ui, -apple-system, sans-serif;
        box-sizing: border-box; width: 100%;
      }
      .pc-brand {
        font-size: 15px; font-weight: 800; color: #F5F7FA;
        letter-spacing: -0.02em; text-decoration: none; flex-shrink: 0;
      }
      .pc-brand em { color: #f07030; font-style: normal; }
      .pc-links {
        display: flex; gap: 2px; margin-left: auto; flex-wrap: nowrap;
        overflow-x: auto; scrollbar-width: none; -ms-overflow-style: none;
        align-items: center;
      }
      .pc-links::-webkit-scrollbar { display: none; }
      .pc-links a {
        font-size: 12px; font-weight: 500; color: #847d74;
        padding: 5px 10px; border-radius: 7px; border: 1px solid transparent;
        text-decoration: none; transition: all 0.12s; white-space: nowrap;
        display: inline-block;
      }
      .pc-links a:hover { background: #161410; color: #F5F7FA; }
      .pc-links a.pc-active {
        background: #1e1b16; color: #f07030; border-color: #272420;
      }
      .pc-extras { display: flex; align-items: center; gap: 8px; margin-left: 8px; flex-shrink: 0; }
    `;
    document.head.appendChild(s);
  }

  // ── Build nav ─────────────────────────────────────────────────────────────
  function buildNav(extraHtml) {
    const linksHtml = NAV_ITEMS
      .filter(item => item.label)
      .map(item => {
        const active = path === item.href ||
          path.startsWith(item.href + '/') ||
          (item.href === '/projects' && path.startsWith('/project/'));
        return `<a href="${item.href}"${active ? ' class="pc-active"' : ''}>${item.label}</a>`;
      }).join('');

    const nav = document.createElement('div');
    nav.id = 'pc-topbar';
    nav.className = 'pc-topbar';
    nav.innerHTML = `
      <a href="/projects" class="pc-brand">Pod<em>Click</em></a>
      <div class="pc-links">${linksHtml}</div>
      ${extraHtml ? `<div class="pc-extras">${extraHtml}</div>` : ''}
    `;
    return nav;
  }

  // ── Inject ────────────────────────────────────────────────────────────────
  function inject() {
    if (document.getElementById('pc-topbar')) return; // already injected

    // Check if there's an existing topbar to replace.
    // Only replace a .topbar that is actually a site nav (has nav links or a
    // brand) — some pages (e.g. project.html) use .topbar for page-local
    // headers with breadcrumbs that JS depends on. Those get the nav
    // prepended ABOVE them instead of being replaced.
    let existing = document.querySelector('.topbar');
    if (existing && !existing.querySelector('nav') && !existing.querySelector('.brand')) {
      existing = null;
    }
    let extraHtml = '';

    if (existing) {
      // Preserve any .topbar-extra children (e.g. Brick badge on walkthrough)
      const extras = existing.querySelector('.topbar-extra, .permit-badge, .brick-label');
      if (extras) extraHtml = extras.outerHTML;

      const nav = buildNav(extraHtml);
      existing.parentNode.replaceChild(nav, existing);
    } else {
      // No existing topbar — prepend to body
      const nav = buildNav('');
      const body = document.body;
      body.insertBefore(nav, body.firstChild);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
