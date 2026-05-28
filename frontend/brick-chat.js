/**
 * brick-chat.js — Brick floating chat widget
 * Phase 3B · PodClick
 *
 * Self-injects #brick-bubble and #brick-panel into document.body.
 * Uses the host page's existing CSS variables — no new tokens.
 * Streams via fetch + ReadableStream (not EventSource — POST only).
 */
(function () {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────────────

  var CHAT_ENDPOINT = '/api/brick/chat';
  var MSG_ENDPOINT  = '/api/brick/messages';
  var STORAGE_KEY   = 'brick_panel_open';
  var MAX_HISTORY   = 60; // messages to load on open

  // Resolve the current screen name from the page URL/title
  function currentScreen() {
    var p = window.location.pathname.replace('/', '').replace('.html', '') || 'home';
    return p || 'home';
  }

  // ── Inject styles ────────────────────────────────────────────────────────────

  var style = document.createElement('style');
  style.textContent = [
    /* --- bubble --- */
    '#brick-bubble {',
    '  position: fixed;',
    '  bottom: 24px;',
    '  right: 24px;',
    '  z-index: 9998;',
    '  width: 48px;',
    '  height: 48px;',
    '  border-radius: 50%;',
    '  background: var(--surface2, #1e1e1e);',
    '  border: 1px solid var(--border, #2e2e2e);',
    '  cursor: pointer;',
    '  display: flex;',
    '  align-items: center;',
    '  justify-content: center;',
    '  box-shadow: 0 2px 8px rgba(0,0,0,0.4);',
    '  transition: background 0.15s ease;',
    '  user-select: none;',
    '}',
    '#brick-bubble:hover { background: var(--surface, #111); }',
    '#brick-bubble svg { pointer-events: none; }',

    /* --- panel --- */
    '#brick-panel {',
    '  position: fixed;',
    '  bottom: 84px;',
    '  right: 24px;',
    '  z-index: 9999;',
    '  width: min(480px, calc(100vw - 32px));',
    '  height: min(560px, calc(100vh - 110px));',
    '  background: var(--bg, #0a0a0a);',
    '  border: 1px solid var(--border, #2e2e2e);',
    '  border-radius: var(--radius, 12px);',
    '  display: flex;',
    '  flex-direction: column;',
    '  overflow: hidden;',
    '  box-shadow: 0 8px 32px rgba(0,0,0,0.6);',
    '  transform: translateY(12px);',
    '  opacity: 0;',
    '  pointer-events: none;',
    '  transition: transform 0.2s ease, opacity 0.2s ease;',
    '}',
    '#brick-panel.brick-open {',
    '  transform: translateY(0);',
    '  opacity: 1;',
    '  pointer-events: all;',
    '}',

    /* mobile: full-screen below 520px */
    '@media (max-width: 520px) {',
    '  #brick-panel {',
    '    bottom: 0; right: 0; left: 0;',
    '    width: 100%; height: 100%;',
    '    border-radius: 0;',
    '    border-left: none; border-right: none; border-bottom: none;',
    '  }',
    '  #brick-bubble { bottom: 16px; right: 16px; }',
    '}',

    /* --- header --- */
    '#brick-panel-header {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 8px;',
    '  padding: 12px 16px;',
    '  border-bottom: 1px solid var(--border, #2e2e2e);',
    '  flex-shrink: 0;',
    '}',
    '#brick-panel-title {',
    '  font-family: var(--font, Inter, sans-serif);',
    '  font-size: 14px;',
    '  font-weight: 600;',
    '  color: var(--text, #e5e5e5);',
    '  flex: 1;',
    '}',
    '#brick-tier-badge {',
    '  font-size: 10px;',
    '  font-weight: 600;',
    '  letter-spacing: 0.05em;',
    '  text-transform: uppercase;',
    '  padding: 2px 7px;',
    '  border-radius: 4px;',
    '  background: var(--surface2, #1e1e1e);',
    '  color: var(--text-muted, #888);',
    '  border: 1px solid var(--border, #2e2e2e);',
    '}',
    '#brick-close-btn {',
    '  background: none;',
    '  border: none;',
    '  cursor: pointer;',
    '  color: var(--text-muted, #888);',
    '  padding: 4px;',
    '  border-radius: 4px;',
    '  display: flex;',
    '  align-items: center;',
    '  line-height: 1;',
    '}',
    '#brick-close-btn:hover { color: var(--text, #e5e5e5); }',

    /* --- messages area --- */
    '#brick-messages {',
    '  flex: 1;',
    '  overflow-y: auto;',
    '  padding: 16px;',
    '  display: flex;',
    '  flex-direction: column;',
    '  gap: 10px;',
    '}',
    '#brick-messages::-webkit-scrollbar { width: 4px; }',
    '#brick-messages::-webkit-scrollbar-thumb { background: var(--border, #2e2e2e); border-radius: 2px; }',

    /* --- individual message bubbles --- */
    '.brick-msg {',
    '  max-width: 85%;',
    '  padding: 8px 12px;',
    '  border-radius: 10px;',
    '  font-family: var(--font, Inter, sans-serif);',
    '  font-size: 13px;',
    '  line-height: 1.55;',
    '  color: var(--text, #e5e5e5);',
    '  white-space: pre-wrap;',
    '  word-break: break-word;',
    '}',
    '.brick-msg-user {',
    '  align-self: flex-end;',
    '  background: var(--accent, #3b5bdb);',
    '  color: #fff;',
    '  border-bottom-right-radius: 3px;',
    '}',
    '.brick-msg-brick {',
    '  align-self: flex-start;',
    '  background: var(--surface2, #1e1e1e);',
    '  border-bottom-left-radius: 3px;',
    '}',
    '.brick-msg-tool {',
    '  align-self: flex-start;',
    '  background: none;',
    '  border: 1px solid var(--border, #2e2e2e);',
    '  color: var(--text-muted, #888);',
    '  font-size: 11px;',
    '  border-radius: 6px;',
    '  padding: 4px 10px;',
    '}',
    '.brick-msg-ts {',
    '  font-size: 10px;',
    '  color: var(--text-muted, #888);',
    '  align-self: center;',
    '  padding: 2px 0;',
    '}',
    '.brick-empty-state {',
    '  align-self: center;',
    '  color: var(--text-muted, #888);',
    '  font-family: var(--font, Inter, sans-serif);',
    '  font-size: 13px;',
    '  margin-top: auto;',
    '  margin-bottom: auto;',
    '  text-align: center;',
    '}',
    /* typing indicator */
    '.brick-typing {',
    '  align-self: flex-start;',
    '  background: var(--surface2, #1e1e1e);',
    '  border-radius: 10px;',
    '  border-bottom-left-radius: 3px;',
    '  padding: 10px 14px;',
    '  display: flex;',
    '  gap: 4px;',
    '  align-items: center;',
    '}',
    '.brick-typing span {',
    '  width: 5px; height: 5px;',
    '  border-radius: 50%;',
    '  background: var(--text-muted, #888);',
    '  animation: brickDot 1.2s infinite;',
    '}',
    '.brick-typing span:nth-child(2) { animation-delay: 0.2s; }',
    '.brick-typing span:nth-child(3) { animation-delay: 0.4s; }',
    '@keyframes brickDot { 0%,80%,100% { opacity: 0.2; } 40% { opacity: 1; } }',

    /* --- input row --- */
    '#brick-input-row {',
    '  display: flex;',
    '  gap: 8px;',
    '  padding: 12px 16px;',
    '  border-top: 1px solid var(--border, #2e2e2e);',
    '  flex-shrink: 0;',
    '}',
    '#brick-input {',
    '  flex: 1;',
    '  background: var(--surface2, #1e1e1e);',
    '  border: 1px solid var(--border, #2e2e2e);',
    '  border-radius: 8px;',
    '  color: var(--text, #e5e5e5);',
    '  font-family: var(--font, Inter, sans-serif);',
    '  font-size: 13px;',
    '  padding: 8px 12px;',
    '  resize: none;',
    '  outline: none;',
    '  line-height: 1.4;',
    '  max-height: 120px;',
    '  overflow-y: auto;',
    '}',
    '#brick-input::placeholder { color: var(--text-muted, #888); }',
    '#brick-input:focus { border-color: var(--accent, #3b5bdb); }',
    '#brick-send-btn {',
    '  background: var(--accent, #3b5bdb);',
    '  border: none;',
    '  border-radius: 8px;',
    '  color: #fff;',
    '  cursor: pointer;',
    '  padding: 0 14px;',
    '  font-size: 13px;',
    '  font-weight: 600;',
    '  font-family: var(--font, Inter, sans-serif);',
    '  flex-shrink: 0;',
    '  transition: opacity 0.15s;',
    '}',
    '#brick-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }',
    '#brick-send-btn:hover:not(:disabled) { opacity: 0.85; }',
  ].join('\n');
  document.head.appendChild(style);

  // ── DOM construction ─────────────────────────────────────────────────────────

  // Bubble
  var bubble = document.createElement('div');
  bubble.id = 'brick-bubble';
  bubble.title = 'Talk to Brick';
  bubble.innerHTML = [
    '<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">',
    '  <!-- Simple hammer icon built from rectangles/paths -->',
    '  <rect x="2" y="10" width="9" height="5" rx="1" fill="#888"/>',
    '  <rect x="9" y="7" width="11" height="4" rx="1.5" fill="#aaa"/>',
    '  <rect x="10.5" y="4" width="3.5" height="5" rx="1" fill="#aaa"/>',
    '</svg>',
  ].join('');
  document.body.appendChild(bubble);

  // Panel
  var panel = document.createElement('div');
  panel.id = 'brick-panel';
  panel.setAttribute('aria-label', 'Brick chat panel');
  panel.innerHTML = [
    '<div id="brick-panel-header">',
    '  <span id="brick-panel-title">Brick</span>',
    '  <span id="brick-tier-badge">—</span>',
    '  <button id="brick-close-btn" title="Close" aria-label="Close Brick panel">',
    '    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">',
    '      <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    '    </svg>',
    '  </button>',
    '</div>',
    '<div id="brick-messages">',
    '  <p class="brick-empty-state">What\'s on your mind?</p>',
    '</div>',
    '<div id="brick-input-row">',
    '  <textarea',
    '    id="brick-input"',
    '    rows="1"',
    '    placeholder="Ask Brick…"',
    '  ></textarea>',
    '  <button id="brick-send-btn" disabled>Send</button>',
    '</div>',
  ].join('');
  document.body.appendChild(panel);

  // ── State ────────────────────────────────────────────────────────────────────

  var isOpen     = false;
  var isStreaming = false;
  var lastMsgTime = null; // ISO string of last visible message

  // ── Element refs ─────────────────────────────────────────────────────────────

  var msgContainer = document.getElementById('brick-messages');
  var inputEl      = document.getElementById('brick-input');
  var sendBtn      = document.getElementById('brick-send-btn');
  var tierBadge    = document.getElementById('brick-tier-badge');

  // ── Tier badge color map ──────────────────────────────────────────────────────

  var TIER_COLORS = {
    'owner_builder': '#94a3b8',
    'draftsman':     '#60a5fa',
    'bricklayer':    '#fb923c',
    'foreman':       '#c084fc',
    'gc':            '#FFB340',
  };

  function applyTierBadge(tier) {
    if (!tier) return;
    var label = tier.replace('_', '-');
    tierBadge.textContent = label;
    var color = TIER_COLORS[tier] || '#888';
    tierBadge.style.color = color;
    tierBadge.style.borderColor = color + '44';
    tierBadge.style.background = color + '18';
  }

  // ── Panel open / close ───────────────────────────────────────────────────────

  function openPanel() {
    isOpen = true;
    panel.classList.add('brick-open');
    try { sessionStorage.setItem(STORAGE_KEY, '1'); } catch (e) {}
    loadHistory();
    loadPermitTier();
    // Delay focus so animation is visible
    setTimeout(function () { inputEl.focus(); }, 220);
  }

  function closePanel() {
    isOpen = false;
    panel.classList.remove('brick-open');
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  // ── Permit tier ──────────────────────────────────────────────────────────────

  function loadPermitTier() {
    fetch('/api/brick/permit')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.current_tier) {
          applyTierBadge(data.current_tier);
        }
      })
      .catch(function () {});
  }

  // ── History load ─────────────────────────────────────────────────────────────

  function loadHistory() {
    fetch(MSG_ENDPOINT + '?limit=' + MAX_HISTORY)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.messages || !data.messages.length) return;
        clearMessages();
        data.messages.forEach(function (m) {
          appendMessage(m.role, m.content, m.created_at);
        });
        scrollToBottom();
      })
      .catch(function () {});
  }

  // ── Message rendering ────────────────────────────────────────────────────────

  function clearMessages() {
    msgContainer.innerHTML = '';
    lastMsgTime = null;
  }

  /** Show a timestamp divider if >1hr has elapsed since the last visible message. */
  function maybeShowTimestamp(isoStr) {
    if (!isoStr) return;
    var now = new Date(isoStr);
    if (lastMsgTime) {
      var last = new Date(lastMsgTime);
      var diffMs = now - last;
      if (diffMs > 60 * 60 * 1000) {
        var el = document.createElement('div');
        el.className = 'brick-msg-ts';
        el.textContent = now.toLocaleString('en-US', {
          month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        });
        msgContainer.appendChild(el);
      }
    }
    lastMsgTime = isoStr;
  }

  function appendMessage(role, content, isoTs) {
    maybeShowTimestamp(isoTs);
    var el = document.createElement('div');
    if (role === 'user') {
      el.className = 'brick-msg brick-msg-user';
    } else if (role === 'tool') {
      el.className = 'brick-msg brick-msg-tool';
    } else {
      el.className = 'brick-msg brick-msg-brick';
    }
    el.textContent = content;
    msgContainer.appendChild(el);
    return el;
  }

  function addTypingIndicator() {
    var el = document.createElement('div');
    el.className = 'brick-typing';
    el.id = 'brick-typing-el';
    el.innerHTML = '<span></span><span></span><span></span>';
    msgContainer.appendChild(el);
    scrollToBottom();
    return el;
  }

  function removeTypingIndicator() {
    var el = document.getElementById('brick-typing-el');
    if (el) el.parentNode.removeChild(el);
  }

  function scrollToBottom() {
    msgContainer.scrollTop = msgContainer.scrollHeight;
  }

  // ── Send message ─────────────────────────────────────────────────────────────

  function sendMessage() {
    var text = inputEl.value.trim();
    if (!text || isStreaming) return;

    // Clear empty state
    var empty = msgContainer.querySelector('.brick-empty-state');
    if (empty) empty.parentNode.removeChild(empty);

    // Show user bubble
    appendMessage('user', text, new Date().toISOString());
    scrollToBottom();

    // Clear input, disable send
    inputEl.value = '';
    inputEl.style.height = '';
    sendBtn.disabled = true;
    isStreaming = true;

    // Typing indicator
    var typingEl = addTypingIndicator();
    var brickBubble = null; // will be created on first token

    fetch(CHAT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        context_screen: currentScreen(),
        context_data: {},
      }),
    })
      .then(function (response) {
        if (!response.ok || !response.body) {
          throw new Error('HTTP ' + response.status);
        }
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';

        function read() {
          return reader.read().then(function (result) {
            if (result.done) {
              finalize();
              return;
            }
            buffer += decoder.decode(result.value, { stream: true });
            var parts = buffer.split('\n\n');
            buffer = parts.pop(); // keep partial last chunk

            parts.forEach(function (part) {
              part = part.trim();
              if (!part.startsWith('data:')) return;
              var raw = part.slice(5).trim();
              if (raw === '[DONE]') {
                finalize();
                return;
              }
              try {
                var obj = JSON.parse(raw);
                if (obj.t !== undefined) {
                  // text token
                  if (!brickBubble) {
                    removeTypingIndicator();
                    brickBubble = appendMessage('brick', '', new Date().toISOString());
                  }
                  brickBubble.textContent += obj.t;
                  scrollToBottom();
                } else if (obj.tool !== undefined) {
                  // tool notification
                  appendMessage('tool', '⚙ ' + obj.tool, new Date().toISOString());
                  scrollToBottom();
                }
              } catch (e) {
                // ignore parse errors on partial lines
              }
            });

            return read();
          });
        }

        return read();
      })
      .catch(function (err) {
        removeTypingIndicator();
        if (!brickBubble) {
          brickBubble = appendMessage('brick', '', new Date().toISOString());
        }
        brickBubble.textContent = 'Lost the signal. Try again.';
        scrollToBottom();
        finalize();
      });

    function finalize() {
      removeTypingIndicator();
      isStreaming = false;
      sendBtn.disabled = inputEl.value.trim().length === 0;
    }
  }

  // ── Input auto-resize ────────────────────────────────────────────────────────

  inputEl.addEventListener('input', function () {
    this.style.height = '';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    sendBtn.disabled = this.value.trim().length === 0 || isStreaming;
  });

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      sendMessage();
    }
  });

  sendBtn.addEventListener('click', sendMessage);

  // ── Toggle / close ───────────────────────────────────────────────────────────

  bubble.addEventListener('click', function () {
    if (isOpen) { closePanel(); } else { openPanel(); }
  });

  document.getElementById('brick-close-btn').addEventListener('click', closePanel);

  // Close on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isOpen) closePanel();
  });

  // ── Restore state from sessionStorage ───────────────────────────────────────

  try {
    if (sessionStorage.getItem(STORAGE_KEY) === '1') {
      // Slight delay so page CSS vars are resolved
      setTimeout(openPanel, 100);
    }
  } catch (e) {}

})();
