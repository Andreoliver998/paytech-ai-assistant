/* PayTech AI — ChatGPT-like UI (vanilla HTML/CSS/JS)
   - Sidebar: search, new chat, conversation list w/ kebab menu
   - Main: header global menu, thread, composer (attach/send/stop)
   - Storage: localStorage (conversations + selectedConversationId + theme)
   - Streaming: SSE via fetch+reader (POST /chat/stream), incremental update of a single TextNode
*/

// =============================
// Config
// =============================
const PAYTECH_BUILD = "2026-02-10_025";
// Prefer explicit IPv4 loopback: on some Windows setups `localhost` resolves to IPv6 (::1)
// while uvicorn is bound only to 127.0.0.1, which can break streaming fetches.
const BACKEND_DEFAULT = "http://127.0.0.1:8000";
const BACKEND_BASE_KEY = "paytech.backendBase";
const DEBUG_STREAM_MODE = (() => {
  try {
    const url = new URL(window.location.href);
    return (url.searchParams.get("debugstream") || "").trim() === "1";
  } catch {
    return false;
  }
})();

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
    // eslint-disable-next-line no-console
    console.error("[paytech]", kind, err);

    const now = Date.now();
    const shouldToast =
      msg && (msg !== _lastErrToastMsg || now - _lastErrToastAt > 2500);

    // NOTE: `toast()` is a function declaration (hoisted), so it's safe to call it here.
    if (shouldToast && typeof toast === "function") {
      _lastErrToastAt = now;
      _lastErrToastMsg = msg;
      try {
        toast(`Erro no frontend: ${msg}`.slice(0, 220), { ms: 6500 });
      } catch {}
    }
  } catch {}
}
window.addEventListener(
  "error",
  (e) => _captureEarlyError("error", e?.error || e?.message || e),
  { capture: true }
);
window.addEventListener(
  "unhandledrejection",
  (e) => _captureEarlyError("unhandledrejection", e?.reason || e),
  { capture: true }
);

function resolveBackendBase() {
  try {
    const url = new URL(window.location.href);
    const qp = (url.searchParams.get("api") || "").trim();
    if (qp) {
      localStorage.setItem(BACKEND_BASE_KEY, qp);
      return qp;
    }
    const saved = (localStorage.getItem(BACKEND_BASE_KEY) || "").trim();
    // Windows gotcha: `localhost` often resolves to IPv6 (::1) while uvicorn is frequently
    // bound only to 127.0.0.1. Non-streaming requests may appear to work intermittently,
    // but streaming is much more sensitive. Normalize the common default to IPv4.
    if (saved === "http://localhost:8000") {
      const normalized = "http://127.0.0.1:8000";
      try { localStorage.setItem(BACKEND_BASE_KEY, normalized); } catch {}
      return normalized;
    }
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
let sendInFlight = false;
let sidebarCollapsed = false;
let useDownloadsInChat = false;
let responseMode = "tecnico"; // tecnico | resumido | didatico | estrategico
let streamingEnabled = true; // true => POST /chat/stream (SSE), false => POST /chat (JSON)
let userId = "";
let backendOnline = null; // boolean | null
let topbarStatusOverride = "";
let downloadsFileCount = null; // number | null

window.__paytech = window.__paytech || {};
// DevTools ergonomics: create global bindings (in addition to `window.__paytech`).
// eslint-disable-next-line no-var
var __paytech = window.__paytech;
// Common typo during debugging.
// eslint-disable-next-line no-var
var _paytech = window.__paytech;
// Also expose as a window property (so DevTools can resolve it consistently).
try { window._paytech = window.__paytech; } catch {}
window.__paytech.build = PAYTECH_BUILD;
try {
  // eslint-disable-next-line no-console
  console.info("[paytech] build", PAYTECH_BUILD);
} catch {}

// Global event bus (must stay the same reference even if the script is loaded twice).
const _ptEvents = Array.isArray(window.__paytech.events)
  ? window.__paytech.events
  : [];
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

    _ptPersistCounter++;
    if (
      _ptPersistCounter % _ptPersistEvery === 0 ||
      String(ev.name).startsWith("fetch:") ||
      String(ev.name).startsWith("sse:")
    ) {
      try {
        localStorage.setItem(
          _PT_DEBUG_KEY,
          JSON.stringify({
            build: PAYTECH_BUILD,
            loadCount: window.__paytech.loadCount,
            at: Date.now(),
            events: _ptEvents.slice(-200),
          })
        );
      } catch {}
    }
  } catch {}
}
window.__paytech.log = ptLog;
ptLog("init", { build: PAYTECH_BUILD, loadCount: window.__paytech.loadCount });

