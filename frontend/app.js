/* PayTech AI — ChatGPT-like UI (vanilla HTML/CSS/JS)
   - Sidebar: search, new chat, conversation list w/ kebab menu
   - Main: header global menu, thread, composer (attach/send/stop)
   - Storage: localStorage (conversations + selectedConversationId + theme)
   - Streaming: SSE via fetch+reader (POST /chat/stream), incremental update of a single TextNode
*/

// =============================
// Config
// =============================
const PAYTECH_BUILD = "2026-02-10_009";
const BACKEND_DEFAULT = "http://localhost:8000";
const BACKEND_BASE_KEY = "paytech.backendBase";

// =============================
// Crash/diagnostic hooks (surface issues instead of "send does nothing")
// =============================
const _earlyErrors = [];
let _lastErrToastAt = 0;
let _lastErrToastMsg = "";
function _captureEarlyError(kind, err) {
  try {
    const msg = `${kind}: ${String(err?.message || err || "")}`.trim();
    _earlyErrors.push({ kind, msg, at: Date.now() });
    // Always log (DevTools)
    // eslint-disable-next-line no-console
    console.error("[paytech]", kind, err);

    // Surface in-UI (avoid "chat não responde" with no visible error).
    const now = Date.now();
    const shouldToast = msg && (msg !== _lastErrToastMsg || (now - _lastErrToastAt) > 2500);
    if (shouldToast && typeof window.toast === "function") {
      _lastErrToastAt = now;
      _lastErrToastMsg = msg;
      try { window.toast(`Erro no frontend: ${msg}`.slice(0, 220), { ms: 6500 }); } catch { }
    }
  } catch { }
}
window.addEventListener("error", (e) => _captureEarlyError("error", e?.error || e?.message || e), { capture: true });
window.addEventListener("unhandledrejection", (e) => _captureEarlyError("unhandledrejection", e?.reason || e), { capture: true });

function resolveBackendBase() {
  try {
    const url = new URL(window.location.href);
    const qp = (url.searchParams.get("api") || "").trim();
    if (qp) {
      localStorage.setItem(BACKEND_BASE_KEY, qp);
      return qp;
    }
    const saved = (localStorage.getItem(BACKEND_BASE_KEY) || "").trim();
    return saved || BACKEND_DEFAULT;
  } catch {
    return BACKEND_DEFAULT;
  }
}

let BACKEND_BASE = resolveBackendBase();
const APP_TITLE = "PayTech AI";
const STORAGE_KEY = "paytech.conversations.v1";
const STORAGE_SELECTED = "paytech.selectedConversationId";
const SIDEBAR_COLLAPSED_KEY = "sidebarCollapsed";
const THEME_KEY = "theme";
const DOWNLOADS_USE_KEY = "downloads.useInChat";
const RESPONSE_MODE_KEY = "paytech.responseMode";
const STREAMING_ENABLED_KEY = "paytech.streamingEnabled";
const USER_ID_KEY = "paytech.userId";
const DOWNLOADS_TOP_K = 6;

// =============================
// DOM
// =============================
const el = {
  app: document.getElementById("app"),
  main: document.querySelector("main.main"),

  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  overlay: document.getElementById("overlay"),
  sidebarLogo: document.getElementById("sidebarLogo"),

  searchInput: document.getElementById("searchInput"),
  searchBtn: document.getElementById("searchBtn"),
  newChat: document.getElementById("newChat"),
  downloadBtn: document.getElementById("downloadBtn"),
  conversationList: document.getElementById("conversationList"),

  currentTitle: document.getElementById("currentTitle"),
  currentSub: document.getElementById("currentSub"),

  chat: document.getElementById("chat"),
  thread: document.getElementById("thread"),
  toBottomBtn: document.getElementById("toBottomBtn"),

  // Empty state
  emptyState: document.getElementById("emptyState"),
  emptySuggestions: document.getElementById("emptySuggestions"),
  emptyInput: document.getElementById("emptyInput"),
  emptySendBtn: document.getElementById("emptySendBtn"),

  fileInput: document.getElementById("fileInput"),
  fileChips: document.getElementById("fileChips"),
  attachBtn: document.getElementById("attachBtn"),
  input: document.getElementById("input"),
  sendBtn: document.getElementById("sendBtn"),

  themeToggle: document.getElementById("themeToggle"),
  responseModeBtn: document.getElementById("responseModeBtn"),
  itemMenu: document.getElementById("itemMenu"),
  exportMenu: document.getElementById("exportMenu"),
  modeMenu: document.getElementById("modeMenu"),

  toast: document.getElementById("toast"),

  // Downloads panel
  downloadsPanel: document.getElementById("downloadsPanel"),
  downloadsClose: document.getElementById("downloadsClose"),
  downloadsUploadBtn: document.getElementById("downloadsUploadBtn"),
  downloadsFileInput: document.getElementById("downloadsFileInput"),
  downloadsUseToggle: document.getElementById("downloadsUseToggle"),
  downloadsSearchInput: document.getElementById("downloadsSearchInput"),
  downloadsSearchBtn: document.getElementById("downloadsSearchBtn"),
  downloadsList: document.getElementById("downloadsList"),
  downloadsResults: document.getElementById("downloadsResults"),
};

// =============================
// State
// =============================
let conversations = [];
let selectedConversationId = null;
let searchQuery = "";

let pendingFiles = []; // File[]

let isGenerating = false;
let currentAbortController = null;
let pinnedToBottom = true;
let sidebarCollapsed = false;
let useDownloadsInChat = false;
let responseMode = "tecnico"; // tecnico | resumido | didatico | estrategico
let streamingEnabled = true; // true => POST /chat/stream (SSE), false => POST /chat (JSON)
let userId = "";
let backendOnline = null; // boolean | null
let topbarStatusOverride = "";
let downloadsFileCount = null; // number | null
window.__paytech = window.__paytech || {};
window.__paytech.build = PAYTECH_BUILD;
try {
  // eslint-disable-next-line no-console
  console.info("[paytech] build", PAYTECH_BUILD);
} catch { }
// Global event bus (must stay the same reference even if the script is loaded twice).
const _ptEvents = Array.isArray(window.__paytech.events) ? window.__paytech.events : [];
window.__paytech.events = _ptEvents;
window.__paytech.loadCount = Number(window.__paytech.loadCount || 0) + 1;
const _PT_DEBUG_KEY = "paytech.debugEvents.v1";
let _ptPersistEvery = 4;
let _ptPersistCounter = 0;
function ptLog(name, data) {
  try {
    const ev = { name: String(name || "event"), at: Date.now(), data: data ?? null };
    _ptEvents.push(ev);
    if (_ptEvents.length > 200) _ptEvents.splice(0, _ptEvents.length - 200);

    // Persist debug trail across reloads (helps when a click triggers a reload or when the user refreshes before copying).
    _ptPersistCounter++;
    if (_ptPersistCounter % _ptPersistEvery === 0 || String(ev.name).startsWith("fetch:") || String(ev.name).startsWith("sse:")) {
      try {
        localStorage.setItem(
          _PT_DEBUG_KEY,
          JSON.stringify({ build: PAYTECH_BUILD, loadCount: window.__paytech.loadCount, at: Date.now(), events: _ptEvents.slice(-200) })
        );
      } catch { }
    }
  } catch { }
}
window.__paytech.log = ptLog;
ptLog("init", { build: PAYTECH_BUILD, loadCount: window.__paytech.loadCount });
window.__paytech.state = () => {
  const sel = (() => {
    try { return getSelectedConversation(); } catch { return null; }
  })();
  return ({
  BACKEND_BASE,
  backendOnline,
  bindOk: !!window.__paytech?.bindOk,
  selectedConversationId,
  selectedConversationFound: !!sel,
  selectedConversationMessages: sel?.messages?.length ?? null,
  conversations: Array.isArray(conversations) ? conversations.length : null,
  isGenerating,
  useDownloadsInChat,
  downloadsFileCount,
  earlyErrors: _earlyErrors.length,
  lastError: _earlyErrors.length ? _earlyErrors[_earlyErrors.length - 1] : null,
  lastEvent: _ptEvents.length ? _ptEvents[_ptEvents.length - 1] : null,
  });
};
window.__paytech.eventsLast = (n = 20) => {
  const k = Math.max(1, Number(n || 20));
  return _ptEvents.slice(-k);
};
window.__paytech.eventsDump = (n = 50) => JSON.stringify(window.__paytech.eventsLast(n), null, 2);
window.__paytech.eventsDumpPersisted = () => {
  try { return localStorage.getItem(_PT_DEBUG_KEY) || ""; } catch { return ""; }
};
window.__paytech.eventsClear = () => {
  try { _ptEvents.splice(0, _ptEvents.length); } catch { }
  try { localStorage.removeItem(_PT_DEBUG_KEY); } catch { }
  ptLog("init", { build: PAYTECH_BUILD, loadCount: window.__paytech.loadCount, cleared: true });
};
window.__paytech.peekComposer = () => {
  const main = String(el.input?.value || "");
  const empty = String(el.emptyInput?.value || "");
  const active = false;
  try {
    // uses app logic (selected conversation)
    // eslint-disable-next-line no-unused-vars
    const _ = hasActiveConversation();
  } catch { }
  const hasActive = (() => {
    try { return hasActiveConversation(); } catch { return false; }
  })();
  const chosen = hasActive ? main : (empty.trim() ? empty : main);
  return {
    hasActiveConversation: hasActive,
    mainLen: main.length,
    emptyLen: empty.length,
    chosenLen: chosen.length,
    chosenPreview: chosen.slice(0, 80),
  };
};

