/* The voice assistant widget: record with silence detection -> stream reply -> play voice. */
(() => {
  const { el, toolLabel } = KIT;
  const SILENT = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
  const ICON = {
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>',
    dots: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
    wave: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/></svg>',
  };
  const STATES = {
    idle: ["mic", "Tap the mic and speak", "Start talking"],
    listening: ["send", "Listening… I'll reply when you pause", "Stop and send"],
    thinking: ["dots", "Thinking…", "Cancel"],
    speaking: ["wave", "Speaking… tap to interrupt", "Interrupt and speak"],
  };
  const CHIPS = {
    public: ["What courses do you offer?", "What are the eligibility marks and fees?", "What are the important admission dates?", "I want to apply for admission"],
    student: ["What's my attendance?", "I want to apply for leave", "Raise an on-duty request", "Any placement drives open?", "What's the leave rule?"],
    teacher: ["Show pending requests for me", "Approve a leave request", "What's today's timetable?"],
  };

  class Assistant {
    constructor(mount, session) {
      this.mount = mount;
      this.session = session;
      this.state = "idle";
      this.conversationId = null;
      this.handsFree = localStorage.getItem("kit_handsfree") === "1";
      this.speakReplies = localStorage.getItem("kit_speak") !== "0";
      this.voice = localStorage.getItem("kit_voice") || "hannah";
      this.audio = new Audio();
      this.queue = [];
      this.playing = false;
      this.audioReceived = false;
      this.browserSpeaking = false;
      this.streamDone = true;
      this.replyText = "";
      this.mic = null;
      this.startingMic = false;
      this.abort = null;
      this.unlocked = false;
      this.render();
    }

    render() {
      const s = this.session;
      const first = (s.name || s.login_id || "").split(" ")[0];
      this.mount.innerHTML = `
        <section class="assistant" aria-label="Voice assistant">
          <div class="a-top">
            <div><span class="a-title"></span><span class="badge"></span></div>
            <div class="toggles">
              <label class="voice-choice"><span>Voice</span><select class="t-voice" aria-label="Reply voice">
                <option value="hannah">Hannah</option><option value="autumn">Autumn</option><option value="diana">Diana</option>
                <option value="austin">Austin</option><option value="daniel">Daniel</option><option value="troy">Troy</option>
              </select></label>
              <label class="toggle"><input type="checkbox" class="t-hands"><span>Hands-free</span></label>
              <label class="toggle"><input type="checkbox" class="t-speak"><span>Voice replies</span></label>
            </div>
          </div>
          <div class="stage">
            <button type="button" class="orb" data-state="idle"><span class="orb-icon"></span></button>
            <p class="state-label" role="status"></p>
            <p class="caption">Tap once, speak naturally, then pause.</p>
          </div>
          <div class="chips" aria-label="Suggestions"></div>
          <div class="log" aria-live="polite"></div>
          <form class="composer">
            <input type="text" maxlength="1000" autocomplete="off" placeholder="Or type your question…" aria-label="Type a message">
            <button class="btn primary" type="submit">Send</button>
          </form>
        </section>`;
      const q = (c) => this.mount.querySelector(c);
      this.orb = q(".orb"); this.orbIcon = q(".orb-icon"); this.stateLabel = q(".state-label");
      this.caption = q(".caption"); this.log = q(".log"); this.input = q(".composer input");
      q(".a-title").textContent = s.role === "public" ? "Talk to us" : `Hi, ${first}`;
      q(".badge").textContent = s.role === "public" ? "Visitor" : s.role;

      const hands = q(".t-hands"), speak = q(".t-speak"), voice = q(".t-voice");
      voice.value = [...voice.options].some((option) => option.value === this.voice) ? this.voice : "hannah";
      hands.checked = this.handsFree; speak.checked = this.speakReplies;
      voice.addEventListener("change", () => {
        this.voice = voice.value;
        localStorage.setItem("kit_voice", this.voice);
        this.say(`${voice.options[voice.selectedIndex].text} will speak the next reply.`);
      });
      hands.addEventListener("change", () => { this.handsFree = hands.checked; localStorage.setItem("kit_handsfree", hands.checked ? "1" : "0"); });
      speak.addEventListener("change", () => { this.speakReplies = speak.checked; localStorage.setItem("kit_speak", speak.checked ? "1" : "0"); if (!speak.checked) this.stopSpeaking(); });

      const chips = q(".chips");
      for (const text of CHIPS[s.role] || CHIPS.public) {
        chips.append(el("button", { class: "chip", type: "button", text, onclick: () => this.submitText(text) }));
      }
      this.orb.addEventListener("click", () => this.onOrb());
      q(".composer").addEventListener("submit", (e) => {
        e.preventDefault();
        const text = this.input.value.trim();
        if (text) { this.input.value = ""; this.submitText(text); }
      });
      document.addEventListener("keydown", (e) => {
        if (e.code === "Space" && document.activeElement === document.body) { e.preventDefault(); this.onOrb(); }
      });
      const hello = s.role === "public"
        ? "Hi! I'm the KIT admissions assistant. Ask me about courses, fees, eligibility or dates, or tell me you'd like to apply."
        : s.role === "student"
          ? `Hi ${first}! I can check your attendance, explain college rules, and submit leave or on-duty requests.`
          : `Hi ${first}! I can help you review and approve student requests.`;
      this.addMsg("bot", hello);
      this.setState("idle");
    }

    setState(state, label) {
      this.state = state;
      const [icon, text, aria] = STATES[state];
      this.orb.dataset.state = state;
      this.orbIcon.innerHTML = ICON[icon];
      this.stateLabel.textContent = label || text;
      this.orb.setAttribute("aria-label", aria);
      if (state !== "listening") this.orb.style.setProperty("--level", "0");
      if (state === "listening") this.caption.textContent = "I'm listening — pause when you finish.";
    }

    addMsg(role, text, tools) {
      const box = el("div", { class: `msg ${role}` }, el("p", { text }));
      if (tools && tools.length) box.append(el("div", { class: "tools" }, tools.map((t) => el("span", { class: "tool-chip", text: toolLabel(t) }))));
      this.log.append(box);
      this.log.scrollTop = this.log.scrollHeight;
      return box;
    }

    say(text) {
      this.caption.textContent = text;
      clearTimeout(this._cap);
      this._cap = setTimeout(() => { this.caption.textContent = ""; }, 5000);
    }

    unlockAudio() {
      if (this.unlocked) return;
      this.unlocked = true;
      this.audio.src = SILENT;
      this.audio.play().catch(() => {});
    }

    onOrb() {
      this.unlockAudio();
      if (this.state === "idle") return this.startListening();
      if (this.state === "listening") return this.stopListening(true);
      if (this.state === "thinking") return this.cancel();
      if (this.state === "speaking") { this.stopSpeaking(); return this.startListening(); }
    }

    /* ---- microphone with automatic end-of-speech detection ---- */
    async startListening() {
      if (this.mic || this.startingMic) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        this.addMsg("system", "This browser cannot record audio. Please type your question instead.");
        return;
      }
      this.startingMic = true;
      this.setState("thinking", "Opening your microphone…");
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: {
          echoCancellation: { ideal: true }, noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true }, channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 },
        } });
      } catch (err) {
        const denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
        this.addMsg("system", denied
          ? "Microphone permission is blocked. Click the lock icon beside the address, allow Microphone, then try again."
          : "I couldn't open your microphone. Check that another app is not using it, then try again.");
        this.setState("idle");
        this.startingMic = false;
        return;
      }
      if (!this.startingMic) { stream.getTracks().forEach((t) => t.stop()); return; }
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported(m));
      let rec;
      try {
        rec = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : undefined);
      } catch (_) {
        stream.getTracks().forEach((t) => t.stop());
        this.addMsg("system", "This browser could not create an audio recording. Please try Chrome or Edge, or type your question.");
        this.setState("idle"); this.startingMic = false;
        return;
      }
      const chunks = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onerror = () => {
        if (this.mic && this.mic.rec === rec) {
          this.addMsg("system", "The recording stopped unexpectedly. Please try again.");
          this.stopListening(false);
        }
      };

      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      await ctx.resume().catch(() => {});
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const t0 = performance.now();
      const calibration = [];
      let threshold = 0.015, lastVoice = 0, voicedMs = 0, heard = false;

      const timer = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
        const rms = Math.sqrt(sum / buf.length);
        const t = performance.now() - t0;
        this.orb.style.setProperty("--level", Math.min(1, rms * 6).toFixed(2));
        if (t < 350) { calibration.push(rms); return; }
        if (calibration.length) {
          // Median calibration ignores a click or first syllable better than a max.
          const ordered = [...calibration].sort((a, b) => a - b);
          const floor = ordered[Math.floor(ordered.length / 2)] || 0;
          threshold = Math.min(0.045, Math.max(0.012, floor * 2.2 + 0.004));
          if (calibration.some((v) => v > threshold)) { heard = true; lastVoice = t; }
          calibration.length = 0;
        }
        if (rms > threshold) { voicedMs += 50; lastVoice = t; if (voicedMs >= 180) heard = true; }
        if (heard && t - lastVoice > 1100) this.stopListening(true);  // paused: send it
        else if (!heard && t > 8000) this.stopListening(true);        // Whisper makes the final decision
        else if (t > 45000) this.stopListening(true);
      }, 50);

      this.mic = { stream, rec, ctx, timer, chunks, mime: rec.mimeType };
      rec.start(250);
      this.startingMic = false;
      this.setState("listening");
    }

    stopListening(send) {
      const m = this.mic;
      if (!m) return;
      this.mic = null;
      clearInterval(m.timer);
      const finish = () => {
        m.stream.getTracks().forEach((t) => t.stop());
        m.ctx.close().catch(() => {});
        const blob = new Blob(m.chunks, { type: m.mime || "audio/webm" });
        // Don't discard a quiet/short recording. Whisper makes a better final call.
        if (send && blob.size > 600) this.sendVoice(blob);
        else {
          this.setState("idle");
          if (send) this.say("I didn't hear anything. Tap the mic and try again.");
        }
      };
      m.rec.onstop = finish;
      if (m.rec.state !== "inactive") m.rec.stop(); else finish();
    }

    /* ---- sending ---- */
    submitText(text) {
      this.unlockAudio();
      if (this.mic) this.stopListening(false);
      this.stopSpeaking(true);
      this.addMsg("user", text);
      this.run("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: this.conversationId, speak: this.speakReplies, voice: this.voice }),
      });
    }

    sendVoice(blob) {
      const ext = blob.type.includes("mp4") ? "mp4" : blob.type.includes("ogg") ? "ogg" : "webm";
      const fd = new FormData();
      fd.append("audio", blob, `speech.${ext}`);
      fd.append("speak", this.speakReplies ? "true" : "false");
      fd.append("voice_name", this.voice);
      if (this.conversationId) fd.append("conversation_id", this.conversationId);
      this.run("/api/voice", { method: "POST", body: fd });
    }

    async run(url, init) {
      this.stopSpeaking(true);
      this.setState("thinking");
      this.streamDone = false;
      this.audioReceived = false;
      this.replyText = "";
      const ac = new AbortController();
      this.abort = ac;
      try {
        const res = await fetch(url, { ...init, credentials: "same-origin", signal: ac.signal });
        if (!res.ok) {
          let msg = "Something went wrong.";
          try { msg = (await res.json()).detail || msg; } catch (_) { /* default */ }
          throw new Error(typeof msg === "string" ? msg : "Please check your input.");
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, i).trim();
            buf = buf.slice(i + 1);
            if (line) this.onEvent(JSON.parse(line));
          }
        }
      } catch (e) {
        if (e.name !== "AbortError") {
          this.addMsg("system", e.message || "Connection problem. Please try again.");
          this.setState("idle");
        }
      } finally {
        this.abort = null;
        this.streamDone = true;
        this.afterStream();
      }
    }

    onEvent(ev) {
      switch (ev.type) {
        case "meta": this.conversationId = ev.conversation_id; break;
        case "transcript": this.addMsg("user", ev.text); break;
        case "status": this.setState("thinking", ev.text); break;
        case "reply":
          this.replyText = ev.text;
          this.addMsg("bot", ev.text, ev.tools);
          if (this.speakReplies) this.setState("thinking", "Preparing voice…");
          break;
        case "audio": this.enqueue(ev.b64); break;
        case "tts": if (!this.audioReceived && this.speakReplies) this.speakBrowser(this.replyText); break;
        case "auth_expired": this.addMsg("system", "Your login expired. Please log in again to continue."); break;
        case "empty": this.say("I couldn't make out any speech. Please try again."); break;
        case "error": this.addMsg("system", ev.message); this.setState("idle"); break;
        default: break;
      }
    }

    afterStream() {
      if (this.state === "thinking") this.setState("idle");
      else if (this.state === "speaking" && !this.playing && !this.queue.length && !this.browserSpeaking) this.finishSpeaking();
    }

    /* ---- playback: server voice, with the browser voice as a fallback ---- */
    enqueue(b64) {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      this.queue.push(URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })));
      this.audioReceived = true;
      if (!this.playing) this.pump();
    }

    pump() {
      const url = this.queue.shift();
      if (!url) {
        this.playing = false;
        if (this.streamDone) this.finishSpeaking();
        return;
      }
      this.playing = true;
      this.setState("speaking");
      const next = () => { URL.revokeObjectURL(url); this.pump(); };
      this.audio.onended = next;
      this.audio.onerror = next;
      this.audio.src = url;
      this.audio.play().catch(() => {
        this.playing = false;
        this.queue.length = 0;
        this.speakBrowser(this.replyText);
      });
    }

    speakBrowser(text) {
      if (!("speechSynthesis" in window) || !text) return this.finishSpeaking();
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text.replace(/[*_`#]/g, ""));
      u.lang = "en-IN";
      const v = speechSynthesis.getVoices().find((x) => /en[-_]IN/i.test(x.lang)) || speechSynthesis.getVoices().find((x) => /^en/i.test(x.lang));
      if (v) u.voice = v;
      u.onend = u.onerror = () => { this.browserSpeaking = false; this.finishSpeaking(); };
      this.browserSpeaking = true;
      this.setState("speaking");
      speechSynthesis.speak(u);
    }

    stopSpeaking(silent) {
      this.audio.pause();
      this.queue.forEach((u) => URL.revokeObjectURL(u));
      this.queue.length = 0;
      this.playing = false;
      this.browserSpeaking = false;
      if ("speechSynthesis" in window) speechSynthesis.cancel();
      if (!silent && this.state === "speaking") this.setState("idle");
    }

    finishSpeaking() {
      this.setState("idle");
      if (this.handsFree) setTimeout(() => { if (this.state === "idle") this.startListening(); }, 450);
    }

    cancel() {
      this.startingMic = false;
      if (this.abort) this.abort.abort();
      this.stopSpeaking(true);
      this.setState("idle");
    }
  }

  window.KitAssistant = Assistant;
})();
