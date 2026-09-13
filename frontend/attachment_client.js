(function (global) {
  "use strict";

  function AttachmentClient(options) {
    options = options || {};
    this.baseUrl = String(options.baseUrl || "").replace(/\/$/, "");
    this.userId = String(options.userId || "webgl-user");
  }

  AttachmentClient.prototype.upload = function (conversationId, file) {
    var form = new FormData();
    form.append("file", file, file.name);
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "/attachments?user_id=" + encodeURIComponent(this.userId), {
      method: "POST", headers: { "X-User-ID": this.userId }, body: form
    }).then(function (data) { return data.attachment; });
  };

  AttachmentClient.prototype.list = function (conversationId) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "/attachments?user_id=" + encodeURIComponent(this.userId), {
      headers: { "X-User-ID": this.userId }
    }).then(function (data) { return data.attachments || []; });
  };

  AttachmentClient.prototype.remove = function (conversationId, attachmentId) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "/attachments/" + encodeURIComponent(attachmentId) + "?user_id=" + encodeURIComponent(this.userId), {
      method: "DELETE", headers: { "X-User-ID": this.userId }
    });
  };

  AttachmentClient.prototype._json = function (path, options) {
    return fetch(this.baseUrl + path, options || {}).then(async function (response) {
      var data = {};
      try { data = await response.json(); } catch (error) {}
      if (!response.ok) {
        var code = String(data.detail || "ATTACHMENT_REQUEST_FAILED");
        var problem = new Error(code);
        problem.code = code;
        problem.status = response.status;
        throw problem;
      }
      return data;
    });
  };

  global.EVAttachmentClient = AttachmentClient;
})(window);