// Always-on capture diagnostics: prove whether UI events are firing even if handlers misbehave.
(() => {
  if (window.__paytechCaptureDiagnosticsInstalled) return;
  window.__paytechCaptureDiagnosticsInstalled = true;

  function flash(elm) {
    try {
      if (!elm) return;
      elm.setAttribute("data-pt-flash", "1");
      window.setTimeout(() => {
        try { elm.removeAttribute("data-pt-flash"); } catch { }
      }, 180);
    } catch { }
  }

  function logComposerClick(kind, target) {
    const btnSend = target?.closest?.("#sendBtn");
    const btnEmpty = target?.closest?.("#emptySendBtn");
    const btn = btnSend || btnEmpty;
    const composer = target?.closest?.(".composer") || target?.closest?.(".empty-composer");

    if (btn) {
      flash(btn);
      ptLog(kind, {
        id: btn.id,
        disabled: !!btn.disabled,
        ariaDisabled: String(btn.getAttribute?.("aria-disabled") || ""),
        isGenerating,
        hasActiveConversation: hasActiveConversation(),
        inputLen: String(el.input?.value || "").length,
        emptyLen: String(el.emptyInput?.value || "").length,
      });
      return;
    }

    if (composer) {
      ptLog("cap:click:composer", {
        tag: String(target?.tagName || ""),
        id: String(target?.id || ""),
        cls: String(target?.className || ""),
        isGenerating,
        hasActiveConversation: hasActiveConversation(),
      });
    }
  }

  document.addEventListener("pointerdown", (e) => logComposerClick("cap:pointerdown", e.target), true);
  document.addEventListener("click", (e) => logComposerClick("cap:click", e.target), true);

  // Catch-all for the bottom composer area (helps detect overlays intercepting the click).
  document.addEventListener("click", (e) => {
    try {
      const y = Number(e?.clientY ?? -1);
      if (!isFinite(y)) return;
      if (y < (window.innerHeight - 170)) return; // only bottom area
      const t = e.target;
      const path = (typeof e.composedPath === "function" ? e.composedPath() : []) || [];
      const pathBrief = path
        .slice(0, 8)
        .map((n) => {
          try {
            if (!n || !n.tagName) return String(n);
            const id = n.id ? `#${n.id}` : "";
            const cls = (n.className && typeof n.className === "string") ? `.${n.className.split(/\s+/).filter(Boolean).slice(0, 2).join(".")}` : "";
            return `${n.tagName}${id}${cls}`;
          } catch {
            return "?";
          }
        });
      ptLog("cap:click:bottom", {
        x: e.clientX,
        y,
        target: {
          tag: String(t?.tagName || ""),
          id: String(t?.id || ""),
          cls: String(t?.className || ""),
        },
        path: pathBrief,
      });
    } catch { }
  }, true);

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    const ta = e.target?.closest?.("#input,#emptyInput");
    if (!ta) return;
    ptLog("cap:enter", {
      id: ta.id,
      isGenerating,
      hasActiveConversation: hasActiveConversation(),
      len: String(ta.value || "").length,
    });
  }, true);
})();
window.__paytech.selftest = async () => {
  const base = String(BACKEND_BASE || "").trim();
  const out = {
    base,
    health: { ok: false, status: null },
    chat: { ok: false, status: null, hasReply: false },
    stream: { ok: false, status: null, contentType: "", sawDelta: false, sawDone: false, bytes: 0 },
    error: null,
  };
  try {
    // health
    try {
      const r = await fetch(`${base}/health`, { method: "GET" });
      out.health.status = r.status;
      out.health.ok = r.ok;
    } catch (e) {
      out.health.ok = false;
      out.error = `health: ${String(e?.message || e)}`;
    }

    // chat (JSON)
    try {
      const r = await fetch(`${base}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [{ role: "user", content: "ping" }] }),
      });
      out.chat.status = r.status;
      out.chat.ok = r.ok;
      const data = await r.json().catch(() => null);
      out.chat.hasReply = !!String(data?.reply || "").trim();
    } catch (e) {
      out.chat.ok = false;
      out.error = `chat: ${String(e?.message || e)}`;
    }

    // stream (SSE) - read a few seconds
    const ac = new AbortController();
    const timer = window.setTimeout(() => {
      try { ac.abort(); } catch { }
    }, 3500);
    try {
      const r = await fetch(`${base}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify({ messages: [{ role: "user", content: "ping" }] }),
        signal: ac.signal,
      });
      out.stream.status = r.status;
      out.stream.contentType = String(r.headers?.get?.("content-type") || "");
      out.stream.ok = r.ok;
      const reader = r.body?.getReader?.();
      if (reader) {
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          out.stream.bytes += value?.byteLength || 0;
          buf += decoder.decode(value, { stream: true });
          if (buf.includes("event: delta")) out.stream.sawDelta = true;
          if (buf.includes("\"phase\": \"done\"") || buf.includes("event: done")) out.stream.sawDone = true;
          if (out.stream.sawDelta && out.stream.bytes > 200) break;
        }
      }
    } catch (e) {
      if (String(e?.name || "") !== "AbortError") {
        out.error = `stream: ${String(e?.message || e)}`;
      }
    } finally {
      try { clearTimeout(timer); } catch { }
    }

    return out;
  } catch (e) {
    out.error = String(e?.message || e);
    return out;
  }
};

// Health ping throttling (avoid spamming /health on repeated failures)
let _healthLastAt = 0;
let _healthLastOk = null; // boolean | null
let _healthLastBase = "";
let _healthInFlight = null; // Promise<boolean> | null
const HEALTH_COOLDOWN_MS = 2500;

// Streaming handles
let streamingMsg = null; // { convId, msgId, contentEl, textNode, cursorEl }
let streamBuffer = "";
let streamFlushRaf = 0;

// Markdown
function getMd() {
  try {
    // @ts-ignore
    if (window.markdownit) {
      // @ts-ignore
      return window.markdownit({
        html: false,
        linkify: true,
        breaks: true,
        typographer: true,
      });
    }
  } catch { }
  return null;
}
const md = getMd();

// =============================
// Utils
// =============================
let _lastBrowserTitle = "";
let _lastFaviconState = "";
let _faviconBaseHref = "";
let _faviconActiveHref = "";
let _faviconIconPromise = null;

function syncBrowserTitle() {
  const c = getSelectedConversation();
  const title = c ? `${String(c.title || "Conversa").trim() || "Conversa"} \u2013 ${APP_TITLE}` : APP_TITLE;
  if (title === _lastBrowserTitle) return;
  _lastBrowserTitle = title;
  document.title = title;
}

async function syncFavicon() {
  const link = document.getElementById("appFavicon");
  if (!link) return;

  if (!_faviconBaseHref) _faviconBaseHref = link.getAttribute("href") || "./assets/pay.png";
  const nextState = isGenerating ? "active" : "idle";
  if (nextState === _lastFaviconState) return;
  _lastFaviconState = nextState;

  if (nextState === "idle") {
    link.setAttribute("href", _faviconBaseHref);
    return;
  }

  try {
    if (_faviconActiveHref) {
      link.setAttribute("href", _faviconActiveHref);
      return;
    }

    if (!_faviconIconPromise) {
      _faviconIconPromise = new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = _faviconBaseHref;
      });
    }

    const img = await _faviconIconPromise;
    const size = 32;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, size, size);
    ctx.drawImage(img, 0, 0, size, size);

    // Subtle "active" badge (no neon)
    const r = 5.2;
    const cx = size - 8;
    const cy = size - 8;
    ctx.beginPath();
    ctx.arc(cx, cy, r + 2.2, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(11,15,20,0.92)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(cx, cy, r + 0.8, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(126,211,33,0.85)";
    ctx.fill();

    _faviconActiveHref = canvas.toDataURL("image/png");
    link.setAttribute("href", _faviconActiveHref);
  } catch {
    // fallback: keep base favicon
    link.setAttribute("href", _faviconBaseHref);
  }
}

function uuid() {
  try {
    return crypto.randomUUID();
  } catch {
    return "id-" + Math.random().toString(16).slice(2) + "-" + Date.now().toString(16);
  }
}

function nowISO() {
  return new Date().toISOString();
}

function clamp(n, a, b) {
  return Math.max(a, Math.min(b, n));
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function fetchWithRetry(url, options, retry = { retries: 1, delayMs: 360 }) {
  const retries = Math.max(0, Number(retry?.retries ?? 1));
  const delayMs = Math.max(0, Number(retry?.delayMs ?? 360));
  let lastErr = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fetch(url, options);
    } catch (e) {
      lastErr = e;
      const aborted = String(options?.signal?.aborted || "") === "true" || String(e?.name || "") === "AbortError";
      if (aborted) throw e;
      if (attempt >= retries) throw e;
      await sleep(delayMs);
    }
  }
  throw lastErr || new Error("fetch failed");
}

async function pingBackendHealth(timeoutMs = 900) {
  const base = String(BACKEND_BASE || "").trim();
  const now = Date.now();
  if (base && base === _healthLastBase && _healthLastOk !== null && (now - _healthLastAt) < HEALTH_COOLDOWN_MS) {
    return _healthLastOk;
  }
  if (_healthInFlight && base && base === _healthLastBase) return await _healthInFlight;

  const ac = new AbortController();
  const timer = window.setTimeout(() => {
    try { ac.abort(); } catch { }
  }, timeoutMs);

  _healthLastBase = base;
  _healthInFlight = (async () => {
    try {
      const res = await fetch(`${BACKEND_BASE}/health`, { method: "GET", signal: ac.signal });
      return !!res.ok;
    } catch {
      return false;
    } finally {
      try { clearTimeout(timer); } catch { }
    }
  })();

  const ok = await _healthInFlight;
  _healthInFlight = null;
  _healthLastAt = Date.now();
  _healthLastOk = ok;
  return ok;
}

function getAlternateBackendBases() {
  const bases = [];
  const cur = String(BACKEND_BASE || "").trim();
  const local = "http://localhost:8000";
  const ip = "http://127.0.0.1:8000";

  if (cur) bases.push(cur);
  if (!bases.includes(local)) bases.push(local);
  if (!bases.includes(ip)) bases.push(ip);

  return bases;
}

async function ensureBackendBaseOnline() {
  const candidates = getAlternateBackendBases();
  for (const base of candidates) {
    const prev = BACKEND_BASE;
    BACKEND_BASE = base;
    const ok = await pingBackendHealth(900);
    if (ok) {
      backendOnline = true;
      try { localStorage.setItem(BACKEND_BASE_KEY, base); } catch { }
      if (prev !== base) toast(`Backend conectado: ${base}`, { ms: 1800 });
      return true;
    }
  }
  backendOnline = false;
  return false;
}

function setTopbarStatus(text) {
  topbarStatusOverride = String(text || "").trim();
  syncTopbarSubtitle();
}

function clearTopbarStatus() {
  topbarStatusOverride = "";
  syncTopbarSubtitle();
}

function syncTopbarSubtitle() {
  if (!el.currentSub) return;
  const active = !!getSelectedConversation();
  const offline = backendOnline === false;

  if (topbarStatusOverride) {
    el.currentSub.textContent = topbarStatusOverride;
    return;
  }

  if (offline) {
    el.currentSub.textContent = "Backend offline";
    return;
  }

  if (!active) {
    el.currentSub.textContent = "";
    return;
  }

  el.currentSub.textContent = isGenerating ? "Gerando…" : "Pronto";
}

function safeJsonParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}

function toast(msg, { ms = 2200 } = {}) {
  if (!el.toast) return;
  el.toast.textContent = String(msg || "");
  el.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.toast.hidden = true), ms);
}

async function copyToClipboard(text) {
  const t = String(text || "");
  try {
    await navigator.clipboard.writeText(t);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      return true;
    } catch {
      return false;
    }
  }
}

function getSystemTheme() {
  try {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

function readStoredTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return (t === "dark" || t === "light") ? t : null;
  } catch {
    return null;
  }
}

function updateThemeToggleIcon() {
  const t = document.documentElement.getAttribute("data-theme") || "light";
  const icon = t === "dark" ? "light_mode" : "dark_mode";
  const label = t === "dark" ? "Alternar para claro" : "Alternar para escuro";
  const ms = el.themeToggle?.querySelector?.(".ms");
  if (ms) ms.textContent = icon;
  if (el.themeToggle) {
    el.themeToggle.title = label;
    el.themeToggle.setAttribute("aria-label", label);
  }
}

function updateResponseModeButton() {
  if (!el.responseModeBtn) return;
  const label = `Modo: ${labelForMode(responseMode)}`;
  el.responseModeBtn.title = label;
  el.responseModeBtn.setAttribute("aria-label", label);
}

function applyTheme(theme, { persist } = { persist: false }) {
  const t = (theme === "dark" || theme === "light") ? theme : "light";
  document.documentElement.setAttribute("data-theme", t);
  if (persist) {
    try { localStorage.setItem(THEME_KEY, t); } catch { }
  }
  updateThemeToggleIcon();
}

function initTheme() {
  applyTheme(readStoredTheme() || getSystemTheme(), { persist: false });
  try {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener?.("change", () => {
      if (!readStoredTheme()) applyTheme(getSystemTheme(), { persist: false });
    });
  } catch { }
}