window.__paytech.state = () => {
  const sel = (() => {
    try {
      return getSelectedConversation();
    } catch {
      return null;
    }
  })();
  return {
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
  };
};
window.__paytech.eventsLast = (n = 20) => {
  const k = Math.max(1, Number(n || 20));
  return _ptEvents.slice(-k);
};
window.__paytech.eventsDump = (n = 50) => JSON.stringify(window.__paytech.eventsLast(n), null, 2);
window.__paytech.eventsDumpPersisted = () => {
  try {
    return localStorage.getItem(_PT_DEBUG_KEY) || "";
  } catch {
    return "";
  }
};
window.__paytech.eventsClear = () => {
  try {
    _ptEvents.splice(0, _ptEvents.length);
  } catch {}
  try {
    localStorage.removeItem(_PT_DEBUG_KEY);
  } catch {}
  ptLog("init", { build: PAYTECH_BUILD, loadCount: window.__paytech.loadCount, cleared: true });
};
window.__paytech.peekComposer = () => {
  const main = String(el.input?.value || "");
  const empty = String(el.emptyInput?.value || "");
  const hasActive = (() => {
    try {
      return hasActiveConversation();
    } catch {
      return false;
    }
  })();
  const chosen = hasActive ? main : empty.trim() ? empty : main;
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
        try {
          elm.removeAttribute("data-pt-flash");
        } catch {}
      }, 180);
    } catch {}
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

  document.addEventListener(
    "click",
    (e) => {
      if (window.__paytech?.bindOk) return;

      // tenta inicializar
      try {
        window.__paytech?.bootOnce?.();
      } catch {}

      // ✅ RE-CHECA: se boot resolveu o bind, NÃO dispara fallback
      if (window.__paytech?.bindOk) return;

      const btn = e.target?.closest?.("#sendBtn");
      if (!btn) return;

      e.preventDefault();
      e.stopPropagation();
      try {
        sendMessage();
      } catch (err) {
        _captureEarlyError("sendMessage(click)", err);
      }
    },
    true
  );


  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      const ta = e.target?.closest?.("#input,#emptyInput");
      if (!ta) return;
      ptLog("cap:enter", {
        id: ta.id,
        isGenerating,
        hasActiveConversation: hasActiveConversation(),
        len: String(ta.value || "").length,
      });
    },
    true
  );
})();

window.__paytech.selftest = async () => {
  const base = String(BACKEND_BASE || "").trim();
  const out = {
    base,
    health: { ok: false, status: null },
    chat: { ok: false, status: null, hasReply: false, error: null },
    stream: {
      ok: false,
      status: null,
      contentType: "",
      sawDelta: false,
      sawDone: false,
      bytes: 0,
      aborted: false,
      error: null,
    },
    error: null,
  };
  try {
    try {
      const r = await fetch(`${base}/health`, { method: "GET" });
      out.health.status = r.status;
      out.health.ok = r.ok;
    } catch (e) {
      out.health.ok = false;
      out.error = `health: ${String(e?.message || e)}`;
    }

    try {
      const acChat = new AbortController();
      const chatTimer = window.setTimeout(() => {
        try {
          acChat.abort();
        } catch {}
      }, 6500);
      const r = await fetch(`${base}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [{ role: "user", content: "ping" }] }),
        signal: acChat.signal,
      });
      out.chat.status = r.status;
      out.chat.ok = r.ok;
      const data = await r.json().catch(() => null);
      out.chat.hasReply = !!String(data?.reply || "").trim();
      try {
        clearTimeout(chatTimer);
      } catch {}
    } catch (e) {
      if (String(e?.name || "") === "AbortError") {
        out.chat.ok = false;
        out.chat.status = out.chat.status ?? "timeout";
        out.chat.error = "timeout (6.5s)";
      } else {
        out.chat.ok = false;
        out.chat.error = String(e?.message || e);
      }
    }

    const ac = new AbortController();
    const timer = window.setTimeout(() => {
      try {
        ac.abort();
      } catch {}
    }, 10000);

    try {
      const r = await fetch(`${base}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
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
          if (buf.includes('"phase": "done"') || buf.includes("event: done")) out.stream.sawDone = true;
          if (out.stream.sawDelta && out.stream.bytes > 200) break;
        }
      }
    } catch (e) {
      if (String(e?.name || "") === "AbortError") {
        out.stream.aborted = true;
        out.stream.error = "timeout (10s)";
      } else {
        out.stream.error = String(e?.message || e);
      }
    } finally {
      try {
        clearTimeout(timer);
      } catch {}
    }

    // Summary error: only fail the whole selftest if health or stream fails.
    if (!out.health.ok && !out.error) out.error = "health failed";
    if (!out.stream.ok && !out.error) out.error = out.stream.error ? `stream: ${out.stream.error}` : "stream failed";
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
let streamingMsg = null; // { convId, msgId, contentEl, textNode, cursorEl, hasFirstChunk, thinkingEl, thinkingTimer, sources, artifacts }
let streamBuffer = "";
let streamFlushRaf = 0;

function flushStreamNow() {
  if (!streamingMsg) return;
  ensureStreamingBind();
  if (streamFlushRaf) {
    try {
      cancelAnimationFrame(streamFlushRaf);
    } catch {}
    streamFlushRaf = 0;
  }
  if (!streamBuffer) return;

  try {
    streamingMsg.textNode?.appendData?.(streamBuffer);
  } catch {}

  try {
    const conv = conversations.find((c) => c.id === streamingMsg.convId);
    const msg = conv?.messages?.find((m) => m.id === streamingMsg.msgId);
    if (msg) msg.content = String(msg.content || "") + streamBuffer;
  } catch {}

  streamBuffer = "";
  scrollToBottomIfNeeded();
}

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
  } catch {}
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
  const title = c ? `${String(c.title || "Conversa").trim() || "Conversa"} – ${APP_TITLE}` : APP_TITLE;
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
  if (base && base === _healthLastBase && _healthLastOk !== null && now - _healthLastAt < HEALTH_COOLDOWN_MS) {
    return _healthLastOk;
  }
  if (_healthInFlight && base && base === _healthLastBase) return await _healthInFlight;

  const ac = new AbortController();
  const timer = window.setTimeout(() => {
    try {
      ac.abort();
    } catch {}
  }, timeoutMs);

  _healthLastBase = base;
  _healthInFlight = (async () => {
    try {
      const res = await fetch(`${BACKEND_BASE}/health`, { method: "GET", signal: ac.signal });
      return !!res.ok;
    } catch {
      return false;
    } finally {
      try {
        clearTimeout(timer);
      } catch {}
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
  const raw = String(BACKEND_BASE || "").trim();
  const cur = raw === "http://localhost:8000" ? "http://127.0.0.1:8000" : raw;
  const ip = "http://127.0.0.1:8000";

  if (cur) bases.push(cur);
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
      try {
        localStorage.setItem(BACKEND_BASE_KEY, base);
      } catch {}
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

  if (isGenerating) {
    el.currentSub.textContent = "Gerando…";
    return;
  }

  el.currentSub.textContent = streamingEnabled ? "Pronto" : "Pronto — Streaming desligado";
}

function safeJsonParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function toast(msg, { ms = 2200 } = {}) {
  if (!el.toast) return;
  el.toast.textContent = String(msg || "");
  el.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.toast.hidden = true), ms);
}
// Expose for diagnostics (DevTools) and early crash hooks.
try {
  window.toast = toast;
} catch {}

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
    return t === "dark" || t === "light" ? t : null;
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
  const t = theme === "dark" || theme === "light" ? theme : "light";
  document.documentElement.setAttribute("data-theme", t);
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {}
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
  } catch {}
}

