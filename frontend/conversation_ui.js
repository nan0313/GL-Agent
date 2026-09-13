(function (global) {
  "use strict";

  var runtime = { client: null, attachments: null, conversationId: null, root: null, select: null, attachmentList: null, attachmentCards: null, scopeNode: null, fileInput: null, poll: null, lastItems: [] };

  function ready() {
    return global.EVAgentBootstrap && global.EVAgentBootstrap.state && global.EVAgentBootstrap.state.panel && global.EVConversationClient && global.EVAttachmentClient;
  }

  function initialize() {
    if (!ready()) {
      setTimeout(initialize, 80);
      return;
    }
    if (document.getElementById("ev-conversation-toolbar")) return;
    var config = global.EV_AGENT_CONFIG || {};
    var userId = String(config.userId || "webgl-user");
    var baseUrl = String(config.agentBaseUrl || "");
    runtime.client = new global.EVConversationClient({ baseUrl: baseUrl, userId: userId });
    runtime.attachments = new global.EVAttachmentClient({ baseUrl: baseUrl, userId: userId });
    buildToolbar(global.EVAgentBootstrap.state.panel);
    refreshConversations(sessionStorage.getItem("ev_agent_session_id"));
    global.addEventListener("agent:conversation-updated", function (event) {
      if (event.detail && event.detail.conversation_id === runtime.conversationId) refreshConversations(runtime.conversationId, false);
    });
  }

  function buildToolbar(panel) {
    installStyles();
    var root = document.createElement("div");
    root.id = "ev-conversation-toolbar";
    root.className = "ev-conversation-toolbar";
    var row = document.createElement("div");
    row.className = "ev-conversation-row";
    var select = document.createElement("select");
    select.setAttribute("aria-label", "当前会话");
    select.addEventListener("change", function () { switchConversation(select.value); });
    row.appendChild(select);
    row.appendChild(button("新建", function () { createConversation(); }));
    row.appendChild(button("重命名", renameConversation));
    row.appendChild(button("删除", deleteConversation));
    var fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".pdf,.docx,.txt,.md,.markdown";
    fileInput.hidden = true;
    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      if (file) uploadAttachment(file).finally(function () { fileInput.value = ""; });
    });
    row.appendChild(button("上传文件", function () { fileInput.click(); }));
    row.appendChild(fileInput);
    var attachments = document.createElement("div");
    attachments.className = "ev-attachment-list";
    root.appendChild(row);
    root.appendChild(attachments);
    var slot = panel.querySelector("[data-conversation-slot]");
    (slot || panel).appendChild(root);
    runtime.root = root;
    runtime.select = select;
    runtime.attachmentList = attachments;
    runtime.attachmentCards = panel.querySelector("[data-attachment-cards]");
    runtime.scopeNode = panel.querySelector("[data-document-scope]");
    runtime.fileInput = fileInput;
    Array.prototype.forEach.call(panel.querySelectorAll("[data-attach-trigger]"), function (trigger) {
      trigger.addEventListener("click", function () { fileInput.click(); });
    });
  }

  function refreshConversations(preferred, switchAfter) {
    return runtime.client.list().then(function (items) {
      if (!items.length) return runtime.client.create("新会话").then(function (created) { items = [created]; return items; });
      return items;
    }).then(function (items) {
      var current = preferred && items.some(function (item) { return item.conversation_id === preferred; }) ? preferred : items[0].conversation_id;
      runtime.select.textContent = "";
      items.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.conversation_id;
        option.textContent = item.title + " · " + item.message_count;
        runtime.select.appendChild(option);
      });
      runtime.select.value = current;
      if (switchAfter !== false && current !== runtime.conversationId) return switchConversation(current);
      return items;
    }).catch(showError);
  }

  function switchConversation(conversationId) {
    if (!conversationId) return Promise.resolve();
    var state = global.EVAgentBootstrap.state;
    if (state.currentController) state.currentController.abort();
    runtime.conversationId = conversationId;
    sessionStorage.setItem("ev_agent_session_id", conversationId);
    state.dialogueState = null;
    state.buffers = [];
    state.bufferQueries = [];
    state.panelRuntime.currentObject = null;
    state.panelRuntime.currentAdminRegion = null;
    runtime.select.value = conversationId;
    return Promise.all([runtime.client.messages(conversationId), refreshAttachments()]).then(function (values) {
      if (runtime.conversationId !== conversationId) return;
      global.EVAgentBootstrap.loadConversationMessages(values[0], conversationId);
    }).catch(showError);
  }

  function createConversation() {
    return runtime.client.create("新会话").then(function (created) {
      return refreshConversations(created.conversation_id);
    }).catch(showError);
  }

  function renameConversation() {
    if (!runtime.conversationId) return;
    var current = runtime.select.options[runtime.select.selectedIndex];
    var title = global.prompt("新的会话名称", current ? current.textContent.split(" · ")[0] : "");
    if (!title || !title.trim()) return;
    runtime.client.rename(runtime.conversationId, title.trim()).then(function () { refreshConversations(runtime.conversationId, false); }).catch(showError);
  }

  function deleteConversation() {
    if (!runtime.conversationId || !global.confirm("删除该会话、消息、附件及其独立索引？此操作不可撤销。")) return;
    runtime.client.remove(runtime.conversationId).then(function () {
      runtime.conversationId = null;
      sessionStorage.removeItem("ev_agent_session_id");
      return refreshConversations(null);
    }).catch(showError);
  }

  function uploadAttachment(file) {
    if (!runtime.conversationId) return Promise.resolve();
    renderUploadingFile(file);
    return runtime.attachments.upload(runtime.conversationId, file).then(function () { return refreshAttachments(); }).catch(showError);
  }

  function refreshAttachments() {
    var conversationId = runtime.conversationId;
    if (!conversationId) return Promise.resolve([]);
    return runtime.attachments.list(conversationId).then(function (items) {
      if (runtime.conversationId !== conversationId) return items;
      renderAttachments(items);
      runtime.lastItems = items.slice();
      var pending = items.some(function (item) { return ["UPLOADED", "PARSING", "INDEXING", "DELETING"].indexOf(item.status) >= 0; });
      clearTimeout(runtime.poll);
      if (pending) runtime.poll = setTimeout(refreshAttachments, 1000);
      global.dispatchEvent(new CustomEvent("agent:attachments-updated", { detail: { conversation_id: conversationId, attachments: items } }));
      return items;
    });
  }

  function renderAttachments(items) {
    runtime.attachmentList.textContent = "";
    if (runtime.attachmentCards) {
      runtime.attachmentCards.textContent = "";
      runtime.attachmentCards.classList.remove("is-friendly-error");
      runtime.attachmentCards.hidden = !items.length;
    }
    if (!items.length) {
      setAttachmentStatus("当前会话暂无文件");
      updateScope([]);
      return;
    }
    items.forEach(function (item) {
      if (item.status === "DELETED") return;
      var status = attachmentStatus(item);
      var chip = document.createElement("span");
      chip.className = "ev-attachment-chip";
      chip.title = item.error_code || item.status;
      var label = document.createElement("span");
      label.textContent = item.filename + " · " + item.status;
      chip.appendChild(label);
      var remove = button("×", function () {
        if (!global.confirm("删除会话附件“" + item.filename + "”及其索引？")) return;
        runtime.attachments.remove(runtime.conversationId, item.attachment_id).then(refreshAttachments).catch(showError);
      });
      remove.title = "删除附件";
      chip.appendChild(remove);
      runtime.attachmentList.appendChild(chip);
      if (runtime.attachmentCards) runtime.attachmentCards.appendChild(attachmentCard(item, status));
    });
    updateScope(items);
  }

  function attachmentCard(item, status) {
    var card = document.createElement("div");
    card.className = "ev-file-card status-" + String(item.status || "").toLowerCase();
    card.dataset.attachmentId = item.attachment_id;
    var icon = document.createElement("span");
    icon.className = "ev-file-icon";
    icon.textContent = fileTypeLabel(item.filename);
    var summary = document.createElement("div");
    summary.className = "ev-file-summary";
    var name = document.createElement("strong");
    name.textContent = item.filename;
    var state = document.createElement("span");
    state.className = "ev-file-state";
    state.textContent = status.icon + " " + status.label + elapsedLabel(item, status.pending);
    summary.appendChild(name);
    summary.appendChild(state);
    card.appendChild(icon);
    card.appendChild(summary);
    if (status.pending) {
      var progress = document.createElement("span");
      progress.className = "ev-file-progress";
      progress.setAttribute("aria-label", status.label);
      card.appendChild(progress);
    }
    return card;
  }

  function renderUploadingFile(file) {
    if (!runtime.attachmentCards) return;
    runtime.attachmentCards.hidden = false;
    runtime.attachmentCards.textContent = "";
    runtime.attachmentCards.appendChild(attachmentCard({ filename: file.name, status: "UPLOADED", created_at: new Date().toISOString() }, { icon: "↑", label: "正在上传文件", pending: true }));
    if (runtime.scopeNode) runtime.scopeNode.textContent = "基于：输变电知识库 + 正在上传的文件";
  }

  function attachmentStatus(item) {
    var values = {
      UPLOADED: { icon: "↑", label: "已上传 · 等待解析", pending: true },
      PARSING: { icon: "◌", label: "正在解析文件", pending: true },
      INDEXING: { icon: "◌", label: "正在生成知识索引", pending: true },
      ACTIVE: { icon: "✓", label: "文件已就绪，可以开始提问", pending: false },
      OCR_REQUIRED: { icon: "!", label: "该文件需要 OCR 才能识别", pending: false },
      FAILED: { icon: "!", label: "文件解析失败，请重新上传", pending: false },
      DELETING: { icon: "◌", label: "正在删除文件", pending: true },
      DELETE_FAILED: { icon: "!", label: "文件删除失败", pending: false }
    };
    return values[item.status] || { icon: "•", label: String(item.status || "状态未知"), pending: false };
  }

  function elapsedLabel(item, pending) {
    if (!pending || !item.created_at) return "";
    var started = new Date(item.created_at).getTime();
    if (!Number.isFinite(started)) return " · 完成后即可提问";
    var seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    return " · 已用 " + seconds + " 秒 · 完成后即可提问";
  }

  function updateScope(items) {
    if (!runtime.scopeNode) return;
    var active = items.filter(function (item) { return item.status === "ACTIVE"; });
    var pending = items.some(function (item) { return ["UPLOADED", "PARSING", "INDEXING"].indexOf(item.status) >= 0; });
    runtime.scopeNode.textContent = "基于：输变电知识库" + (active.length ? " + " + (active.length === 1 ? active[0].filename : active.length + " 个当前文件") : pending ? " + 正在处理的文件" : "");
  }

  function fileTypeLabel(filename) {
    var match = String(filename || "").match(/\.([^.]+)$/);
    return match ? match[1].slice(0, 4).toUpperCase() : "FILE";
  }

  function setAttachmentStatus(text) {
    runtime.attachmentList.textContent = text;
  }

  function showError(error) {
    var code = String(error && (error.code || error.message) || error);
    var friendly = ({
      ATTACHMENT_DUPLICATE: "文件已存在，无需重复上传",
      ATTACHMENT_TOO_LARGE: "文件过大，无法上传",
      ATTACHMENT_UNSUPPORTED_FILE_TYPE: "暂不支持该文件类型",
      ATTACHMENT_EMPTY_FILE: "文件内容为空，无法上传"
    })[code] || "文件操作失败，请稍后重试";
    if (runtime.attachmentCards) {
      runtime.attachmentCards.hidden = false;
      runtime.attachmentCards.textContent = friendly;
      runtime.attachmentCards.classList.add("is-friendly-error");
    }
    setAttachmentStatus(friendly + "（" + code + "）");
    global.dispatchEvent(new CustomEvent("agent:attachment-error", { detail: { code: code, message: friendly } }));
    if (global.console && console.warn) console.warn("[EV Attachment]", code);
  }

  function button(label, handler) {
    var node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    node.addEventListener("click", handler);
    return node;
  }

  function installStyles() {
    if (document.getElementById("ev-conversation-style")) return;
    var style = document.createElement("style");
    style.id = "ev-conversation-style";
    style.textContent = ".ev-conversation-toolbar{display:flex;flex-direction:column;gap:8px;padding:10px 0}.ev-conversation-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.ev-conversation-row select{min-width:150px;max-width:100%;flex:1;background:#101f35;color:#e9f1fb;border:1px solid rgba(122,160,205,.22);border-radius:8px;padding:7px}.ev-attachment-list{display:flex;gap:5px;flex-wrap:wrap;color:#91a5bf;font-size:11px}.ev-attachment-chip{display:inline-flex;align-items:center;gap:3px;border-radius:12px;padding:3px 5px 3px 8px;max-width:100%;background:#162942}.ev-attachment-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ev-attachment-chip button{min-height:18px!important;padding:0 5px!important;border-radius:50%!important}";
    document.head.appendChild(style);
  }

  global.EVConversationUI = {
    initialize: initialize,
    refresh: refreshConversations,
    create: createConversation,
    openFilePicker: function () { if (runtime.fileInput) runtime.fileInput.click(); },
    focusHistory: function () { if (runtime.select) runtime.select.focus(); },
    currentConversationId: function () { return runtime.conversationId; },
    attachments: function () { return runtime.lastItems.slice(); }
  };
  initialize();
})(window);