// =============================
// Storage
// =============================
function loadState() {
  const raw = (() => {
    try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
  })();
  const parsed = raw ? safeJsonParse(raw) : null;
  const list = Array.isArray(parsed) ? parsed : [];

  conversations = list
    .map((c) => normalizeConversation(c))
    .filter(Boolean);

  // most recent first
  conversations.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));

  // Restore last selected conversation when possible (prevents "chat looks stuck" after refresh).
  selectedConversationId = null;
  try {
    const saved = String(localStorage.getItem(STORAGE_SELECTED) || "").trim();
    if (saved && conversations.some((c) => c.id === saved)) {
      selectedConversationId = saved;
    }
  } catch { }
  // If there is only one conversation, auto-open it for better UX.
  if (!selectedConversationId && conversations.length === 1) {
    selectedConversationId = conversations[0].id;
  }

  try {
    const v = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (v == null) {
      // compat: migra do key antigo, se existir
      const legacy = localStorage.getItem("paytech.sidebarCollapsed");
      if (legacy != null) localStorage.setItem(SIDEBAR_COLLAPSED_KEY, legacy);
    }
    sidebarCollapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    sidebarCollapsed = false;
  }

  try {
    useDownloadsInChat = localStorage.getItem(DOWNLOADS_USE_KEY) === "1";
  } catch {
    useDownloadsInChat = false;
  }

  try {
    responseMode = normalizeMode(localStorage.getItem(RESPONSE_MODE_KEY) || "tecnico");
  } catch {
    responseMode = "tecnico";
  }

  try {
    const v = String(localStorage.getItem(STREAMING_ENABLED_KEY) || "").trim().toLowerCase();
    if (!v) streamingEnabled = true;
    else streamingEnabled = (v === "1" || v === "true" || v === "on" || v === "yes");
  } catch {
    streamingEnabled = true;
  }

  try {
    userId = String(localStorage.getItem(USER_ID_KEY) || "").trim();
    if (!userId) {
      userId = uuid();
      localStorage.setItem(USER_ID_KEY, userId);
    }
  } catch {
    userId = userId || uuid();
  }
}

function persistState() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations)); } catch { }
  try { localStorage.setItem(STORAGE_SELECTED, selectedConversationId || ""); } catch { }
  try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? "1" : "0"); } catch { }
  try { localStorage.setItem(RESPONSE_MODE_KEY, normalizeMode(responseMode)); } catch { }
  try { localStorage.setItem(STREAMING_ENABLED_KEY, streamingEnabled ? "1" : "0"); } catch { }
  try { if (userId) localStorage.setItem(USER_ID_KEY, userId); } catch { }
}

function normalizeConversation(c) {
  if (!c || typeof c !== "object") return null;
  const id = String(c.id || "").trim() || uuid();
  const createdAt = String(c.createdAt || nowISO());
  const updatedAt = String(c.updatedAt || createdAt);
  const title = String(c.title || "Conversa").trim() || "Conversa";
  const messages = Array.isArray(c.messages) ? c.messages.map(normalizeMessage).filter(Boolean) : [];
  const titleSource = String(c.titleSource || "").trim() || "local"; // local | ai | manual
  const titleAutoDone = !!c.titleAutoDone;
  return { id, title, createdAt, updatedAt, messages, titleSource, titleAutoDone };
}

function normalizeMessage(m) {
  if (!m || typeof m !== "object") return null;
  const id = String(m.id || "").trim() || uuid();
  const role = (m.role === "user" || m.role === "assistant" || m.role === "system") ? m.role : "assistant";
  const content = String(m.content || "");
  const ts = String(m.ts || nowISO());
  const attachments = Array.isArray(m.attachments) ? m.attachments.map((a) => ({
    name: String(a?.name || ""),
    type: String(a?.type || ""),
    size: Number(a?.size || 0),
  })) : [];
  const interrupted = !!m.interrupted;
  const sources = Array.isArray(m.sources) ? m.sources : [];
  const artifacts = Array.isArray(m.artifacts) ? m.artifacts : [];
  return { id, role, content, ts, attachments, interrupted, sources, artifacts };
}

function getSelectedConversation() {
  return conversations.find((c) => c.id === selectedConversationId) || null;
}

function upsertConversation(conv) {
  const i = conversations.findIndex((c) => c.id === conv.id);
  if (i >= 0) conversations[i] = conv;
  else conversations.unshift(conv);
  conversations.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
}

function createConversation({ title = "Conversa" } = {}) {
  const id = uuid();
  const ts = nowISO();
  return { id, title, createdAt: ts, updatedAt: ts, messages: [], titleSource: "local", titleAutoDone: false };
}

// =============================
// Menus (per conversation)
// =============================
let lastMenuAnchorEl = null;

function closeMenus({ restoreFocus = false } = {}) {
  const anchor = lastMenuAnchorEl;
  for (const m of [el.itemMenu, el.exportMenu, el.modeMenu]) {
    if (!m) continue;
    m.hidden = true;
    m.innerHTML = "";
  }
  try {
    if (anchor?.classList?.contains?.("kebab")) anchor.setAttribute("aria-expanded", "false");
  } catch { }
  lastMenuAnchorEl = null;
  if (restoreFocus && anchor && document.contains(anchor)) {
    try { anchor.focus(); } catch { }
  }
}

function menuPosition(menuEl, anchorEl) {
  const r = anchorEl.getBoundingClientRect();
  const pad = 10;
  const w = 260;
  const x = clamp(r.right - w, pad, window.innerWidth - w - pad);
  const y = clamp(r.bottom + 8, pad, window.innerHeight - pad - 180);
  menuEl.style.left = `${x}px`;
  menuEl.style.top = `${y}px`;
}

function normalizeMode(m) {
  const t = String(m || "").trim().toLowerCase();
  if (t === "tecnico" || t === "resumido" || t === "didatico" || t === "estrategico") return t;
  return "tecnico";
}

function labelForMode(m) {
  switch (normalizeMode(m)) {
    case "resumido": return "Resumido";
    case "didatico": return "Didático";
    case "estrategico": return "Estratégico";
    default: return "Técnico";
  }
}

function renderModeMenu() {
  if (!el.modeMenu) return;
  const current = normalizeMode(responseMode);
  const streamingLabel = streamingEnabled ? "Ligado" : "Desligado";
  el.modeMenu.innerHTML = `
    <div class="menu-title">Modo de resposta</div>
    <button class="menu-item" data-action="set-mode" data-mode="tecnico" role="menuitem">
      <span>Técnico</span>
      <span class="right">${current === "tecnico" ? "✓" : ""}</span>
    </button>
    <button class="menu-item" data-action="set-mode" data-mode="resumido" role="menuitem">
      <span>Resumido</span>
      <span class="right">${current === "resumido" ? "✓" : ""}</span>
    </button>
    <button class="menu-item" data-action="set-mode" data-mode="didatico" role="menuitem">
      <span>Didático</span>
      <span class="right">${current === "didatico" ? "✓" : ""}</span>
    </button>
    <button class="menu-item" data-action="set-mode" data-mode="estrategico" role="menuitem">
      <span>Estratégico</span>
      <span class="right">${current === "estrategico" ? "✓" : ""}</span>
    </button>
    <div class="menu-sep" aria-hidden="true"></div>
    <button class="menu-item" data-action="toggle-streaming" role="menuitem">
      <span>Streaming</span>
      <span class="right">${streamingLabel}</span>
    </button>
  `;
}

function openModeMenu(anchorEl) {
  if (!el.modeMenu) return;
  closeMenus();
  renderModeMenu();
  el.modeMenu.hidden = false;
  menuPosition(el.modeMenu, anchorEl);
  lastMenuAnchorEl = anchorEl;
  el.modeMenu.querySelector("button.menu-item")?.focus?.();
}

function renderItemMenu(convId) {
  el.itemMenu.innerHTML = `
    <button class="menu-item" data-action="rename" data-id="${convId}" role="menuitem">Renomear</button>
    <button class="menu-item" data-action="delete" data-id="${convId}" role="menuitem">Excluir</button>
    <button class="menu-item" data-action="download" data-id="${convId}" role="menuitem">Baixar conversa</button>
  `;
}

function openItemMenu(anchorEl, convId) {
  closeMenus();
  renderItemMenu(convId);
  el.itemMenu.hidden = false;
  menuPosition(el.itemMenu, anchorEl);
  lastMenuAnchorEl = anchorEl;
  try { anchorEl?.setAttribute?.("aria-expanded", "true"); } catch { }
  el.itemMenu.querySelector("button.menu-item")?.focus?.();
}

function renderExportMenu(convId) {
  el.exportMenu.innerHTML = `
    <div class="menu-title">Baixar conversa</div>
    <button class="menu-item" data-action="export-docx" data-id="${convId}" role="menuitem">
      <span>Word (ABNT)</span>
      <span class="right">.docx</span>
    </button>
    <button class="menu-item" data-action="export-pdf" data-id="${convId}" role="menuitem">
      <span>PDF (ABNT)</span>
      <span class="right">.pdf</span>
    </button>
  `;
}

function openExportMenu(anchorEl, convId) {
  // Important: don't call closeMenus() here because it clears the itemMenu DOM,
  // which would detach the clicked "Baixar conversa" button and break anchoring
  // on some browsers (menu opens in a weird place / appears not to open).
  if (el.itemMenu) {
    el.itemMenu.hidden = true;
    el.itemMenu.innerHTML = "";
  }
  renderExportMenu(convId);
  el.exportMenu.hidden = false;
  menuPosition(el.exportMenu, anchorEl);
  lastMenuAnchorEl = anchorEl;
  el.exportMenu.querySelector("button.menu-item")?.focus?.();
}


// =============================
// Rendering
// =============================
function renderHeader() {
  const c = getSelectedConversation();
  const active = !!c;
  el.currentTitle.textContent = active ? (c?.title || "Conversa") : "";
  syncTopbarSubtitle();
  syncBrowserTitle();
}

function renderLayout() {
  const active = !!getSelectedConversation();
  if (el.main) el.main.classList.toggle("is-empty", !active);
  if (el.emptyState) el.emptyState.hidden = active;
  if (el.toBottomBtn) el.toBottomBtn.hidden = true;
  if (!active) {
    try { el.thread?.replaceChildren?.(); } catch { }
  }
}

function renderSidebarList() {
  const q = (searchQuery || "").trim().toLowerCase();
  const list = q
    ? conversations.filter((c) => (c.title || "").toLowerCase().includes(q))
    : conversations;

  el.conversationList.replaceChildren();
  const frag = document.createDocumentFragment();
  const collapsed = sidebarCollapsed && !window.matchMedia("(max-width: 900px)").matches;

  for (const c of list) {
    const item = document.createElement("div");
    item.className = "conv-item";
    item.tabIndex = 0;
    item.dataset.id = c.id;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(c.id === selectedConversationId));

    const title = document.createElement("div");
    title.className = "conv-title";
    if (collapsed) {
      const t = (c.title || "C").trim();
      title.textContent = (t[0] || "C").toUpperCase();
      title.title = c.title || "Conversa";
    } else {
      title.textContent = c.title || "Conversa";
    }

    const kebab = document.createElement("button");
    kebab.className = "kebab";
    kebab.type = "button";
    kebab.title = "Opções";
    kebab.setAttribute("aria-label", "Opções");
    kebab.setAttribute("aria-haspopup", "menu");
    kebab.setAttribute("aria-expanded", "false");
    kebab.textContent = "⋯";
    kebab.dataset.action = "kebab";
    kebab.dataset.id = c.id;
    if (collapsed) kebab.style.display = "none";

    item.appendChild(title);
    item.appendChild(kebab);
    frag.appendChild(item);
  }

  el.conversationList.appendChild(frag);
}

function clearThread() {
  el.thread.replaceChildren();
  // If a stream is in-flight, keep the logical handle so:
  // - watchdog fallback can still fire
  // - deltas can re-bind to the new DOM after a rerender/navigation
  // We drop DOM pointers because they are no longer valid after replaceChildren().
  if (streamingMsg) {
    streamingMsg.contentEl = null;
    streamingMsg.textNode = null;
    streamingMsg.cursorEl = null;
    streamingMsg.thinkingEl = null;
  }
  streamBuffer = "";
  if (streamFlushRaf) { cancelAnimationFrame(streamFlushRaf); streamFlushRaf = 0; }
}

