"""Web-Serial-Client für Browser ↔ Display-Controller (USB-CDC, #…$ Framing).

Modus WEB: Der Display arbeitet wie Remote-USB (RS485-TX stumm). Der Browser relayed
alle Display-Frames per ``POST /api/bus`` an die Bridge und schreibt die Antworten
(Rotor-ACKs, Soll-Spiegelung) zurück auf den Display-USB.
"""

from __future__ import annotations


def web_serial_js() -> str:
    """JS ohne f-string-Doppelklammern — als Rohstring in compass_html einbetten."""
    return r"""
    // —— Web Serial (Display-Controller) ——
    // window.* damit sichtbar auch außerhalb von try/catch (Block-Scope von const)
    window.WebSerial = (function() {
      const BAUD = 115200;
      const NUM_RE = /[-+]?\d+(?:[.,]\d+)?/g;
      const RELAY_MS = 60;
      const KEEPALIVE_MS = 1800;
      let port = null;
      let reader = null;
      let writer = null;
      let keepReading = false;
      let rxBuf = '';
      let pending = [];
      let relayTimer = null;
      let keepaliveTimer = null;
      let relayBusy = false;
      let relaySince = 0;
      let relayOut = [];
      let relayOk = false;
      let relayFailCount = 0;
      let linkMode = null;
      let pcId = 1;
      let contId = 2;
      let localLive = false;
      let lastError = '';
      let onStatus = null;
      let txQueue = Promise.resolve();

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
        if (cfg.cont_id != null) contId = Number(cfg.cont_id) || contId;
        // Browser-Master für Config-Abfragen an den Display (nicht die Display-ID)
        let want = 1;
        if (cfg.pc_id != null) want = Number(cfg.pc_id) || 1;
        pcId = (want === contId) ? ((contId === 1) ? 3 : 1) : want;
      }
      function setCallbacks(cbs) {
        onStatus = cbs && cbs.onStatus;
      }
      function emitStatus() {
        if (typeof onStatus === 'function') {
          onStatus({
            connected: !!port,
            localLive: localLive,
            busReady: relayOk,
            linkMode: linkMode,
            supported: supported(),
            secure: secureOk(),
            lastError: lastError,
            rotorOk: relayOk,
            contId: contId,
            pcId: pcId
          });
        }
      }
      async function writeFrame(frame) {
        if (!writer) throw new Error('not connected');
        const data = new TextEncoder().encode(frame);
        await writer.write(data);
      }
      function waitReply(pred, timeoutMs) {
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
      /** true wenn eine eigene Anfrage (Config an den Display) beantwortet wurde. */
      function dispatchToPending(tel) {
        for (let i = 0; i < pending.length; i++) {
          const p = pending[i];
          try {
            if (p.pred(tel)) {
              clearTimeout(p.timer);
              pending.splice(i, 1);
              p.resolve(tel);
              return true;
            }
          } catch (e) {}
        }
        return false;
      }
      function onDisplayLine(line) {
        const tel = parseLine(line);
        if (!tel) return;
        if (dispatchToPending(tel)) return;
        // Display-Master (Controller) → Bridge/Rotor
        if (tel.src === contId && tel.dst !== pcId) {
          relayOut.push(tel.raw);
          if (relayOut.length > 200) relayOut = relayOut.slice(-120);
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
              if (hash >= 0) onDisplayLine(chunk.slice(hash));
            }
            if (rxBuf.length > 8192) rxBuf = rxBuf.slice(-4096);
          } catch (e) {
            break;
          }
        }
      }
      function enqueue(fn) {
        const run = txQueue.then(fn, fn);
        txQueue = run.then(function() {}, function() {});
        return run;
      }
      async function requestTo(dst, cmd, params, timeoutMs) {
        return enqueue(async function() {
          const d = (dst != null) ? dst : contId;
          const frame = build(pcId, d, cmd, (params != null) ? params : '0');
          const want = 'ACK_' + cmd;
          const nak = 'NAK_' + cmd;
          const p = waitReply(function(tel) {
            if (tel.cmd !== want && tel.cmd !== nak) return false;
            return tel.src === d;
          }, timeoutMs);
          await writeFrame(frame);
          return p;
        });
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
        return null;
      }
      function sleep(ms) {
        return new Promise(function(resolve) { setTimeout(resolve, ms); });
      }
      /** Display auf WEB (= Remote-USB-Verhalten, Bridge übernimmt den Rotor-Bus). */
      async function ensureWebLinkMode() {
        await queryLinkMode();
        if (linkMode !== 2) {
          try {
            const tel = await request('SETLINKMODE', '2', 2500);
            if (tel && tel.cmd === 'ACK_SETLINKMODE') linkMode = 2;
          } catch (e) {}
          await sleep(500);
          await queryLinkMode();
        }
        if (linkMode !== 2) {
          lastError = 'Display nicht im Modus WEB — linke Taste bis „WEB“ drücken';
        } else {
          lastError = '';
        }
        emitStatus();
      }
      async function relayTick() {
        if (!port || !localLive || relayBusy) return;
        relayBusy = true;
        const outLines = relayOut.splice(0, relayOut.length);
        try {
          const res = await fetch('/api/bus', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tx: outLines, since: relaySince }),
            cache: 'no-store'
          });
          const data = await res.json();
          if (!data || !data.ok) {
            relayOk = false;
            relayFailCount++;
            if (data && data.error === 'desktop_remote_usb_active') {
              lastError = 'Desktop-App nutzt gerade Remote-USB — dort „Controller Remote USB“ ausschalten';
            } else {
              lastError = 'Bridge-Relay nicht erreichbar';
            }
            emitStatus();
          } else {
            const wasOk = relayOk;
            relayOk = true;
            relayFailCount = 0;
            if (lastError.indexOf('Bridge') >= 0 || lastError.indexOf('Desktop') >= 0) lastError = '';
            if (typeof data.seq === 'number') relaySince = data.seq;
            const rx = data.rx || [];
            for (let i = 0; i < rx.length; i++) {
              try { await writeFrame(rx[i].line); } catch (e) { break; }
            }
            if (!wasOk) emitStatus();
          }
        } catch (e) {
          // Nicht verlorene Display-Frames zurücklegen
          relayOut = outLines.concat(relayOut);
          if (relayOut.length > 200) relayOut = relayOut.slice(-120);
          relayOk = false;
          relayFailCount++;
          lastError = 'Bridge-Relay nicht erreichbar';
          emitStatus();
        }
        relayBusy = false;
      }
      function startRelay() {
        stopRelay();
        relaySince = 0;
        relayOut = [];
        relayTimer = setInterval(function() { relayTick(); }, RELAY_MS);
        // Display „PC online“ halten (≤ 4,5 s ohne Frames → gelb / „Warten auf PC“)
        keepaliveTimer = setInterval(function() {
          if (!port || !localLive) return;
          request('GETLINKMODE', '0', 900).then(function(tel) {
            if (tel && tel.cmd === 'ACK_GETLINKMODE') {
              const v = parseInt(String(tel.params).replace(',', '.'), 10);
              if (!isNaN(v) && v !== linkMode) { linkMode = v; emitStatus(); }
            }
          }).catch(function() {});
        }, KEEPALIVE_MS);
        relayTick();
      }
      function stopRelay() {
        if (relayTimer) { clearInterval(relayTimer); relayTimer = null; }
        if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
      }
      function httpsHelpMessage() {
        return (
          'Der Browser zeigt den COM-Port-Dialog nur in einem Secure Context.\n\n' +
          'So geht’s:\n' +
          '1) In RotorTcpBridge: Einstellungen → Webserver → „HTTPS (TLS) aktiv“\n' +
          '2) Seite neu öffnen als https://<IP>:<Port>/compass\n' +
          '3) Zertifikat-Warnung mit „Weiter/Advanced“ einmal akzeptieren\n\n' +
          'Alternativ auf dem gleichen PC: http://127.0.0.1:<Port>/compass'
        );
      }
      async function requestPortFromUser() {
        if (!secureOk()) {
          const err = new Error(httpsHelpMessage());
          err.code = 'NEED_HTTPS';
          throw err;
        }
        if (!supported()) {
          const err = new Error('Web Serial nicht verfügbar — bitte Chrome oder Edge verwenden');
          err.code = 'NO_SERIAL';
          throw err;
        }
        return navigator.serial.requestPort({});
      }
      async function attachPort(p) {
        if (!p) throw new Error('Kein Port');
        await p.open({ baudRate: BAUD, bufferSize: 8192 });
        try {
          if (p.setSignals) await p.setSignals({ dataTerminalReady: false, requestToSend: false });
        } catch (e) {}
        port = p;
        keepReading = true;
        reader = p.readable.getReader();
        writer = p.writable.getWriter();
        readLoop();
        localLive = true;
        relayOk = false;
        lastError = '';
        emitStatus();
        await ensureWebLinkMode();
        startRelay();
      }
      async function connect() {
        const p = await requestPortFromUser();
        await attachPort(p);
      }
      async function disconnect() {
        localLive = false;
        relayOk = false;
        stopRelay();
        try {
          await fetch('/api/bus', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ disconnect: true }),
            cache: 'no-store'
          });
        } catch (e) {}
        keepReading = false;
        try { if (reader) { await reader.cancel(); reader.releaseLock(); } } catch (e) {}
        try { if (writer) { writer.releaseLock(); } } catch (e) {}
        reader = null; writer = null;
        try { if (port) await port.close(); } catch (e) {}
        port = null;
        pending.forEach(function(p) { try { clearTimeout(p.timer); p.reject(new Error('closed')); } catch (e) {} });
        pending = [];
        lastError = '';
        emitStatus();
      }
      function isLocal() { return !!(port && localLive); }
      /** Steuerung läuft über den Server (Relay) — nie lokal am Rotor vorbei. */
      function canCommand() { return false; }
      function getLinkMode() { return linkMode; }
      return {
        supported, secureOk, httpsHelpMessage, setIds, setCallbacks,
        requestPortFromUser, attachPort, connect, disconnect,
        isLocal, canCommand, getLinkMode, linkModeName, queryLinkMode,
        build, parseLine
      };
    })();
"""


__all__ = ["web_serial_js"]
