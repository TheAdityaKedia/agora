// Collections (internally "canvases"): the browser side shared by
// index.html (adding events in "canvas mode") and canvas.html (the
// collection itself). Plain script, no build:
// it defines window.AgoraCanvas. Design: feature-specs/event-canvases.md.
//
// Everything stored in the browser goes through the try/catch helpers below:
// storage can be missing (private windows, blocked site data) and the pages
// must still work, just without remembering things.
(function () {
  "use strict";

  // API base URLs: the Function URLs printed by deploy-canvas-api.yml. Pages
  // served from localhost use dev (or ?api=<url>, remembered); everything else
  // uses prod.
  const PROD_API = "https://khtxsdc3dt44sceskb7ffpxgby0tlxra.lambda-url.us-east-1.on.aws";
  const DEV_API = "https://fuc2fcq22zetifam6zgdg7guq40urcqh.lambda-url.us-east-1.on.aws";

  const K = {
    client: "agora.canvas.client",
    name: "agora.canvas.name",
    mine: "agora.canvas.mine",
    active: "agora.canvas.active",
    shared: "agora.canvas.shared",
    api: "agora.canvas.api",
  };
  const MINE_MAX = 100;

  const memory = {};  // fallback when storage is unavailable
  function load(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw !== null) return JSON.parse(raw);
    } catch {}
    return key in memory ? memory[key] : fallback;
  }
  function save(key, value) {
    memory[key] = value;
    try {
      if (value === null || value === undefined) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  }

  const isLocal = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  // Read ?api= now: index.html rewrites its URL on the first render.
  if (isLocal) {
    const override = new URLSearchParams(location.search).get("api");
    if (override) save(K.api, override);
  }
  function apiBase() {
    if (!isLocal) return PROD_API;
    return (load(K.api, null) || DEV_API).replace(/\/+$/, "");
  }

  // Not auth: dedups votes ("one 👍 per browser") and lets a future account
  // claim the canvases this browser created.
  function clientId() {
    let id = load(K.client, null);
    if (!id || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
      id = (crypto.randomUUID ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)),
                     b => b.toString(16).padStart(2, "0")).join(""));
      save(K.client, id);
    }
    return id;
  }

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  async function api(method, path, body) {
    let resp;
    try {
      resp = await fetch(apiBase() + path, {
        method,
        headers: Object.assign({"X-Agora-Client": clientId()},
                               body ? {"Content-Type": "application/json"} : {}),
        body: body ? JSON.stringify(body) : undefined,
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "Couldn't reach Agora. Check your connection and try again.");
    }
    let data = null;
    try { data = await resp.json(); } catch {}
    if (!resp.ok) {
      const msg = resp.status === 429 ? "Too many changes at once. Wait a minute and try again."
        : (data && data.error) || "Something went wrong (" + resp.status + ").";
      throw new ApiError(resp.status, msg.charAt(0).toUpperCase() + msg.slice(1));
    }
    return data;
  }

  // --- Canvases this browser has opened or made ("Your canvases"). ---
  function myCanvases() {
    const list = load(K.mine, []);
    return Array.isArray(list) ? list.filter(c => c && typeof c.id === "string") : [];
  }
  // Every collection opened or started here, newest first, for "Your
  // collections". `yours`: started in this browser (vs shared with you);
  // `sent_copy`: a copy you made to share. Flags from earlier visits are
  // kept when a later view doesn't carry them.
  function rememberCanvas(c, extra) {
    const all = myCanvases();
    const prev = all.find(x => x.id === c.id) || {};
    const rest = all.filter(x => x.id !== c.id);
    rest.unshift(Object.assign({}, prev, {
      id: c.id, name: c.name, date_from: c.date_from || null,
      date_to: c.date_to || null, opened_at: Date.now(),
    }, typeof c.yours === "boolean" ? {yours: c.yours} : {}, extra || {}));
    save(K.mine, rest.slice(0, MINE_MAX));
  }
  function forgetCanvas(id) {
    save(K.mine, myCanvases().filter(x => x.id !== id));
  }

  // --- The canvas the main page is adding to ("canvas mode"). It switches
  // itself off a day after it was last used, so someone who comes back to
  // browse isn't still adding to last week's plan. (A ?canvas= link always
  // turns it back on.) ---
  const ACTIVE_TTL_MS = 24 * 3600e3;
  function activeCanvas() {
    const a = load(K.active, null);
    if (!a || typeof a.id !== "string") return null;
    if (!(Date.now() - (a.used_at || 0) < ACTIVE_TTL_MS)) {
      save(K.active, null);
      return null;
    }
    return a;
  }
  // Every load or change on the main page re-saves it, renewing the day.
  function setActiveCanvas(c) {
    save(K.active, c ? {id: c.id, name: c.name, date_from: c.date_from || null,
                        date_to: c.date_to || null, used_at: Date.now()} : null);
  }

  const canvasPath = (id) => "canvas.html?c=" + encodeURIComponent(id);
  function canvasUrl(id) { return new URL(canvasPath(id), location.href).href; }
  // The main page in canvas mode, filtered to the canvas's dates if it has any.
  function browseUrl(c) {
    const p = new URLSearchParams({canvas: c.id});
    if (c.date_from) p.set("from", c.date_from);
    if (c.date_to) p.set("to", c.date_to);
    return "./?" + p.toString();
  }

  // --- UI pieces both pages use: a toast, and small modal dialogs. ---
  injectStyles();

  let toastEl = null, toastTimer = null;
  function toast(message, action) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "ac-toast";
      toastEl.setAttribute("role", "status");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = "";
    const span = document.createElement("span");
    span.textContent = message;
    toastEl.appendChild(span);
    if (action) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = action.label;
      b.addEventListener("click", () => { hide(); action.run(); });
      toastEl.appendChild(b);
    }
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hide, action ? 6000 : 3500);
    function hide() { toastEl.classList.remove("show"); }
  }

  // A modal form. fields: [{name, label, type, value, required, max, hint,
  // placeholder, autocomplete, inputmode}];
  // validate(values) returns an error message or "". Resolves to
  // {field: value} on submit, or null if dismissed.
  function formDialog({title, text, fields, submit = "Save", cancel = "Cancel", validate}) {
    return new Promise((resolve) => {
      const dlg = document.createElement("dialog");
      dlg.className = "ac-dialog";
      const form = document.createElement("form");
      form.method = "dialog";
      const h = document.createElement("h2");
      h.textContent = title;
      form.appendChild(h);
      if (text) {
        const p = document.createElement("p");
        p.className = "ac-dialog-text";
        p.textContent = text;
        form.appendChild(p);
      }
      const inputs = {};
      for (const f of fields) {
        const label = document.createElement("label");
        label.className = "ac-field";
        const cap = document.createElement("span");
        cap.textContent = f.label;
        label.appendChild(cap);
        const input = document.createElement(f.type === "textarea" ? "textarea" : "input");
        if (f.type !== "textarea") input.type = f.type || "text";
        input.name = f.name;
        input.value = f.value || "";
        if (f.required) input.required = true;
        if (f.max) input.maxLength = f.max;
        if (f.placeholder) input.placeholder = f.placeholder;
        if (f.autocomplete) input.autocomplete = f.autocomplete;
        if (f.inputmode) input.inputMode = f.inputmode;
        label.appendChild(input);
        if (f.hint) {
          const hint = document.createElement("small");
          hint.textContent = f.hint;
          label.appendChild(hint);
        }
        form.appendChild(label);
        inputs[f.name] = input;
      }
      const err = document.createElement("p");
      err.className = "ac-dialog-error";
      err.hidden = true;
      form.appendChild(err);
      const row = document.createElement("div");
      row.className = "ac-dialog-actions";
      const no = document.createElement("button");
      no.type = "button"; no.className = "ac-btn"; no.textContent = cancel;
      const yes = document.createElement("button");
      yes.type = "submit"; yes.className = "ac-btn ac-primary"; yes.textContent = submit;
      row.append(no, yes);
      form.appendChild(row);
      dlg.appendChild(form);
      document.body.appendChild(dlg);

      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        if (dlg.open) dlg.close();
        dlg.remove();
        resolve(value);
      };
      no.addEventListener("click", () => finish(null));
      dlg.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });
      dlg.addEventListener("click", (e) => { if (e.target === dlg) finish(null); });
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const out = {};
        for (const [k, el] of Object.entries(inputs)) out[k] = el.value.trim();
        const bad = fields.find(f => f.required && !out[f.name]);
        if (bad) { inputs[bad.name].focus(); return; }
        if (validate) {
          const msg = validate(out);
          if (msg) { err.textContent = msg; err.hidden = false; return; }
        }
        finish(out);
      });
      dlg.showModal();
      const first = form.querySelector("input, textarea");
      if (first) first.focus();
    });
  }

  // The visitor's display name, asked once ("What's your name?") and
  // remembered. Resolves to null if they dismiss the prompt.
  async function ensureName(force = false) {
    const current = load(K.name, "");
    if (current && !force) return current;
    const out = await formDialog({
      title: current ? "Change your name" : "What's your name?",
      text: "People on this collection see it next to your 👍, comments and additions. No account needed.",
      fields: [{name: "name", label: "Your name", value: current, required: true, max: 40,
                autocomplete: "given-name"}],
      submit: "Save",
    });
    if (!out) return null;
    save(K.name, out.name);
    return out.name;
  }
  const currentName = () => load(K.name, "");

  // Collections this browser has shared as-is ("Share this collection"):
  // their pages show votes, comments and "the plan" from then on.
  const isShared = (id) => (load(K.shared, []) || []).includes(id);
  function markShared(id) {
    const list = (load(K.shared, []) || []).filter(x => x !== id);
    list.unshift(id);
    save(K.shared, list.slice(0, 100));
  }

  // New collection: name + optional dates. No name prompt: curating for
  // yourself never asks who you are. Resolves to the collection.
  async function createCanvasDialog() {
    const out = await formDialog({
      title: "Start a collection",
      text: "Save events that interest you, or plan something with friends: add events, then share the link if you like.",
      fields: [
        {name: "name", label: "Name", required: true, max: 80, placeholder: "Weekend ideas"},
        {name: "date_from", label: "From (optional)", type: "date"},
        {name: "date_to", label: "To (optional)", type: "date"},
      ],
      submit: "Create",
      validate: (v) => v.date_from && v.date_to && v.date_from > v.date_to
        ? "The end date is before the start date." : "",
    });
    if (!out) return null;
    const res = await api("POST", "/canvases", {
      name: out.name, actor_name: currentName(),
      date_from: out.date_from || null, date_to: out.date_to || null,
    });
    rememberCanvas(res.canvas);
    return res.canvas;
  }

  // A dialog of big choice buttons ({value, label, desc}). Resolves to the
  // chosen value, or null if dismissed.
  function choiceDialog({title, text, options, cancel = "Cancel"}) {
    return new Promise((resolve) => {
      const dlg = document.createElement("dialog");
      dlg.className = "ac-dialog";
      const box = document.createElement("form");
      box.method = "dialog";
      const h = document.createElement("h2");
      h.textContent = title;
      box.appendChild(h);
      if (text) {
        const p = document.createElement("p");
        p.className = "ac-dialog-text";
        p.textContent = text;
        box.appendChild(p);
      }
      let done = false;
      const finish = (v) => {
        if (done) return;
        done = true;
        if (dlg.open) dlg.close();
        dlg.remove();
        resolve(v);
      };
      for (const o of options) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ac-choice";
        b.dataset.value = o.value;
        const strong = document.createElement("b");
        strong.textContent = o.label;
        b.appendChild(strong);
        if (o.desc) {
          const d = document.createElement("span");
          d.textContent = o.desc;
          b.appendChild(d);
        }
        b.addEventListener("click", () => finish(o.value));
        box.appendChild(b);
      }
      const row = document.createElement("div");
      row.className = "ac-dialog-actions";
      const no = document.createElement("button");
      no.type = "button"; no.className = "ac-btn"; no.textContent = cancel;
      no.addEventListener("click", () => finish(null));
      row.appendChild(no);
      box.appendChild(row);
      dlg.appendChild(box);
      document.body.appendChild(dlg);
      dlg.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });
      dlg.addEventListener("click", (e) => { if (e.target === dlg) finish(null); });
      dlg.showModal();
    });
  }

  async function share(url, title) {
    const coarse = matchMedia("(pointer: coarse)").matches;
    if (navigator.share && coarse) {
      try { await navigator.share({title, url}); } catch {}
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied. Send it to anyone you like.");
    } catch {
      toast("Couldn't copy. Copy the address bar instead.");
    }
  }

  function injectStyles() {
    const css = `
      .ac-toast { position: fixed; left: 50%; bottom: 84px; z-index: 60;
        transform: translate(-50%, 20px); opacity: 0; pointer-events: none;
        display: flex; align-items: center; gap: 12px; max-width: calc(100vw - 32px);
        padding: 10px 14px; border-radius: 10px; font-size: 14px;
        background: var(--fg); color: var(--bg); box-shadow: 0 8px 24px rgba(0,0,0,0.25);
        transition: opacity .18s, transform .18s; }
      .ac-toast.show { opacity: 1; transform: translate(-50%, 0); pointer-events: auto; }
      .ac-toast button { background: none; border: 0; color: inherit; font: inherit;
        font-weight: 700; text-decoration: underline; cursor: pointer; padding: 4px; }
      .ac-dialog { border: 1px solid var(--border); border-radius: 14px; padding: 0;
        background: var(--bg); color: var(--fg); width: min(420px, calc(100vw - 32px)); }
      .ac-dialog::backdrop { background: rgba(0,0,0,0.4); }
      .ac-dialog form { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
      .ac-dialog h2 { margin: 0; font-size: 20px; }
      .ac-dialog-text { margin: 0; color: var(--muted); font-size: 14px; }
      .ac-field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
      .ac-field input, .ac-field textarea { font: inherit; font-size: 16px; color: var(--fg);
        background: var(--input-bg); border: 1px solid var(--border); border-radius: 8px;
        padding: 9px 10px; color-scheme: light dark; }
      .ac-field textarea { min-height: 84px; resize: vertical; }
      .ac-field input:focus, .ac-field textarea:focus { outline: none; border-color: var(--accent); }
      .ac-dialog-error { margin: 0; color: var(--error); font-size: 14px; }
      .ac-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
      .ac-btn { font: inherit; padding: 8px 14px; border-radius: 8px; cursor: pointer;
        background: var(--input-bg); color: var(--fg); border: 1px solid var(--border); }
      .ac-btn:hover { border-color: var(--accent); }
      .ac-primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); font-weight: 600; }
      .ac-choice { display: flex; flex-direction: column; gap: 2px; text-align: left; cursor: pointer;
        font: inherit; padding: 12px 14px; border-radius: 10px; color: var(--fg);
        background: var(--input-bg); border: 1px solid var(--border); }
      .ac-choice:hover, .ac-choice:focus-visible { border-color: var(--accent); }
      .ac-choice span { color: var(--muted); font-size: 14px; }
      @media (prefers-reduced-motion: reduce) { .ac-toast { transition: none; } }
    `;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  window.AgoraCanvas = {
    api, ApiError, clientId, ensureName, currentName, myCanvases, rememberCanvas,
    forgetCanvas, activeCanvas, setActiveCanvas, canvasUrl, canvasPath, browseUrl,
    toast, formDialog, choiceDialog, createCanvasDialog, share, isShared, markShared,
  };
})();