function ensureStreamingBind() {
  if (!streamingMsg) return false;
  try {
    const conv = conversations.find((c) => c.id === streamingMsg.convId);
    const msg = conv?.messages?.find((m) => m.id === streamingMsg.msgId);
    const existingText = String(msg?.content || "");

    const article = el.thread?.querySelector?.(`article.msg[data-msg-id="${streamingMsg.msgId}"]`);
    const contentEl = article?.querySelector?.(".content");
    if (!contentEl) return false;

    const isSame = streamingMsg.contentEl === contentEl && streamingMsg.textNode && streamingMsg.textNode.isConnected;
    if (isSame) return true;

    const textNode = document.createTextNode(existingText);
    contentEl.replaceChildren(textNode);
    const cursor = document.createElement("span");
    cursor.className = "writing-cursor";
    contentEl.appendChild(cursor);

    streamingMsg.contentEl = contentEl;
    streamingMsg.textNode = textNode;
    streamingMsg.cursorEl = cursor;
    streamingMsg.thinkingEl = null;
    // If we already have content, mark as started so we don't re-insert thinking.
    if (existingText.trim()) streamingMsg.hasFirstChunk = true;

    // Re-show thinking if we still haven't received the first chunk.
    if (!streamingMsg.hasFirstChunk) {
      try {
        const t = document.createElement("span");
        t.className = "thinking";
        t.textContent = streamingMsg.thinkingText || "Analisando…";
        streamingMsg.thinkingEl = t;
        contentEl.insertBefore(t, cursor);
      } catch { }
    }

    ptLog("stream:rebind", { convId: streamingMsg.convId, msgId: streamingMsg.msgId, hasText: !!existingText.trim() });
    return true;
  } catch {
    return false;
  }
}

function renderThread() {
  clearThread();
  const c = getSelectedConversation();
  if (!c) return;
  const frag = document.createDocumentFragment();
  for (const m of c.messages) {
    frag.appendChild(buildMessageEl(m));
  }
  el.thread.appendChild(frag);
  pinnedToBottom = true;
  scrollToBottom(true);
  updateToBottomBtn();
}

function buildMessageEl(msg) {
  const article = document.createElement("article");
  article.className = `msg ${msg.role === "user" ? "user" : "assistant"}`;
  article.dataset.msgId = msg.id;

  const content = document.createElement("div");
  content.className = "content";
  article.appendChild(content);

  if (msg.role === "assistant") {
    renderAssistantContent(content, msg.content || "");
    if (msg.interrupted) {
      const status = document.createElement("div");
      status.className = "msg-status";
      status.textContent = "(interrompido)";
      article.appendChild(status);
    }
    if (Array.isArray(msg.sources) && msg.sources.length) {
      renderSources(content, msg.sources);
    }
    if (Array.isArray(msg.artifacts) && msg.artifacts.length) {
      renderArtifacts(content, msg.artifacts);
    }
  } else {
    // user: leitura limpa (sem markdown pesado aqui)
    content.textContent = msg.content || "";
    if (msg.attachments?.length) {
      const files = document.createElement("div");
      files.className = "files";
      files.style.marginTop = "8px";
      files.style.color = "var(--muted)";
      files.style.fontSize = "12px";
      files.textContent = `Anexos: ${msg.attachments.map((a) => a.name).filter(Boolean).join(", ")}`;
      article.appendChild(files);
    }
  }

  return article;
}

function renderSources(contentEl, items) {
  const root = contentEl.closest?.(".msg") || contentEl.parentElement;
  if (!root) return;
  root.querySelector?.(".sources")?.remove?.();

  const wrap = document.createElement("section");
  wrap.className = "sources";

  const title = document.createElement("div");
  title.className = "menu-title";
  title.textContent = "Fontes";
  wrap.appendChild(title);

  const list = document.createElement("div");
  list.style.display = "grid";
  list.style.gap = "8px";

  (items || []).forEach((it) => {
    const card = document.createElement("div");
    card.className = "dl-result";

    const top = document.createElement("div");
    top.className = "top";
    const file = document.createElement("div");
    file.className = "file";
    const meta = [];
    if (it.page) meta.push(`p.${it.page}`);
    if (it.sheet) meta.push(`aba ${it.sheet}`);
    if (it.rowRange) meta.push(`linhas ${it.rowRange}`);
    file.textContent = `${it.filename || "arquivo"}${meta.length ? " • " + meta.join(" • ") : ""}`;
    top.appendChild(file);
    card.appendChild(top);

    const snip = document.createElement("div");
    snip.className = "snippet";
    snip.textContent = it.snippet || "";
    card.appendChild(snip);

    list.appendChild(card);
  });

  wrap.appendChild(list);
  root.appendChild(wrap);
}

