/**
 * PodClick Screen Recorder Widget v5
 * Loom-style: screen + floating circular camera bubble composited on canvas.
 */
(function () {
  'use strict';

  let _mr = null, _chunks = [], _blob = null;
  let _screenStream = null, _camStream = null, _micStream = null;
  let _micPreviewStream = null, _audioCtx = null, _analyser = null, _vuRAF = null;
  let _composeRAF = null, _composeCanvas = null, _composeCtx = null;
  let _screenVid = null, _camVid = null;
  let _timer = null, _secs = 0, _active = false, _visible = false;
  let _useHeadphones = true, _vuRunning = false;

  function _fmt(s) {
    return String(Math.floor(s / 60)).padStart(2,'0') + ':' + String(s % 60).padStart(2,'0');
  }

  // ── Styles ─────────────────────────────────────────────────────────
  const css = document.createElement('style');
  css.textContent = `
    #pcRec-launcher{position:fixed;bottom:24px;right:24px;z-index:99999;display:flex;flex-direction:column;align-items:flex-end;gap:10px;}
    #pcRec-fab{width:52px;height:52px;border-radius:50%;background:#d95f1e;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;color:#fff;box-shadow:0 4px 14px rgba(0,0,0,.35);transition:transform .12s,background .2s;}
    #pcRec-fab:hover{transform:scale(1.08);}
    #pcRec-fab.recording{background:#ef4444;animation:pcRecPulse 1.4s ease-in-out infinite;}
    @keyframes pcRecPulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.5);}50%{box-shadow:0 0 0 14px rgba(239,68,68,0);}}
    #pcRec-panel{background:#111009;border:1px solid #272420;border-radius:16px;padding:18px;width:320px;box-shadow:0 8px 32px rgba(0,0,0,.6);color:#e5ddd0;font-family:system-ui,sans-serif;font-size:13px;max-height:88vh;overflow-y:auto;}
    #pcRec-panel.hidden{display:none;}
    #pcRec-panel::-webkit-scrollbar{width:4px;}
    #pcRec-panel::-webkit-scrollbar-thumb{background:#272420;border-radius:2px;}
    .pR-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;}
    .pR-title{font-weight:700;font-size:14px;}
    .pR-x{background:none;border:none;color:#7a7060;cursor:pointer;font-size:20px;line-height:1;padding:0;}
    .pR-x:hover{color:#e5ddd0;}
    .pR-lbl{font-size:10px;font-weight:700;color:#7a7060;text-transform:uppercase;letter-spacing:.08em;margin:12px 0 6px;}
    .pR-sel{width:100%;padding:8px 10px;background:#0b0a08;border:1px solid #272420;border-radius:8px;color:#e5ddd0;font-size:12px;outline:none;cursor:pointer;margin-bottom:10px;}
    .pR-sel:focus{border-color:#d95f1e;}
    .pR-hp{display:flex;gap:8px;margin-bottom:12px;}
    .pR-hp-btn{flex:1;padding:6px;border-radius:20px;font-size:11px;font-weight:700;border:1px solid #272420;background:#0b0a08;color:#7a7060;cursor:pointer;transition:all .15s;}
    .pR-hp-btn.on{background:#d95f1e;border-color:#d95f1e;color:#fff;}
    .pR-mic-test-row{display:flex;align-items:center;gap:8px;margin-bottom:12px;}
    .pR-test-btn{padding:6px 12px;border-radius:7px;border:1px solid #272420;background:#0b0a08;color:#a09880;font-size:11px;cursor:pointer;white-space:nowrap;}
    .pR-test-btn.active{background:rgba(163,190,140,.15);border-color:#a3be8c;color:#a3be8c;}
    .pR-vu-track{flex:1;height:10px;background:#0b0a08;border:1px solid #272420;border-radius:5px;overflow:hidden;}
    .pR-vu-fill{height:100%;width:0%;border-radius:5px;background:linear-gradient(90deg,#a6e3a1 0%,#fab387 70%,#f38ba8 100%);transition:width .05s ease-out;}
    .pR-opt{display:flex;align-items:center;gap:6px;font-size:12px;color:#a09880;margin-bottom:10px;cursor:pointer;}
    .pR-opt input{accent-color:#d95f1e;cursor:pointer;}

    /* Camera bubble preview */
    .pR-cam-preview-wrap{position:relative;margin-bottom:10px;display:none;}
    .pR-cam-preview-wrap.show{display:block;}
    #pcRec-camPreview{width:80px;height:80px;border-radius:50%;object-fit:cover;border:3px solid #d95f1e;display:block;}
    .pR-bubble-pos{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;}
    .pR-pos-btn{flex:1;padding:5px 4px;border-radius:6px;border:1px solid #272420;background:#0b0a08;color:#7a7060;font-size:10px;cursor:pointer;text-align:center;transition:all .15s;}
    .pR-pos-btn.on{border-color:#d95f1e;color:#d95f1e;background:rgba(99,102,241,.1);}
    .pR-bubble-size{margin-top:6px;}
    .pR-bubble-size input{width:100%;accent-color:#d95f1e;}
    .pR-bubble-size label{font-size:10px;color:#7a7060;display:flex;justify-content:space-between;}

    hr.pR-hr{border:none;border-top:1px solid #272420;margin:14px 0;}
    .pR-big{width:100%;padding:11px;border-radius:10px;border:none;font-weight:700;font-size:13px;cursor:pointer;transition:opacity .15s,transform .1s;}
    .pR-big:hover:not(:disabled){opacity:.88;transform:translateY(-1px);}
    .pR-big:disabled{opacity:.5;cursor:not-allowed;}
    .pR-big.start{background:#d95f1e;color:#fff;}
    .pR-big.stop{background:#ef4444;color:#fff;}
    .pR-hint{font-size:11px;color:#7a7060;text-align:center;margin-top:10px;}
    .pR-timer{text-align:center;font-size:32px;font-weight:700;letter-spacing:.08em;font-variant-numeric:tabular-nums;color:#f38ba8;margin-bottom:6px;}
    .pR-status{text-align:center;font-size:11px;color:#7a7060;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px;}
    .pR-vid{width:100%;border-radius:8px;background:#000;max-height:150px;object-fit:contain;border:1px solid #272420;margin-bottom:8px;}
    .pR-row{display:flex;gap:8px;}
    .pR-sm{flex:1;padding:8px 6px;border-radius:7px;border:1px solid #272420;background:#0b0a08;color:#a09880;font-size:12px;cursor:pointer;transition:all .15s;}
    .pR-sm:hover{background:#272420;color:#e5ddd0;}
    .pR-sm:disabled{opacity:.4;cursor:default;}
    .pR-note{font-size:11px;color:#7a7060;margin-top:10px;line-height:1.5;}
    #pcRec-toast{position:fixed;bottom:90px;right:24px;z-index:100000;background:#272420;color:#e5ddd0;border-radius:8px;padding:8px 14px;font-size:12px;font-family:system-ui,sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.4);opacity:0;transition:opacity .2s;pointer-events:none;}
    #pcRec-toast.show{opacity:1;}
  `;
  document.head.appendChild(css);

  // ── DOM ────────────────────────────────────────────────────────────
  const launcher = document.createElement('div');
  launcher.id = 'pcRec-launcher';
  launcher.innerHTML = `
    <div id="pcRec-panel" class="hidden">
      <div class="pR-hdr">
        <span class="pR-title">🎬 Screen Recorder</span>
        <button class="pR-x" id="pcRec-X">×</button>
      </div>

      <!-- IDLE -->
      <div id="pcRec-idle">

        <!-- Mic -->
        <div class="pR-lbl">🎙️ Microphone</div>
        <select class="pR-sel" id="pcRec-micSel"><option value="">Default mic</option></select>
        <div class="pR-mic-test-row">
          <button class="pR-test-btn" id="pcRec-testBtn" onclick="window._pcTestMic()">🎙 Test Mic</button>
          <div class="pR-vu-track"><div class="pR-vu-fill" id="pcRec-vuFill"></div></div>
        </div>
        <div class="pR-lbl">🎧 Using headphones?</div>
        <div class="pR-hp">
          <button class="pR-hp-btn on" id="pcRec-hpY" onclick="window._pcHP(true)">Yes — headphones</button>
          <button class="pR-hp-btn" id="pcRec-hpN" onclick="window._pcHP(false)">No — speakers</button>
        </div>

        <hr class="pR-hr">

        <!-- Camera bubble -->
        <div class="pR-lbl">📹 Floating Camera (Loom-style)</div>
        <label class="pR-opt">
          <input type="checkbox" id="pcRec-camChk" onchange="window._pcToggleCamSetup()"> Show floating head bubble
        </label>

        <div id="pcRec-camSetup" style="display:none">
          <select class="pR-sel" id="pcRec-camSel"><option value="">Default camera</option></select>

          <!-- Live bubble preview -->
          <div class="pR-cam-preview-wrap" id="pcRec-camPreviewWrap">
            <video id="pcRec-camPreview" autoplay playsinline muted></video>
            <span style="font-size:10px;color:#7a7060;margin-left:8px;vertical-align:middle;">Live preview</span>
          </div>

          <!-- Position picker -->
          <div class="pR-lbl">Bubble position</div>
          <div class="pR-bubble-pos">
            <button class="pR-pos-btn" data-pos="bl" onclick="window._pcSetPos('bl')">↙ Bottom left</button>
            <button class="pR-pos-btn on" data-pos="br" onclick="window._pcSetPos('br')">↘ Bottom right</button>
            <button class="pR-pos-btn" data-pos="tl" onclick="window._pcSetPos('tl')">↖ Top left</button>
            <button class="pR-pos-btn" data-pos="tr" onclick="window._pcSetPos('tr')">↗ Top right</button>
          </div>

          <!-- Bubble size -->
          <div class="pR-bubble-size" style="margin-top:8px;">
            <label>
              <span>Bubble size</span>
              <span id="pcRec-sizeVal">20%</span>
            </label>
            <input type="range" id="pcRec-sizeRange" min="12" max="35" value="20"
              oninput="document.getElementById('pcRec-sizeVal').textContent=this.value+'%'">
          </div>
        </div>

        <hr class="pR-hr">

        <!-- Speaker -->
        <div class="pR-lbl">🔊 Speaker / Output</div>
        <select class="pR-sel" id="pcRec-spkSel"><option value="">Default speaker</option></select>

        <hr class="pR-hr">

        <label class="pR-opt">
          <input type="checkbox" id="pcRec-micChk" checked> Include microphone audio
        </label>
        <button class="pR-big start" id="pcRec-startBtn" onclick="window._pcStart()">⏺ Start Recording</button>
        <p class="pR-hint">Chooses screen or window after you click start</p>
      </div>

      <!-- RECORDING -->
      <div id="pcRec-rec" style="display:none">
        <div class="pR-timer" id="pcRec-runTimer">00:00</div>
        <div class="pR-status">🔴 Recording…</div>
        <button class="pR-big stop" onclick="window._pcStop()">⏹ Stop</button>
      </div>

      <!-- PREVIEW -->
      <div id="pcRec-prev" style="display:none">
        <video id="pcRec-vid" class="pR-vid" controls></video>
        <div class="pR-row">
          <button class="pR-sm" onclick="window._pcDlWebM()">⬇ WebM</button>
          <button class="pR-sm" id="pcRec-mp4Btn" onclick="window._pcConvert()">🎬 MP4</button>
          <button class="pR-sm" onclick="window._pcAgain()">🔄 Again</button>
        </div>
        <p class="pR-note"><strong style="color:#a09880;">TikTok review:</strong> Download MP4 → <a href="https://developers.tiktok.com/apps/" target="_blank" style="color:#89b4fa;">developers.tiktok.com</a></p>
      </div>
    </div>

    <button id="pcRec-fab" title="Screen Recorder">🎬</button>
  `;
  document.body.appendChild(launcher);

  const toastEl = document.createElement('div');
  toastEl.id = 'pcRec-toast';
  document.body.appendChild(toastEl);

  // Hidden elements for compositing
  _screenVid = document.createElement('video');
  _screenVid.autoplay = true;
  _screenVid.muted = true;
  _screenVid.playsInline = true;
  _screenVid.style.display = 'none';
  document.body.appendChild(_screenVid);

  _camVid = document.createElement('video');
  _camVid.autoplay = true;
  _camVid.muted = true;
  _camVid.playsInline = true;
  _camVid.style.display = 'none';
  document.body.appendChild(_camVid);

  // ── State ──────────────────────────────────────────────────────────
  let _bubblePos  = 'br';   // tl | tr | bl | br
  let _bubbleSize = 20;     // % of canvas height

  // ── Toast ──────────────────────────────────────────────────────────
  let _toastT = null;
  function _toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(_toastT);
    _toastT = setTimeout(() => toastEl.classList.remove('show'), 3200);
  }

  // ── FAB ────────────────────────────────────────────────────────────
  document.getElementById('pcRec-fab').addEventListener('click', () => {
    if (_active) { window._pcStop(); return; }
    _visible = !_visible;
    document.getElementById('pcRec-panel').classList.toggle('hidden', !_visible);
    if (_visible) _loadDevices();
  });
  document.getElementById('pcRec-X').addEventListener('click', () => {
    document.getElementById('pcRec-panel').classList.add('hidden');
    _visible = false;
    _killVU();
    _stopCamPreview();
  });

  // ── Load device lists ──────────────────────────────────────────────
  async function _loadDevices() {
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      probe.getTracks().forEach(t => t.stop());
    } catch (_) {}
    try {
      const all  = await navigator.mediaDevices.enumerateDevices();
      const mics = all.filter(d => d.kind === 'audioinput');
      const cams = all.filter(d => d.kind === 'videoinput');
      const spks = all.filter(d => d.kind === 'audiooutput');

      document.getElementById('pcRec-micSel').innerHTML =
        mics.map((d,i) => `<option value="${d.deviceId}">${d.label||'Microphone '+(i+1)}</option>`).join('');

      document.getElementById('pcRec-camSel').innerHTML =
        cams.map((d,i) => `<option value="${d.deviceId}">${d.label||'Camera '+(i+1)}</option>`).join('');

      document.getElementById('pcRec-spkSel').innerHTML =
        '<option value="">Default speaker</option>' +
        spks.map((d,i) => `<option value="${d.deviceId}">${d.label||'Speaker '+(i+1)}</option>`).join('');

      document.getElementById('pcRec-camSel').onchange = _restartCamPreview;
    } catch (e) { console.warn('[pcRec] enumerate:', e); }
  }

  // ── Headphones ─────────────────────────────────────────────────────
  window._pcHP = function (val) {
    _useHeadphones = val;
    document.getElementById('pcRec-hpY').classList.toggle('on', val);
    document.getElementById('pcRec-hpN').classList.toggle('on', !val);
  };

  // ── Bubble position + size ─────────────────────────────────────────
  window._pcSetPos = function (pos) {
    _bubblePos = pos;
    document.querySelectorAll('.pR-pos-btn').forEach(b => b.classList.toggle('on', b.dataset.pos === pos));
  };

  // ── Camera bubble setup toggle ─────────────────────────────────────
  window._pcToggleCamSetup = function () {
    const on = document.getElementById('pcRec-camChk').checked;
    document.getElementById('pcRec-camSetup').style.display = on ? 'block' : 'none';
    if (on) _restartCamPreview(); else _stopCamPreview();
  };

  async function _restartCamPreview() {
    _stopCamPreview();
    const camId = document.getElementById('pcRec-camSel')?.value;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { deviceId: camId ? { exact: camId } : undefined, width: 320, height: 320 },
        audio: false,
      });
      _camStream = stream;
      const prev = document.getElementById('pcRec-camPreview');
      prev.srcObject = stream;
      document.getElementById('pcRec-camPreviewWrap').classList.add('show');
    } catch (err) {
      _toast('Camera access failed: ' + err.message);
    }
  }

  function _stopCamPreview() {
    if (_camStream && !_active) {
      _camStream.getTracks().forEach(t => t.stop());
      _camStream = null;
    }
    const prev = document.getElementById('pcRec-camPreview');
    if (prev) prev.srcObject = null;
    document.getElementById('pcRec-camPreviewWrap')?.classList.remove('show');
  }

  // ── Mic test ───────────────────────────────────────────────────────
  window._pcTestMic = async function () {
    if (_vuRunning) {
      _killVU();
      document.getElementById('pcRec-testBtn').classList.remove('active');
      document.getElementById('pcRec-testBtn').textContent = '🎙 Test Mic';
      return;
    }
    const micId = document.getElementById('pcRec-micSel')?.value;
    try {
      _micPreviewStream = await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: micId ? { exact: micId } : undefined, echoCancellation: !_useHeadphones, noiseSuppression: !_useHeadphones, autoGainControl: !_useHeadphones },
        video: false,
      });
      _audioCtx = new AudioContext();
      _analyser = _audioCtx.createAnalyser();
      _analyser.fftSize = 512;
      _analyser.smoothingTimeConstant = 0.65;
      _audioCtx.createMediaStreamSource(_micPreviewStream).connect(_analyser);
      const buf = new Uint8Array(_analyser.frequencyBinCount);
      const fill = document.getElementById('pcRec-vuFill');
      _vuRunning = true;
      function tick() {
        if (!_vuRunning || !_analyser) return;
        _vuRAF = requestAnimationFrame(tick);
        _analyser.getByteFrequencyData(buf);
        const avg = buf.reduce((a,b) => a+b, 0) / buf.length;
        if (fill) fill.style.width = Math.min(100, Math.round(avg * 3)) + '%';
      }
      tick();
      document.getElementById('pcRec-testBtn').classList.add('active');
      document.getElementById('pcRec-testBtn').textContent = '⏹ Stop Test';
    } catch (err) { _toast('Mic access failed: ' + err.message); }
  };

  function _killVU() {
    _vuRunning = false;
    if (_vuRAF) { cancelAnimationFrame(_vuRAF); _vuRAF = null; }
    _analyser = null;
    if (_audioCtx) { try { _audioCtx.close(); } catch(_){} _audioCtx = null; }
    if (_micPreviewStream) { _micPreviewStream.getTracks().forEach(t => t.stop()); _micPreviewStream = null; }
    const fill = document.getElementById('pcRec-vuFill');
    if (fill) fill.style.width = '0%';
  }

  // ── Canvas compositor ──────────────────────────────────────────────
  function _startCompositor(screenStream, camStream) {
    _composeCanvas = document.createElement('canvas');
    // Match screen resolution
    const vt = screenStream.getVideoTracks()[0];
    const settings = vt.getSettings();
    _composeCanvas.width  = settings.width  || 1920;
    _composeCanvas.height = settings.height || 1080;
    _composeCtx = _composeCanvas.getContext('2d');

    _screenVid.srcObject = screenStream;
    if (camStream) _camVid.srcObject = camStream;

    function draw() {
      if (!_active) return;
      _composeRAF = requestAnimationFrame(draw);
      const W = _composeCanvas.width;
      const H = _composeCanvas.height;

      // Draw screen
      if (_screenVid.readyState >= 2) {
        _composeCtx.drawImage(_screenVid, 0, 0, W, H);
      } else {
        _composeCtx.fillStyle = '#000';
        _composeCtx.fillRect(0, 0, W, H);
      }

      // Draw camera bubble
      if (camStream && _camVid.readyState >= 2) {
        const sizeRange = document.getElementById('pcRec-sizeRange');
        const sizePct = sizeRange ? parseInt(sizeRange.value) : 20;
        const bubbleD = Math.round(H * sizePct / 100);
        const pad = Math.round(H * 0.025);
        let bx, by;
        switch (_bubblePos) {
          case 'tl': bx = pad;           by = pad;           break;
          case 'tr': bx = W-bubbleD-pad; by = pad;           break;
          case 'bl': bx = pad;           by = H-bubbleD-pad; break;
          default:   bx = W-bubbleD-pad; by = H-bubbleD-pad; break; // br
        }
        const cx = bx + bubbleD / 2;
        const cy = by + bubbleD / 2;
        const r  = bubbleD / 2;

        // Shadow
        _composeCtx.save();
        _composeCtx.shadowColor = 'rgba(0,0,0,0.5)';
        _composeCtx.shadowBlur  = 16;
        _composeCtx.beginPath();
        _composeCtx.arc(cx, cy, r, 0, Math.PI * 2);
        _composeCtx.fillStyle = '#000';
        _composeCtx.fill();
        _composeCtx.restore();

        // Clip circle + draw camera
        _composeCtx.save();
        _composeCtx.beginPath();
        _composeCtx.arc(cx, cy, r, 0, Math.PI * 2);
        _composeCtx.clip();
        _composeCtx.drawImage(_camVid, bx, by, bubbleD, bubbleD);
        _composeCtx.restore();

        // Ring border
        _composeCtx.beginPath();
        _composeCtx.arc(cx, cy, r, 0, Math.PI * 2);
        _composeCtx.strokeStyle = '#d95f1e';
        _composeCtx.lineWidth = Math.max(3, Math.round(bubbleD * 0.03));
        _composeCtx.stroke();
      }
    }
    draw();
    return _composeCanvas.captureStream(30);
  }

  function _stopCompositor() {
    if (_composeRAF) { cancelAnimationFrame(_composeRAF); _composeRAF = null; }
    _screenVid.srcObject = null;
    _camVid.srcObject = null;
    _composeCanvas = null;
    _composeCtx = null;
  }

  // ── Start ──────────────────────────────────────────────────────────
  window._pcStart = async function () {
    const btn = document.getElementById('pcRec-startBtn');
    btn.disabled = true; btn.textContent = 'Preparing…';

    // Kill all previews first
    _killVU();
    const useCam = document.getElementById('pcRec-camChk').checked;
    // Keep camStream alive if bubble enabled — we need it
    if (!useCam) _stopCamPreview();

    await new Promise(r => setTimeout(r, 500));

    try {
      const wantMic = document.getElementById('pcRec-micChk').checked;
      const micId   = document.getElementById('pcRec-micSel')?.value;
      const camId   = document.getElementById('pcRec-camSel')?.value;

      // Screen capture
      _screenStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30, width: { ideal: 1920 } },
        audio: false,
      });

      // Camera for bubble (fresh stream if not already running)
      let activeCamStream = null;
      if (useCam) {
        try {
          // Stop preview stream, get fresh one
          if (_camStream) { _camStream.getTracks().forEach(t => t.stop()); _camStream = null; }
          activeCamStream = await navigator.mediaDevices.getUserMedia({
            video: { deviceId: camId ? { exact: camId } : undefined, width: 640, height: 640, frameRate: 30 },
            audio: false,
          });
          _camStream = activeCamStream;
        } catch (err) {
          _toast('Camera unavailable — recording without bubble');
          activeCamStream = null;
        }
      }

      // Build composite or raw stream
      let videoStream;
      if (activeCamStream) {
        videoStream = _startCompositor(_screenStream, activeCamStream);
      } else {
        videoStream = _screenStream;
      }

      // Mic track
      const tracks = [...videoStream.getVideoTracks()];
      if (wantMic) {
        try {
          _micStream = await navigator.mediaDevices.getUserMedia({
            audio: { deviceId: micId ? { exact: micId } : undefined, echoCancellation: !_useHeadphones, noiseSuppression: !_useHeadphones, autoGainControl: !_useHeadphones },
            video: false,
          });
          _micStream.getAudioTracks().forEach(t => tracks.push(t));
        } catch (_) { _toast('Mic unavailable — no audio'); }
      }

      _chunks = [];
      _secs   = 0;

      const mime = ['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm']
        .find(m => MediaRecorder.isTypeSupported(m)) || '';

      _mr = new MediaRecorder(new MediaStream(tracks), {
        ...(mime ? { mimeType: mime } : {}),
        videoBitsPerSecond: 3_000_000,
      });
      _mr.ondataavailable = e => { if (e.data && e.data.size > 0) _chunks.push(e.data); };
      _mr.onstop = _showPreview;
      _mr.start(2000);
      _active = true;

      document.getElementById('pcRec-fab').classList.add('recording');
      document.getElementById('pcRec-idle').style.display = 'none';
      document.getElementById('pcRec-rec').style.display  = 'block';
      document.getElementById('pcRec-panel').classList.remove('hidden');
      _visible = true;

      _timer = setInterval(() => {
        _secs++;
        const el = document.getElementById('pcRec-runTimer');
        if (el) el.textContent = _fmt(_secs);
      }, 1000);

      _screenStream.getVideoTracks()[0].addEventListener('ended', () => {
        if (_active) window._pcStop();
      });

    } catch (err) {
      btn.disabled = false; btn.textContent = '⏺ Start Recording';
      if (err.name !== 'NotAllowedError') _toast('Could not start: ' + err.message);
    }
  };

  // ── Stop ───────────────────────────────────────────────────────────
  window._pcStop = function () {
    if (!_mr || !_active) return;
    _active = false;
    clearInterval(_timer);
    _stopCompositor();
    try { _mr.stop(); } catch (_) {}
    [_screenStream, _camStream, _micStream].forEach(s => {
      if (s) s.getTracks().forEach(t => t.stop());
    });
    _screenStream = _camStream = _micStream = null;
    document.getElementById('pcRec-fab').classList.remove('recording');
    document.getElementById('pcRec-rec').style.display = 'none';
  };

  function _showPreview() {
    _blob = new Blob(_chunks, { type: 'video/webm' });
    const vid = document.getElementById('pcRec-vid');
    if (vid) {
      vid.src = URL.createObjectURL(_blob);
      const spkId = document.getElementById('pcRec-spkSel')?.value;
      if (spkId && typeof vid.setSinkId === 'function') vid.setSinkId(spkId).catch(() => {});
    }
    document.getElementById('pcRec-prev').style.display = 'block';
    _toast('✅ Done — ' + _fmt(_secs));
  }

  // ── Download / Convert ─────────────────────────────────────────────
  window._pcDlWebM = function () {
    if (!_blob) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(_blob);
    a.download = 'recording-' + Date.now() + '.webm';
    a.click();
  };

  window._pcConvert = async function () {
    if (!_blob) return;
    const btn = document.getElementById('pcRec-mp4Btn');
    btn.disabled = true; btn.textContent = '⏳…';
    _toast('Converting to MP4…');
    try {
      const fd = new FormData();
      fd.append('file', _blob, 'recording.webm');
      const res = await fetch('/api/screen-record/convert', { method: 'POST', body: fd });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await res.blob());
      a.download = 'recording-' + Date.now() + '.mp4';
      a.click();
      _toast('✅ MP4 downloaded!');
    } catch (err) {
      _toast('Convert failed: ' + err.message);
    } finally {
      btn.disabled = false; btn.textContent = '🎬 MP4';
    }
  };

  window._pcAgain = function () {
    _blob = null; _chunks = []; _secs = 0;
    const vid = document.getElementById('pcRec-vid');
    if (vid) vid.src = '';
    document.getElementById('pcRec-runTimer').textContent = '00:00';
    document.getElementById('pcRec-idle').style.display  = 'block';
    document.getElementById('pcRec-prev').style.display  = 'none';
    document.getElementById('pcRec-rec').style.display   = 'none';
    const btn = document.getElementById('pcRec-startBtn');
    btn.disabled = false; btn.textContent = '⏺ Start Recording';
    document.getElementById('pcRec-testBtn').classList.remove('active');
    document.getElementById('pcRec-testBtn').textContent = '🎙 Test Mic';
    _loadDevices();
  };

})();
