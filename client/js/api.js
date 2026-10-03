// client/js/api.js
// Thin fetch wrapper around the API described in docs/architecture/api_spec.md.
// - JSON in / JSON out (except PDF upload which is multipart).
// - Throws an Error with a human message on !ok responses.
// - Shows a toast on error unless the caller passes { silent: true }.

import { toast } from "./util.js";

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, isForm = false, silent = false } = {}) {
  const opts = { method, headers: {} };
  if (isForm) {
    opts.body = body; // FormData - browser sets content-type with boundary
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (networkErr) {
    const msg = "サーバーに接続できません。ネットワーク/サーバー状態を確認してください。";
    if (!silent) toast(msg, { type: "error" });
    throw new ApiError(msg, 0, null);
  }

  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }

  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `${res.status} ${res.statusText}`;
    const msg = typeof detail === "string" ? detail : JSON.stringify(detail);
    if (!silent) toast(msg, { type: "error" });
    throw new ApiError(msg, res.status, data ? data.detail : null);
  }
  return data;
}

export const api = {
  // --- meta ---
  getMeta: () => request("/api/meta"),

  // --- registration ---
  createSourcePdf(file, force = false, opts = {}) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("force", force ? "true" : "false");
    return request("/api/sources/pdf", { method: "POST", body: fd, isForm: true, ...opts });
  },
  createSourceUrl(url, force = false, opts = {}) {
    return request("/api/sources/url", { method: "POST", body: { url, force }, ...opts });
  },
  createSourceText({ content, title = null, filename = null, force = false }, opts = {}) {
    return request("/api/sources/text", { method: "POST", body: { content, title, filename, force }, ...opts });
  },

  // --- library ---
  listSources({ q = "", status = "", tag = "" } = {}, opts = {}) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (status) params.set("status", status);
    if (tag) params.set("tag", tag);
    const qs = params.toString();
    return request(`/api/sources${qs ? `?${qs}` : ""}`, opts);
  },
  getSource(id, opts = {}) {
    return request(`/api/sources/${id}`, opts);
  },
  patchSource(id, patch, opts = {}) {
    return request(`/api/sources/${id}`, { method: "PATCH", body: patch, ...opts });
  },
  deleteSource(id, opts = {}) {
    return request(`/api/sources/${id}`, { method: "DELETE", ...opts });
  },
  reanalyzeSource(id, opts = {}) {
    return request(`/api/sources/${id}/reanalyze`, { method: "POST", ...opts });
  },

  // --- document ---
  getDocument(id, opts = {}) {
    return request(`/api/sources/${id}/document`, opts);
  },
  fileUrl(id) {
    return `/api/sources/${id}/file`;
  },

  // --- kgpack / Paper Brief (all validation and import decisions stay on the server) ---
  validateKgpack(file) {
    const fd = new FormData();
    fd.append("file", file);
    return request("/api/import/kgpack/validate", { method: "POST", body: fd, isForm: true, silent: true });
  },
  previewKgpack(file, { sourceId = null, excludeFields = [] } = {}) {
    const fd = new FormData();
    fd.append("file", file);
    if (sourceId) fd.append("source_id", sourceId);
    fd.append("exclude_fields", JSON.stringify(excludeFields));
    return request("/api/import/kgpack/preview", { method: "POST", body: fd, isForm: true, silent: true });
  },
  commitKgpack(previewId) {
    return request("/api/import/kgpack/commit", {
      method: "POST", body: { preview_id: previewId, confirmed: true }, silent: true,
    });
  },
  getPaperBrief(sourceId, opts = {}) {
    return request(`/api/sources/${sourceId}/paper-brief`, opts);
  },

  // --- questions / translation / highlights ---
  askQuestion(sourceId, payload, opts = {}) {
    return request(`/api/sources/${sourceId}/questions`, { method: "POST", body: payload, ...opts });
  },
  listQuestions(sourceId, opts = {}) {
    return request(`/api/sources/${sourceId}/questions`, opts);
  },
  translate({ source_id, text, block_id = null }, opts = {}) {
    return request("/api/translate", { method: "POST", body: { source_id, text, block_id }, ...opts });
  },
  createHighlight({ source_id, anchor, color = "yellow", comment = null }, opts = {}) {
    return request("/api/highlights", { method: "POST", body: { source_id, anchor, color, comment }, ...opts });
  },
  deleteHighlight(id, opts = {}) {
    return request(`/api/highlights/${id}`, { method: "DELETE", ...opts });
  },

  // --- knowledge / note ---
  createKnowledge(payload, opts = {}) {
    return request("/api/knowledge", { method: "POST", body: payload, ...opts });
  },
  suggestKnowledge(payload, opts = {}) {
    // This endpoint never returns 5xx per spec; still guard defensively.
    return request("/api/knowledge/suggest", { method: "POST", body: payload, silent: true, ...opts });
  },
  getNote(sourceId, opts = {}) {
    return request(`/api/sources/${sourceId}/note`, opts);
  },
  patchKnowledge(id, patch, opts = {}) {
    return request(`/api/knowledge/${id}`, { method: "PATCH", body: patch, ...opts });
  },
  deleteKnowledge(id, opts = {}) {
    return request(`/api/knowledge/${id}`, { method: "DELETE", ...opts });
  },

  // --- search / export ---
  search(q, opts = {}) {
    const params = new URLSearchParams({ q });
    return request(`/api/search?${params.toString()}`, opts);
  },
  fetchExportMd(id, opts = {}) {
    // request() expects JSON; export.md is text/markdown so fetch directly here.
    return fetch(`/api/sources/${id}/export.md?download=0`).then((res) => {
      if (!res.ok) throw new ApiError(`エクスポートの取得に失敗しました (${res.status})`, res.status);
      return res.text();
    }).catch((err) => {
      if (!opts.silent) toast(err.message || "エクスポートの取得に失敗しました", { type: "error" });
      throw err;
    });
  },
  exportMdUrl(id, download = 1) {
    return `/api/sources/${id}/export.md?download=${download}`;
  },
  exportJsonUrl(id) {
    return `/api/sources/${id}/export.json`;
  },
  exportAllUrl() {
    return "/api/export/all.json";
  },
};

export { ApiError };
