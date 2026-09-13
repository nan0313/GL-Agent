(function (global) {
  "use strict";

  var state = {
    started: false,
    config: null,
    health: null,
    root: null,
    baseUrl: "",
    userId: "ev-customer",
    conversations: [],
    conversationId: null,
    messages: [],
    attachments: [],
    knowledgeSources: [],
    citations: [],
    busy: false,
    controller: null,
    attachmentPoll: null,
    settings: null,
    windowingReady: false,
    resizeObserver: null,
    interaction: null,
    interactionFrame: null,
    streamMetrics: null,
    deletingAttachments: {}
  };

  var PUBLIC_EVENT_NAMES = {
    attachment_status: true,
    answer_delta: true,
    answer_completed: true,
    final_result: true,
    user_facing_error: true,
    citation_metadata: true
  };
  var WINDOW_STORAGE = {
    x: "customer.agent.window.x",
    y: "customer.agent.window.y",
    width: "customer.agent.window.width",
    height: "customer.agent.window.height"
  };
  var SETTINGS_STORAGE = "customer.agent.settings";
  var SIDEBAR_STORAGE = "customer.agent.sidebar.collapsed";
  var DEFAULT_SETTINGS = { accent: "#2563eb", fontSize: "15px", textColor: "#172033" };
  var ACCENT_PRESETS = ["#2563eb", "#1d4ed8", "#0891b2", "#7c3aed", "#16835f"];

  function start(config, health) {
    state.config = config || {};
    state.health = health || {};
    state.baseUrl = String(state.config.agentBaseUrl || "").replace(/\/$/, "");
    if (state.started) return;
    state.started = true;
    state.settings = loadSettings();
    var stylesheet = loadStyles();
    buildApp();
    applySettings(state.settings, false);
    bindApp();
    if (stylesheet.sheet) {
      global.requestAnimationFrame(function () { global.requestAnimationFrame(initializeFloatingWindow); });
    } else {
      stylesheet.addEventListener("load", initializeFloatingWindow, { once: true });
    }
    refreshConversations().catch(showRequestError);
  }

  function loadStyles() {
    var existing = document.getElementById("ev-customer-agent-style");
    if (existing) return existing;
    var link = document.createElement("link");
    link.id = "ev-customer-agent-style";
    link.rel = "stylesheet";
    link.href = state.baseUrl + "/frontend/customer_agent.css";
    document.head.appendChild(link);
    return link;
  }

  function buildApp() {
    var root = document.createElement("div");
    root.id = "ev-customer-app";
    var webglOverlay = !!(global.viewer && global.viewer.scene);
    var sidebarCollapsed = localStorage.getItem(SIDEBAR_STORAGE) !== "false";
    root.className = webglOverlay ? "is-webgl-overlay" : "is-standalone";
    if (sidebarCollapsed) root.classList.add("is-sidebar-collapsed");
    var sidebarToggleLabel = sidebarCollapsed ? "展开侧边栏" : "收起侧边栏";
    var sidebarToggleGlyph = sidebarCollapsed ? "›" : "‹";
    root.innerHTML =
      '<button class="evc-panel-launcher" type="button" data-panel-open aria-label="打开 EV Agent" title="打开 EV Agent">EV</button>' +
      '<button class="evc-sidebar-backdrop" type="button" data-sidebar-backdrop aria-label="收起侧边栏" tabindex="-1"></button>' +
      '<aside class="evc-sidebar" aria-label="主导航">' +
        '<div class="evc-brand">' +
          '<button class="evc-mark" type="button" data-collapse aria-label="收起或展开侧边栏" title="收起或展开侧边栏">EV</button>' +
          '<div class="evc-brand-copy"><strong>EV Agent</strong><span>输变电智能工作空间</span></div>' +
          '<button class="evc-icon-button evc-sidebar-toggle" type="button" data-collapse aria-label="' + sidebarToggleLabel + '" title="' + sidebarToggleLabel + '">' + sidebarToggleGlyph + '</button>' +
        '</div>' +
        '<button class="evc-new-button" type="button" data-new-chat aria-label="新建对话" title="新建对话"><span class="evc-nav-icon" aria-hidden="true">＋</span><span>新建对话</span></button>' +
        '<nav class="evc-nav">' +
          '<button class="evc-nav-button" type="button" data-nav="knowledge" aria-label="输变电知识库" title="输变电知识库"><span class="evc-nav-icon" aria-hidden="true">◇</span><span class="evc-nav-label">输变电知识库</span></button>' +
          '<button class="evc-nav-button" type="button" data-nav="files" aria-label="已上传文件" title="已上传文件"><span class="evc-nav-icon" aria-hidden="true">▱</span><span class="evc-nav-label">已上传文件</span></button>' +
          '<button class="evc-nav-button" type="button" data-nav="settings" aria-label="设置" title="设置"><span class="evc-nav-icon" aria-hidden="true">⚙</span><span class="evc-nav-label">设置</span></button>' +
        '</nav>' +
        '<div class="evc-section-label">最近会话</div>' +
        '<div class="evc-session-list" data-session-list></div>' +
        '<div class="evc-sidebar-bottom">' +
          '<div class="evc-robot" aria-hidden="true">' +
            '<span class="evc-robot-antenna"></span>' +
            '<span class="evc-robot-head"><i class="evc-robot-eye"></i><i class="evc-robot-eye"></i></span>' +
            '<span class="evc-robot-body"><b>EV</b></span>' +
          '</div>' +
          '<div class="evc-sidebar-foot">本地工作空间 · 数据保存在当前设备</div>' +
        '</div>' +
      '</aside>' +
      '<main class="evc-shell">' +
        '<header class="evc-header">' +
          '<div class="evc-drag-handle" data-drag-handle>' +
            '<div class="evc-breadcrumb"><strong>EV Agent</strong> <span>/ 输变电知识库</span></div>' +
            '<span class="evc-drag-grip" aria-hidden="true">⋮⋮</span>' +
          '</div>' +
          '<div class="evc-header-actions"><button class="evc-icon-button evc-panel-toggle" type="button" data-panel-toggle aria-label="收起 EV Agent" title="收起 EV Agent">›</button><button class="evc-icon-button" type="button" data-more aria-label="更多选项" title="更多选项">•••</button><span class="evc-avatar" aria-label="当前用户">EV</span></div>' +
        '</header>' +
        '<section class="evc-workspace" aria-label="对话工作区">' +
          '<div class="evc-conversation" data-conversation role="log" aria-live="polite">' +
            '<div class="evc-thread" data-thread></div>' +
          '</div>' +
          '<div class="evc-composer-zone">' +
            '<div class="evc-files" data-files></div>' +
            '<form class="evc-composer" data-composer>' +
              '<button class="evc-icon-button" type="button" data-attach aria-label="上传文件" title="上传文件">＋</button>' +
              '<textarea rows="1" data-input aria-label="向 EV Agent 提问" placeholder="向 EV Agent 提问…"></textarea>' +
              '<button class="evc-send" type="submit" data-send aria-label="发送消息" title="发送消息">↑</button>' +
              '<input type="file" data-file-input accept=".pdf,.docx,.txt,.md,.markdown" multiple hidden>' +
            '</form>' +
            '<div class="evc-composer-hint" data-composer-hint>Enter 发送 · Shift+Enter 换行</div>' +
          '</div>' +
          '<aside class="evc-context" data-context hidden aria-label="上下文详情">' +
            '<div class="evc-context-head"><h2 data-context-title>来源</h2><button class="evc-icon-button" type="button" data-context-close aria-label="关闭详情" title="关闭">×</button></div>' +
            '<div data-context-content></div>' +
          '</aside>' +
        '</section>' +
      '</main>' +
      '<span class="evc-resize-handle evc-resize-n" data-resize="n" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-e" data-resize="e" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-s" data-resize="s" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-w" data-resize="w" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-ne" data-resize="ne" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-se" data-resize="se" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-sw" data-resize="sw" aria-hidden="true"></span>' +
      '<span class="evc-resize-handle evc-resize-nw" data-resize="nw" aria-hidden="true"></span>';
    document.body.appendChild(root);
    state.root = root;
    renderMessages();
  }

  function bindApp() {
    var root = state.root;
    root.querySelector("[data-panel-toggle]").addEventListener("click", function () { setPanelCollapsed(true); });
    root.querySelector("[data-panel-open]").addEventListener("click", function () { setPanelCollapsed(false); });
    global.addEventListener("agent:open-panel", function () { setPanelCollapsed(false); });
    root.querySelectorAll("[data-collapse]").forEach(function (button) {
      button.addEventListener("click", function () {
        setSidebarCollapsed(!root.classList.contains("is-sidebar-collapsed"));
      });
    });
    root.querySelector("[data-sidebar-backdrop]").addEventListener("click", function () { setSidebarCollapsed(true); });
    root.querySelector("[data-new-chat]").addEventListener("click", function () { createConversation().catch(showRequestError); });
    root.querySelector('[data-nav="files"]').addEventListener("click", function () { openUploadedFiles().catch(showRequestError); });
    root.querySelector('[data-nav="knowledge"]').addEventListener("click", function () { openKnowledgeLibrary().catch(showRequestError); });
    root.querySelector('[data-nav="settings"]').addEventListener("click", openSettings);
    root.querySelector("[data-more]").addEventListener("click", openSettings);
    root.querySelector("[data-context-close]").addEventListener("click", closeContext);
    root.querySelector("[data-attach]").addEventListener("click", function () { root.querySelector("[data-file-input]").click(); });
    root.querySelector("[data-file-input]").addEventListener("change", function (event) {
      uploadFiles(Array.prototype.slice.call(event.target.files || [])).finally(function () { event.target.value = ""; });
    });
    var composer = root.querySelector("[data-composer]");
    var input = root.querySelector("[data-input]");
    composer.addEventListener("submit", function (event) {
      event.preventDefault();
      sendMessage(input.value).catch(showRequestError);
    });
    input.addEventListener("input", resizeComposer);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        composer.requestSubmit();
      }
    });
    input.addEventListener("paste", function (event) {
      var files = Array.prototype.slice.call(event.clipboardData && event.clipboardData.files || []);
      if (files.length) {
        event.preventDefault();
        uploadFiles(files).catch(showRequestError);
      }
    });
    ["dragenter", "dragover"].forEach(function (name) {
      composer.addEventListener(name, function (event) { event.preventDefault(); composer.classList.add("is-dragging"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      composer.addEventListener(name, function (event) { event.preventDefault(); composer.classList.remove("is-dragging"); });
    });
    composer.addEventListener("drop", function (event) {
      uploadFiles(Array.prototype.slice.call(event.dataTransfer && event.dataTransfer.files || [])).catch(showRequestError);
    });
    root.querySelector("[data-drag-handle]").addEventListener("pointerdown", beginWindowDrag);
    root.querySelectorAll("[data-resize]").forEach(function (handle) {
      handle.addEventListener("pointerdown", beginWindowResize);
    });
    global.addEventListener("resize", clampFloatingWindow);
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      var panel = root.querySelector("[data-context]");
      if (!panel.hidden) closeContext();
      else if (!root.classList.contains("is-sidebar-collapsed") && root.classList.contains("is-narrow")) setSidebarCollapsed(true);
    });
  }

  function setSidebarCollapsed(collapsed) {
    if (!state.root) return;
    state.root.classList.toggle("is-sidebar-collapsed", collapsed === true);
    localStorage.setItem(SIDEBAR_STORAGE, String(collapsed === true));
    state.root.querySelectorAll("[data-collapse]").forEach(function (button) {
      button.setAttribute("aria-expanded", String(collapsed !== true));
      button.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
      button.title = collapsed ? "展开侧边栏" : "收起侧边栏";
    });
  }

  function setPanelCollapsed(collapsed) {
    if (!state.root || !state.root.classList.contains("is-webgl-overlay")) return;
    if (collapsed === true && !state.root.classList.contains("is-panel-collapsed")) persistWindowRect(state.root.getBoundingClientRect());
    state.root.classList.toggle("is-panel-collapsed", collapsed === true);
    var toggle = state.root.querySelector("[data-panel-toggle]");
    if (toggle) toggle.setAttribute("aria-expanded", String(collapsed !== true));
    if (collapsed !== true) {
      restoreWindowLayout();
      global.setTimeout(function () {
        var input = state.root && state.root.querySelector("[data-input]");
        if (input) input.focus();
      }, 0);
    }
  }

  function initializeFloatingWindow() {
    if (state.windowingReady || !state.root || !state.root.classList.contains("is-webgl-overlay")) return;
    state.windowingReady = true;
    restoreInitialWindowLayout();
    if (global.ResizeObserver) {
      state.resizeObserver = new ResizeObserver(function (entries) {
        var width = entries[0] && entries[0].contentRect.width || state.root.getBoundingClientRect().width;
        state.root.classList.toggle("is-narrow", width < 650);
      });
      state.resizeObserver.observe(state.root);
    }
  }

  function defaultWindowRect() {
    var margin = 14;
    var availableWidth = Math.max(320, global.innerWidth - margin * 2);
    var availableHeight = Math.max(360, global.innerHeight - margin * 2);
    var width = Math.min(availableWidth, Math.min(780, Math.max(680, global.innerWidth * .42)));
    var height = Math.min(availableHeight, 900);
    return { x: global.innerWidth - width - margin, y: margin, width: width, height: height };
  }

  function storedWindowRect() {
    var values = {};
    Object.keys(WINDOW_STORAGE).forEach(function (key) {
      values[key] = Number(localStorage.getItem(WINDOW_STORAGE[key]));
    });
    return Object.keys(values).every(function (key) { return Number.isFinite(values[key]) && values[key] > (key === "x" || key === "y" ? -1 : 0); }) ? values : null;
  }

  function restoreInitialWindowLayout() {
    if (!state.root || !state.root.classList.contains("is-webgl-overlay") || state.root.classList.contains("is-panel-collapsed")) return;
    var fallback = defaultWindowRect();
    var stored = storedWindowRect() || fallback;
    var initial = clampWindowRect({
      x: global.innerWidth - stored.width - 14,
      y: 14,
      width: stored.width,
      height: stored.height
    });
    applyWindowRect(initial);
    persistWindowRect(state.root.getBoundingClientRect());
  }

  function restoreWindowLayout() {
    if (!state.root || !state.root.classList.contains("is-webgl-overlay") || state.root.classList.contains("is-panel-collapsed")) return;
    applyWindowRect(clampWindowRect(storedWindowRect() || defaultWindowRect()));
  }

  function resetWindowLayout() {
    Object.keys(WINDOW_STORAGE).forEach(function (key) { localStorage.removeItem(WINDOW_STORAGE[key]); });
    applyWindowRect(clampWindowRect(defaultWindowRect()));
    persistWindowRect(state.root.getBoundingClientRect());
  }

  function clampFloatingWindow() {
    if (!state.root || !state.root.classList.contains("is-webgl-overlay") || state.root.classList.contains("is-panel-collapsed")) return;
    global.requestAnimationFrame(function () {
      var rect = state.root.getBoundingClientRect();
      applyWindowRect(clampWindowRect({ x: rect.left, y: rect.top, width: rect.width, height: rect.height }));
      persistWindowRect(state.root.getBoundingClientRect());
    });
  }

  function clampWindowRect(rect) {
    var margin = Math.min(6, Math.max(0, Math.floor(Math.min(global.innerWidth, global.innerHeight) / 20)));
    var maxWidth = Math.max(280, global.innerWidth - margin * 2);
    var maxHeight = Math.max(320, global.innerHeight - margin * 2);
    var minWidth = Math.min(440, maxWidth);
    var minHeight = Math.min(420, maxHeight);
    var width = Math.min(maxWidth, Math.max(minWidth, Number(rect.width) || minWidth));
    var height = Math.min(maxHeight, Math.max(minHeight, Number(rect.height) || minHeight));
    var x = Math.min(global.innerWidth - margin - width, Math.max(margin, Number(rect.x) || 0));
    var y = Math.min(global.innerHeight - margin - height, Math.max(margin, Number(rect.y) || 0));
    return { x: x, y: y, width: width, height: height };
  }

  function applyWindowRect(rect) {
    if (!state.root) return;
    state.root.style.left = Math.round(rect.x) + "px";
    state.root.style.top = Math.round(rect.y) + "px";
    state.root.style.right = "auto";
    state.root.style.bottom = "auto";
    state.root.style.width = Math.round(rect.width) + "px";
    state.root.style.height = Math.round(rect.height) + "px";
  }

  function persistWindowRect(rect) {
    if (!rect || !state.root || state.root.classList.contains("is-panel-collapsed")) return;
    localStorage.setItem(WINDOW_STORAGE.x, String(Math.round(rect.left)));
    localStorage.setItem(WINDOW_STORAGE.y, String(Math.round(rect.top)));
    localStorage.setItem(WINDOW_STORAGE.width, String(Math.round(rect.width)));
    localStorage.setItem(WINDOW_STORAGE.height, String(Math.round(rect.height)));
  }

  function beginWindowDrag(event) {
    if (event.button !== 0 || !state.root.classList.contains("is-webgl-overlay") || state.root.classList.contains("is-panel-collapsed")) return;
    beginWindowInteraction(event, "move");
  }

  function beginWindowResize(event) {
    if (event.button !== 0 || !state.root.classList.contains("is-webgl-overlay") || state.root.classList.contains("is-panel-collapsed")) return;
    beginWindowInteraction(event, String(event.currentTarget.dataset.resize || "se"));
  }

  function beginWindowInteraction(event, mode) {
    event.preventDefault();
    var rect = state.root.getBoundingClientRect();
    state.interaction = {
      pointerId: event.pointerId,
      mode: mode,
      startX: event.clientX,
      startY: event.clientY,
      latestX: event.clientX,
      latestY: event.clientY,
      rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
    };
    state.root.classList.add("is-window-interacting");
    event.currentTarget.setPointerCapture && event.currentTarget.setPointerCapture(event.pointerId);
    document.addEventListener("pointermove", updateWindowInteraction);
    document.addEventListener("pointerup", finishWindowInteraction);
    document.addEventListener("pointercancel", finishWindowInteraction);
  }

  function updateWindowInteraction(event) {
    if (!state.interaction || event.pointerId !== state.interaction.pointerId) return;
    state.interaction.latestX = event.clientX;
    state.interaction.latestY = event.clientY;
    if (state.interactionFrame) return;
    state.interactionFrame = global.requestAnimationFrame(renderWindowInteraction);
  }

  function renderWindowInteraction() {
    state.interactionFrame = null;
    var item = state.interaction;
    if (!item) return;
    var dx = item.latestX - item.startX;
    var dy = item.latestY - item.startY;
    var next = { x: item.rect.x, y: item.rect.y, width: item.rect.width, height: item.rect.height };
    if (item.mode === "move") {
      next.x += dx;
      next.y += dy;
    } else {
      if (item.mode.indexOf("e") >= 0) next.width += dx;
      if (item.mode.indexOf("s") >= 0) next.height += dy;
      if (item.mode.indexOf("w") >= 0) { next.x += dx; next.width -= dx; }
      if (item.mode.indexOf("n") >= 0) { next.y += dy; next.height -= dy; }
    }
    var clamped = clampWindowRect(next);
    if (item.mode.indexOf("w") >= 0 && clamped.width !== next.width) clamped.x = item.rect.x + item.rect.width - clamped.width;
    if (item.mode.indexOf("n") >= 0 && clamped.height !== next.height) clamped.y = item.rect.y + item.rect.height - clamped.height;
    applyWindowRect(clampWindowRect(clamped));
  }

  function finishWindowInteraction(event) {
    if (!state.interaction || (event.pointerId !== undefined && event.pointerId !== state.interaction.pointerId)) return;
    if (state.interactionFrame) {
      global.cancelAnimationFrame(state.interactionFrame);
      state.interactionFrame = null;
      renderWindowInteraction();
    }
    state.interaction = null;
    state.root.classList.remove("is-window-interacting");
    document.removeEventListener("pointermove", updateWindowInteraction);
    document.removeEventListener("pointerup", finishWindowInteraction);
    document.removeEventListener("pointercancel", finishWindowInteraction);
    persistWindowRect(state.root.getBoundingClientRect());
  }

  async function api(path, options) {
    var response = await fetch(state.baseUrl + path, options || {});
    var data = {};
    try { data = await response.json(); } catch (ignore) {}
    if (!response.ok) throw new Error(String(data.detail || "请求暂时未完成，请重试。"));
    return data;
  }

  async function refreshConversations(preferred) {
    var data = await api("/api/conversations?user_id=" + encodeURIComponent(state.userId), { headers: { "X-User-ID": state.userId } });
    state.conversations = data.conversations || [];
    if (!state.conversations.length) return createConversation();
    var saved = sessionStorage.getItem("ev_customer_conversation_id");
    var target = preferred || saved;
    if (!target || !state.conversations.some(function (item) { return item.conversation_id === target; })) target = state.conversations[0].conversation_id;
    renderSessions();
    return switchConversation(target);
  }

  async function createConversation() {
    var data = await api("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-ID": state.userId },
      body: JSON.stringify({ user_id: state.userId, title: "新会话" })
    });
    state.conversations.unshift(data.conversation);
    renderSessions();
    return switchConversation(data.conversation.conversation_id);
  }

  async function switchConversation(conversationId) {
    if (!conversationId) return;
    if (state.controller) state.controller.abort();
    state.conversationId = conversationId;
    sessionStorage.setItem("ev_customer_conversation_id", conversationId);
    renderSessions();
    var values = await Promise.all([
      api("/api/conversations/" + encodeURIComponent(conversationId) + "/messages?user_id=" + encodeURIComponent(state.userId) + "&limit=500", { headers: { "X-User-ID": state.userId } }),
      refreshAttachments()
    ]);
    if (state.conversationId !== conversationId) return;
    state.messages = (values[0].messages || []).map(function (item) {
      return { role: item.role, content: item.content, status: item.status, final_response: item.final_response || null };
    });
    var latestFinal = state.messages.slice().reverse().find(function (item) { return item.final_response; });
    state.citations = latestFinal && latestFinal.final_response.citations || [];
    renderMessages();
  }

  function renderSessions() {
    var list = state.root.querySelector("[data-session-list]");
    list.textContent = "";
    state.conversations.slice(0, 30).forEach(function (item) {
      var row = document.createElement("div");
      row.className = "evc-session-row" + (item.conversation_id === state.conversationId ? " is-current" : "");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "evc-session-button";
      button.textContent = item.title || "新会话";
      button.title = button.textContent;
      button.setAttribute("aria-current", String(item.conversation_id === state.conversationId));
      button.addEventListener("click", function () { switchConversation(item.conversation_id).catch(showRequestError); });
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "evc-session-delete";
      remove.textContent = "×";
      remove.title = "删除对话";
      remove.setAttribute("aria-label", "删除对话“" + (item.title || "新会话") + "”");
      remove.addEventListener("click", function (event) {
        event.stopPropagation();
        deleteConversation(item).catch(showRequestError);
      });
      row.appendChild(button);
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  async function deleteConversation(item) {
    if (!item || !item.conversation_id) return;
    if (state.busy && item.conversation_id === state.conversationId) {
      global.alert("当前回答仍在生成，请完成后再删除这个对话。");
      return;
    }
    var title = item.title || "新会话";
    if (!global.confirm("删除对话“" + title + "”及其中的消息、附件和文档索引？此操作不可撤销。")) return;
    await api("/api/conversations/" + encodeURIComponent(item.conversation_id) + "?user_id=" + encodeURIComponent(state.userId), {
      method: "DELETE",
      headers: { "X-User-ID": state.userId }
    });
    if (item.conversation_id !== state.conversationId) {
      await refreshConversationListOnly();
      return;
    }
    clearTimeout(state.attachmentPoll);
    state.conversationId = null;
    state.messages = [];
    state.attachments = [];
    state.citations = [];
    state.deletingAttachments = {};
    sessionStorage.removeItem("ev_customer_conversation_id");
    closeContext();
    renderFiles();
    renderMessages();
    await refreshConversations();
  }

  function renderMessages() {
    var thread = state.root.querySelector("[data-thread]");
    thread.textContent = "";
    if (!state.messages.length) {
      var empty = document.createElement("section");
      empty.className = "evc-empty";
      empty.innerHTML = '<div class="evc-empty-mark" aria-hidden="true">EV</div><h1>有什么输变电工程问题需要处理？</h1><p>查询规范、总结文件，或处理当前模型中的业务对象。</p><div class="evc-suggestions"></div>';
      var suggestions = ["查询输变电工程模型规范", "总结刚上传的文件", "比较变电、架空和电缆模型层级", "检查当前模型属性"];
      suggestions.forEach(function (text) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "evc-chip";
        button.textContent = text;
        button.addEventListener("click", function () { focusComposer(text); });
        empty.querySelector(".evc-suggestions").appendChild(button);
      });
      thread.appendChild(empty);
      return;
    }
    state.messages.forEach(function (message) { appendMessageNode(message.role, message.content, { status: message.status }); });
    scrollBottom();
  }

  function appendMessageNode(role, content, options) {
    options = options || {};
    var thread = state.root.querySelector("[data-thread]");
    var article = document.createElement("article");
    article.className = "evc-message evc-message-" + (role === "user" ? "user" : "assistant");
    article.dataset.role = role;
    var body = document.createElement("div");
    body.className = "evc-message-body";
    var value = document.createElement("div");
    value.className = "evc-message-content";
    if (options.error) {
      value.classList.add("evc-error");
      value.textContent = content;
    } else if (role === "assistant") {
      renderMarkdown(value, content || "");
    } else {
      value.textContent = content || "";
    }
    body.appendChild(value);
    if (role === "assistant" && !options.pending) body.appendChild(messageTools(content, options.actions || [], options.downloads || []));
    article.appendChild(body);
    if (role === "user") article.appendChild(userMessageTools(content));
    thread.appendChild(article);
    article.__content = value;
    article.__body = body;
    scrollBottom();
    return article;
  }

  function userMessageTools(content) {
    var tools = document.createElement("div");
    tools.className = "evc-user-message-tools";
    var copy = document.createElement("button");
    copy.type = "button";
    copy.className = "evc-user-action";
    copy.setAttribute("aria-label", "复制问题");
    copy.title = "复制问题";
    copy.textContent = "⧉";
    copy.addEventListener("click", function () {
      copyText(content).then(function () { flashButton(copy, "✓", "已复制"); });
    });
    var edit = document.createElement("button");
    edit.type = "button";
    edit.className = "evc-user-action";
    edit.setAttribute("aria-label", "编辑后重新发送");
    edit.title = "编辑后重新发送";
    edit.textContent = "✎";
    edit.addEventListener("click", function () {
      focusComposer(content);
      var hint = state.root.querySelector("[data-composer-hint]");
      hint.textContent = "编辑后重新发送 · 原消息会保留";
      hint.classList.add("is-editing");
    });
    tools.appendChild(copy);
    tools.appendChild(edit);
    return tools;
  }

  function messageTools(content, actions, downloads) {
    var tools = document.createElement("div");
    tools.className = "evc-message-tools";
    var copy = document.createElement("button");
    copy.type = "button";
    copy.className = "evc-action-button";
    copy.textContent = "复制";
    copy.addEventListener("click", function () {
      copyText(content).then(function () { flashButton(copy, "已复制", "已复制"); });
    });
    tools.appendChild(copy);
    if (String(content || "").trim()) {
      [
        { format: "docx", label: "下载 Word" },
        { format: "markdown", label: "下载 Markdown" }
      ].forEach(function (item) {
        var exportButton = document.createElement("button");
        exportButton.type = "button";
        exportButton.className = "evc-action-button";
        exportButton.textContent = item.label;
        exportButton.title = "将这条回答生成文件";
        exportButton.addEventListener("click", function () {
          exportAnswer(content, item.format, exportButton).catch(showRequestError);
        });
        tools.appendChild(exportButton);
      });
    }
    (downloads || []).forEach(function (download) {
      var link = document.createElement("a");
      link.className = "evc-action-button";
      link.href = download.url;
      link.download = download.file_name;
      link.textContent = download.label;
      link.setAttribute("aria-label", download.label);
      tools.appendChild(link);
    });
    (actions || []).filter(function (action) {
      return !(action.kind === "capture_scene" && (downloads || []).length);
    }).forEach(function (action) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "evc-action-button";
      button.textContent = action.label;
      button.addEventListener("click", function () { executeBusinessActions([action]).catch(showRequestError); });
      tools.appendChild(button);
    });
    return tools;
  }

  async function sendMessage(text) {
    var query = String(text || "").trim();
    if (!query || state.busy || !state.conversationId) return;
    state.busy = true;
    setComposerBusy(true);
    state.root.querySelector("[data-input]").value = "";
    var composerHint = state.root.querySelector("[data-composer-hint]");
    composerHint.textContent = "Enter 发送 · Shift+Enter 换行";
    composerHint.classList.remove("is-editing");
    resizeComposer();
    if (state.messages.length === 0) state.root.querySelector("[data-thread]").textContent = "";
    state.messages.push({ role: "user", content: query });
    appendMessageNode("user", query);
    var assistant = appendMessageNode("assistant", "", { pending: true });
    assistant.classList.add("is-pending");
    assistant.__content.innerHTML = '<span aria-label="正在准备回答">•••</span>';
    state.controller = new AbortController();
    try {
      var selected = await selectedObject();
      var payload = {
        session_id: state.conversationId,
        conversation_id: state.conversationId,
        user_id: state.userId,
        role: "user",
        query: query,
        gis_context: { selected_object: selected || null, active_layers: [], camera_state: {} }
      };
      var final = await consumeCustomerStream(payload, assistant, state.controller.signal);
      if (!final) throw new Error("请求暂时未完成，请重试。");
      var hadBusinessActions = (final.business_actions || []).length > 0;
      var actionResult = await executeBusinessActions(final.business_actions || []);
      if (actionResult.error) {
        final.answer_markdown = actionResult.error;
        final.error = { message: actionResult.error };
        final.business_actions = [];
      } else if (actionResult.markdown) {
        final.answer_markdown = final.answer_markdown + "\n\n" + actionResult.markdown;
      }
      final.downloads = actionResult.downloads || [];
      finishAssistant(assistant, final);
      state.messages.push({ role: "assistant", content: final.answer_markdown, status: final.error ? "failed" : "success", final_response: final });
      if (hadBusinessActions) await persistBusinessResult(final);
      await refreshConversationListOnly();
      // History reload intentionally strips internal metadata; keep the
      // customer-safe citation cards for the answer currently on screen.
      state.citations = final.citations || [];
    } catch (error) {
      if (!(state.controller && state.controller.signal.aborted)) {
        finishAssistant(assistant, { answer_markdown: friendlyError(error), error: { message: friendlyError(error) }, business_actions: [] });
      }
    } finally {
      state.busy = false;
      state.controller = null;
      setComposerBusy(false);
      focusComposer();
    }
  }

  async function persistBusinessResult(final) {
    try {
      await api("/api/conversations/" + encodeURIComponent(state.conversationId) + "/business-result", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-User-ID": state.userId },
        body: JSON.stringify({
          user_id: state.userId,
          answer_markdown: final.answer_markdown,
          succeeded: !final.error
        })
      });
    } catch (ignore) {
      // Keep the verified result on screen even if history persistence is temporarily unavailable.
    }
  }

  async function refreshConversationListOnly() {
    var data = await api("/api/conversations?user_id=" + encodeURIComponent(state.userId), { headers: { "X-User-ID": state.userId } });
    state.conversations = data.conversations || [];
    renderSessions();
  }

  async function consumeCustomerStream(payload, assistant, signal) {
    var response = await fetch(state.baseUrl + "/api/agent/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(payload),
      signal: signal
    });
    if (!response.ok || !response.body) throw new Error("请求暂时未完成，请重试。");
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    var answer = "";
    var final = null;
    var metrics = { deltaCount: 0, firstDeltaAt: null, answerCompletedAt: null, finalResultAt: null };
    state.streamMetrics = metrics;
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
      var boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        var frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        var event = parseSSEFrame(frame);
        if (event && !PUBLIC_EVENT_NAMES[event.name]) {
          boundary = buffer.indexOf("\n\n");
          continue;
        }
        if (event && event.name === "answer_delta") {
          answer += String(event.data.delta || "");
          metrics.deltaCount += 1;
          metrics.firstDeltaAt = metrics.firstDeltaAt || Date.now();
          scheduleStreamingRender(assistant, answer);
        } else if (event && event.name === "citation_metadata") {
          state.citations = event.data.citations || [];
        } else if (event && event.name === "answer_completed" && !answer) {
          answer = String(event.data.answer_markdown || "");
          metrics.answerCompletedAt = Date.now();
          scheduleStreamingRender(assistant, answer);
        } else if (event && event.name === "answer_completed") {
          metrics.answerCompletedAt = Date.now();
        } else if (event && event.name === "user_facing_error") {
          answer = String(event.data.message || "请求暂时未完成，请重试。");
          scheduleStreamingRender(assistant, answer);
        } else if (event && event.name === "final_result") {
          final = event.data;
          metrics.finalResultAt = Date.now();
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (final && !final.answer_markdown) final.answer_markdown = answer;
    if (final && !final.citations) final.citations = state.citations;
    return final;
  }

  function scheduleStreamingRender(assistant, answer) {
    assistant.__streamAnswer = answer;
    if (assistant.__streamFrame) return;
    assistant.__streamFrame = global.requestAnimationFrame(function () {
      assistant.__streamFrame = null;
      assistant.classList.remove("is-pending");
      assistant.classList.add("is-streaming");
      assistant.__content.textContent = assistant.__streamAnswer || "";
      scrollBottom();
    });
  }

  function parseSSEFrame(frame) {
    if (!frame || frame.charAt(0) === ":") return null;
    var name = "message";
    var data = [];
    frame.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) name = line.slice(6).trim();
      if (line.indexOf("data:") === 0) data.push(line.slice(5).trim());
    });
    try { return { name: name, data: JSON.parse(data.join("\n") || "{}") }; }
    catch (ignore) { return null; }
  }

  function finishAssistant(node, final) {
    var content = String(final.answer_markdown || "");
    state.citations = final.citations || [];
    if (node.__streamFrame) {
      global.cancelAnimationFrame(node.__streamFrame);
      node.__streamFrame = null;
    }
    node.classList.remove("is-pending", "is-streaming");
    renderMarkdown(node.__content, content);
    var oldTools = node.__body.querySelector(".evc-message-tools");
    if (oldTools) oldTools.remove();
    node.__body.appendChild(messageTools(content, final.business_actions || [], final.downloads || []));
    if (final.error) node.__content.classList.add("evc-error");
    scrollBottom();
  }

  function copyText(value) {
    var text = String(value || "");
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy unavailable")); }
      catch (error) { reject(error); }
      finally { input.remove(); }
    });
  }

  function flashButton(button, label, title) {
    var original = button.textContent;
    var originalTitle = button.title;
    button.textContent = label;
    button.title = title;
    button.setAttribute("aria-label", title);
    global.setTimeout(function () {
      button.textContent = original;
      button.title = originalTitle;
      button.setAttribute("aria-label", originalTitle);
    }, 1100);
  }

  async function executeBusinessActions(actions) {
    actions = (actions || []).filter(function (item) { return item.auto_execute !== false; });
    if (!actions.length) return {};
    if (!global.AgentUIEventQueue || !global.WebGLAgentBridge) {
      return { error: "当前页面没有可用的场景，请在模型工作区中执行此操作。" };
    }
    var targetError = await validateBusinessActionTargets(actions);
    if (targetError) return { error: targetError };
    var cameraBefore = cameraPositionSnapshot();
    return new Promise(function (resolve) {
      var settled = false;
      var queue = new global.AgentUIEventQueue({
        bridge: global.WebGLAgentBridge,
        sourceAnchors: global.EVWebGLSourceAnchors,
        handlerTimeoutMs: 15000,
        onUpdate: function (item, items) {
          if (settled || !items.length || items.some(function (entry) { return ["queued", "pending", "executing"].indexOf(entry.status) >= 0; })) return;
          settled = true;
          var failed = items.find(function (entry) { return entry.status === "failed"; });
          if (failed) return resolve({ error: publicActionError(failed) });
          var effectError = validateBusinessActionEffects(actions, cameraBefore);
          if (effectError) return resolve({ error: effectError });
          resolve({ markdown: businessResultMarkdown(items), downloads: businessDownloads(items) });
        }
      });
      actions.forEach(function (action, index) {
        queue.enqueue({ type: actionEventType(action), payload: action.payload || {}, sequence: index + 1, event_id: "customer-action-" + Date.now() + "-" + index });
      });
    });
  }

  function cameraPositionSnapshot() {
    var camera = global.viewer && global.viewer.camera;
    var position = camera && (camera.positionWC || camera.position);
    if (!position) return null;
    var x = Number(position.x);
    var y = Number(position.y);
    var z = Number(position.z);
    return isFinite(x) && isFinite(y) && isFinite(z) ? { x: x, y: y, z: z } : null;
  }

  function validateBusinessActionEffects(actions, cameraBefore) {
    var requiresCameraMove = (actions || []).some(function (action) {
      return action.kind === "view_location" || action.kind === "view_admin_region";
    });
    if (!requiresCameraMove || !cameraBefore) return null;
    var cameraAfter = cameraPositionSnapshot();
    if (!cameraAfter) return "当前页面没有可用的场景，请在模型工作区中执行此操作。";
    var distanceSquared = Math.pow(cameraAfter.x - cameraBefore.x, 2) + Math.pow(cameraAfter.y - cameraBefore.y, 2) + Math.pow(cameraAfter.z - cameraBefore.z, 2);
    return distanceSquared > 0.0001 ? null : "当前场景中没有找到对应对象。";
  }

  async function validateBusinessActionTargets(actions) {
    var anchors = global.EVWebGLSourceAnchors;
    var names = [];
    (actions || []).forEach(function (action) {
      var payload = action.payload || {};
      if (["view_location", "highlight_object", "view_properties"].indexOf(action.kind) < 0) return;
      if (payload.object_id || payload.longitude !== undefined || payload.lon !== undefined || payload.lng !== undefined || payload.coordinates) return;
      var name = String(payload.object_name || payload.name || payload.target || "").trim();
      if (name && names.indexOf(name) < 0) names.push(name);
    });
    if (!names.length) return null;
    if (!anchors || typeof anchors.findBusinessObjectsByName !== "function") {
      return "当前页面没有可用的场景，请在模型工作区中执行此操作。";
    }
    for (var index = 0; index < names.length; index += 1) {
      var exact = await Promise.resolve(anchors.findBusinessObjectsByName(names[index], { exact: true }));
      var matches = Array.isArray(exact) ? exact : [];
      if (!matches.length) {
        var partial = await Promise.resolve(anchors.findBusinessObjectsByName(names[index], { exact: false }));
        matches = Array.isArray(partial) ? partial : [];
      }
      if (!matches.length) return "当前场景中没有找到对应对象。";
      if (matches.length > 1) return "当前场景中找到多个同名对象，请提供更具体的名称。";
    }
    return null;
  }

  function actionEventType(action) {
    var payload = action.payload || {};
    var hasCoordinates = payload.longitude !== undefined || payload.lon !== undefined || payload.lng !== undefined || Array.isArray(payload.coordinates);
    var map = {
      view_location: hasCoordinates ? "fly_to_coordinates" : "fly_to_object",
      view_admin_region: "locate_admin_region",
      highlight_object: "gis_highlight",
      clear_highlight: "clear_highlight",
      view_properties: "get_object_properties",
      view_admin_properties: "get_admin_region_properties",
      open_context: "open_panel",
      show_table: "show_table",
      reset_view: "reset_view",
      highlight_boundary: "highlight_admin_boundary",
      find_object: "search_business_objects",
      nearby_objects: "query_nearby_objects",
      create_range: "create_buffer",
      range_results: "query_objects_in_buffer",
      capture_scene: "screenshot"
    };
    return map[action.kind] || action.kind;
  }

  function businessResultMarkdown(items) {
    var propertyItem = items.find(function (item) { return item.type === "get_object_properties" || item.type === "get_admin_region_properties"; });
    if (!propertyItem) return "";
    var result = propertyItem.result && propertyItem.result.result || propertyItem.result || {};
    var properties = result.business_properties || result.standard_properties || result.properties || {};
    var keys = Object.keys(properties).filter(function (key) { return key.charAt(0) !== "_" && typeof properties[key] !== "object"; }).slice(0, 20);
    if (!keys.length) return "";
    return "| 属性 | 值 |\n|---|---|\n" + keys.map(function (key) { return "| " + tableText(key) + " | " + tableText(properties[key]) + " |"; }).join("\n");
  }

  function businessDownloads(items) {
    return (items || []).map(function (item) {
      var result = item.result && item.result.result || item.result || {};
      if (item.type !== "screenshot" || !result.data_url) return null;
      return {
        label: "下载截图",
        file_name: String(result.file_name || "EV-Agent-WebGL.png"),
        url: String(result.data_url)
      };
    }).filter(Boolean);
  }

  function publicActionError(item) {
    var text = String(item.error_code || item.error || "").toUpperCase();
    if (text.indexOf("NOT_FOUND") >= 0 || text.indexOf("NO_MATCH") >= 0) return "当前场景中没有找到对应对象。";
    return "请求暂时未完成，请重试。";
  }

  async function selectedObject() {
    try {
      if (global.EVWebGLSelectedObjectBridge && global.EVWebGLSelectedObjectBridge.getSelectedObject) {
        return await Promise.resolve(global.EVWebGLSelectedObjectBridge.getSelectedObject());
      }
    } catch (ignore) {}
    return null;
  }

  async function uploadFiles(files) {
    if (!state.conversationId || !files.length) return;
    for (var i = 0; i < files.length; i += 1) {
      renderUploading(files[i]);
      var form = new FormData();
      form.append("file", files[i], files[i].name);
      try {
        await api("/api/conversations/" + encodeURIComponent(state.conversationId) + "/attachments?user_id=" + encodeURIComponent(state.userId), {
          method: "POST", headers: { "X-User-ID": state.userId }, body: form
        });
      } catch (error) {
        showNotice(friendlyError(error));
      }
    }
    await refreshAttachments();
  }

  async function refreshAttachments() {
    if (!state.conversationId) return [];
    var conversationId = state.conversationId;
    var data = await api("/api/conversations/" + encodeURIComponent(conversationId) + "/attachments?user_id=" + encodeURIComponent(state.userId), { headers: { "X-User-ID": state.userId } });
    if (conversationId !== state.conversationId) return [];
    state.attachments = data.attachments || [];
    renderFiles();
    var panel = state.root.querySelector("[data-context]");
    if (panel && panel.dataset.mode === "files" && !panel.hidden) renderUploadedFilesPanel();
    clearTimeout(state.attachmentPoll);
    if (state.attachments.some(function (item) { return ["UPLOADED", "PARSING", "INDEXING"].indexOf(item.status) >= 0; })) {
      state.attachmentPoll = setTimeout(function () { refreshAttachments().catch(function () {}); }, 1200);
    }
    return state.attachments;
  }

  function renderUploading(file) {
    state.attachments = [{ filename: file.name, status: "UPLOADED", mime_type: file.type }].concat(state.attachments);
    renderFiles();
  }

  function renderFiles() {
    var root = state.root.querySelector("[data-files]");
    root.textContent = "";
    state.attachments.filter(function (item) { return item.status !== "DELETED"; }).slice(0, 4).forEach(function (item) {
      var deleting = !!state.deletingAttachments[item.attachment_id];
      var status = fileStatus(deleting ? "DELETING" : item.status);
      var card = document.createElement("div");
      card.className = "evc-file-card " + status.className;
      var extension = String(item.filename || "FILE").split(".").pop().slice(0, 4).toUpperCase();
      card.innerHTML = '<span class="evc-file-icon">' + escapeHtml(extension) + '</span><span class="evc-file-copy"><strong>' + escapeHtml(item.filename || "文件") + '</strong><span>' + escapeHtml(status.label) + '</span></span>';
      if (item.attachment_id) card.appendChild(attachmentDeleteButton(item, deleting));
      root.appendChild(card);
    });
  }

  function attachmentDeleteButton(item, disabled) {
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "evc-file-delete";
    remove.textContent = "×";
    remove.title = disabled ? "正在删除" : "删除文件";
    remove.setAttribute("aria-label", "删除文件“" + (item.filename || "文件") + "”");
    remove.disabled = disabled === true;
    remove.addEventListener("click", function () { deleteAttachment(item).catch(showRequestError); });
    return remove;
  }

  async function deleteAttachment(item) {
    if (!item || !item.attachment_id || state.deletingAttachments[item.attachment_id]) return;
    if (!global.confirm("删除文件“" + (item.filename || "文件") + "”及其文档索引？此操作不可撤销。")) return;
    state.deletingAttachments[item.attachment_id] = true;
    renderFiles();
    var panel = state.root.querySelector("[data-context]");
    if (panel && panel.dataset.mode === "files" && !panel.hidden) renderUploadedFilesPanel();
    try {
      await api("/api/conversations/" + encodeURIComponent(state.conversationId) + "/attachments/" + encodeURIComponent(item.attachment_id) + "?user_id=" + encodeURIComponent(state.userId), {
        method: "DELETE",
        headers: { "X-User-ID": state.userId }
      });
      state.attachments = state.attachments.filter(function (value) { return value.attachment_id !== item.attachment_id; });
    } finally {
      delete state.deletingAttachments[item.attachment_id];
      await refreshAttachments();
    }
  }

  async function openKnowledgeLibrary() {
    var panel = openContextPanel("knowledge", "输变电知识库");
    var content = state.root.querySelector("[data-context-content]");
    content.innerHTML = '<div class="evc-context-loading">正在读取知识库文件…</div>';
    var data = await api("/api/knowledge/sources");
    state.knowledgeSources = data.sources || [];
    content.textContent = "";
    var summary = document.createElement("p");
    summary.className = "evc-library-summary";
    summary.textContent = "共 " + state.knowledgeSources.length + " 个文件，均可用于当前知识问答。";
    content.appendChild(summary);
    var list = document.createElement("div");
    list.className = "evc-library-list";
    state.knowledgeSources.forEach(function (item) {
      list.appendChild(libraryFileNode(item.filename, item.file_type, item.size_bytes, "已就绪"));
    });
    content.appendChild(list);
    panel.querySelector("[data-context-close]").focus();
  }

  async function openUploadedFiles() {
    openContextPanel("files", "已上传文件");
    await refreshAttachments();
    renderUploadedFilesPanel();
  }

  function renderUploadedFilesPanel() {
    var content = state.root.querySelector("[data-context-content]");
    content.textContent = "";
    var upload = document.createElement("button");
    upload.type = "button";
    upload.className = "evc-library-upload";
    upload.textContent = "上传文件";
    upload.addEventListener("click", function () { state.root.querySelector("[data-file-input]").click(); });
    content.appendChild(upload);
    var active = state.attachments.filter(function (item) { return item.status !== "DELETED"; });
    if (!active.length) {
      var empty = document.createElement("p");
      empty.className = "evc-library-empty";
      empty.textContent = "当前会话还没有上传文件。";
      content.appendChild(empty);
      return;
    }
    var list = document.createElement("div");
    list.className = "evc-library-list";
    active.forEach(function (item) {
      var deleting = !!state.deletingAttachments[item.attachment_id];
      var status = fileStatus(deleting ? "DELETING" : item.status);
      list.appendChild(libraryFileNode(
        item.filename,
        String(item.filename || "").split(".").pop(),
        item.size,
        status.label,
        function () { return deleteAttachment(item); },
        deleting
      ));
    });
    content.appendChild(list);
  }

  function openSettings() {
    openContextPanel("settings", "界面设置");
    if (state.root.classList.contains("is-narrow")) setSidebarCollapsed(true);
    var content = state.root.querySelector("[data-context-content]");
    content.innerHTML =
      '<div class="evc-settings">' +
        '<section class="evc-setting-group"><h3>界面颜色</h3><div class="evc-color-swatches" data-accent-swatches></div></section>' +
        '<section class="evc-setting-group"><h3>字体大小</h3><div class="evc-segmented" role="group" aria-label="字体大小">' +
          '<button type="button" data-font-size="14px">小</button><button type="button" data-font-size="15px">标准</button><button type="button" data-font-size="17px">大</button>' +
        '</div></section>' +
        '<section class="evc-setting-group"><label for="evc-text-color"><span>正文字体颜色</span></label>' +
          '<div class="evc-color-field"><select id="evc-text-preset" aria-label="正文字体颜色预设">' +
            '<option value="#172033">默认</option><option value="#0f172a">深色</option><option value="#475569">浅灰</option><option value="custom">自定义</option>' +
          '</select><input id="evc-text-color" type="color" aria-label="选择自定义正文字体颜色" value="' + escapeHtml(state.settings.textColor) + '"></div>' +
          '<p class="evc-setting-feedback" data-setting-feedback aria-live="polite"></p>' +
        '</section>' +
        '<section class="evc-setting-actions"><button type="button" data-reset-settings>恢复默认设置</button><button type="button" data-reset-window>恢复默认位置和大小</button></section>' +
      '</div>';
    var swatches = content.querySelector("[data-accent-swatches]");
    ACCENT_PRESETS.forEach(function (color, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "evc-color-swatch";
      button.style.setProperty("--swatch", color);
      button.dataset.accent = color;
      button.setAttribute("aria-label", ["默认蓝", "深蓝", "青色", "紫色", "绿色"][index]);
      button.title = button.getAttribute("aria-label");
      button.addEventListener("click", function () {
        applySettings(Object.assign({}, state.settings, { accent: color }), true);
        syncSettingsControls(content);
      });
      swatches.appendChild(button);
    });
    content.querySelectorAll("[data-font-size]").forEach(function (button) {
      button.addEventListener("click", function () {
        applySettings(Object.assign({}, state.settings, { fontSize: button.dataset.fontSize }), true);
        syncSettingsControls(content);
      });
    });
    var preset = content.querySelector("#evc-text-preset");
    var picker = content.querySelector("#evc-text-color");
    preset.addEventListener("change", function () {
      if (preset.value === "custom") { picker.focus(); return; }
      applyTextColor(preset.value, content);
    });
    picker.addEventListener("input", function () {
      preset.value = "custom";
      applyTextColor(picker.value, content);
    });
    content.querySelector("[data-reset-settings]").addEventListener("click", function () {
      applySettings(Object.assign({}, DEFAULT_SETTINGS), true);
      syncSettingsControls(content);
      content.querySelector("[data-setting-feedback]").textContent = "已恢复默认设置";
    });
    content.querySelector("[data-reset-window]").addEventListener("click", function () {
      resetWindowLayout();
      content.querySelector("[data-setting-feedback]").textContent = "已恢复默认布局";
    });
    syncSettingsControls(content);
  }

  function loadSettings() {
    try {
      var stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE) || "null");
      return normalizeSettings(stored || DEFAULT_SETTINGS);
    } catch (ignore) {
      return Object.assign({}, DEFAULT_SETTINGS);
    }
  }

  function normalizeSettings(value) {
    value = value || {};
    return {
      accent: validHexColor(value.accent) ? value.accent.toLowerCase() : DEFAULT_SETTINGS.accent,
      fontSize: ["14px", "15px", "17px"].indexOf(value.fontSize) >= 0 ? value.fontSize : DEFAULT_SETTINGS.fontSize,
      textColor: validHexColor(value.textColor) && contrastRatio(value.textColor, "#ffffff") >= 4.5 ? value.textColor.toLowerCase() : DEFAULT_SETTINGS.textColor
    };
  }

  function applySettings(value, persist) {
    var next = normalizeSettings(value);
    state.settings = next;
    if (state.root) {
      state.root.style.setProperty("--agent-accent", next.accent);
      state.root.style.setProperty("--agent-accent-hover", shadeHex(next.accent, -.12));
      state.root.style.setProperty("--agent-accent-soft", hexRgba(next.accent, .11));
      state.root.style.setProperty("--agent-font-size", next.fontSize);
      state.root.style.setProperty("--agent-text-color", next.textColor);
    }
    if (persist) localStorage.setItem(SETTINGS_STORAGE, JSON.stringify(next));
    return next;
  }

  function applyTextColor(color, content) {
    var feedback = content.querySelector("[data-setting-feedback]");
    if (!validHexColor(color) || contrastRatio(color, "#ffffff") < 4.5) {
      feedback.textContent = "该颜色与白色背景对比度不足，请选择更深的颜色。";
      content.querySelector("#evc-text-color").value = state.settings.textColor;
      return;
    }
    feedback.textContent = "设置已保存";
    applySettings(Object.assign({}, state.settings, { textColor: color }), true);
  }

  function syncSettingsControls(content) {
    content.querySelectorAll("[data-accent]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.accent.toLowerCase() === state.settings.accent));
    });
    content.querySelectorAll("[data-font-size]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.fontSize === state.settings.fontSize));
    });
    var preset = content.querySelector("#evc-text-preset");
    var picker = content.querySelector("#evc-text-color");
    var known = ["#172033", "#0f172a", "#475569"];
    preset.value = known.indexOf(state.settings.textColor) >= 0 ? state.settings.textColor : "custom";
    picker.value = state.settings.textColor;
  }

  function validHexColor(value) { return /^#[0-9a-f]{6}$/i.test(String(value || "")); }
  function hexRgb(value) {
    var hex = String(value).slice(1);
    return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
  }
  function hexRgba(value, alpha) { var rgb = hexRgb(value); return "rgba(" + rgb.join(",") + "," + alpha + ")"; }
  function shadeHex(value, amount) {
    var rgb = hexRgb(value).map(function (channel) { return Math.max(0, Math.min(255, Math.round(channel * (1 + amount)))); });
    return "#" + rgb.map(function (channel) { return channel.toString(16).padStart(2, "0"); }).join("");
  }
  function contrastRatio(foreground, background) {
    function luminance(value) {
      var channels = hexRgb(value).map(function (item) { var c = item / 255; return c <= .03928 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4); });
      return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
    }
    var first = luminance(foreground); var second = luminance(background);
    return (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
  }

  function libraryFileNode(filename, type, size, status, onDelete, deleting) {
    var row = document.createElement("article");
    row.className = "evc-library-file" + (onDelete ? " has-action" : "");
    var icon = document.createElement("span");
    icon.className = "evc-library-file-icon";
    icon.textContent = String(type || "FILE").slice(0, 4).toUpperCase();
    var copy = document.createElement("span");
    copy.className = "evc-library-file-copy";
    var title = document.createElement("strong");
    title.textContent = filename || "文件";
    title.title = title.textContent;
    var meta = document.createElement("span");
    meta.textContent = [formatFileSize(size), status].filter(Boolean).join(" · ");
    copy.appendChild(title);
    copy.appendChild(meta);
    row.appendChild(icon);
    row.appendChild(copy);
    if (onDelete) {
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "evc-library-delete";
      remove.textContent = deleting ? "删除中…" : "删除";
      remove.title = deleting ? "正在删除" : "删除文件";
      remove.setAttribute("aria-label", "删除文件“" + (filename || "文件") + "”");
      remove.disabled = deleting === true;
      remove.addEventListener("click", function () { Promise.resolve(onDelete()).catch(showRequestError); });
      row.appendChild(remove);
    }
    return row;
  }

  function formatFileSize(value) {
    var size = Number(value || 0);
    if (!size) return "";
    if (size >= 1024 * 1024) return (size / 1024 / 1024).toFixed(size >= 10 * 1024 * 1024 ? 0 : 1) + " MB";
    if (size >= 1024) return Math.round(size / 1024) + " KB";
    return size + " B";
  }

  async function exportAnswer(content, format, button) {
    if (!state.conversationId || !String(content || "").trim()) return;
    var original = button.textContent;
    button.disabled = true;
    button.textContent = "正在生成…";
    try {
      var current = state.conversations.find(function (item) { return item.conversation_id === state.conversationId; });
      var data = await api("/api/conversations/" + encodeURIComponent(state.conversationId) + "/artifacts", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-ID": state.userId },
        body: JSON.stringify({
          user_id: state.userId,
          format: format,
          title: current && current.title && current.title !== "新会话" ? current.title : "EV Agent 回答",
          content_markdown: String(content)
        })
      });
      var artifact = data.artifact || {};
      if (!artifact.download_url) throw new Error("文件暂时无法生成，请重试。");
      var link = document.createElement("a");
      link.href = state.baseUrl + artifact.download_url;
      link.download = artifact.filename || "EV Agent 回答";
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      button.textContent = "已生成";
    } finally {
      global.setTimeout(function () { button.disabled = false; button.textContent = original; }, 1200);
    }
  }

  function fileStatus(status) {
    var map = {
      UPLOADED: ["正在上传…", ""], PARSING: ["正在解析…", ""], INDEXING: ["正在解析…", ""],
      ACTIVE: ["已就绪，可以开始提问", "evc-file-ready"], OCR_REQUIRED: ["暂时无法识别此文件", "evc-file-failed"],
      FAILED: ["解析失败，请重新上传", "evc-file-failed"], DELETING: ["正在删除…", ""],
      DELETE_FAILED: ["删除失败，请重试", "evc-file-failed"]
    };
    var value = map[status] || ["处理中…", ""];
    return { label: value[0], className: value[1] };
  }

  function renderMarkdown(root, markdown) {
    root.textContent = "";
    var lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    var index = 0;
    while (index < lines.length) {
      var line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      if (/^```/.test(line)) {
        var language = line.slice(3).trim();
        var codeLines = [];
        index += 1;
        while (index < lines.length && !/^```/.test(lines[index])) { codeLines.push(lines[index]); index += 1; }
        index += 1;
        var pre = document.createElement("pre");
        var code = document.createElement("code");
        if (language) code.dataset.language = language;
        code.textContent = codeLines.join("\n");
        pre.appendChild(code); root.appendChild(pre); continue;
      }
      if (index + 1 < lines.length && isTableSeparator(lines[index + 1]) && hasTableDivider(line)) {
        var tableLines = [line, lines[index + 1]];
        index += 2;
        while (index < lines.length && hasTableDivider(lines[index]) && lines[index].trim()) { tableLines.push(lines[index]); index += 1; }
        root.appendChild(markdownTable(tableLines)); continue;
      }
      var heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) { var h = document.createElement("h" + heading[1].length); setInline(h, heading[2]); root.appendChild(h); index += 1; continue; }
      if (/^>\s?/.test(line)) { var quote = document.createElement("blockquote"); setInline(quote, line.replace(/^>\s?/, "")); root.appendChild(quote); index += 1; continue; }
      if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
        var ordered = /^\s*\d+[.)]\s+/.test(line);
        var list = document.createElement(ordered ? "ol" : "ul");
        while (index < lines.length && (ordered ? /^\s*\d+[.)]\s+/.test(lines[index]) : /^\s*[-*+]\s+/.test(lines[index]))) {
          var li = document.createElement("li");
          setInline(li, lines[index].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/, ""));
          list.appendChild(li); index += 1;
        }
        root.appendChild(list); continue;
      }
      var paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) { paragraph.push(lines[index]); index += 1; }
      var p = document.createElement("p"); setInline(p, paragraph.join("\n")); root.appendChild(p);
    }
    root.querySelectorAll("[data-citation]").forEach(function (button) {
      button.addEventListener("click", function () { openCitation(Number(button.dataset.citation)); });
    });
  }

  function isBlockStart(lines, index) {
    var line = lines[index];
    return /^```|^#{1,3}\s|^>\s?|^\s*[-*+]\s+|^\s*\d+[.)]\s+/.test(line) || (index + 1 < lines.length && isTableSeparator(lines[index + 1]) && hasTableDivider(line));
  }

  function setInline(node, text) {
    var html = escapeHtml(text).replace(/\n/g, "<br>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\[(\d+)\]/g, '<button type="button" class="evc-citation-ref" data-citation="$1" aria-label="查看来源 $1">[$1]</button>');
    node.innerHTML = html;
  }

  function markdownTable(lines) {
    var wrap = document.createElement("div"); wrap.className = "evc-table-wrap";
    var table = document.createElement("table");
    var head = document.createElement("thead"); var body = document.createElement("tbody");
    var headers = splitTableRow(lines[0]); var alignments = splitTableRow(lines[1]).map(tableAlignment); var row = document.createElement("tr");
    headers.forEach(function (value, index) { var cell = document.createElement("th"); if (alignments[index]) cell.style.textAlign = alignments[index]; setInline(cell, value); row.appendChild(cell); });
    head.appendChild(row);
    lines.slice(2).forEach(function (line) {
      var tr = document.createElement("tr"); var values = splitTableRow(line);
      headers.forEach(function (_, index) { var td = document.createElement("td"); if (alignments[index]) td.style.textAlign = alignments[index]; setInline(td, values[index] || ""); tr.appendChild(td); });
      body.appendChild(tr);
    });
    table.appendChild(head); table.appendChild(body); wrap.appendChild(table); return wrap;
  }

  function hasTableDivider(line) { return /[|｜]/.test(String(line || "")); }
  function splitTableRow(line) {
    var text = String(line || "").trim().replace(/｜/g, "|");
    if (text.charAt(0) === "|") text = text.slice(1);
    if (text.charAt(text.length - 1) === "|" && text.charAt(text.length - 2) !== "\\") text = text.slice(0, -1);
    var cells = []; var cell = ""; var escaped = false; var inCode = false;
    for (var index = 0; index < text.length; index += 1) {
      var char = text.charAt(index);
      if (escaped) { cell += char; escaped = false; continue; }
      if (char === "\\") { escaped = true; cell += char; continue; }
      if (char === "`") { inCode = !inCode; cell += char; continue; }
      if (char === "|" && !inCode) { cells.push(cell.trim().replace(/\\\|/g, "|")); cell = ""; continue; }
      cell += char;
    }
    cells.push(cell.trim().replace(/\\\|/g, "|"));
    return cells;
  }
  function isTableSeparator(line) {
    if (!hasTableDivider(line)) return false;
    var cells = splitTableRow(line);
    return cells.length >= 2 && cells.every(function (value) { return /^:?-{3,}:?$/.test(value.replace(/\s/g, "")); });
  }
  function tableAlignment(value) {
    var text = String(value || "").replace(/\s/g, "");
    if (/^:-+:$/.test(text)) return "center";
    if (/^-+:$/.test(text)) return "right";
    if (/^:-+$/.test(text)) return "left";
    return "";
  }

  function openCitation(index) {
    var item = state.citations.find(function (citation) { return Number(citation.index) === index; });
    if (!item) return;
    var panel = state.root.querySelector("[data-context]");
    panel.dataset.mode = "citation";
    state.root.querySelector("[data-context-title]").textContent = "引用来源";
    var content = state.root.querySelector("[data-context-content]");
    content.innerHTML = '<article class="evc-citation-card"><h3>' + escapeHtml(item.title || item.source_name) + '</h3><p>' + escapeHtml(item.source_name || "") + '</p><p>' + escapeHtml([item.section, item.page ? "第 " + item.page + " 页" : ""].filter(Boolean).join(" · ")) + '</p>' + (item.excerpt ? '<blockquote>' + escapeHtml(item.excerpt) + '</blockquote>' : '') + '</article>';
    panel.hidden = false;
    panel.querySelector("[data-context-close]").focus();
  }
  function openContextPanel(mode, title) {
    var panel = state.root.querySelector("[data-context]");
    panel.dataset.mode = mode;
    panel.hidden = false;
    state.root.querySelector("[data-context-title]").textContent = title;
    return panel;
  }
  function closeContext() {
    var panel = state.root.querySelector("[data-context]");
    panel.hidden = true;
    panel.dataset.mode = "";
  }

  function focusComposer(value) {
    var input = state.root.querySelector("[data-input]");
    if (value !== undefined) input.value = value;
    resizeComposer(); input.focus();
  }
  function resizeComposer() { var input = state.root.querySelector("[data-input]"); input.style.height = "auto"; input.style.height = Math.min(180, Math.max(38, input.scrollHeight)) + "px"; }
  function setComposerBusy(busy) { state.root.querySelector("[data-send]").disabled = busy; state.root.querySelector("[data-input]").disabled = busy; }
  function scrollBottom() { var area = state.root.querySelector("[data-conversation]"); requestAnimationFrame(function () { area.scrollTop = area.scrollHeight; }); }
  function showNotice(message) { if (!state.messages.length) state.root.querySelector("[data-thread]").textContent = ""; appendMessageNode("assistant", message, { error: false }); }
  function showRequestError(error) { showNotice(friendlyError(error)); }
  function friendlyError(error) {
    var message = String(error && error.message || error || "");
    if (/Failed to fetch|NetworkError|Load failed|network request/i.test(message)) return "请求暂时未完成，请重试。";
    return message && !/[A-Z_]{3,}|Traceback|Error:|\b\d{3}\b/.test(message) ? message : "请求暂时未完成，请重试。";
  }
  function escapeHtml(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]; }); }
  function tableText(value) { return String(value == null ? "-" : value).replace(/\|/g, "\\|").replace(/\r?\n/g, " "); }

  global.EVCustomerAgentApp = { start: start, state: state, renderMarkdown: renderMarkdown };
})(window);