function renderArtifacts(contentEl, items) {
  const root = contentEl.closest?.(".msg") || contentEl.parentElement;
  if (!root) return;
  root.querySelector?.(".artifacts")?.remove?.();

  const wrap = document.createElement("section");
  wrap.className = "artifacts";

  const title = document.createElement("div");
  title.className = "menu-title";
  title.textContent = "Artefatos";
  wrap.appendChild(title);

  const row = document.createElement("div");
  row.style.display = "flex";
  row.style.gap = "8px";
  row.style.flexWrap = "wrap";

  (items || []).forEach((a) => {
    const link = document.createElement("a");
    link.className = "btn subtle";
    link.href = `${BACKEND_BASE}${a.url || ""}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.innerHTML = `<span class="ms" aria-hidden="true">download</span><span>${a.name || "download"}</span>`;
    row.appendChild(link);
  });

  wrap.appendChild(row);
  root.appendChild(wrap);
}

function renderAssistantContent(container, markdown) {
  const raw = String(markdown || "");

  if (!md || !window.DOMPurify) {
    container.textContent = raw;
    return;
  }

  const html = md.render(raw);
  // sanitize then insert once
  const safe = window.DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
  });
  container.innerHTML = safe;
  enhanceCodeBlocks(container);
}

function enhanceCodeBlocks(container) {
  const pres = container.querySelectorAll("pre > code");
  pres.forEach((code) => {
    const pre = code.parentElement;
    if (!pre || pre.parentElement?.classList.contains("codewrap")) return;
    const wrap = document.createElement("div");
    wrap.className = "codewrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = "Copiar";
    btn.addEventListener("click", async () => {
      const ok = await copyToClipboard(code.textContent || "");
      btn.textContent = ok ? "Copiado" : "Falhou";
      setTimeout(() => (btn.textContent = "Copiar"), 900);
    });

    pre.replaceWith(wrap);
    wrap.appendChild(pre);
    wrap.appendChild(btn);
  });
}

function appendMessageToThread(msg) {
  const node = buildMessageEl(msg);
  el.thread.appendChild(node);
  return node;
}

// =============================
// Scroll intelligence
// =============================
function isNearBottom() {
  const sc = el.chat;
  if (!sc) return true;
  const distance = sc.scrollHeight - sc.scrollTop - sc.clientHeight;
  return distance <= 140;
}

function scrollToBottom(force = false) {
  const sc = el.chat;
  if (!sc) return;
  if (!force && !pinnedToBottom) return;
  sc.scrollTop = sc.scrollHeight;
}

function scrollToBottomIfNeeded() {
  if (pinnedToBottom) scrollToBottom(false);
  updateToBottomBtn();
}

// =============================
// Composer: files + autosize
// =============================
function renderFileChips() {
  if (!pendingFiles.length) {
    el.fileChips.hidden = true;
    el.fileChips.replaceChildren();
    return;
  }
  el.fileChips.hidden = false;
  el.fileChips.replaceChildren();

  const frag = document.createDocumentFragment();
  pendingFiles.forEach((f, idx) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    const name = document.createElement("span");
    name.textContent = f.name;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "chip-x";
    x.textContent = "✕";
    x.title = "Remover";
    x.addEventListener("click", () => {
      pendingFiles.splice(idx, 1);
      renderFileChips();
      updateComposerControls();
    });
    chip.appendChild(name);
    chip.appendChild(x);
    frag.appendChild(chip);
  });
  el.fileChips.appendChild(frag);
}

function autoResize() {
  const ta = el.input;
  if (!ta) return;
  ta.style.height = "0px";
  const next = clamp(ta.scrollHeight, 24, 180);
  ta.style.height = `${next}px`;
}

// =============================
// Conversation ops
// =============================
function selectConversation(id) {
  if (!id || id === selectedConversationId) return;
  selectedConversationId = id;
  persistState();
  syncBrowserTitle();
  renderLayout();
  renderHeader();
  renderSidebarList();
  renderThread();
  closeSidebarIfMobile();
}

function goHome() {
  if (isGenerating) stopGenerating();
  selectedConversationId = null;
  persistState();
  syncBrowserTitle();
  closeMenus();
  renderLayout();
  renderHeader();
  renderSidebarList();
  renderThread();
  updateComposerControls();
  try { el.emptyInput?.focus?.(); } catch { }
}

function renameConversation(id) {
  const c = conversations.find((x) => x.id === id);
  if (!c) return;
  const next = prompt("Renomear conversa:", c.title || "Conversa");
  if (next == null) return;
  const title = String(next).trim();
  if (!title) return;
  c.title = title;
  c.titleSource = "manual";
  c.titleAutoDone = true;
  c.updatedAt = nowISO();
  upsertConversation(c);
  persistState();
  syncBrowserTitle();
  renderHeader();
  renderSidebarList();
}

function deleteConversation(id) {
  const idx = conversations.findIndex((x) => x.id === id);
  if (idx < 0) return;
  const c = conversations[idx];
  const name = (c?.title || "Conversa").trim() || "Conversa";
  if (!confirm(`Excluir "${name}"?`)) return;

  conversations.splice(idx, 1);
  if (selectedConversationId === id) {
    selectedConversationId = null;
  }

  persistState();
  syncBrowserTitle();
  renderLayout();
  renderHeader();
  renderSidebarList();
  renderThread();
}

// =============================
// Sending + Streaming
// =============================
function hasActiveConversation() {
  return !!getSelectedConversation();
}

function deriveTitleFromFirstMessage(text) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (!t) return "Conversa";
  const cleaned = t.replace(/^["'“”‘’]+|["'“”‘’]+$/g, "").trim();
  const head = cleaned.split(/[.!?]\s/)[0] || cleaned;
  const max = 48;
  return (head.length > max ? head.slice(0, max - 1).trimEnd() + "…" : head) || "Conversa";
}

async function maybeAutoTitleConversation(convId) {
  const conv = conversations.find((c) => c.id === convId);
  if (!conv) return;
  if (conv.titleSource === "manual" || conv.titleAutoDone) return;

  const users = (conv.messages || []).filter((m) => m.role === "user");
  const assistants = (conv.messages || []).filter((m) => m.role === "assistant");
  if (users.length !== 1 || assistants.length !== 1) return;

  const firstUser = String(users[0]?.content || "").trim();
  const firstAssistant = String(assistants[0]?.content || "").trim();
  if (!firstUser || !firstAssistant) return;

  conv.titleAutoDone = true; // avoid duplicate calls
  upsertConversation(conv);
  persistState();

  try {
    const res = await fetch(`${BACKEND_BASE}/titles/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        conversation_id: conv.id,
        first_user: firstUser,
        first_assistant: firstAssistant,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json().catch(() => null);
    const title = String(data?.title || "").trim();
    if (!title) return;
    if (conv.titleSource === "manual") return;
    if (title && title !== conv.title) {
      conv.title = title;
      conv.titleSource = "ai";
      conv.updatedAt = nowISO();
      upsertConversation(conv);
      persistState();
      syncBrowserTitle();
      renderHeader();
      renderSidebarList();
    }
  } catch {
    // keep local title
  }
}

function readComposerText() {
  const main = String(el.input?.value || "");
  const empty = String(el.emptyInput?.value || "");
  if (hasActiveConversation()) return main;
  // Empty-state UX: users may type in the bottom composer instead of the center composer.
  // Accept whichever has content to avoid "send does nothing".
  return empty.trim() ? empty : main;
}

function clearComposerText() {
  if (el.input) {
    el.input.value = "";
    autoResize();
  }
  if (el.emptyInput) {
    el.emptyInput.value = "";
    autoResizeEmpty();
  }
}

function focusComposer() {
  const target = hasActiveConversation() ? el.input : el.emptyInput;
  try { target?.focus?.(); } catch { }
}

function autoResizeEmpty() {
  const ta = el.emptyInput;
  if (!ta) return;
  ta.style.height = "0px";
  const next = clamp(ta.scrollHeight, 44, 220);
  ta.style.height = `${next}px`;
}

function setGenerating(on) {
  isGenerating = !!on;
  if (el.sendBtn) {
    const ms = el.sendBtn.querySelector?.(".ms");
    if (ms) ms.textContent = isGenerating ? "stop" : "arrow_upward";
    el.sendBtn.classList.toggle("primary", !isGenerating);
    el.sendBtn.classList.toggle("stop", isGenerating);
    const label = isGenerating ? "Parar geração" : "Enviar";
    el.sendBtn.title = isGenerating ? "Parar" : "Enviar";
    el.sendBtn.setAttribute("aria-label", label);
  }
  if (el.attachBtn) el.attachBtn.disabled = isGenerating;
  updateComposerControls();
  syncFavicon();
  try { el.chat?.setAttribute?.("aria-busy", isGenerating ? "true" : "false"); } catch { }
  renderHeader();
}

function canSendNow() {
  const text = readComposerText().trim();
  const hasPayload = !!text || pendingFiles.length > 0;
  return hasPayload && !isGenerating;
}

function updateComposerControls() {
  const main = String(el.input?.value || "").trim();
  const empty = String(el.emptyInput?.value || "").trim();
  const text = hasActiveConversation() ? main : (empty || main);
  const hasPayload = !!text || pendingFiles.length > 0;

  // Main action button: when generating it becomes "Stop" and must remain clickable.
  // Don't hard-disable: disabled buttons don't dispatch click events and the UI feels "stuck".
  // We gate in `sendMessage()` and show a toast when empty.
  if (el.sendBtn) {
    el.sendBtn.disabled = false;
    el.sendBtn.setAttribute("aria-disabled", String(!isGenerating && !hasPayload));
  }

  // Empty-state send is never used as "Stop"; disable during generation and when empty.
  if (el.emptySendBtn) {
    el.emptySendBtn.disabled = false;
    el.emptySendBtn.setAttribute("aria-disabled", String(isGenerating || !hasPayload));
  }
}

function buildBackendMessages(conv) {
  return (conv.messages || [])
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .map((m) => ({ role: m.role, content: m.content }))
    .filter((m) => String(m?.content || "").trim().length > 0);
}

function effectiveUseDownloads() {
  // If the user enabled "use downloads" but there are no documents, avoid wasting time on tool phase.
  if (!useDownloadsInChat) return false;
  // If we don't know yet (user never opened downloads panel), be optimistic:
  // the backend will quickly return zero hits if there are no documents.
  if (downloadsFileCount == null) return true;
  if (downloadsFileCount === 0) return false;
  return true;
}

function startStreamingIntoMessage({ convId, msgId, contentEl }) {
  // single node update: TextNode + cursor
  const textNode = document.createTextNode("");
  contentEl.replaceChildren(textNode);
  const cursor = document.createElement("span");
  cursor.className = "writing-cursor";
  contentEl.appendChild(cursor);

  streamingMsg = {
    convId,
    msgId,
    contentEl,
    textNode,
    cursorEl: cursor,
    hasFirstChunk: false,
    thinkingEl: null,
    thinkingText: "Analisando…",
    sources: null,
    artifacts: null,
    thinkingTimer: window.setTimeout(() => {
      if (!streamingMsg || streamingMsg.msgId !== msgId) return;
      if (streamingMsg.hasFirstChunk) return;
      const conv = conversations.find((c) => c.id === convId);
      const msg = conv?.messages?.find((m) => m.id === msgId);
      if (!msg || (msg.content || "").trim()) return;
      const t = document.createElement("span");
      t.className = "thinking";
      t.textContent = streamingMsg.thinkingText || "Analisando…";
      streamingMsg.thinkingEl = t;
      try { contentEl.insertBefore(t, cursor); } catch { }
    }, 260),
  };
  streamBuffer = "";
  if (streamFlushRaf) { cancelAnimationFrame(streamFlushRaf); streamFlushRaf = 0; }
}

function setThinkingStatus(nextText) {
  if (!streamingMsg) return;
  ensureStreamingBind();
  const t = String(nextText || "").trim();
  if (!t) return;
  streamingMsg.thinkingText = t;
  try {
    if (streamingMsg.thinkingEl) streamingMsg.thinkingEl.textContent = t;
  } catch { }
}

function ensureThinkingVisible() {
  if (!streamingMsg) return;
  ensureStreamingBind();
  if (streamingMsg.hasFirstChunk) return;
  if (streamingMsg.thinkingEl) return;
  try {
    const t = document.createElement("span");
    t.className = "thinking";
    t.textContent = streamingMsg.thinkingText || "Analisando…";
    streamingMsg.thinkingEl = t;
    streamingMsg.contentEl?.insertBefore?.(t, streamingMsg.cursorEl);
  } catch { }
}

function setStreamingSources(items) {
  if (!streamingMsg) return;
  streamingMsg.sources = Array.isArray(items) ? items : [];
}

function pushStreamingArtifact(artifact) {
  if (!streamingMsg) return;
  if (!Array.isArray(streamingMsg.artifacts)) streamingMsg.artifacts = [];
  streamingMsg.artifacts.push(artifact);
}

function streamAppend(text) {
  if (!streamingMsg) return;
  ensureStreamingBind();
  if (!streamingMsg.hasFirstChunk) {
    streamingMsg.hasFirstChunk = true;
    try { streamingMsg.thinkingEl?.remove?.(); } catch { }
    streamingMsg.thinkingEl = null;
    if (streamingMsg.thinkingTimer) {
      clearTimeout(streamingMsg.thinkingTimer);
      streamingMsg.thinkingTimer = 0;
    }
  }
  streamBuffer += String(text || "");
  if (streamFlushRaf) return;
  streamFlushRaf = requestAnimationFrame(() => {
    streamFlushRaf = 0;
    if (!streamingMsg || !streamBuffer) return;
    streamingMsg.textNode.appendData(streamBuffer);
    // keep state in-memory in sync
    const conv = conversations.find((c) => c.id === streamingMsg.convId);
    const msg = conv?.messages?.find((m) => m.id === streamingMsg.msgId);
    if (msg) msg.content += streamBuffer;
    streamBuffer = "";
    scrollToBottomIfNeeded();
  });
}

function finalizeStreaming({ ok, errorMessage } = { ok: true }) {
  if (!streamingMsg) return;
  const { convId, msgId, sources, artifacts } = streamingMsg;
  const contentEl = streamingMsg.contentEl;
  const cursorEl = streamingMsg.cursorEl;
  try { streamingMsg.thinkingEl?.remove?.(); } catch { }
  if (streamingMsg.thinkingTimer) clearTimeout(streamingMsg.thinkingTimer);
  try { cursorEl?.remove?.(); } catch { }

  const conv = conversations.find((c) => c.id === convId);
  const msg = conv?.messages?.find((m) => m.id === msgId);
  if (!conv || !msg) {
    streamingMsg = null;
    return;
  }

  if (!ok) {
    msg.content = String(errorMessage || "Falha ao gerar resposta.");
    if (contentEl) contentEl.textContent = msg.content;
  } else {
    if (contentEl) renderAssistantContent(contentEl, msg.content);
  }

  if (sources && Array.isArray(sources) && sources.length) {
    msg.sources = sources;
    if (contentEl) renderSources(contentEl, sources);
  }
  if (artifacts && Array.isArray(artifacts) && artifacts.length) {
    msg.artifacts = artifacts;
    if (contentEl) renderArtifacts(contentEl, artifacts);
  }

  streamingMsg = null;
  conv.updatedAt = nowISO();
  upsertConversation(conv);
  persistState();
  renderSidebarList();

  // Auto-title after the first assistant reply (non-blocking)
  if (ok) setTimeout(() => maybeAutoTitleConversation(convId), 0);
}

function finalizeStreamingInterrupted() {
  if (!streamingMsg) return;
  const { convId, msgId, contentEl, cursorEl } = streamingMsg;
  try { streamingMsg.thinkingEl?.remove?.(); } catch { }
  if (streamingMsg.thinkingTimer) clearTimeout(streamingMsg.thinkingTimer);
  if (cursorEl) cursorEl.remove();

  const conv = conversations.find((c) => c.id === convId);
  const msg = conv?.messages?.find((m) => m.id === msgId);
  if (!conv || !msg) {
    streamingMsg = null;
    return;
  }

  msg.interrupted = true;
  renderAssistantContent(contentEl, msg.content || "");
  const article = contentEl.closest?.(".msg");
  if (article && !article.querySelector(".msg-status")) {
    const status = document.createElement("div");
    status.className = "msg-status";
    status.textContent = "(interrompido)";
    article.appendChild(status);
  }

  streamingMsg = null;
  conv.updatedAt = nowISO();
  upsertConversation(conv);
  persistState();
  renderSidebarList();
}

async function sendMessage() {
  ptLog("sendMessage:enter", { isGenerating, hasActiveConversation: hasActiveConversation(), pendingFiles: pendingFiles.length });
  if (isGenerating) return;

  // Capture raw composer values BEFORE any UI clears them (useful for debugging "I typed but it says empty").
  const mainRaw = String(el.input?.value || "");
  const emptyRaw = String(el.emptyInput?.value || "");
  ptLog("sendMessage:composer:raw", {
    hasActiveConversation: hasActiveConversation(),
    mainLen: mainRaw.length,
    emptyLen: emptyRaw.length,
    pendingFiles: pendingFiles.length,
  });

  const text = readComposerText().trim();
  ptLog("sendMessage:composer", { textLen: text.length });
  if (!text && pendingFiles.length === 0) {
    ptLog("sendMessage:empty", { mainLen: mainRaw.length, emptyLen: emptyRaw.length });
    toast("Digite uma mensagem ou anexe um arquivo.");
    focusComposer();
    return;
  }

  // Create a conversation on first message (empty state -> active thread)
  let conv = getSelectedConversation();
  const creating = !conv;
  if (!conv) {
    ptLog("sendMessage:createConversation", { title: deriveTitleFromFirstMessage(text) });
    conv = createConversation({ title: deriveTitleFromFirstMessage(text) });
    upsertConversation(conv);
    selectedConversationId = conv.id;
    persistState();
    syncBrowserTitle();
    renderLayout();
    renderHeader();
    renderSidebarList();
    renderThread();
    setTimeout(() => {
      try { el.input?.focus?.(); } catch { }
    }, 0);
  }

  const filesToSend = pendingFiles.slice();

  // pinned logic snapshot (avoid forcing if user scrolled up)
  pinnedToBottom = isNearBottom();

  const userMsg = {
    id: uuid(),
    role: "user",
    content: text || (pendingFiles.length ? "(anexo)" : ""),
    ts: nowISO(),
    attachments: pendingFiles.map((f) => ({ name: f.name, type: f.type, size: f.size })),
  };
  conv.messages.push(userMsg);

  const assistantMsg = {
    id: uuid(),
    role: "assistant",
    content: "",
    ts: nowISO(),
    attachments: [],
  };
  conv.messages.push(assistantMsg);

  if (creating) {
    conv.title = deriveTitleFromFirstMessage(text);
  }
  syncBrowserTitle();

  conv.updatedAt = nowISO();
  upsertConversation(conv);
  persistState();

  // UI: append only the new nodes (não rerender do thread inteiro)
  appendMessageToThread(userMsg);
  const assistantNode = appendMessageToThread(assistantMsg);
  const assistantContent = assistantNode.querySelector(".content");
  startStreamingIntoMessage({ convId: conv.id, msgId: assistantMsg.id, contentEl: assistantContent });
  scrollToBottomIfNeeded();

  // reset composer
  clearComposerText();
  pendingFiles = [];
  renderFileChips();
  el.fileInput.value = "";
  updateComposerControls();

  setGenerating(true);
  ptLog("sendMessage:generating", { convId: conv.id });
  currentAbortController = new AbortController();
  let watchdogTimer = 0;
  let watchdogFallback = false;
  let emptyStreamDone = false;
  let finalized = false;
  let uploadedDocsForThisMessage = false;

  const uploadFilesForChat = async (files) => {
    const list = Array.isArray(files) ? files : (files ? [files] : []);
    if (!list.length) return false;

    // Upload attachments into the Downloads library (same pipeline used by RAG/tools).
    // Backend supports: POST /downloads/upload (multipart: files[])
    const fd = new FormData();
    for (const f of list) fd.append("files", f, f.name);

    const res = await fetchWithRetry(`${BACKEND_BASE}/downloads/upload`, {
      method: "POST",
      body: fd,
      signal: currentAbortController.signal,
    });
    if (!res.ok) {
      const t = await res.text().catch(() => "");
      throw new Error(`Upload falhou (${res.status}). ${t ? t.slice(0, 240) : ""}`.trim());
    }
    return true;
  };

  const runChatFallback = async (reason) => {
    try {
      if (!streamingMsg) return false;
      if (streamingMsg.hasFirstChunk) return false; // already streaming real tokens

      watchdogFallback = true;
      setTopbarStatus("Reconectando…");
      setThinkingStatus(String(reason || "Reconectando…"));
      ensureThinkingVisible();
      try { currentAbortController?.abort?.(); } catch { }

      const conv2 = conversations.find((c) => c.id === conv.id);
      const payload = {
        messages: buildBackendMessages(conv2 || conv),
        user_id: userId,
        thread_id: conv.id,
        title: conv.title,
        response_mode: responseMode,
        use_downloads: effectiveUseDownloads() || uploadedDocsForThisMessage,
        downloads_top_k: DOWNLOADS_TOP_K,
      };

      const r2 = await fetchWithRetry(`${BACKEND_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      ptLog("fetch:/chat:fallback:response", { status: r2.status, ok: r2.ok, ct: r2.headers?.get?.("content-type") || "" });
      if (!r2.ok) {
        const t2 = await r2.text().catch(() => "");
        finalizeStreaming({ ok: false, errorMessage: `Fallback /chat retornou ${r2.status}. ${t2 ? t2.slice(0, 240) : ""}`.trim() });
        finalized = true;
        return true;
      }
      const data = await r2.json().catch(() => null);
      const reply = String(data?.reply || "").trim();
      if (!reply) {
        finalizeStreaming({ ok: false, errorMessage: "Fallback /chat retornou vazio." });
        finalized = true;
        return true;
      }

      const conv3 = conversations.find((c) => c.id === conv.id);
      const msg3 = conv3?.messages?.find((m) => m.id === assistantMsg.id);
      if (msg3) msg3.content = reply;
      finalizeStreaming({ ok: true });
      finalized = true;
      return true;
    } catch (e) {
      finalizeStreaming({ ok: false, errorMessage: `Falha no fallback /chat. ${String(e?.message || "")}`.trim() });
      finalized = true;
      return true;
    } finally {
      clearTopbarStatus();
    }
  };

  try {
    // Non-stream mode: always use /chat (JSON) for maximum compatibility.
    if (!streamingEnabled) {
      if (filesToSend.length > 0) {
        toast("Anexos ainda não estão disponíveis sem streaming. Enviando só o texto.");
      }

      setTopbarStatus("Gerando…");
      setThinkingStatus("Gerando…");
      ensureThinkingVisible();

      const payload = {
        messages: buildBackendMessages(conv),
        user_id: userId,
        thread_id: conv.id,
        title: conv.title,
        response_mode: responseMode,
        use_downloads: effectiveUseDownloads(),
        downloads_top_k: DOWNLOADS_TOP_K,
      };

      const r = await fetchWithRetry(`${BACKEND_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: currentAbortController.signal,
      });
      ptLog("fetch:/chat:response", { status: r.status, ok: r.ok, ct: r.headers?.get?.("content-type") || "" });

      if (!r.ok) {
        const t = await r.text().catch(() => "");
        finalizeStreaming({ ok: false, errorMessage: `Servidor retornou ${r.status}. ${t ? t.slice(0, 240) : ""}`.trim() });
        finalized = true;
        return;
      }

      const data = await r.json().catch(() => null);
      const reply = String(data?.reply || "").trim();
      if (!reply) {
        finalizeStreaming({ ok: false, errorMessage: "Resposta vazia do servidor." });
        finalized = true;
        return;
      }

      const conv2 = conversations.find((c) => c.id === conv.id);
      const msg2 = conv2?.messages?.find((m) => m.id === assistantMsg.id);
      if (msg2) msg2.content = reply;

      const sources = Array.isArray(data?.sources) ? data.sources : [];
      finalizeStreaming({ ok: true, sources });
      finalized = true;
      return;
    }

    const hasFiles = filesToSend.length > 0;

    if (hasFiles) {
      setTopbarStatus("Enviando documentos…");
      setThinkingStatus("Enviando documentos…");
      ensureThinkingVisible();

      // Persist toggle so the user sees docs being used in subsequent messages too.
      setDownloadsUse(true);

      try {
        uploadedDocsForThisMessage = await uploadFilesForChat(filesToSend);
        await refreshDownloadsList({ silent: true });
      } catch (e) {
        clearTopbarStatus();
        finalizeStreaming({ ok: false, errorMessage: String(e?.message || "Falha no upload dos documentos.") });
        return;
      }
    }

    const res = await fetchWithRetry(`${BACKEND_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: buildBackendMessages(conv),
        user_id: userId,
        thread_id: conv.id,
        title: conv.title,
        response_mode: responseMode,
        use_downloads: effectiveUseDownloads() || uploadedDocsForThisMessage,
        downloads_top_k: DOWNLOADS_TOP_K,
      }),
      signal: currentAbortController.signal,
    });
    ptLog("fetch:/chat/stream:response", { status: res.status, ok: res.ok, ct: res.headers?.get?.("content-type") || "" });

    if (!res.ok) {
      const t = await res.text().catch(() => "");
      finalizeStreaming({ ok: false, errorMessage: `Servidor retornou ${res.status}. ${t ? t.slice(0, 240) : ""}`.trim() });
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
      finalizeStreaming({ ok: false, errorMessage: "Resposta inválida do servidor (sem stream)." });
      return;
    }

    const contentType = String(res.headers?.get?.("content-type") || "").toLowerCase();

    // If the stream stalls before the first chunk (proxy buffering/reload), fall back to non-streaming /chat.
    watchdogTimer = window.setTimeout(async () => {
      await runChatFallback("Sem resposta no streaming. Usando fallback…");
    }, 12000);

    if (contentType.includes("text/event-stream")) {
      let sawAnyDelta = false;
      await readSseStream(reader, {
        onStatus: (st) => {
          const phase = String(st?.phase || "").trim().toLowerCase();
          ptLog("sse:status", { phase });
          if (phase === "thinking") {
            setThinkingStatus("Analisando…");
            ensureThinkingVisible();
            setTopbarStatus("Analisando…");
          } else if (phase === "tool") {
            setThinkingStatus("Consultando documentos…");
            ensureThinkingVisible();
            setTopbarStatus("Consultando documentos…");
          } else if (phase === "answer") {
            // will be replaced by the first chunk soon
            clearTopbarStatus();
          } else if (phase === "done") {
            // If server says done but we didn't get any tokens, fall back to /chat.
            if (!streamingMsg?.hasFirstChunk) {
              emptyStreamDone = true;
              return;
            }
            clearTopbarStatus();
            finalizeStreaming({ ok: true });
          } else if (phase === "error") {
            clearTopbarStatus();
            finalizeStreaming({ ok: false, errorMessage: st?.message || "Erro no streaming." });
          }
        },
        onDelta: (t) => {
          if (!sawAnyDelta) {
            sawAnyDelta = true;
            ptLog("sse:delta:first", { len: String(t || "").length, sample: String(t || "").slice(0, 30) });
          }
          streamAppend(t);
        },
        onSources: (p) => setStreamingSources(p?.items),
        onArtifact: (a) => pushStreamingArtifact(a),
        onError: (e) => {
          ptLog("sse:error", { msg: String(e?.message || e || "") });
          clearTopbarStatus();
          finalizeStreaming({ ok: false, errorMessage: e?.message || "Erro no streaming." });
        },
        onDone: () => {
          ptLog("sse:done", { sawAnyDelta });
          clearTopbarStatus();
          finalizeStreaming({ ok: true });
        },
      });
    } else {
      // Back-compat: plain text stream (no SSE framing)
      const decoder = new TextDecoder();
      let sawAny = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        if (chunk) {
          if (!sawAny) {
            sawAny = true;
            ptLog("stream:text:first", { len: chunk.length, sample: chunk.slice(0, 30) });
          }
          streamAppend(chunk);
        }
      }
      const tail = decoder.decode();
      if (tail) streamAppend(tail);
      clearTopbarStatus();
      finalizeStreaming({ ok: true });
    }

    // If the stream ended without a terminal event, keep partial output and mark it interrupted.
    if (watchdogFallback) return;
    if (emptyStreamDone) {
      await runChatFallback("Stream finalizou sem tokens. Usando fallback…");
      return;
    }
    if (streamingMsg && streamingMsg.convId === conv.id) {
      const conv2 = conversations.find((c) => c.id === conv.id);
      const msg2 = conv2?.messages?.find((m) => m.id === assistantMsg.id);
      const hasAny = !!String(msg2?.content || "").trim();
      clearTopbarStatus();
      if (hasAny) {
        finalizeStreamingInterrupted();
        toast("Resposta interrompida (possível reload do backend).");
      } else {
        finalizeStreaming({ ok: false, errorMessage: "Stream finalizou sem resposta (possível reload do backend)." });
      }
    }
  } catch (e) {
    if (String(e?.name || "") === "AbortError") {
      if (!watchdogFallback) finalizeStreamingInterrupted();
    } else {
      // Some environments/proxies block event-stream; try immediate non-stream fallback.
      const didFallback = await runChatFallback("Falha no streaming. Tentando fallback…");
      if (didFallback) return;

      const ok = await ensureBackendBaseOnline();
      syncTopbarSubtitle();

      const extra = ok
        ? "O backend respondeu ao /health. Isso costuma ser reinício do servidor (reload) ou bloqueio do navegador (CORS)."
        : "Backend parece offline. Inicie com backend/run_dev.ps1 (porta 8000).";

      finalizeStreaming({
        ok: false,
        errorMessage: `Falha ao conectar ao backend (${BACKEND_BASE}). ${String(e?.message || "")}`.trim() + `\n${extra}`,
      });
    }
  } finally {
    if (watchdogTimer) {
      try { clearTimeout(watchdogTimer); } catch { }
      watchdogTimer = 0;
    }
    setGenerating(false);
    currentAbortController = null;
    clearTopbarStatus();
  }
}

function stopGenerating() {
  try { currentAbortController?.abort(); } catch { }
  finalizeStreamingInterrupted();
  setGenerating(false);
  currentAbortController = null;
}

// =============================
// To-bottom button
// =============================
function updateToBottomBtn() {
  if (!el.toBottomBtn) return;
  const hasThread = !!getSelectedConversation();
  el.toBottomBtn.hidden = !hasThread || pinnedToBottom || isNearBottom();
}

// =============================
// SSE parsing (POST + fetch reader)
// =============================
async function readSseStream(reader, handlers) {
  const decoder = new TextDecoder();
  let buf = "";
  let event = "message";
  let dataLines = [];
  let sawTerminal = false;

  function splitLeadingJson(text) {
    const s = String(text || "");
    let i = 0;
    while (i < s.length && /\s/.test(s[i])) i++;
    const first = s[i];
    if (first !== "{" && first !== "[") return null;

    let depth = 0;
    let inString = false;
    let esc = false;
    for (let j = i; j < s.length; j++) {
      const ch = s[j];
      if (inString) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === "\"") inString = false;
        continue;
      }
      if (ch === "\"") { inString = true; continue; }
      if (ch === "{" || ch === "[") depth++;
      if (ch === "}" || ch === "]") depth--;
      if (depth === 0) {
        const jsonText = s.slice(i, j + 1);
        const rest = s.slice(j + 1);
        return { jsonText, rest };
      }
    }
    return null;
  }

  const emit = () => {
    if (!dataLines.length) return;
    const dataRaw = dataLines.join("\n");
    dataLines = [];
    const trimmed = dataRaw.trim();
    if (trimmed === "[DONE]") {
      sawTerminal = true;
      handlers.onDone?.();
      event = "message";
      return;
    }

    // Some backends send: "{...json meta...}text..." in the same data frame.
    // Try splitting a leading JSON payload before extracting delta text.
    const split = splitLeadingJson(dataRaw);
    const jsonPrefix = split?.jsonText || null;
    const restAfterJson = split?.rest || "";

    const payload = safeJsonParse(jsonPrefix ?? dataRaw);
    const deltaText = (() => {
      const rest = (restAfterJson || "").trimStart();
      if (rest) return rest;
      if (payload == null) return dataRaw;
      if (typeof payload === "string") return payload;
      if (typeof payload?.text === "string") return payload.text;
      if (typeof payload?.t === "string") return payload.t;
      if (payload?.t != null) return String(payload.t);
      if (payload?.delta != null) return String(payload.delta);
      if (payload?.content != null) return String(payload.content);
      if (payload?.text != null) return String(payload.text);
      return dataRaw;
    })();

    if (event === "delta") {
      handlers.onDelta?.(deltaText);
    } else if (event === "status") {
      handlers.onStatus?.(payload);
      if (payload && typeof payload === "object" && String(payload.phase || "").toLowerCase() === "done") {
        sawTerminal = true;
        handlers.onDone?.(payload);
      }
      if (payload && typeof payload === "object" && String(payload.phase || "").toLowerCase() === "error") {
        sawTerminal = true;
      }
    } else if (event === "meta") {
      // metadata frame; do not render as assistant text
      handlers.onMeta?.(payload);
    } else if (event === "sources") {
      handlers.onSources?.(payload);
    } else if (event === "artifact") {
      handlers.onArtifact?.(payload);
    } else if (event === "citations") {
      handlers.onCitations?.(payload);
    } else if (event === "error") {
      sawTerminal = true;
      handlers.onError?.(payload || { message: "Erro." });
    } else if (event === "done") {
      sawTerminal = true;
      handlers.onDone?.(payload);
    } else {
      // Back-compat: servers that omit `event:` (default "message") are treated as delta text.
      // Avoid dumping JSON objects into the chat.
      if (payload && typeof payload === "object" && !Array.isArray(payload)) return;
      handlers.onDelta?.(deltaText);
    }
    event = "message";
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      let line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);

      if (line === "") {
        emit();
        continue;
      }
      if (line.startsWith("event:")) {
        event = line.slice(6).trim() || "message";
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
  }
  // flush any pending event on stream end
  emit();
  return sawTerminal;
}

// =============================
// Sidebar open/close (mobile)
// =============================
function openSidebar() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    el.sidebar.classList.add("open");
    el.overlay.hidden = false;
  }
}

function closeSidebarIfMobile() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    el.sidebar.classList.remove("open");
    el.overlay.hidden = true;
  }
}

function applySidebarCollapsed() {
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  if (isMobile) {
    el.app.classList.remove("sb-collapsed");
    return;
  }
  el.app.classList.toggle("sb-collapsed", !!sidebarCollapsed);
}

function toggleSidebar() {
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  if (isMobile) {
    if (el.sidebar.classList.contains("open")) closeSidebarIfMobile();
    else openSidebar();
  } else {
    sidebarCollapsed = !sidebarCollapsed;
    applySidebarCollapsed();
    persistState();
    renderSidebarList();
  }
}

// =============================
// Export helpers (Word/PDF via backend)
// =============================
function conversationForExport(conv) {
  if (!conv) return null;
  return {
    id: String(conv.id || ""),
    title: String(conv.title || "Conversa"),
    createdAt: conv.createdAt || null,
    updatedAt: conv.updatedAt || null,
    messages: (conv.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      ts: m.ts || null,
    })),
  };
}

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function parseFilenameFromContentDisposition(headerValue) {
  const v = String(headerValue || "");
  // attachment; filename="x.pdf"
  const m = v.match(/filename\\*?=(?:UTF-8''|\"?)([^\";]+)\"?/i);
  if (!m) return null;
  try { return decodeURIComponent(m[1]); } catch { return m[1]; }
}

async function exportConversationDocx(convId) {
  const conv = conversations.find((x) => x.id === convId);
  const payload = conversationForExport(conv);
  if (!payload) return;
  try {
    const res = await fetch(`${BACKEND_BASE}/export/conversation/docx`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation: payload }),
    });
    if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
    const blob = await res.blob();
    const name = parseFilenameFromContentDisposition(res.headers.get("content-disposition")) || "conversa.docx";
    downloadBlob(name, blob);
  } catch {
    toast("Falha ao gerar documento.");
  }
}

async function exportConversationPdf(convId) {
  const conv = conversations.find((x) => x.id === convId);
  const payload = conversationForExport(conv);
  if (!payload) return;
  try {
    const res = await fetch(`${BACKEND_BASE}/export/conversation/pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation: payload }),
    });
    if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
    const blob = await res.blob();
    const name = parseFilenameFromContentDisposition(res.headers.get("content-disposition")) || "conversa.pdf";
    downloadBlob(name, blob);
  } catch {
    toast("Falha ao gerar documento.");
  }
}

