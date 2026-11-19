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
  cam: $("s-cam"),
  preview: $("s-preview"),
  thumb: $("s-thumb"),
  thumbMeta: $("s-thumb-meta"),
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

let cameras = [];     // /setup/options の cameras。ドロップダウンの並びと同じ順
let thumbStamp = 0;   // 縮小画像のキャッシュ避け。一覧を作り直すたびに変える

function setBusy(busy) {
  els.start.disabled = busy || !els.cam.value;
  els.refresh.disabled = busy;
  els.cam.disabled = busy || !cameras.length;
  for (const input of els.form.querySelectorAll("input")) input.disabled = busy;
}

function cameraLabel(c) {
  const name = c.name || `カメラ ${c.index}`;
  const size = c.readable ? `${c.width}×${c.height}` : "読み取れない";
  return `${name}  (index ${c.index} ・ ${size})`;
}

// ドロップダウンで選んでいるカメラの縮小画像と説明を出す
function showPreview() {
  const c = cameras.find((x) => String(x.index) === els.cam.value);
  els.preview.hidden = !c;
  if (!c) return;
  els.thumb.hidden = !c.has_thumbnail;
  if (c.has_thumbnail) els.thumb.src = `/setup/thumb/${c.index}?t=${thumbStamp}`;
  els.thumbMeta.textContent = c.has_thumbnail
    ? "一覧を作った時点の1枚"
    : "映像を取得できませんでした (開けたが読み取れないカメラです)";
  els.thumbMeta.className = c.has_thumbnail ? "" : "warn";
}

function renderCameras(cams, defaultIndex) {
  cameras = cams;
  thumbStamp = Date.now();
  els.cam.replaceChildren();
  if (!cams.length) {
    els.camNote.textContent =
      "カメラが見つかりませんでした。接続を確かめて「再読み込み」を押してください。";
    els.camNote.className = "s-note err";
    showPreview();
    return;
  }
  els.camNote.textContent = "使うカメラを選んでください。";
  els.camNote.className = "s-note";
  for (const c of cams) {
    const opt = el("option", "", cameraLabel(c));
    opt.value = String(c.index);
    opt.selected = c.index === defaultIndex;
    els.cam.append(opt);
  }
  showPreview();
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
  const cam = els.cam.value;
  const model = chosenModel();
  if (!cam) return setStatus("カメラを選んでください", "err");
  if (!model) return setStatus("モデルを選んでください", "err");
  setBusy(true);
  setStatus("起動しています", "busy");
  try {
    const res = await fetch("/setup/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_index: Number(cam), model }),
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
els.cam.addEventListener("change", () => { showPreview(); setBusy(false); });
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
