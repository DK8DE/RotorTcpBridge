
    // —— Web Serial (Display-Controller) ——
    const WebSerial = (function() {
      const BAUD = 115200;
      const NUM_RE = /[-+]?\d+(?:[.,]\d+)?/g;
      let port = null;
      let reader = null;
      let writer = null;
      let keepReading = false;
      let rxBuf = '';
      let pending = [];
      let pollTimer = null;
      let linkMode = null;
      let pcId = 1;
      let contId = 2;
      let azId = 20;
      let elId = 21;
      let enableEl = false;
      let localLive = false;
      let onLive = null;
      let onStatus = null;

      function lastNumber(params) {
        const m = String(params || '').match(NUM_RE);
        if (!m || !m.length) return 0;
        return parseFloat(m[m.length - 1].replace(',', '.'));
      }
      function calcCs(src, dst, params) {
        return Number(src) + Number(dst) + lastNumber(params);
      }
      function fmtCs(cs) {
        if (Math.abs(cs - Math.round(cs)) < 0.005) return String(Math.round(cs));
        let s = cs.toFixed(2).replace('.', ',');
        if (s.indexOf(',') >= 0) s = s.replace(/0+$/, '').replace(/,$/, '');
        return s;
      }
      function build(src, dst, cmd, params) {
        const p = (params == null || params === '') ? '0' : String(params);
        const cs = calcCs(src, dst, p);
        return '#' + src + ':' + dst + ':' + cmd + ':' + p + ':' + fmtCs(cs) + '$';
      }
      function parseLine(line) {
        line = String(line || '').trim();
        if (!(line.charAt(0) === '#' && line.charAt(line.length - 1) === '$')) return null;
        const body = line.slice(1, -1);
        const parts = body.split(':');
        if (parts.length < 5) return null;
        const src = parseInt(parts[0], 10);
        const dst = parseInt(parts[1], 10);
        const cmd = parts[2];
        const params = parts.slice(3, -1).join(':');
        const cs = parseFloat(String(parts[parts.length - 1]).replace(',', '.'));
        if (isNaN(src) || isNaN(dst) || isNaN(cs)) return null;
        const expected = calcCs(src, dst, params);
        return { src, dst, cmd, params, cs, ok: Math.abs(cs - expected) <= 0.02, raw: line };
      }
      function supported() {
        return !!(navigator && navigator.serial);
      }
      function secureOk() {
        return !!(window.isSecureContext);
      }
      function setIds(cfg) {
        if (!cfg) return;
        if (cfg.pc_id != null) pcId = Number(cfg.pc_id) || pcId;
        else if (cfg.master_id != null) {
          const m = Number(cfg.master_id);
          pcId = (m > 0) ? m : 1;
        }
        if (cfg.cont_id != null) contId = Number(cfg.cont_id) || contId;
        if (cfg.az_id != null) azId = Number(cfg.az_id) || azId;
        if (cfg.el_id != null) elId = Number(cfg.el_id) || elId;
        enableEl = !!cfg.enable_el;
      }
      function setCallbacks(cbs) {
        onLive = cbs && cbs.onLive;
        onStatus = cbs && cbs.onStatus;
      }
      function emitStatus() {
        if (typeof onStatus === 'function') {
          onStatus({
            connected: !!port,
            localLive: localLive,
            linkMode: linkMode,
            supported: supported(),
            secure: secureOk()
          });
        }
      }
      async function writeFrame(frame) {
        if (!writer) throw new Error('not connected');
        const data = new TextEncoder().encode(frame);
        await writer.write(data);
      }
      function waitReply(pred, timeoutMs) {
        const t0 = Date.now();
        const ms = timeoutMs || 1200;
        return new Promise(function(resolve, reject) {
          const item = {
            pred: pred,
            resolve: resolve,
            reject: reject,
            timer: setTimeout(function() {
              const i = pending.indexOf(item);
              if (i >= 0) pending.splice(i, 1);
              reject(new Error('timeout'));
            }, ms)
          };
          pending.push(item);
        });
      }
      function dispatchLine(line) {
        const tel = parseLine(line);
        if (!tel) return;
        for (let i = 0; i < pending.length; i++) {
          const p = pending[i];
          try {
            if (p.pred(tel)) {
              clearTimeout(p.timer);
              pending.splice(i, 1);
              p.resolve(tel);
              return;
            }
          } catch (e) {}
        }
      }
      async function readLoop() {
        const dec = new TextDecoder();
        while (keepReading && port && reader) {
          try {
            const r = await reader.read();
            if (r.done) break;
            rxBuf += dec.decode(r.value || new Uint8Array(), { stream: true });
            let idx;
            while ((idx = rxBuf.indexOf('$')) >= 0) {
              const chunk = rxBuf.slice(0, idx + 1);
              rxBuf = rxBuf.slice(idx + 1);
              const hash = chunk.lastIndexOf('#');
              if (hash >= 0) dispatchLine(chunk.slice(hash));
            }
            if (rxBuf.length > 8192) rxBuf = rxBuf.slice(-4096);
          } catch (e) {
            break;
          }
        }
      }
      async function requestTo(dst, cmd, params, timeoutMs) {
        const d = (dst != null) ? dst : contId;
        const frame = build(pcId, d, cmd, (params != null) ? params : '0');
        const want = 'ACK_' + cmd;
        const nak = 'NAK_' + cmd;
        const p = waitReply(function(tel) {
          return tel.cmd === want || tel.cmd === nak;
        }, timeoutMs);
        await writeFrame(frame);
        return p;
      }
      async function request(cmd, params, timeoutMs) {
        return requestTo(contId, cmd, params, timeoutMs);
      }
      function linkModeName(m) {
        if (m === 1) return 'PC-USB';
        if (m === 2) return 'WEB';
        if (m === 0) return 'RS485';
        return '—';
      }
      async function queryLinkMode() {
        try {
          const tel = await request('GETLINKMODE', '0', 1500);
          if (tel.cmd === 'ACK_GETLINKMODE') {
            linkMode = parseInt(String(tel.params).replace(',', '.'), 10);
            if (isNaN(linkMode)) linkMode = null;
            emitStatus();
            return linkMode;
          }
        } catch (e) {}
        try {
          const tel = await request('GETCONREMOTE', '0', 1500);
          if (tel.cmd === 'ACK_GETCONREMOTE') {
            const v = parseInt(String(tel.params).replace(',', '.'), 10);
            linkMode = (v === 1) ? 1 : 0;
            emitStatus();
            return linkMode;
          }
        } catch (e2) {}
        return null;
      }
      function parseDeg(params) {
        const n = lastNumber(params);
        return isNaN(n) ? null : n;
      }
      async function pollOnce() {
        if (!port || !localLive) return;
        const live = { az: {}, el: {} };
        try {
          const pos = await requestTo(azId, 'GETPOSDG', '0', 900);
          if (pos && pos.cmd.indexOf('ACK_GETPOSDG') === 0) {
            live.az.ist = parseDeg(pos.params);
          }
        } catch (e) {}
        try {
          const ref = await requestTo(azId, 'GETREF', '0', 700);
          if (ref && ref.cmd.indexOf('ACK_GETREF') === 0) {
            live.az.referenced = lastNumber(ref.params) >= 0.5;
          }
        } catch (e) {}
        live.az.online = true;
        live.az.moving = false;
        if (enableEl && elId) {
          try {
            const epos = await requestTo(elId, 'GETPOSDG', '0', 900);
            if (epos && epos.cmd.indexOf('ACK_GETPOSDG') === 0) {
              live.el.ist = parseDeg(epos.params);
            }
          } catch (e) {}
          live.el.online = true;
        }
        if (typeof onLive === 'function') onLive(live);
      }
      function startPoll() {
        stopPoll();
        pollTimer = setInterval(function() { pollOnce(); }, 330);
        pollOnce();
      }
      function stopPoll() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      }
      async function connect() {
        if (!supported()) {
          throw new Error('Web Serial nicht verfügbar — bitte Chrome oder Edge verwenden');
        }
        if (!secureOk()) {
          throw new Error(
            'Ohne HTTPS blockiert der Browser den COM-Port-Dialog.\n' +
            'Einstellungen → Webserver → „HTTPS (TLS) aktiv“ einschalten,\n' +
            'Seite neu unter https://…/compass öffnen und Zertifikat akzeptieren.'
          );
        }
        const p = await navigator.serial.requestPort({});
        await p.open({ baudRate: BAUD, bufferSize: 8192 });
        // DTR/RTS nicht togglen — Display-Reset vermeiden
        try {
          if (p.setSignals) await p.setSignals({ dataTerminalReady: false, requestToSend: false });
        } catch (e) {}
        port = p;
        keepReading = true;
        reader = p.readable.getReader();
        writer = p.writable.getWriter();
        readLoop();
        localLive = true;
        emitStatus();
        await queryLinkMode();
        startPoll();
      }
      async function disconnect() {
        localLive = false;
        stopPoll();
        keepReading = false;
        try { if (reader) { await reader.cancel(); reader.releaseLock(); } } catch (e) {}
        try { if (writer) { writer.releaseLock(); } } catch (e) {}
        reader = null; writer = null;
        try { if (port) await port.close(); } catch (e) {}
        port = null;
        pending.forEach(function(p) { try { clearTimeout(p.timer); p.reject(new Error('closed')); } catch (e) {} });
        pending = [];
        emitStatus();
      }
      async function setAz(deg) {
        const d = Number(deg);
        if (isNaN(d)) return;
        await requestTo(azId, 'SETPOSDG', String(d.toFixed(1)).replace('.', ','), 1200);
      }
      async function setEl(deg) {
        const d = Number(deg);
        if (isNaN(d)) return;
        await requestTo(elId, 'SETPOSDG', String(d.toFixed(1)).replace('.', ','), 1200);
      }
      async function stopAz() { await requestTo(azId, 'STOP', '0', 800); }
      async function stopEl() { await requestTo(elId, 'STOP', '0', 800); }
      async function refAz() { await requestTo(azId, 'SETREF', '0', 1500); }
      async function refEl() { await requestTo(elId, 'SETREF', '0', 1500); }
      function isLocal() { return !!(port && localLive); }
      function getLinkMode() { return linkMode; }
      return {
        supported, secureOk, setIds, setCallbacks, connect, disconnect,
        isLocal, getLinkMode, linkModeName, queryLinkMode,
        setAz, setEl, stopAz, stopEl, refAz, refEl, build, parseLine
      };
    })();

console.log(typeof WebSerial, Object.keys(WebSerial));