// =============================
// Downloads panel (library)
// =============================
function setDownloadsUse(on) {
  useDownloadsInChat = !!on;
  try { localStorage.setItem(DOWNLOADS_USE_KEY, useDownloadsInChat ? "1" : "0"); } catch { }
  if (el.downloadsUseToggle) el.downloadsUseToggle.checked = useDownloadsInChat;
}

function openDownloadsPanel() {
  if (!el.downloadsPanel) return;
  el.downloadsPanel.hidden = false;
  if (el.downloadsUseToggle) el.downloadsUseToggle.checked = useDownloadsInChat;
  refreshDownloadsList();
  try { el.downloadsSearchInput?.focus?.(); } catch { }
}

function closeDownloadsPanel() {
  if (!el.downloadsPanel) return;
  el.downloadsPanel.hidden = true;
}

function humanSize(n) {
  const b = Number(n || 0);
  if (!isFinite(b) || b <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function renderDownloadsList(files) {
  if (!el.downloadsList) return;
  el.downloadsList.replaceChildren();
  const frag = document.createDocumentFragment();

  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "menu-title";
    empty.textContent = "Nenhum arquivo enviado.";
    frag.appendChild(empty);
    el.downloadsList.appendChild(frag);
    return;
  }

  for (const f of files) {
    const row = document.createElement("div");
    row.className = "dl-item";
    row.dataset.id = f.id;

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = f.filename || "arquivo";
    const meta = document.createElement("div");
    meta.className = "meta";
    const createdAt = f.created_at || f.createdAt || "";
    const type = f.mime || (f.ext ? "." + f.ext : "");
    meta.textContent = `${type || ""} • ${humanSize(f.size)}${createdAt ? " • " + String(createdAt).replace("T", " ") : ""}`;
    left.appendChild(name);
    left.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "actions";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "mini";
    del.title = "Excluir";
    del.setAttribute("aria-label", "Excluir");
    del.dataset.action = "delete";
    del.innerHTML = `<span class="ms" aria-hidden="true">delete</span>`;
    actions.appendChild(del);

    row.appendChild(left);
    row.appendChild(actions);
    frag.appendChild(row);
  }

  el.downloadsList.appendChild(frag);
}

function renderDownloadsResults(items) {
  if (!el.downloadsResults) return;
  el.downloadsResults.replaceChildren();
  const frag = document.createDocumentFragment();

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "menu-title";
    empty.textContent = "Sem resultados.";
    frag.appendChild(empty);
    el.downloadsResults.appendChild(frag);
    return;
  }

  for (const it of items) {
    const card = document.createElement("div");
    card.className = "dl-result";
    card.dataset.filename = it.filename || "";
    card.dataset.snippet = it.snippet || "";

    const top = document.createElement("div");
    top.className = "top";
    const file = document.createElement("div");
    file.className = "file";
    file.textContent = it.filename || "arquivo";
    const score = document.createElement("div");
    score.className = "score";
    score.textContent = typeof it.score === "number" ? `score ${it.score.toFixed(3)}` : "";
    top.appendChild(file);
    top.appendChild(score);
    const snip = document.createElement("div");
    snip.className = "snippet";
    snip.textContent = it.snippet || "";
    card.appendChild(top);
    card.appendChild(snip);

    const actions = document.createElement("div");
    actions.className = "dl-actions";
    const use = document.createElement("button");
    use.type = "button";
    use.className = "btn subtle dl-use";
    use.dataset.action = "use";
    use.innerHTML = `<span class="ms" aria-hidden="true">chat</span><span>Usar no chat</span>`;
    actions.appendChild(use);
    card.appendChild(actions);

    frag.appendChild(card);
  }

  el.downloadsResults.appendChild(frag);
}

