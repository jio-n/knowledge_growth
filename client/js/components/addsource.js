// client/js/components/addsource.js
// Add-source modal: PDF file / URL / text+markdown paste, with duplicate-detection
// follow-up dialog (既存を開く / 新規として登録).

import { api } from "../api.js";
import { el, openModal, toast, fmtDate } from "../util.js";

const MODE_LABEL = { pdf: "+ PDF", url: "+ URL", text: "+ テキスト" };

export function openAddSourceModal(defaultMode = "pdf") {
  const tabButtons = {};
  const panels = {};
  const resultHost = el("div", {});
  let modal;

  async function runSubmit(btn, callFn, retryForce) {
    btn.disabled = true;
    resultHost.textContent = "";
    try {
      const res = await callFn();
      if (res && res.duplicate) {
        renderDuplicate(res.existing || [], retryForce);
        return;
      }
      const source = res.source;
      modal.close();
      toast("資料を登録しました", { type: "success" });
      location.hash = `#/read/${source.id}`;
    } catch (err) {
      toast((err && err.message) || "登録に失敗しました", { type: "error" });
    } finally {
      btn.disabled = false;
    }
  }

  function renderDuplicate(existing, retryForce) {
    resultHost.textContent = "";
    resultHost.append(el("div", { class: "duplicate-list" },
      el("p", {}, "既に登録済みの資料と一致する可能性があります。"),
      ...existing.map((ex) => el("div", { class: "duplicate-item" },
        el("div", {},
          el("div", {}, ex.title || "(無題)"),
          el("div", { class: "duplicate-item__meta" }, `${ex.type || ""} ・ ${fmtDate(ex.created_at)}`),
        ),
        el("button", {
          class: "btn btn--sm", type: "button",
          onClick: () => { modal.close(); location.hash = `#/read/${ex.id}`; },
        }, "既存を開く"),
      )),
      el("div", { class: "modal__actions" },
        el("button", {
          class: "btn btn--sm btn--primary", type: "button",
          onClick: () => retryForce(),
        }, "新規として登録"),
      ),
    ));
  }

  function buildPdfPanel() {
    const fileInput = el("input", { type: "file", accept: "application/pdf" });
    const submitBtn = el("button", { class: "btn btn--primary", type: "button", onClick: () => submit(false) }, "登録");
    const root = el("div", { class: "addsource-panel" },
      el("div", { class: "field" }, el("label", {}, "PDFファイル"), fileInput),
      el("div", { class: "modal__actions" }, submitBtn),
    );
    async function submit(force) {
      const file = fileInput.files && fileInput.files[0];
      if (!file) { toast("PDFファイルを選択してください", { type: "error" }); return; }
      await runSubmit(submitBtn, () => api.createSourcePdf(file, force, { silent: true }), () => submit(true));
    }
    return { root, submit };
  }

  function buildUrlPanel() {
    const urlInput = el("input", { type: "url", placeholder: "https://example.com/paper" });
    const submitBtn = el("button", { class: "btn btn--primary", type: "button", onClick: () => submit(false) }, "登録");
    const root = el("div", { class: "addsource-panel" },
      el("div", { class: "field" }, el("label", {}, "URL"), urlInput),
      el("div", { class: "modal__actions" }, submitBtn),
    );
    async function submit(force) {
      const url = urlInput.value.trim();
      if (!url) { toast("URLを入力してください", { type: "error" }); return; }
      await runSubmit(submitBtn, () => api.createSourceUrl(url, force, { silent: true }), () => submit(true));
    }
    return { root, submit };
  }

  function buildTextPanel() {
    const titleInput = el("input", { type: "text", placeholder: "(任意)" });
    const filenameInput = el("input", { type: "text", placeholder: "例: notes.md(省略可)" });
    const contentArea = el("textarea", { rows: 8, placeholder: "本文を貼り付け(Markdown可)" });
    const submitBtn = el("button", { class: "btn btn--primary", type: "button", onClick: () => submit(false) }, "登録");
    const root = el("div", { class: "addsource-panel" },
      el("div", { class: "field" }, el("label", {}, "タイトル"), titleInput),
      el("div", { class: "field" }, el("label", {}, "ファイル名"), filenameInput,
        el("div", { class: "field__hint" }, ".md 拡張子を付けるとMarkdownとして扱われます")),
      el("div", { class: "field" }, el("label", {}, "本文"), contentArea),
      el("div", { class: "modal__actions" }, submitBtn),
    );

    async function submit(force) {
      const content = contentArea.value.trim();
      if (!content) { toast("本文を入力してください", { type: "error" }); return; }
      await runSubmit(submitBtn, () => api.createSourceText({
        content, title: titleInput.value.trim() || null, filename: filenameInput.value.trim() || null, force,
      }, { silent: true }), () => submit(true));
    }

    return { root, submit };
  }

  const pdfPanel = buildPdfPanel();
  const urlPanel = buildUrlPanel();
  const textPanel = buildTextPanel();
  panels.pdf = pdfPanel;
  panels.url = urlPanel;
  panels.text = textPanel;

  const tabsBar = el("div", { class: "addsource-tabs" },
    ...Object.keys(MODE_LABEL).map((mode) => {
      const btn = el("button", { type: "button", onClick: () => switchMode(mode) }, MODE_LABEL[mode]);
      tabButtons[mode] = btn;
      return btn;
    }),
  );

  function switchMode(mode) {
    resultHost.textContent = "";
    for (const m of Object.keys(MODE_LABEL)) {
      tabButtons[m].classList.toggle("active", m === mode);
      panels[m].root.hidden = m !== mode;
    }
  }

  const body = el("div", {},
    el("h2", {}, "資料を追加"),
    tabsBar,
    pdfPanel.root, urlPanel.root, textPanel.root,
    resultHost,
  );

  modal = openModal(body);
  switchMode(defaultMode);
  return modal;
}