// =============================
// Storage
// =============================
function loadState() {
  const raw = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  })();
  const parsed = raw ? safeJsonParse(raw) : null;
  const list = Array.isArray(parsed) ? parsed : [];

  conversations = list.map((c) => normalizeConversation(c)).filter(Boolean);

  conversations.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));

  selectedConversationId = null;
  try {
    const saved = String(localStorage.getItem(STORAGE_SELECTED) || "").trim();
    if (saved && conversations.some((c) => c.id === saved)) {
      selectedConversationId = saved;
    }
  } catch {}
  if (!selectedConversationId && conversations.length === 1) {
    selectedConversationId = conversations[0].id;
  }

  try {
    const v = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (v == null) {
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
    else streamingEnabled = v === "1" || v === "true" || v === "on" || v === "yes";
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
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  } catch {}
  try {
    localStorage.setItem(STORAGE_SELECTED, selectedConversationId || "");
  } catch {}
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? "1" : "0");
  } catch {}
  try {
    localStorage.setItem(RESPONSE_MODE_KEY, normalizeMode(responseMode));
  } catch {}
  try {
    localStorage.setItem(STREAMING_ENABLED_KEY, streamingEnabled ? "1" : "0");
  } catch {}
  try {
    if (userId) localStorage.setItem(USER_ID_KEY, userId);
  } catch {}
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
  const role = m.role === "user" || m.role === "assistant" || m.role === "system" ? m.role : "assistant";
  const content = String(m.content || "");
  const ts = String(m.ts || nowISO());
  const attachments = Array.isArray(m.attachments)
    ? m.attachments.map((a) => ({
        name: String(a?.name || ""),
        type: String(a?.type || ""),
        size: Number(a?.size || 0),
      }))
    : [];
  const interrupted = !!m.interrupted;
  const sources = [];
  const artifacts = Array.isArray(m.artifacts) ? m.artifacts : [];
  const sourcesRequested = !!m.sourcesRequested;
  return { id, role, content, ts, attachments, interrupted, sources, artifacts, sourcesRequested };
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
  } catch {}
  lastMenuAnchorEl = null;
  if (restoreFocus && anchor && document.contains(anchor)) {
    try {
      anchor.focus();
    } catch {}
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
    case "resumido":
      return "Resumido";
    case "didatico":
      return "Didático";
    case "estrategico":
      return "Estratégico";
    default:
      return "Técnico";
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
  if (!el.itemMenu) return;
  el.itemMenu.innerHTML = `
    <button class="menu-item" data-action="rename" data-id="${convId}" role="menuitem">Renomear</button>
    <button class="menu-item" data-action="delete" data-id="${convId}" role="menuitem">Excluir</button>
    <button class="menu-item" data-action="download" data-id="${convId}" role="menuitem">Baixar conversa</button>
  `;
}

function openItemMenu(anchorEl, convId) {
  if (!el.itemMenu) return;
  closeMenus();
  renderItemMenu(convId);
  el.itemMenu.hidden = false;
  menuPosition(el.itemMenu, anchorEl);
  lastMenuAnchorEl = anchorEl;
  try {
    anchorEl?.setAttribute?.("aria-expanded", "true");
  } catch {}
  el.itemMenu.querySelector("button.menu-item")?.focus?.();
}

function renderExportMenu(convId) {
  if (!el.exportMenu) return;
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
  if (el.itemMenu) {
    el.itemMenu.hidden = true;
    el.itemMenu.innerHTML = "";
  }
  renderExportMenu(convId);
  if (!el.exportMenu) return;
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
  if (el.currentTitle) el.currentTitle.textContent = active ? (c?.title || "Conversa") : "";
  syncTopbarSubtitle();
  syncBrowserTitle();
}

function renderLayout() {
  const active = !!getSelectedConversation();
  if (el.main) el.main.classList.toggle("is-empty", !active);
  if (el.emptyState) el.emptyState.hidden = active;
  if (el.toBottomBtn) el.toBottomBtn.hidden = true;
  if (!active) {
    try {
      el.thread?.replaceChildren?.();
    } catch {}
  }
}

function renderSidebarList() {
  if (!el.conversationList) return;
  const q = (searchQuery || "").trim().toLowerCase();
  const list = q ? conversations.filter((c) => (c.title || "").toLowerCase().includes(q)) : conversations;

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
  el.thread?.replaceChildren?.();
  if (streamingMsg) {
    streamingMsg.contentEl = null;
    streamingMsg.textNode = null;
    streamingMsg.cursorEl = null;
    streamingMsg.thinkingEl = null;
  }
  streamBuffer = "";
  if (streamFlushRaf) {
    cancelAnimationFrame(streamFlushRaf);
    streamFlushRaf = 0;
  }
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

    const isSame =
      streamingMsg.contentEl === contentEl &&
      streamingMsg.textNode &&
      streamingMsg.textNode.isConnected;

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
    if (existingText.trim()) streamingMsg.hasFirstChunk = true;

    if (!streamingMsg.hasFirstChunk) {
      try {
        const t = document.createElement("span");
        t.className = "thinking";
        t.textContent = streamingMsg.thinkingText || "Analisando…";
        streamingMsg.thinkingEl = t;
        contentEl.insertBefore(t, cursor);
      } catch {}
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
  if (!c || !el.thread) return;
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
    // UX rule: never render sources in chat messages.
    if (Array.isArray(msg.artifacts) && msg.artifacts.length) {
      renderArtifacts(content, msg.artifacts);
    }
  } else {
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
  const safe = window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
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
  el.thread?.appendChild?.(node);
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
  if (!el.fileChips) return;
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
  try {
    el.emptyInput?.focus?.();
  } catch {}
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

  conv.titleAutoDone = true;
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
  } catch {}
}