async function refreshDownloadsList({ silent = false } = {}) {
  try {
    const res = await fetch(`${BACKEND_BASE}/downloads`, { method: "GET" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const files = Array.isArray(data?.files) ? data.files : [];
    downloadsFileCount = files.length;
    renderDownloadsList(files);
  } catch {
    downloadsFileCount = 0;
    renderDownloadsList([]);
    if (!silent) toast("Falha ao carregar downloads.");
  }
}

async function uploadDownloadFiles(files) {
  const list = Array.isArray(files) ? files : (files ? [files] : []);
  if (!list.length) return;
  const fd = new FormData();
  for (const f of list) fd.append("files", f, f.name);
  try {
    const res = await fetch(`${BACKEND_BASE}/downloads/upload`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
    toast(list.length > 1 ? "Documentos enviados." : "Documento enviado.");
    await refreshDownloadsList();
  } catch {
    toast("Falha no upload.");
  }
}

async function runDownloadsSearch() {
  const q = (el.downloadsSearchInput?.value || "").trim();
  if (!q) {
    renderDownloadsResults([]);
    return;
  }
  try {
    const res = await fetch(`${BACKEND_BASE}/downloads/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, top_k: DOWNLOADS_TOP_K }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderDownloadsResults(Array.isArray(data?.items) ? data.items : []);
  } catch {
    toast("Falha na busca.");
  }
}

// =============================
// Events
// =============================
function bindEvents() {
  // If this runs, we consider the UI "bound". Used by fallback handlers below.
  window.__paytech.bindOk = false;

  // Scroll pinning
  el.chat.addEventListener("scroll", () => {
    pinnedToBottom = isNearBottom();
    updateToBottomBtn();
  }, { passive: true });

  // To-bottom
  el.toBottomBtn?.addEventListener?.("click", () => {
    pinnedToBottom = true;
    scrollToBottom(true);
    updateToBottomBtn();
  });

  // Sidebar open/close (mobile)
  el.sidebarToggle.addEventListener("click", () => toggleSidebar());
  el.sidebarLogo?.addEventListener?.("click", () => toggleSidebar());
  el.overlay.addEventListener("click", () => closeSidebarIfMobile());

  // Search
  el.searchInput.addEventListener("input", () => {
    searchQuery = el.searchInput.value || "";
    renderSidebarList();
  });
  el.searchBtn?.addEventListener?.("click", () => {
    // In compact mode, expand then focus the input.
    if (sidebarCollapsed) toggleSidebar();
    setTimeout(() => {
      try { el.searchInput?.focus?.(); } catch { }
    }, 0);
  });

  // New chat
  el.newChat.addEventListener("click", () => {
    pendingFiles = [];
    renderFileChips();
    goHome();
    closeSidebarIfMobile();
  });

  // Downloads panel
  el.downloadBtn?.addEventListener?.("click", () => openDownloadsPanel());
  el.downloadsClose?.addEventListener?.("click", () => closeDownloadsPanel());
  el.downloadsPanel?.addEventListener?.("click", (e) => {
    if (e.target === el.downloadsPanel) closeDownloadsPanel();
  });
  el.downloadsUploadBtn?.addEventListener?.("click", () => el.downloadsFileInput?.click?.());
  el.downloadsFileInput?.addEventListener?.("change", () => {
    const files = Array.from(el.downloadsFileInput.files || []);
    if (files.length) uploadDownloadFiles(files);
    el.downloadsFileInput.value = "";
  });
  el.downloadsUseToggle?.addEventListener?.("change", () => setDownloadsUse(!!el.downloadsUseToggle.checked));
  el.downloadsSearchBtn?.addEventListener?.("click", runDownloadsSearch);
  el.downloadsSearchInput?.addEventListener?.("keydown", (e) => {
    if (e.key === "Enter") runDownloadsSearch();
  });
  el.downloadsList?.addEventListener?.("click", async (e) => {
    const btn = e.target?.closest?.("button");
    const item = e.target?.closest?.(".dl-item");
    if (!btn || !item) return;
    if (btn.dataset.action !== "delete") return;
    const id = item.dataset.id;
    if (!id) return;
    if (!confirm("Excluir este arquivo?")) return;
    try {
      const res = await fetch(`${BACKEND_BASE}/downloads/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast("Excluído.");
      await refreshDownloadsList();
    } catch {
      toast("Falha ao excluir.");
    }
  });

  el.downloadsResults?.addEventListener?.("click", (e) => {
    const btn = e.target?.closest?.("button");
    const card = e.target?.closest?.(".dl-result");
    if (!btn || !card) return;
    if (btn.dataset.action !== "use") return;
    const filename = card.dataset.filename || "arquivo";
    const snippet = card.dataset.snippet || "";
    const insert = `Contexto do documento \"${filename}\":\n${snippet}\n\n`;
    const target = hasActiveConversation() ? el.input : el.emptyInput;
    if (target) target.value = insert + (target.value || "");
    if (hasActiveConversation()) autoResize();
    else autoResizeEmpty();
    try { target?.focus?.(); } catch { }
    toast("Trecho inserido no chat.");
  });

  // Conversation list click + kebab
  el.conversationList.addEventListener("click", (e) => {
    const t = e.target;
    const kebab = t?.closest?.("button.kebab");
    if (kebab) {
      e.preventDefault();
      e.stopPropagation();
      const id = kebab.dataset.id;
      openItemMenu(kebab, id);
      return;
    }
    const item = t?.closest?.(".conv-item");
    if (item?.dataset?.id) selectConversation(item.dataset.id);
  });

  el.conversationList.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const item = e.target?.closest?.(".conv-item");
    if (item?.dataset?.id) selectConversation(item.dataset.id);
  });

  // Composer: attach
  el.attachBtn.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    const files = Array.from(el.fileInput.files || []);
    if (!files.length) return;
    pendingFiles = pendingFiles.concat(files);
    renderFileChips();
    el.fileInput.value = "";
    updateComposerControls();
  });

  // Composer: input
  el.input.addEventListener("input", autoResize);
  el.input.addEventListener("input", updateComposerControls);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  el.sendBtn.addEventListener("click", () => {
    ptLog("ui:sendBtn:click", { isGenerating, canSend: canSendNow() });
    if (isGenerating) stopGenerating();
    else sendMessage();
  });

  // Empty state: central input
  el.emptyInput?.addEventListener?.("input", autoResizeEmpty);
  el.emptyInput?.addEventListener?.("input", updateComposerControls);
  el.emptyInput?.addEventListener?.("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  el.emptySendBtn?.addEventListener?.("click", sendMessage);

  // If focus isn't inside an editable element, typing should focus the composer.
  // Fixes "I typed but it says empty" when focus is on the page/container.
  document.addEventListener("keydown", (e) => {
    try {
      if (e.defaultPrevented) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // printable characters only (ignore navigation keys)
      if (e.key !== "Enter" && String(e.key || "").length !== 1) return;
      const ae = document.activeElement;
      const tag = String(ae?.tagName || "").toLowerCase();
      const isEditable = !!ae?.isContentEditable || tag === "textarea" || tag === "input";
      if (isEditable) return;
      focusComposer();
    } catch { }
  }, true);

  // Clicking the composer background should focus the textarea.
  try {
    const footer = document.querySelector("footer.composer");
    footer?.addEventListener?.(
      "pointerdown",
      (e) => {
        const t = e.target;
        if (t?.closest?.("textarea,button,a,input,label")) return;
        focusComposer();
      },
      { capture: true }
    );
  } catch { }

  // Empty suggestions
  el.emptySuggestions?.addEventListener?.("click", (e) => {
    const btn = e.target?.closest?.("button.pill");
    if (!btn) return;
    const text = (btn.dataset.suggest || btn.textContent || "").trim();
    if (!text) return;
    if (!el.emptyInput) return;
    el.emptyInput.value = text;
    autoResizeEmpty();
    updateComposerControls();
    try { el.emptyInput.focus(); } catch { }
  });

  // Theme toggle (header)
  el.themeToggle?.addEventListener?.("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(current === "dark" ? "light" : "dark", { persist: true });
    toast("Tema atualizado.");
  });

  // Response mode menu
  el.responseModeBtn?.addEventListener?.("click", (e) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    openModeMenu(el.responseModeBtn);
  });

  el.modeMenu?.addEventListener?.("click", (e) => {
    const btn = e.target?.closest?.("button.menu-item");
    if (!btn) return;
    const action = String(btn.dataset.action || "");
    if (action === "set-mode") {
      responseMode = normalizeMode(btn.dataset.mode);
      persistState();
      updateResponseModeButton();
      closeMenus({ restoreFocus: true });
      toast(`Modo: ${labelForMode(responseMode)}`);
      return;
    }
    if (action === "toggle-streaming") {
      streamingEnabled = !streamingEnabled;
      persistState();
      closeMenus({ restoreFocus: true });
      toast(`Streaming: ${streamingEnabled ? "ligado" : "desligado"} (${streamingEnabled ? "/chat/stream" : "/chat"})`);
      return;
    }
  });

  // Menu click handling
  document.addEventListener("click", (e) => {
    const target = e.target;
    const inMenu = target?.closest?.(".menu");
    const isMenuBtn = target?.closest?.(".kebab");
    if (!inMenu && !isMenuBtn) closeMenus({ restoreFocus: false });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenus({ restoreFocus: true });
      closeSidebarIfMobile();
      closeDownloadsPanel();
    }
  });

  el.itemMenu.addEventListener("click", (e) => {
    const btn = e.target?.closest?.("button.menu-item");
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id;
    if (action === "download") {
      // anchor to the kebab button (stable DOM element)
      openExportMenu(lastMenuAnchorEl || btn, id);
      return;
    }
    closeMenus({ restoreFocus: true });
    if (action === "rename") renameConversation(id);
    else if (action === "delete") deleteConversation(id);
  });

  el.exportMenu?.addEventListener?.("click", async (e) => {
    const btn = e.target?.closest?.("button.menu-item");
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id;
    closeMenus({ restoreFocus: true });
    if (action === "export-docx") await exportConversationDocx(id);
    else if (action === "export-pdf") await exportConversationPdf(id);
  });

  window.addEventListener("resize", () => {
    // close menus on layout changes
    closeMenus();
    if (!window.matchMedia("(max-width: 900px)").matches) {
      el.overlay.hidden = true;
      el.sidebar.classList.remove("open");
    }
    applySidebarCollapsed();
    renderSidebarList();
  });

  window.__paytech.bindOk = true;
}

