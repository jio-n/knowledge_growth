// client/js/views/library.js
// 画面1: ライブラリ (#/) - source cards, add-source entry points, status/tag filters,
// per-card reading-status quick select. (Cross-material search dropdown lives in the
// global header - see main.js - separate from this view's own title/author/summary filter.)

import { api } from "../api.js";
import { el, debounce, fmtDate, parseStringArray } from "../util.js";
import { setSourcesCache, READING_STATUS_LABEL, SOURCE_TYPE_LABEL } from "../state.js";
import { openAddSourceModal } from "../components/addsource.js";

export async function render(container, isCurrent = () => true) {
  container.textContent = "";
  const filters = { q: "", status: "", tag: "" };

  const grid = el("div", { class: "source-grid" });
  const toolbar = buildToolbar();
  const root = el("div", { class: "library" }, toolbar.root, grid);
  container.append(root);

  function buildToolbar() {
    const addPdfBtn = el("button", { class: "btn", type: "button", onClick: () => openAddSourceModal("pdf") }, "+ PDF");
    const addUrlBtn = el("button", { class: "btn", type: "button", onClick: () => openAddSourceModal("url") }, "+ URL");
    const addTextBtn = el("button", { class: "btn", type: "button", onClick: () => openAddSourceModal("text") }, "+ テキスト");

    const searchInput = el("input", {
      type: "search",
      placeholder: "資料を検索(タイトル/著者/要約)",
      style: { minWidth: "240px" },
      onInput: debounce((e) => { filters.q = e.target.value; load(); }, 300),
    });

    const statusSelect = el("select", {
      onChange: (e) => { filters.status = e.target.value; load(); },
    },
      el("option", { value: "" }, "状態: すべて"),
      ...Object.entries(READING_STATUS_LABEL).map(([k, v]) => el("option", { value: k }, v)),
    );

    const tagSelect = el("select", {
      onChange: (e) => { filters.tag = e.target.value; load(); },
    }, el("option", { value: "" }, "タグ: すべて"));

    const rootEl = el("div", { class: "library__toolbar" },
      addPdfBtn, addUrlBtn, addTextBtn,
      searchInput,
      el("div", { class: "spacer" }),
      el("div", { class: "library__filters" }, statusSelect, tagSelect),
    );
    return { root: rootEl, statusSelect, tagSelect, searchInput };
  }

  function buildStatusSelect(source) {
    return el("select", {
      onClick: (e) => e.stopPropagation(),
      onChange: async (e) => {
        try {
          await api.patchSource(source.id, { reading_status: e.target.value });
        } catch {
          // api.js already toasted; revert visually on next load
        }
      },
    }, ...Object.entries(READING_STATUS_LABEL).map(([k, v]) =>
      el("option", { value: k, selected: k === source.reading_status || undefined }, v)));
  }

  function renderCard(source) {
    const authors = parseStringArray(source.authors);
    const cardTags = parseStringArray(source.tags);
    const byline = [
      authors.length ? authors.join("、") : null,
      source.year || null,
    ].filter(Boolean).join(" ・ ");

    const summaryEl = source.one_line_summary
      ? el("div", { class: "source-card__summary" }, source.one_line_summary)
      : el("div", { class: "source-card__summary source-card__summary--muted" },
          source.analysis_status === "pending" || source.analysis_status === "running"
            ? "構造化抽出中..." : "(要約なし)");

    const card = el("div", {
      class: "source-card",
      onClick: (e) => {
        if (e.target.closest("select")) return;
        location.hash = `#/read/${source.id}`;
      },
    },
      el("div", { class: "source-card__top" },
        el("div", { class: "source-card__title" }, source.title || "(無題)"),
        el("span", { class: "badge badge--type" }, SOURCE_TYPE_LABEL[source.type] || source.type),
      ),
      byline ? el("div", { class: "source-card__byline" }, byline) : null,
      summaryEl,
      cardTags.length
        ? el("div", { class: "source-card__tags" }, ...cardTags.map((t) => el("span", { class: "tag-chip" }, t)))
        : null,
      el("div", { class: "source-card__row" },
        buildStatusSelect(source),
        el("div", {}, `最終閲覧: ${fmtDate(source.last_opened_at)}`),
      ),
      el("div", { class: "source-card__row" },
        el("div", { class: "source-card__stats" },
          `知識 ${source.knowledge_count ?? 0} ・ 質問 ${source.question_count ?? 0} ・ 未解決 ${source.open_question_count ?? 0}`,
        ),
      ),
    );
    return card;
  }

  function populateTagOptions(sources) {
    const tagsSet = new Set();
    for (const s of sources) parseStringArray(s.tags).forEach((t) => tagsSet.add(t));
    const current = toolbar.tagSelect.value;
    toolbar.tagSelect.textContent = "";
    toolbar.tagSelect.append(el("option", { value: "" }, "タグ: すべて"));
    for (const t of Array.from(tagsSet).sort()) {
      toolbar.tagSelect.append(el("option", { value: t, selected: t === current || undefined }, t));
    }
  }

  async function load() {
    let sources = [];
    try {
      const res = await api.listSources(filters);
      sources = res.sources || [];
    } catch {
      if (isCurrent()) grid.textContent = "";
      return;
    }
    if (!isCurrent()) return; // superseded by another navigation while this fetch was in flight
    setSourcesCache(sources);
    grid.textContent = "";
    if (!sources.length) {
      grid.append(el("div", { class: "library__empty" }, "資料がありません。上のボタンから追加してください。"));
    } else {
      for (const source of sources) grid.append(renderCard(source));
    }
    populateTagOptions(sources);
  }

  await load();
}
