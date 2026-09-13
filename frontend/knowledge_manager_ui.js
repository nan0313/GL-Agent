(function (global) {
  "use strict";

  var runtime = { baseUrl: "", role: "developer", dialog: null, list: null, status: null, poll: null };

  function initialize() {
    var bootstrap = global.EVAgentBootstrap;
    if (!bootstrap || !bootstrap.state || !bootstrap.state.panel) {
      setTimeout(initialize, 100);
      return;
    }
    if (document.getElementById("ev-knowledge-manager")) return;
    var config = global.EV_AGENT_CONFIG || {};
    runtime.baseUrl = String(config.agentBaseUrl || "").replace(/\/$/, "");
    runtime.role = String(config.role || "developer");
    installStyles();
    var launch = bootstrap.state.panel.querySelector("[data-knowledge-trigger]");
    if (launch) launch.addEventListener("click", open);
    buildDialog(bootstrap.state.panel);
  }

  function buildDialog(panel) {
    var dialog = document.createElement("div");
    dialog.id = "ev-knowledge-manager";
    dialog.className = "ev-knowledge-manager";
    dialog.hidden = true;
    var card = document.createElement("div");
    card.className = "ev-knowledge-card";
    var heading = document.createElement("div");
    heading.className = "ev-knowledge-heading";
    var title = document.createElement("strong");
    title.textContent = "全局知识库";
    heading.appendChild(title);
    heading.appendChild(button("关闭", close));
    var controls = document.createElement("div");
    controls.className = "ev-knowledge-controls";
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,.docx,.txt,.md,.markdown,.html,.htm,.csv";
    input.hidden = true;
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (file) upload(file).finally(function () { input.value = ""; });
    });
    controls.appendChild(button("上传到暂存区", function () { input.click(); }));
    controls.appendChild(button("刷新", refresh));
    controls.appendChild(input);
    var status = document.createElement("div");
    status.className = "ev-knowledge-status";
    var list = document.createElement("div");
    list.className = "ev-knowledge-list";
    card.appendChild(heading);
    card.appendChild(controls);
    card.appendChild(status);
    card.appendChild(list);
    dialog.appendChild(card);
    panel.appendChild(dialog);
    runtime.dialog = dialog;
    runtime.list = list;
    runtime.status = status;
  }

  function open() {
    runtime.dialog.hidden = false;
    refresh();
  }

  function close() {
    runtime.dialog.hidden = true;
    clearTimeout(runtime.poll);
  }

  function upload(file) {
    var form = new FormData();
    form.append("file", file, file.name);
    form.append("role", runtime.role);
    form.append("auto_activate", "false");
    setStatus("正在上传到暂存区…");
    return json("/api/rag/sources/upload", { method: "POST", body: form }).then(function () {
      setStatus("上传完成；请先验证，再手动激活。\n");
      return refresh();
    }).catch(showError);
  }

  function refresh() {
    return Promise.all([json("/rag/sources"), json("/rag/jobs?limit=20")]).then(function (values) {
      renderSources(values[0].sources || []);
      var jobs = values[1].jobs || [];
      var active = jobs.find(function (job) { return job.status === "queued" || job.status === "running"; });
      if (active) {
        setStatus("增量索引任务 " + active.job_id + "：" + active.status);
        clearTimeout(runtime.poll);
        runtime.poll = setTimeout(refresh, 1500);
      } else if (jobs[0]) {
        setStatus("最近任务 " + jobs[0].job_id + "：" + jobs[0].status);
        if (jobs[0].status === "success") validateIndex();
      } else {
        setStatus("没有运行中的索引任务。上传默认只进入暂存区。 ");
      }
      return values;
    }).catch(showError);
  }

  function renderSources(items) {
    runtime.list.textContent = "";
    if (!items.length) {
      runtime.list.textContent = "暂无知识源";
      return;
    }
    items.forEach(function (source) {
      var row = document.createElement("div");
      row.className = "ev-knowledge-source";
      var summary = document.createElement("div");
      summary.className = "ev-knowledge-summary";
      var name = document.createElement("strong");
      name.textContent = source.display_name || source.file_name || source.source_id;
      var meta = document.createElement("span");
      meta.textContent = source.source_status + " / " + source.parser_status + " / " + source.index_status + " · chunks " + (source.chunk_count || 0);
      summary.appendChild(name);
      summary.appendChild(meta);
      var actions = document.createElement("div");
      actions.className = "ev-knowledge-actions";
      if (["staging", "rejected", "error", "validated"].indexOf(source.source_status) >= 0) {
        actions.appendChild(button("验证", function () { validateSource(source); }));
      }
      if (source.source_status === "validated" || source.source_status === "disabled") {
        actions.appendChild(button("激活", function () { sourceAction(source, "activate"); }));
      }
      if (source.source_status === "active") {
        actions.appendChild(button("停用", function () { sourceAction(source, "disable"); }));
        actions.appendChild(button("重建", function () { sourceAction(source, "reindex"); }));
      }
      actions.appendChild(button("删除", function () {
        if (global.confirm("删除知识源“" + (source.display_name || source.source_id) + "”并增量清理索引？")) sourceAction(source, "delete");
      }));
      row.appendChild(summary);
      row.appendChild(actions);
      runtime.list.appendChild(row);
    });
  }

  function validateSource(source) {
    var area = String(source.relative_location || "").split("/")[0].toLowerCase();
    if (["active", "staging", "rejected"].indexOf(area) < 0) area = "active";
    return json("/rag/sources/validate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: runtime.role, source_id: source.source_id, area: area })
    }).then(refresh).catch(showError);
  }

  function sourceAction(source, action) {
    var method = action === "delete" ? "DELETE" : "POST";
    var suffix = action === "delete" ? "" : "/" + action;
    return json("/rag/sources/" + encodeURIComponent(source.source_id) + suffix, {
      method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: runtime.role })
    }).then(refresh).catch(showError);
  }

  function validateIndex() {
    return json("/rag/index/validate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: runtime.role, mode: "incremental" })
    }).catch(function () {});
  }

  function json(path, options) {
    return fetch(runtime.baseUrl + path, options || {}).then(async function (response) {
      var data = {};
      try { data = await response.json(); } catch (error) {}
      if (!response.ok) throw new Error(String(data.detail || "KNOWLEDGE_REQUEST_FAILED"));
      return data;
    });
  }

  function setStatus(text) { runtime.status.textContent = text; }
  function showError(error) { setStatus("操作失败：" + String(error && error.message || error)); }
  function button(label, handler) {
    var node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    node.addEventListener("click", handler);
    return node;
  }

  function installStyles() {
    if (document.getElementById("ev-knowledge-style")) return;
    var style = document.createElement("style");
    style.id = "ev-knowledge-style";
    style.textContent = ".ev-knowledge-manager{position:absolute;inset:56px 0 0;z-index:12;padding:16px;background:rgba(4,12,25,.86);backdrop-filter:blur(12px);box-sizing:border-box}.ev-knowledge-manager[hidden]{display:none!important}.ev-knowledge-card{height:100%;display:flex;flex-direction:column;gap:12px;padding:18px;box-sizing:border-box;border:1px solid rgba(122,160,205,.18);border-radius:16px;background:#101f35;box-shadow:0 20px 50px rgba(0,0,0,.38)}.ev-knowledge-heading,.ev-knowledge-controls,.ev-knowledge-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.ev-knowledge-heading strong{font-size:16px}.ev-knowledge-status{color:#91a5bf;min-height:18px}.ev-knowledge-list{flex:1;overflow:auto;display:flex;flex-direction:column;gap:8px}.ev-knowledge-source{display:flex;gap:8px;justify-content:space-between;padding:10px;border-radius:10px;background:#162942}.ev-knowledge-summary{display:flex;flex-direction:column;min-width:0}.ev-knowledge-summary strong,.ev-knowledge-summary span{overflow-wrap:anywhere}.ev-knowledge-summary span{font-size:11px;color:#91a5bf}";
    document.head.appendChild(style);
  }

  global.EVKnowledgeManagerUI = { initialize: initialize, refresh: refresh, open: open, close: close };
  initialize();
})(window);
