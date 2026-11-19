// 起動画面: カメラと検出モデルを選んで検出を始める
// (理由: docs/adr/0043-startup-selection-screen.md)。
// サーバから受け取る名前 (カメラ名・モデル名) は textContent でだけ差し込む。
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  device: $("s-device"),
  form: $("s-form"),
  refresh: $("s-refresh"),
  camNote: $("s-cam-note"),
  cams: $("s-cams"),
  models: $("s-models"),
  modelNote: $("s-model-note"),
  custom: $("s-custom"),
  customRadio: $("s-custom-radio"),
  status: $("s-status"),
  start: $("s-start"),
};

let polling = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function setStatus(text, kind) {
  els.status.textContent = text;
  els.status.className = "s-status" + (kind ? " " + kind : "");
}

function setBusy(busy) {
  els.start.disabled = busy || !els.cams.querySelector("input:checked");
  els.refresh.disabled = busy;
  for (const input of els.form.querySelectorAll("input")) input.disabled = busy;
}

function renderCameras(cams, defaultIndex) {
  els.cams.replaceChildren();
  if (!cams.length) {
    els.camNote.textContent =
      "カメラが見つかりませんでした。接続を確かめて「再読み込み」を押してください。";
    els.camNote.className = "s-note err";
    return;
  }
  els.camNote.textContent = "使うカメラを選んでください。画像は一覧を作った時点の1枚です。";
  els.camNote.className = "s-note";
  const stamp = Date.now();
  for (const c of cams) {
    const label = el("label", "s-cam");
    const input = el("input");
    input.type = "radio";
    input.name = "camera";
    input.value = String(c.index);
    input.checked = c.index === defaultIndex;
    input.addEventListener("change", () => setBusy(false));
    label.append(input);

    if (c.has_thumbnail) {
      const img = el("img", "s-cam-thumb");
      img.alt = "";
      img.src = `/setup/thumb/${c.index}?t=${stamp}`;
      label.append(img);
    } else {
      label.append(el("span", "s-cam-nothumb", "映像を取得できませんでした"));
    }

    const body = el("span", "s-cam-body");
    body.append(el("span", "s-cam-name", c.name || `カメラ ${c.index}`));
    const meta = c.readable
      ? `index ${c.index} ・ ${c.width}×${c.height}`
      : `index ${c.index} ・ 開けたが読み取れない`;
    body.append(el("span", "s-cam-meta" + (c.readable ? "" : " warn"), meta));
    label.append(body);
    els.cams.append(label);
  }
}

function renderModels(models, defaultModel) {
  els.models.replaceChildren();
  let matched = false;
  for (const m of models) {
    const label = el("label", "s-model");
    const input = el("input");
    input.type = "radio";
    input.name = "model";
    input.value = m.name;
    if (m.name === defaultModel && !matched) {
      input.checked = true;
      matched = true;
    }
    label.append(input);
    const body = el("span", "s-model-body");
    body.append(el("span", "s-model-name", m.name));
    body.append(
      m.local
        ? el("span", "s-badge", `手元にあり ${m.size_mb}MB`)
        : el("span", "s-badge dl", "初回にダウンロード"),
    );
    body.append(el("span", "s-model-desc", m.description));
    label.append(body);
    els.models.append(label);
  }
  if (!matched) {
    els.customRadio.checked = true;
    els.custom.value = defaultModel || "";
  }
}

function chosenModel() {
  const picked = els.form.querySelector('input[name="model"]:checked');
  if (!picked) return "";
  return picked.value === "__custom__" ? els.custom.value.trim() : picked.value;
}

async function loadOptions(refresh) {
  setBusy(true);
  els.camNote.textContent = "接続中のカメラを調べています...";
  els.camNote.className = "s-note";
  setStatus("");
  try {
    const res = await fetch(`/setup/options${refresh ? "?refresh=1" : ""}`);
    const data = await res.json();
    if (res.status === 409) {
      // 立ち上げ中・稼働中 (別のタブで開始した等)
      followStatus(data);
      return;
    }
    els.device.textContent = `推論デバイス: ${data.device}  ・  モデルの保存先: ${data.model_dir}`;
    renderCameras(data.cameras, data.default.cam_index);
    renderModels(data.models, data.default.model);
    els.modelNote.textContent = "「初回にダウンロード」のモデルは、開始時に自動で取得します (ネット接続が必要)。";
    if (data.state === "error" && data.message) setStatus(data.message, "err");
  } catch (e) {
    setStatus(`サーバに接続できませんでした: ${e}`, "err");
  } finally {
    // 立ち上げ中を追いかけている間は操作させない
    if (!polling) setBusy(false);
  }
}

function followStatus(s) {
  if (s.state === "running") {
    setStatus("検出を開始しました。画面を切り替えます", "busy");
    location.replace("/");
    return;
  }
  if (s.state === "starting") {
    setBusy(true);
    setStatus(s.message || "起動しています", "busy");
    if (!polling) polling = setInterval(poll, 500);
    return;
  }
  stopPolling();
  setBusy(false);
  if (s.state === "error") setStatus(s.message, "err");
}

function stopPolling() {
  if (polling) clearInterval(polling);
  polling = null;
}

async function poll() {
  try {
    const res = await fetch("/setup/status");
    followStatus(await res.json());
  } catch (e) {
    stopPolling();
    setBusy(false);
    setStatus(`サーバに接続できませんでした: ${e}`, "err");
  }
}

els.form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const cam = els.form.querySelector('input[name="camera"]:checked');
  const model = chosenModel();
  if (!cam) return setStatus("カメラを選んでください", "err");
  if (!model) return setStatus("モデルを選んでください", "err");
  setBusy(true);
  setStatus("起動しています", "busy");
  try {
    const res = await fetch("/setup/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_index: Number(cam.value), model }),
    });
    const data = await res.json();
    if (!res.ok && res.status !== 409) {
      setBusy(false);
      return setStatus(data.detail || "開始できませんでした", "err");
    }
    followStatus(data);
  } catch (e) {
    setBusy(false);
    setStatus(`サーバに接続できませんでした: ${e}`, "err");
  }
});

els.refresh.addEventListener("click", () => loadOptions(true));
// 「その他」の欄に打ち込み始めたら、その選択肢を選んだことにする
els.custom.addEventListener("input", () => { els.customRadio.checked = true; });

(async () => {
  try {
    const res = await fetch("/setup/status");
    const s = await res.json();
    if (s.state === "running" || s.state === "starting") return followStatus(s);
  } catch (e) {
    // サーバに届かなければ loadOptions 側でも同じ失敗を表示する
  }
  loadOptions(false);
})();
