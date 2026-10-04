import { el } from "../util.js";

const LABELS = {unavailable: "○ Codex未インストール", disconnected: "○ AI未接続",
  starting: "○ 起動中", ready: "● 接続済み", auth_required: "○ ChatGPT未接続", error: "○ AI未接続 · 接続エラー"};

export function mountAIStatus(host) {
  const details = el("details", {class: "ai-status"});
  const summary = el("summary", {}, "AI · 確認中");
  const panel = el("div", {class: "ai-status__panel"});
  details.append(summary, panel);
  host.replaceChildren(details);
  let current, busy = false, authLink = null, activeTurn = null, smokeAbort = null;
  const message = el("p", {role: "status", "aria-live": "polite"});
  const output = el("pre", {class: "ai-smoke-output", "aria-live": "polite"});

  async function call(path, method = "POST") {
    const response = await fetch(`/api/ai/${path}`, {method, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "AI未接続");
    return data;
  }
  async function action(fn) {
    if (busy) return;
    busy = true;
    message.textContent = "処理中…";
    render();
    try { await fn(); message.textContent = ""; }
    catch (error) { message.textContent = error.message; }
    finally { busy = false; await refresh(); }
  }
  const button = (label, fn, disabled = false) => el("button", {
    type: "button", disabled: busy || disabled, onClick: () => action(fn)}, label);

  function render() {
    if (!current) return;
    const name = current.runtime === "codex_chatgpt_plan" ? "Codex / ChatGPT plan" : current.runtime;
    summary.textContent = `AI · ${name} · ${LABELS[current.state] || "○ AI未接続"}`;
    panel.replaceChildren(el("p", {}, "PDF・Import済みBrief・Note・Highlight・exportはAI未接続でも使えます。"));
    if (current.runtime === "codex_chatgpt_plan") {
      if (current.state === "auth_required") {
        panel.append(button("ChatGPTで接続", async () => {
          const data = await call("login");
          authLink = data.authorization_url;
        }, current.auth.login_state === "pending"));
      }
      if (current.auth.login_state === "pending") {
        panel.append(el("p", {}, "ブラウザで認証してください。完了を待っています。"), button("ログインをキャンセル", async () => {
          await call("login/cancel"); authLink = null;
        }));
      }
      if (["failed", "cancelled"].includes(current.auth.login_state)) {
        panel.append(el("p", {role: "status"}, current.auth.login_state === "failed" ? "ログインに失敗しました。再試行できます。" : "ログインをキャンセルしました。"));
      }
      if (authLink && current.auth.login_state === "pending") {
        panel.append(el("a", {href: authLink, target: "_blank", rel: "noopener noreferrer"}, "ブラウザで認証を開く ↗"));
      }
      if (current.auth.state === "authenticated") {
        authLink = null;
        panel.append(button("ログアウト", async () => { await call("logout"); }));
      }
      panel.append(button("再接続", async () => { authLink = null; await call("restart"); }));
      panel.append(el("a", {href: "https://developers.openai.com/codex/cli/", target: "_blank", rel: "noopener noreferrer"}, "セットアップ方法 ↗"));
      if (current.state === "unavailable") panel.append(el("p", {}, "Codex CLIをインストールしてください。Windowsではnative executableのパス指定が必要な場合があります。"));
      panel.append(el("p", {}, "認証情報はCodexのOS keyringに保存します。keyringが使える環境で接続してください。"));
    }
    panel.append(button("状態を確認", () => refresh(true)));
    panel.append(button("最小接続テスト", smoke, current.state !== "ready" || smokeAbort !== null));
    if (activeTurn) panel.append(el("button", {type: "button", onClick: async () => {
      try { await call(`sessions/${encodeURIComponent(activeTurn.session_id)}/turns/${encodeURIComponent(activeTurn.turn_id)}/cancel`); }
      catch (error) { message.textContent = error.message; }
    }}, "接続テストを中断"));
    panel.append(message, output);
  }
  async function refresh(force = false, polling = false) {
    try { const next = await call(`status${force ? "?refresh=true" : ""}`, "GET");
      const changed = JSON.stringify(next) !== JSON.stringify(current);
      current = next;
      if (changed || force || !polling) render(); }
    catch { summary.textContent = "AI · ○ 状態を取得できません"; }
  }
  async function smoke() {
    output.textContent = "";
    smokeAbort = new AbortController();
    let reader;
    try {
      const response = await fetch("/api/ai/smoke", {method: "POST", signal: smokeAbort.signal});
      if (!response.ok) throw new Error((await response.json()).detail || "AI未接続");
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const frames = buffer.split("\n\n"); buffer = frames.pop();
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.kind === "started") { activeTurn = event; render(); }
          if (event.kind === "delta") output.textContent += event.text;
          if (event.kind === "cancelled") output.textContent += "\n中断しました";
          if (event.kind === "error") throw new Error(`AI未接続 / ${event.code}`);
        }
      }
    } finally { reader?.releaseLock(); smokeAbort = null; activeTurn = null; render(); }
  }
  window.addEventListener("pagehide", () => smokeAbort?.abort());
  window.addEventListener("kg-ai-connect", async () => {
    details.open = true;
    await refresh(true);
    if (current?.state === "auth_required" && !busy && current.auth.login_state !== "pending") {
      await action(async () => { const data = await call("login"); authLink = data.authorization_url; });
    }
    panel.querySelector("button, a")?.focus();
  });
  // Polling never starts login or inference; account/read is requested explicitly
  // or after auth notifications. Only changed status re-renders during polling.
  setInterval(() => { if (!busy) refresh(false, true); }, 3000);
  refresh();
}