function readComposerText() {
  const main = String(el.input?.value || "");
  const empty = String(el.emptyInput?.value || "");

  // Defensive: users sometimes type in the "other" textarea (empty-state vs main composer)
  // depending on focus/scroll/layout. Accept whichever has content to avoid "send does nothing".
  if (main.trim()) return main;
  if (empty.trim()) return empty;

  return hasActiveConversation() ? main : empty;
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
  const main = String(el.input?.value || "");
  const empty = String(el.emptyInput?.value || "");
  const target = main.trim() ? el.input : (empty.trim() ? el.emptyInput : (hasActiveConversation() ? el.input : el.emptyInput));
  try {
    target?.focus?.();
  } catch {}
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
  try {
    el.chat?.setAttribute?.("aria-busy", isGenerating ? "true" : "false");
  } catch {}
  renderHeader();
}

function canSendNow() {
  const text = readComposerText().trim();
  const hasPayload = !!text || pendingFiles.length > 0;
  return hasPayload && !isGenerating;
}

function updateComposerControls() {
  const text = readComposerText().trim();
  const hasPayload = !!text || pendingFiles.length > 0;

  if (el.sendBtn) {
    // When generating, the send button acts as "Stop" and must stay enabled.
    const disabled = !isGenerating && !hasPayload;
    el.sendBtn.disabled = disabled;
    el.sendBtn.setAttribute("aria-disabled", String(disabled));
  }

  if (el.emptySendBtn) {
    const disabled = !isGenerating && !hasPayload;
    el.emptySendBtn.disabled = disabled;
    el.emptySendBtn.setAttribute("aria-disabled", String(disabled));
  }
}

function buildBackendMessages(conv) {
  return (conv.messages || [])
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .map((m) => ({ role: m.role, content: m.content }))
    .filter((m) => String(m?.content || "").trim().length > 0);
}

function effectiveUseDownloads() {
  if (!useDownloadsInChat) return false;
  if (downloadsFileCount == null) return true;
  if (downloadsFileCount === 0) return false;
  return true;
}

function startStreamingIntoMessage({ convId, msgId, contentEl, sourcesRequested = false }) {
  if (!contentEl) {
    try {
      const article = el.thread?.querySelector?.(`article.msg[data-msg-id="${msgId}"]`);
      contentEl = article?.querySelector?.(".content") || null;
    } catch {}
  }
  if (!contentEl) return false;

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
    sources: null,
    artifacts: null,
    sourcesRequested: !!sourcesRequested,
    thinkingText: "Analisando…",
    thinkingTimer: 0,
  };
  // Make the assistant bubble visibly "alive" immediately (no silent blank state).
  ensureThinkingVisible();
  streamBuffer = "";
  if (streamFlushRaf) {
    cancelAnimationFrame(streamFlushRaf);
    streamFlushRaf = 0;
  }
  return true;
}

function setThinkingStatus(nextText) {
  if (!streamingMsg) return;
  ensureStreamingBind();
  const t = String(nextText || "").trim();
  if (!t) return;
  streamingMsg.thinkingText = t;
  try {
    if (streamingMsg.thinkingEl) streamingMsg.thinkingEl.textContent = t;
  } catch {}
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
  } catch {}
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
    try {
      streamingMsg.thinkingEl?.remove?.();
    } catch {}
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
    streamingMsg.textNode?.appendData?.(streamBuffer);

    const conv = conversations.find((c) => c.id === streamingMsg.convId);
    const msg = conv?.messages?.find((m) => m.id === streamingMsg.msgId);
    if (msg) msg.content = String(msg.content || "") + streamBuffer;

    streamBuffer = "";
    scrollToBottomIfNeeded();
  });
}

/**
 * ✅ FIX CRÍTICO:
 * Antes você chamava finalizeStreaming({ ok: true, sources }) mas a função não aceitava sources/artifacts.
 * Agora aceita `sources` e `artifacts` (opcionais), e também usa o que estiver em `streamingMsg` quando não passar.
 */
function finalizeStreaming({ ok, errorMessage, sources, artifacts } = { ok: true }) {
  if (!streamingMsg) return;

  // Critical: deltas are buffered + flushed via rAF. If many SSE events arrive in one JS tick,
  // we can finalize before the rAF runs, making the UI look empty even though Network shows data.
  flushStreamNow();

  const { convId, msgId } = streamingMsg;
  const contentEl = streamingMsg.contentEl;
  const cursorEl = streamingMsg.cursorEl;

  const finalSources = [];
  const finalArtifacts = Array.isArray(artifacts) ? artifacts : Array.isArray(streamingMsg.artifacts) ? streamingMsg.artifacts : [];

  try {
    streamingMsg.thinkingEl?.remove?.();
  } catch {}
  if (streamingMsg.thinkingTimer) clearTimeout(streamingMsg.thinkingTimer);
  try {
    cursorEl?.remove?.();
  } catch {}

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

  msg.sources = [];
  msg.sourcesRequested = false;
  if (finalArtifacts.length) {
    msg.artifacts = finalArtifacts;
    if (contentEl) renderArtifacts(contentEl, finalArtifacts);
  }

  streamingMsg = null;
  conv.updatedAt = nowISO();
  upsertConversation(conv);
  persistState();
  renderSidebarList();

  if (ok) setTimeout(() => maybeAutoTitleConversation(convId), 0);
}