// =============================
// Boot
// =============================
function boot() {
  initTheme();
  loadState();
  // Persist empty selection (so refresh doesn't reopen a thread)
  persistState();
  syncBrowserTitle();
  updateResponseModeButton();

  applySidebarCollapsed();
  renderLayout();
  renderHeader();
  renderSidebarList();
  renderThread();
  renderFileChips();
  autoResize();
  autoResizeEmpty();
  updateComposerControls();
  bindEvents();

  pingBackendHealth(900).then((ok) => {
    if (ok) {
      backendOnline = true;
      syncTopbarSubtitle();
      // Keep RAG toggle effective across reloads (even if the user doesn't reopen the Downloads panel).
      if (useDownloadsInChat) refreshDownloadsList({ silent: true });
      return;
    }
    ensureBackendBaseOnline().then((finalOk) => {
      syncTopbarSubtitle();
      if (finalOk && useDownloadsInChat) refreshDownloadsList({ silent: true });
      if (!finalOk) toast(`Backend offline (${BACKEND_BASE})`, { ms: 3500 });
    });
  });

  try { el.emptyInput?.focus?.(); } catch { }
}

// Fallback send handlers: if boot/bindEvents crashed mid-way, keep chat usable.
// We only act when `bindEvents()` didn't complete successfully.
(() => {
  if (window.__paytechFallbackSendHandlersInstalled) return;
  window.__paytechFallbackSendHandlersInstalled = true;

  document.addEventListener("click", (e) => {
    if (window.__paytech?.bindOk) return;
    const btn = e.target?.closest?.("#sendBtn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    try { sendMessage(); } catch (err) { _captureEarlyError("sendMessage(click)", err); }
  }, true);

  document.addEventListener("keydown", (e) => {
    if (window.__paytech?.bindOk) return;
    if (e.key !== "Enter" || e.shiftKey) return;
    const ta = e.target?.closest?.("#input,#emptyInput");
    if (!ta) return;
    e.preventDefault();
    e.stopPropagation();
    try { sendMessage(); } catch (err) { _captureEarlyError("sendMessage(keydown)", err); }
  }, true);
})();

document.addEventListener("DOMContentLoaded", () => {
  try {
    boot();
    // Flush early errors (if any) into the UI toast so it is visible without DevTools.
    if (_earlyErrors.length) {
      const last = _earlyErrors[_earlyErrors.length - 1];
      toast(`Erro no frontend: ${last.msg}`, { ms: 6500 });
    }
  } catch (err) {
    _captureEarlyError("boot", err);
    try { toast(`Falha ao iniciar UI: ${String(err?.message || err)}`.trim(), { ms: 7000 }); } catch { }
  }
});
