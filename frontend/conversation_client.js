(function (global) {
  "use strict";

  function ConversationClient(options) {
    options = options || {};
    this.baseUrl = String(options.baseUrl || "").replace(/\/$/, "");
    this.userId = String(options.userId || "webgl-user");
  }

  ConversationClient.prototype.create = function (title) {
    return this._json("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-ID": this.userId },
      body: JSON.stringify({ user_id: this.userId, title: title || "新会话" })
    }).then(function (data) { return data.conversation; });
  };

  ConversationClient.prototype.list = function () {
    return this._json("/api/conversations?user_id=" + encodeURIComponent(this.userId), {
      headers: { "X-User-ID": this.userId }
    }).then(function (data) { return data.conversations || []; });
  };

  ConversationClient.prototype.get = function (conversationId) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "?user_id=" + encodeURIComponent(this.userId), {
      headers: { "X-User-ID": this.userId }
    }).then(function (data) { return data.conversation; });
  };

  ConversationClient.prototype.rename = function (conversationId, title) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-User-ID": this.userId },
      body: JSON.stringify({ user_id: this.userId, title: title })
    }).then(function (data) { return data.conversation; });
  };

  ConversationClient.prototype.remove = function (conversationId) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "?user_id=" + encodeURIComponent(this.userId), {
      method: "DELETE", headers: { "X-User-ID": this.userId }
    });
  };

  ConversationClient.prototype.messages = function (conversationId) {
    return this._json("/api/conversations/" + encodeURIComponent(conversationId) + "/messages?user_id=" + encodeURIComponent(this.userId) + "&limit=500", {
      headers: { "X-User-ID": this.userId }
    }).then(function (data) { return data.messages || []; });
  };

  ConversationClient.prototype._json = function (path, options) {
    return fetch(this.baseUrl + path, options || {}).then(async function (response) {
      var data = {};
      try { data = await response.json(); } catch (error) {}
      if (!response.ok) {
        var problem = new Error(String(data.detail || "CONVERSATION_REQUEST_FAILED"));
        problem.status = response.status;
        throw problem;
      }
      return data;
    });
  };

  global.EVConversationClient = ConversationClient;
})(window);