function finalizeStreamingInterrupted() {
  if (!streamingMsg) return;
  flushStreamNow();
  const { convId, msgId, contentEl, cursorEl } = streamingMsg;
  try {
    streamingMsg.thinkingEl?.remove?.();
  } catch {}
  if (streamingMsg.thinkingTimer) clearTimeout(streamingMsg.thinkingTimer);
  if (cursorEl) cursorEl.remove();

  const conv = conversations.find((c) => c.id === convId);
  const msg = conv?.messages?.find((m) => m.id === msgId);
  if (!conv || !msg) {
    streamingMsg = null;
    return;
  }

  msg.interrupted = true;
  if (!String(msg.content || "").trim()) {
    msg.content = "Geração interrompida.";
  }
  if (contentEl) renderAssistantContent(contentEl, msg.content || "");

  const article = contentEl?.closest?.(".msg");
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

function userAskedForSourcesFromText(text) {
  const t = String(text || "").trim().toLowerCase();
  if (!t) return false;

  if (
    t.startsWith("/fontes") ||
    t.startsWith("/kb") ||
    t.startsWith("/buscar") ||
    t.startsWith("/pesquisar")
  ) {
    return true;
  }

  if (
    t.includes("cite as fontes") ||
    t.includes("mostrar fontes") ||
    t.includes("mostre fontes") ||
    t.includes("quais fontes") ||
    t.includes("de onde veio")
  ) {
    return true;
  }

  const asksComprovante = t.includes("comprovante");
  const asksLookup = ["buscar", "busque", "procurar", "procure", "pesquisar", "pesquise", "consultar", "consulte"].some((v) => t.includes(v));
  return asksComprovante && asksLookup;
}

async function sendMessage() {
  try {
    console.count("sendMessage");
  } catch {}

  ptLog("sendMessage:enter", { isGenerating, hasActiveConversation: hasActiveConversation(), pendingFiles: pendingFiles.length });
  if (isGenerating) return;
  if (sendInFlight) return;
  sendInFlight = true;

  const mainRaw = String(el.input?.value || "");
  const emptyRaw = String(el.emptyInput?.value || "");
  ptLog("sendMessage:composer:raw", {
    hasActiveConversation: hasActiveConversation(),
    mainLen: mainRaw.length,
    emptyLen: emptyRaw.length,
    pendingFiles: pendingFiles.length,
  });

  const text = readComposerText().trim();
  const userAskedForSources = userAskedForSourcesFromText(text);
  ptLog("sendMessage:composer", { textLen: text.length });
  if (!text && pendingFiles.length === 0) {
    ptLog("sendMessage:empty", { mainLen: mainRaw.length, emptyLen: emptyRaw.length });
    toast("Digite uma mensagem ou anexe um arquivo.");
    focusComposer();
    sendInFlight = false;
    return;
  }

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
      try {
        el.input?.focus?.();
      } catch {}
    }, 0);
  }

  const filesToSend = pendingFiles.slice();

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

  appendMessageToThread(userMsg);
  const assistantNode = appendMessageToThread(assistantMsg);
  const assistantContent =
    assistantNode?.querySelector?.(".content") ||
    el.thread?.querySelector?.(`article.msg[data-msg-id="${assistantMsg.id}"] .content`) ||
    null;

  // ✅ GUARD: evita crash silencioso quando o HTML não tem `.content`/`#thread`.
  assistantMsg.sourcesRequested = userAskedForSources;
  const okBind = startStreamingIntoMessage({ convId: conv.id, msgId: assistantMsg.id, contentEl: assistantContent, sourcesRequested: userAskedForSources });
  if (!okBind) {
    const err = "Falha ao iniciar thread (UI). Verifique se existe `#thread` e `.content` no HTML e se o DOM não foi recriado.";
    ptLog("sendMessage:bind:fail", {
      hasThread: !!el.thread,
      assistantNodeTag: String(assistantNode?.tagName || ""),
      assistantNodeHasContent: !!assistantNode?.querySelector?.(".content"),
    });

    // Make the failure visible in the assistant bubble (no silent failures).
    assistantMsg.content = err;
    conv.updatedAt = nowISO();
    upsertConversation(conv);
    persistState();
    renderThread();
    toast(err, { ms: 6500 });
    sendInFlight = false;
    return;
  }

  scrollToBottomIfNeeded();

  clearComposerText();
  pendingFiles = [];
  renderFileChips();
  try { if (el.fileInput) el.fileInput.value = ""; } catch {}
  updateComposerControls();

  setGenerating(true);
  ptLog("sendMessage:generating", { convId: conv.id });
  currentAbortController = new AbortController();

  let watchdogTimer = 0;
  let watchdogFallback = false;
  let emptyStreamDone = false;

  let uploadedDocsForThisMessage = false;

  const uploadFilesForChat = async (files) => {
    const list = Array.isArray(files) ? files : files ? [files] : [];
    if (!list.length) return false;

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
      if (streamingMsg.hasFirstChunk) return false;

      watchdogFallback = true;
      setTopbarStatus("Reconectando…");
      setThinkingStatus(String(reason || "Reconectando…"));
      ensureThinkingVisible();
      try { currentAbortController?.abort?.(); } catch {}

      // Before falling back to non-stream /chat, try to ensure we are pointing at a reachable backend.
      // This mitigates common local issues (localhost IPv6 ::1 vs uvicorn bound to 127.0.0.1, reloads, etc.).
      const baseBefore = String(BACKEND_BASE || "").trim();
      const healthOk = await ensureBackendBaseOnline();
      const baseAfter = String(BACKEND_BASE || "").trim();
      ptLog("fetch:/chat:fallback:health", { ok: !!healthOk, baseBefore, baseAfter });

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

      const doPost = () =>
        fetchWithRetry(
          `${BACKEND_BASE}/chat`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          },
          { retries: 2, delayMs: 420 }
        );

      let r2 = null;
      try {
        r2 = await doPost();
      } catch (e) {
        // One more attempt after cycling backend base candidates.
        const ok2 = await ensureBackendBaseOnline();
        const baseRetry = String(BACKEND_BASE || "").trim();
        ptLog("fetch:/chat:fallback:retry", { ok: !!ok2, base: baseRetry, err: String(e?.message || e || "") });
        if (ok2) r2 = await doPost();
        else throw e;
      }

      ptLog("fetch:/chat:fallback:response", { status: r2.status, ok: r2.ok, ct: r2.headers?.get?.("content-type") || "" });

      if (!r2.ok) {
        const t2 = await r2.text().catch(() => "");
        finalizeStreaming({ ok: false, errorMessage: `Fallback /chat retornou ${r2.status}. ${t2 ? t2.slice(0, 240) : ""}`.trim() });
        return true;
      }

      const data = await r2.json().catch(() => null);
      const reply = String(data?.reply || "").trim();
      if (!reply) {
        finalizeStreaming({ ok: false, errorMessage: "Fallback /chat retornou vazio." });
        return true;
      }

      const conv3 = conversations.find((c) => c.id === conv.id);
      const msg3 = conv3?.messages?.find((m) => m.id === assistantMsg.id);
      if (msg3) msg3.content = reply;

      const sources = Array.isArray(data?.sources) ? data.sources : [];
      const artifacts = Array.isArray(data?.artifacts) ? data.artifacts : [];
      finalizeStreaming({ ok: true, sources: userAskedForSources ? sources : [], artifacts });
      return true;
    } catch (e) {
      const base = String(BACKEND_BASE || "").trim();
      const ok = await pingBackendHealth(900).catch(() => false);
      const hints = ok
        ? "O backend respondeu ao /health. Isso costuma ser bloqueio do navegador (CORS/mixed-content) ou um erro de rede momentâneo."
        : "Backend parece offline. Inicie com backend/run_dev.ps1 (porta 8000) e confirme /health.";
      finalizeStreaming({
        ok: false,
        errorMessage: `Falha no fallback /chat (${base}). ${String(e?.message || "")}`.trim() + `\n${hints}`,
      });
      return true;
    } finally {
      clearTopbarStatus();
    }
  };

  try {
    if (!streamingEnabled) {
      const slowHintTimer = window.setTimeout(() => {
        try {
          toast("Streaming está desligado; /chat pode demorar. Ative em Modo de resposta → Streaming.", { ms: 5200 });
        } catch {}
      }, 5000);

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
      try {
        clearTimeout(slowHintTimer);
      } catch {}

      ptLog("fetch:/chat:response", { status: r.status, ok: r.ok, ct: r.headers?.get?.("content-type") || "" });

      if (!r.ok) {
        const t = await r.text().catch(() => "");
        finalizeStreaming({ ok: false, errorMessage: `Servidor retornou ${r.status}. ${t ? t.slice(0, 240) : ""}`.trim() });
        return;
      }

      const data = await r.json().catch(() => null);
      const reply = String(data?.reply || "").trim();
      if (!reply) {
        finalizeStreaming({ ok: false, errorMessage: "Resposta vazia do servidor." });
        return;
      }

      const conv2 = conversations.find((c) => c.id === conv.id);
      const msg2 = conv2?.messages?.find((m) => m.id === assistantMsg.id);
      if (msg2) msg2.content = reply;

      const sources = Array.isArray(data?.sources) ? data.sources : [];
      const artifacts = Array.isArray(data?.artifacts) ? data.artifacts : [];
      finalizeStreaming({ ok: true, sources: userAskedForSources ? sources : [], artifacts });
      return;
    }

    const hasFiles = filesToSend.length > 0;

    if (hasFiles) {
      setTopbarStatus("Enviando documentos…");
      setThinkingStatus("Enviando documentos…");
      ensureThinkingVisible();

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

    // Make it obvious (UI + logs) that we are about to connect to the stream.
    setTopbarStatus("Conectando…");
    setThinkingStatus("Conectando…");
    ensureThinkingVisible();
    const streamPath = DEBUG_STREAM_MODE ? "/debug/stream" : "/chat/stream";
    ptLog("fetch:/chat/stream:start", { base: BACKEND_BASE, path: streamPath, hasFiles, useDownloads: effectiveUseDownloads() || uploadedDocsForThisMessage });

    const res = await fetchWithRetry(`${BACKEND_BASE}${streamPath}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
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
      // Fallback: if streaming isn't available (older browsers/proxies), read the full body at once.
      const t = await res.text().catch(() => "");
      if (!t.trim()) {
        finalizeStreaming({ ok: false, errorMessage: "Resposta inválida do servidor (sem stream)." });
        return;
      }
      streamAppend(t);
      clearTopbarStatus();
      finalizeStreaming({ ok: true });
      return;
    }

    const contentType = String(res.headers?.get?.("content-type") || "").toLowerCase();

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
            clearTopbarStatus();
          } else if (phase === "done") {
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
      const didFallback = await runChatFallback("Falha no streaming. Tentando fallback…");
      if (didFallback) return;

      const ok = await ensureBackendBaseOnline();
      syncTopbarSubtitle();

      const extra = ok
        ? "O backend respondeu ao /health. Isso costuma ser reinício do servidor (reload) ou bloqueio do navegador (CORS)."
        : "Backend parece offline. Inicie com backend/run_dev.ps1 (porta 8000).";

      finalizeStreaming({
        ok: false,
        errorMessage:
          `Falha ao conectar ao backend (${BACKEND_BASE}). ${String(e?.message || "")}`.trim() + `\n${extra}`,
      });
    }
  } finally {
    if (watchdogTimer) {
      try {
        clearTimeout(watchdogTimer);
      } catch {}
      watchdogTimer = 0;
    }
    setGenerating(false);
    currentAbortController = null;
    clearTopbarStatus();
    sendInFlight = false;
  }
}

function stopGenerating() {
  try {
    currentAbortController?.abort();
  } catch {}
  finalizeStreamingInterrupted();
  toast("Geração interrompida.", { ms: 1800 });
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
        else if (ch === '"') inString = false;
        continue;
      }
      if (ch === '"') {
        inString = true;
        continue;
      }
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

    const split = splitLeadingJson(dataRaw);
    const jsonPrefix = split?.jsonText || null;
    const restAfterJson = split?.rest || "";

    const payload = safeJsonParse(jsonPrefix ?? dataRaw);

    // Some SSE servers don't set `event:` and instead embed a `{type:"delta"|"status"|...}` inside data.
    let eventName = String(event || "message").trim() || "message";
    try {
      if (
        (eventName === "message" || eventName === "event") &&
        payload &&
        typeof payload === "object" &&
        !Array.isArray(payload)
      ) {
        const t = String(payload.type || payload.event || "").trim();
        if (t) eventName = t;
      }
    } catch {}
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

    if (eventName === "delta") {
      handlers.onDelta?.(deltaText);
    } else if (eventName === "status") {
      handlers.onStatus?.(payload);
      if (payload && typeof payload === "object" && String(payload.phase || "").toLowerCase() === "done") {
        sawTerminal = true;
      }
      if (payload && typeof payload === "object" && String(payload.phase || "").toLowerCase() === "error") {
        sawTerminal = true;
      }
    } else if (eventName === "meta") {
      handlers.onMeta?.(payload);
    } else if (eventName === "sources") {
      handlers.onSources?.(payload);
    } else if (eventName === "artifact") {
      handlers.onArtifact?.(payload);
    } else if (eventName === "citations") {
      handlers.onCitations?.(payload);
    } else if (eventName === "error") {
      sawTerminal = true;
      handlers.onError?.(payload || { message: "Erro." });
    } else if (eventName === "done") {
      sawTerminal = true;
      handlers.onDone?.(payload);
    } else {
      // Fallback: if data looks like a text delta, render it; otherwise ignore structured payloads.
      const looksLikeTextDelta =
        typeof deltaText === "string" &&
        deltaText !== "" &&
        !(payload && typeof payload === "object" && !Array.isArray(payload) && jsonPrefix);
      if (looksLikeTextDelta) handlers.onDelta?.(deltaText);
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
      } else {
        // Ignore `id:`, `retry:`, comments, etc.
      }
    }
  }

  emit();
  return sawTerminal;
}

// =============================
// Sidebar open/close (mobile)
// =============================
function openSidebar() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    el.sidebar?.classList?.add?.("open");
    if (el.overlay) el.overlay.hidden = false;
  }
}

function closeSidebarIfMobile() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    el.sidebar?.classList?.remove?.("open");
    if (el.overlay) el.overlay.hidden = true;
  }
}

function applySidebarCollapsed() {
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  if (isMobile) {
    el.app?.classList?.remove?.("sb-collapsed");
    return;
  }
  el.app?.classList?.toggle?.("sb-collapsed", !!sidebarCollapsed);
}

function toggleSidebar() {
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  if (isMobile) {
    if (el.sidebar?.classList?.contains?.("open")) closeSidebarIfMobile();
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
  const m = v.match(/filename\*?=(?:UTF-8''|"?)([^";]+)"?/i);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return m[1];
  }
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
  try {
    localStorage.setItem(DOWNLOADS_USE_KEY, useDownloadsInChat ? "1" : "0");
  } catch {}
  if (el.downloadsUseToggle) el.downloadsUseToggle.checked = useDownloadsInChat;
}

function openDownloadsPanel() {
  if (!el.downloadsPanel) return;
  el.downloadsPanel.hidden = false;
  if (el.downloadsUseToggle) el.downloadsUseToggle.checked = useDownloadsInChat;
  refreshDownloadsList();
  try {
    el.downloadsSearchInput?.focus?.();
  } catch {}
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
  const list = Array.isArray(files) ? files : files ? [files] : [];
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
  // Idempotent: app.js can be loaded twice accidentally (multiple script tags, shim loaders, etc.).
  // Duplicate event handlers can immediately abort streaming (one handler starts, the other sees isGenerating and stops).
  if (window.__paytech.eventsBound) return;
  window.__paytech.eventsBound = true;

  window.__paytech.bindOk = false;

  if (el.chat) {
    el.chat.addEventListener(
      "scroll",
      () => {
        pinnedToBottom = isNearBottom();
        updateToBottomBtn();
      },
      { passive: true }
    );
  }

  el.toBottomBtn?.addEventListener?.("click", () => {
    pinnedToBottom = true;
    scrollToBottom(true);
    updateToBottomBtn();
  });

  el.sidebarToggle?.addEventListener?.("click", () => toggleSidebar());
  el.sidebarLogo?.addEventListener?.("click", () => toggleSidebar());
  el.overlay?.addEventListener?.("click", () => closeSidebarIfMobile());

  el.searchInput?.addEventListener?.("input", () => {
    searchQuery = el.searchInput.value || "";
    renderSidebarList();
  });
  el.searchBtn?.addEventListener?.("click", () => {
    if (sidebarCollapsed) toggleSidebar();
    setTimeout(() => {
      try {
        el.searchInput?.focus?.();
      } catch {}
    }, 0);
  });

  el.newChat?.addEventListener?.("click", () => {
    pendingFiles = [];
    renderFileChips();
    goHome();
    closeSidebarIfMobile();
  });

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
    const insert = `Contexto do documento "${filename}":\n${snippet}\n\n`;
    const target = hasActiveConversation() ? el.input : el.emptyInput;
    if (target) target.value = insert + (target.value || "");
    if (hasActiveConversation()) autoResize();
    else autoResizeEmpty();
    try {
      target?.focus?.();
    } catch {}
    toast("Trecho inserido no chat.");
  });

  el.conversationList?.addEventListener?.("click", (e) => {
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

  el.conversationList?.addEventListener?.("keydown", (e) => {
    if (e.key !== "Enter") return;
    const item = e.target?.closest?.(".conv-item");
    if (item?.dataset?.id) selectConversation(item.dataset.id);
  });

  el.attachBtn?.addEventListener?.("click", () => el.fileInput?.click?.());
  el.fileInput?.addEventListener?.("change", () => {
    const files = Array.from(el.fileInput.files || []);
    if (!files.length) return;
    pendingFiles = pendingFiles.concat(files);
    renderFileChips();
    el.fileInput.value = "";
    updateComposerControls();
  });

  el.input?.addEventListener?.("input", autoResize);
  el.input?.addEventListener?.("input", updateComposerControls);
  el.input?.addEventListener?.("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  el.sendBtn?.addEventListener?.("click", () => {
    ptLog("ui:sendBtn:click", { isGenerating, canSend: canSendNow() });
    if (isGenerating) stopGenerating();
    else sendMessage();
  });

  el.emptyInput?.addEventListener?.("input", autoResizeEmpty);
  el.emptyInput?.addEventListener?.("input", updateComposerControls);
  el.emptyInput?.addEventListener?.("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  el.emptySendBtn?.addEventListener?.("click", sendMessage);

  document.addEventListener(
    "keydown",
    (e) => {
      try {
        if (e.defaultPrevented) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.key !== "Enter" && String(e.key || "").length !== 1) return;
        const ae = document.activeElement;
        const tag = String(ae?.tagName || "").toLowerCase();
        const isEditable = !!ae?.isContentEditable || tag === "textarea" || tag === "input";
        if (isEditable) return;
        focusComposer();
      } catch {}
    },
    true
  );

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
  } catch {}

  el.emptySuggestions?.addEventListener?.("click", (e) => {
    const btn = e.target?.closest?.("button.pill");
    if (!btn) return;
    const text = (btn.dataset.suggest || btn.textContent || "").trim();
    if (!text) return;
    if (!el.emptyInput) return;
    el.emptyInput.value = text;
    autoResizeEmpty();
    updateComposerControls();
    try {
      el.emptyInput.focus();
    } catch {}
  });

  el.themeToggle?.addEventListener?.("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(current === "dark" ? "light" : "dark", { persist: true });
    toast("Tema atualizado.");
  });

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

  el.itemMenu?.addEventListener?.("click", (e) => {
    const btn = e.target?.closest?.("button.menu-item");
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id;
    if (action === "download") {
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
    closeMenus();
    if (!window.matchMedia("(max-width: 900px)").matches) {
      if (el.overlay) el.overlay.hidden = true;
      el.sidebar?.classList?.remove?.("open");
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
      if (useDownloadsInChat) refreshDownloadsList({ silent: true });
      return;
    }
    ensureBackendBaseOnline().then((finalOk) => {
      syncTopbarSubtitle();
      if (finalOk && useDownloadsInChat) refreshDownloadsList({ silent: true });
      if (!finalOk) toast(`Backend offline (${BACKEND_BASE})`, { ms: 3500 });
    });
  });

  try {
    el.emptyInput?.focus?.();
  } catch {}
}

// Fallback send handlers: if boot/bindEvents crashed mid-way, keep chat usable.
(() => {
  if (window.__paytechFallbackSendHandlersInstalled) return;
  window.__paytechFallbackSendHandlersInstalled = true;

  document.addEventListener(
    "click",
    (e) => {
      if (window.__paytech?.bindOk) return;
      try {
        window.__paytech?.bootOnce?.();
      } catch {}
      const btn = e.target?.closest?.("#sendBtn");
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      try {
        sendMessage();
      } catch (err) {
        _captureEarlyError("sendMessage(click)", err);
      }
    },
    true
  );

  document.addEventListener(
    "keydown",
    (e) => {
      if (window.__paytech?.bindOk) return;

      try {
        window.__paytech?.bootOnce?.();
      } catch {}

      // Re-check after attempting to boot.
      if (window.__paytech?.bindOk) return;

      if (e.key !== "Enter" || e.shiftKey) return;
      const ta = e.target?.closest?.("#input,#emptyInput");
      if (!ta) return;

      e.preventDefault();
      e.stopPropagation();
      try {
        sendMessage();
      } catch (err) {
        _captureEarlyError("sendMessage(keydown)", err);
      }
    },
    true
  );
})();

function bootOnce() {
  if (window.__paytech.booted) return;
  window.__paytech.booted = true;
  try {
    boot();
    if (_earlyErrors.length) {
      const last = _earlyErrors[_earlyErrors.length - 1];
      toast(`Erro no frontend: ${last.msg}`, { ms: 6500 });
    }
  } catch (err) {
    _captureEarlyError("boot", err);
    try {
      toast(`Falha ao iniciar UI: ${String(err?.message || err)}`.trim(), { ms: 7000 });
    } catch {}
  }
}
window.__paytech.bootOnce = bootOnce;

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootOnce, { once: true });
} else {
  // Handles dynamic script loading (e.g. script shim injected after DOMContentLoaded).
  setTimeout(bootOnce, 0);
} 
