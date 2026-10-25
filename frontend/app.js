// キー操作:
//   m        : モードの円盤を開く (単体鑑定 / 広域鑑定 / 調整)。
//              数字で即確定 / ←↑↓→ で選び Enter で確定。調整モード中も効く
//   ←↑↓→    : 単体鑑定で対象を選び替える(その向きにある最も近い検出へ)
//   Enter    : 選んでいる対象に鑑定を固定 / 固定を解除
//   Esc      : 選択・固定を解除して照準ベースに戻す
//   説明ウィンドウ表示中: Enter/Esc でウィンドウを閉じる (それ以外のキーは無効)
//   c        : 調整モードの出入り(ズーム・切り抜き位置・保存はこの中だけ)
//   d        : デバッグ映像のON/OFF (AR表示は OFF にして黒背景+枠だけにする)
//   1〜6     : 枠の意匠(デザイン)を切替 ※デバッグ用。意匠決定後に削除する
// 調整モード中:
//   ←↑↓→    : 切り抜き位置(表示位置)の移動
//   + / = / -: デジタルズームの増減
//   f / v    : 円盤と鑑定ウィンドウの文字サイズ (f=大きく v=小さく、保存される)
//   0        : ズーム・位置・文字サイズをリセット / s : 現在のズーム/位置を保存
//   m        : モードの円盤を開く。鑑定法を選べばそのまま調整を抜けられる
// ズーム/位置はサーバ側のカメラ切り抜きを動かすので、映像も検出枠も一緒に動く。
// 調整を 'c' の中へ隔離したのは、矢印キーを対象の選択に譲るため
// (理由: docs/adr/0019-key-binding-policy-v2.md)
// 鑑定法/選択/固定はこのページだけで完結させる (理由: docs/adr/0010-client-side-display-policy.md)
const stage = document.getElementById("stage");
const video = document.getElementById("video");
const reconnect = document.getElementById("reconnect");
const overlay = document.getElementById("overlay");
const hud = document.getElementById("hud");
const pState = document.getElementById("p-state");
const pModeName = document.getElementById("p-modename");
const calibGuide = document.getElementById("calib-guide");
const pMsg = document.getElementById("p-msg");
const reticle = document.getElementById("reticle");
const wheel = document.getElementById("wheel");
const explainWin = document.getElementById("explain");
const eName = document.getElementById("e-name");
const eBody = document.getElementById("e-body");
const eWait = document.getElementById("e-wait");
let showVideo = true;

const STEP_ZOOM = 0.1;
const STEP_OFFSET = 0.01;

// --- UIの拡大率 (円盤と鑑定ウィンドウ) ---------------------------------------
// ズーム/位置がサーバ側のカメラ値なのに対し、文字の大きさは見ている画面(グラス)
// 固有の値なので localStorage に置く。サーバへは保存しない。
const UI_SCALE_KEY = "ui-scale";
const UI_SCALE_MIN = 0.7;
const UI_SCALE_MAX = 1.5;
const STEP_UI_SCALE = 0.05;

function loadUiScale() {
  // サイトデータを遮断した設定では localStorage に触れること自体が例外になる。
  // ここはトップレベルから呼ばれるため、握り潰さないと起動ごと止まる
  // (モードの復元と同じ扱い: docs/adr/0029-mode-persistence.md)。
  let raw = null;
  try {
    raw = localStorage.getItem(UI_SCALE_KEY);
  } catch {}
  const v = parseFloat(raw);
  if (Number.isNaN(v)) return 1.0;
  return Math.min(UI_SCALE_MAX, Math.max(UI_SCALE_MIN, v));
}

function applyUiScale(v) {
  // 0.05 刻みで丸めないと 1.1000000000000001 のような値が localStorage に残る
  const s = Math.round(Math.min(UI_SCALE_MAX, Math.max(UI_SCALE_MIN, v)) * 100) / 100;
  uiScale = s;
  stage.style.setProperty("--ui-scale", s);
  // 保存できなくても表示は済んでいる。覚えられないだけなので黙って続ける。
  try {
    localStorage.setItem(UI_SCALE_KEY, String(s));
  } catch {}
  return s;
}

let uiScale = loadUiScale();
applyUiScale(uiScale);

// --- 鑑定法 -----------------------------------------------------------------
// 単体鑑定: 常に1件だけ枠を出す。矢印で対象を選び替え、Enter で固定する。
// 広域鑑定: 信頼度の高い順に最大10件を出す(既定。従来の挙動に近い)。
const MODE_SINGLE = "single";
const MODE_MULTI = "multi";
const MULTI_MAX = 10;
// 調整は鑑定法ではないが、利用者から見れば「今どのモードにいるか」は同じ問いなので
// 円盤に並べる (理由: docs/adr/0021-mode-wheel-with-calibration.md)
const MODE_CALIB = "calib";
// 円盤の並び順・数字キーの割り当て・表示名はこの配列が唯一の出所。モードを
// 足すときはここに1行足せば、円盤の配置もキーも案内文も追従する。
const MODES = [
  { id: MODE_SINGLE, key: "1", num: "壱", name: "単体鑑定" },
  { id: MODE_MULTI, key: "2", num: "弐", name: "広域鑑定" },
  { id: MODE_CALIB, key: "3", num: "参", name: "調整" },
];
const MODE_LABEL = {};
for (const m of MODES) MODE_LABEL[m.id] = m.name;

// 直前に使っていた鑑定法を次回の起動へ引き継ぐ。UIの拡大率(UI_SCALE_KEY、上記)と
// 同じ作法で localStorage に置く。調整は鑑定法ではなく一時的な状態なので保存の
// 対象にしない(調整モードのまま終えても次回は既定の MODE_MULTI から始まる)。
const MODE_KEY = "mode";

function loadMode() {
  // Cookie/サイトデータを遮断したブラウザ設定では localStorage へのアクセス自体が
  // 例外を投げ、トップレベルの呼び出しなので握り潰さないとスクリプト全体が
  // 止まって起動できなくなる。読めなければ既定(MODE_MULTI)へ倒す。
  try {
    const v = localStorage.getItem(MODE_KEY);
    // 無い/壊れている/未知のモードIDなら既定(MODE_MULTI)へ
    return v === MODE_SINGLE || v === MODE_MULTI ? v : MODE_MULTI;
  } catch {
    return MODE_MULTI;
  }
}

let mode = loadMode();

// --- モードの円盤 -----------------------------------------------------------
// 'm' で画面中央に開く。数字なら即確定、矢印はカーソルを動かして Enter で確定。
// 開いている間はすべてのキーを盤の操作として吸収し、本来の機能は動かさない
// (理由: docs/adr/0019-key-binding-policy-v2.md)
const WHEEL_MS = 3000;   // 無操作での自動キャンセル。キー操作のたびに延長する
const WHEEL_R = 34;      // 環の半径。#wheel の一辺に対する %
let wheelOpen = false;
let wheelCursor = 0;     // Enter で確定する項目 = 12時の指標に来ているもの
let wheelTimer = null;
// 盤の回転角(度)。足し引きで持つので、一周しても巻き戻らず同じ向きへ回り続ける
// (理由: docs/adr/0023-rotating-mode-wheel.md)
let wheelTurn = 0;
let wheelRotor = null;   // 項目を載せる回転体
const wheelItems = [];   // 円周上の項目。起動時に1回だけ作る

// --- 調整モード -------------------------------------------------------------
// 矢印キーを対象の選択に譲ったため、ズーム・切り抜き位置・保存は 'c' で入る
// 調整モードの中だけで効かせる (理由: docs/adr/0019-key-binding-policy-v2.md)
let calibMode = false;

// --- 説明ウィンドウ (単体鑑定の固定で開く) -----------------------------------
// 固定した対象の説明文はサーバ (/explain → Ollama) で生成する。生成は秒単位の
// 処理なので、同一ラベルはこちらでもキャッシュして再取得しない。サーバ側にも
// 同じキャッシュがあるが、重複して投げない意味でフロントの分も持つ。
let explainOpen = false;
let explainLabel = null;          // 窓を開いた対象のラベル。届いた応答が今の対象のものか判定に使う
const explainCache = new Map();   // label -> 説明文

function renderExplain() {
  explainWin.classList.toggle("on", explainOpen);
}

function openExplain(target) {
  explainOpen = true;
  explainLabel = target.label;
  eName.textContent = target.label_ja || target.label;
  const cached = explainCache.get(target.label);
  if (cached !== undefined) {
    eBody.textContent = cached;
    speak(target.label, cached);
  } else {
    eBody.textContent = "鑑定中…";
    requestExplain(target.label, target.label_ja || target.label);
  }
  renderExplain();
  repaint();  // 照準を消す(窓が中央で照準と重なるため)
}

function closeExplain() {
  stopSpeak();
  explainOpen = false;
  explainLabel = null;
  renderExplain();
  // 窓を開いている間は見失いによる自動解除を止めていた (理由:
  // docs/adr/0028-readout-holds-the-target.md)。閉じた時点で既に猶予を過ぎていれば、
  // ここで通常どおり解除する。そうしないと「窓は閉じたのに見失い状態だけ残る」になる。
  if (sel && selLostAt && Date.now() - selLostAt >= SEL_GRACE_MS) {
    const wasLocked = selLocked;
    clearSel();
    // repaint() は関数末尾で1回だけ行う。toast() ではなく setToast() を使い、
    // ここでの二重描画を避ける(見失いの猶予切れ分岐 visibleBoxes() と同じ作法)。
    setToast(wasLocked
      ? "固定を解除 ── 対象を見失いました"
      : "選択を解除 ── 対象を見失いました");
  }
  repaint();  // 消えた照準を戻す(解除した場合は上の報せも一緒に描かれる)
}

// 説明文を取りに行く。届いたときに窓が別の対象へ変わっていたら捨てる。
// 失敗は窓の中に載せる(toast も併用)。生成中・失敗とも innerHTML は使わない。
function requestExplain(label, name) {
  fetch("/explain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, ja: name }),
  })
    .then((r) => {
      if (!r.ok) {
        // 503 の detail(日本語メッセージ)をそのまま窓に載せるため拾う
        return r.json().then((d) => { throw new Error(d.detail || "鑑定に失敗しました"); });
      }
      return r.json();
    })
    .then((d) => {
      explainCache.set(d.label, d.text);
      if (explainOpen && explainLabel === d.label) {
        eBody.textContent = d.text;
        speak(d.label, d.text);
      }
    })
    .catch((err) => {
      if (!(explainOpen && explainLabel === label)) return;
      eBody.textContent = err.message || "鑑定に失敗しました";
      setToast("鑑定に失敗しました ── Ollama が起動しているか確認してください", 3000);
    });
}

// --- 鑑定文の読み上げ ---------------------------------------------------------
// 説明ウィンドウに鑑定文が出たら、それを声(サーバ側 /speak → say)で読み上げる。
// 音声はあくまで補助機能なので、失敗しても説明文の表示には一切影響させない
// (取得失敗のtoastも出さない。表示さえできれば鑑定としては成立するため)。
let speakAudio = null;   // 再生中の Audio
let speakSeq = 0;        // 遅れて届いた音声を捨てるための世代番号(窓を閉じた/対象が変わった後の再生を防ぐ)

// 音声を取りに行っている間だけ「読み上げ準備中…」を出す。声が鳴るまで数秒かかる
// ことがあり(docs/adr/0027-speech-prefetch-during-generation.md)、無表示だと
// 読み上げの有無が利用者に伝わらないため。
function setSpeakWait(on) {
  eWait.style.display = on ? "block" : "none";
}

function stopSpeak() {
  speakSeq++;
  setSpeakWait(false);
  if (speakAudio) {
    speakAudio.pause();
    URL.revokeObjectURL(speakAudio.src);
    speakAudio = null;
  }
}

function speak(label, text) {
  stopSpeak();
  const seq = speakSeq;
  setSpeakWait(true);
  fetch("/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, text }),
  })
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error("tts"))))
    .then((blob) => {
      if (seq !== speakSeq) return;   // 待つ間に窓が閉じた/対象が変わった
      setSpeakWait(false);
      const a = new Audio(URL.createObjectURL(blob));
      // 最後まで鳴り終えた分はその場で解放する。途中で止めた分は stopSpeak が始末する。
      a.addEventListener("ended", () => URL.revokeObjectURL(a.src));
      speakAudio = a;
      a.play().catch(() => {});   // 自動再生が拒否されても黙って諦める
    })
    .catch(() => {
      if (seq === speakSeq) setSpeakWait(false);   // 読み上げの失敗は鑑定の表示に影響させない
    });
}

// --- [DEBUG] 意匠(デザイン)の切り替え ---------------------------------------
// 枠の見た目候補を見比べる暫定機能。意匠決定後は本節・default節・themePartを削除
// (理由: docs/adr/0014-box-theme-and-dom-reuse.md)
const DEBUG_THEME_KEYS = true;
const THEMES = [
  { key: "1", id: "arcane", name: "壱:蒼の走査" },
  { key: "2", id: "grimoire", name: "弐:金の魔導書" },
  { key: "3", id: "sigil", name: "参:紫の秘術陣" },
  { key: "4", id: "legacy", name: "肆:素の緑(現行)" },
  { key: "5", id: "ring", name: "伍:白環の銘板" },
  { key: "6", id: "azure", name: "六:蒼環の菱標" },
];
// 採用する意匠。id で指す (理由: docs/adr/0014-box-theme-and-dom-reuse.md)
const DEFAULT_THEME_ID = "azure";   // 六: 蒼環の菱標
let themeIndex = Math.max(0, THEMES.findIndex((t) => t.id === DEFAULT_THEME_ID));
function setTheme(i) {
  themeIndex = i;
  stage.dataset.theme = THEMES[i].id;
  toast(`意匠: ${THEMES[i].name}`);
}
// フラグを false にしたら data-theme を付けない = ベースCSS(素の緑)に戻る。
if (DEBUG_THEME_KEYS) stage.dataset.theme = THEMES[themeIndex].id;
// --- [DEBUG] ここまで -------------------------------------------------------

// --- 対象の選択と固定 -------------------------------------------------------
// 選択(矢印で選ぶ)も固定(Enter)も、「毎フレーム決め直さずに同じ物体を追い続ける」
// 点は同じなので仕組みを共有する。トラッキングIDが無いため IoU で簡易追従する
// (理由: docs/adr/0012-lock-tracking-by-iou.md, docs/adr/0020-arrow-target-navigation.md)
const SEL_GRACE_MS = 2000;   // 見失ってもこの間は最後の枠を残す。過ぎたら自動解除
const SEL_IOU_SAME = 0.1;    // 同じラベルなら緩めに一致とみなす
const SEL_IOU_ANY = 0.4;     // ラベルが揺れた場合は重なりを厳しく見る
let sel = null;          // 選択/固定中の枠(検出1件のコピー)。null なら照準ベース
let selLocked = false;   // true なら固定(Enter済み)、false なら選択中
let selLostAt = 0;       // 見失った時刻(ms)。0 なら追従できている
let selGraceTimer = null;  // 猶予切れの描き直し予約(再追従できたら取り消す)
let currentTarget = null;  // 単体鑑定で今表示している1件(矢印の起点・Enterの対象)

// 保存結果などの一時メッセージ。HUDはWS受信ごとに描き直されるため、
// 期限(ms)を持たせて render() 側で合成する。
let toastText = "";
let toastUntil = 0;
let lastPayload = null;  // 直近のWSペイロード(WS受信を待たずに描き直すため)

// 新フレームが来ないと /ws は送信しないため、直近のペイロードで描き直す (理由: docs/adr/0010-client-side-display-policy.md)
function repaint() {
  if (lastPayload) render(lastPayload);
}

// メッセージの設定だけを行う。render() の中から呼んでも再帰しないよう、
// 描き直しを伴う toast() と分けてある。
function setToast(text, ms = 2000) {
  toastText = text;
  toastUntil = Date.now() + ms;
  // 期限切れでメッセージを消すための描き直し(新フレームが来ない場合の保険)
  setTimeout(() => { if (Date.now() >= toastUntil) repaint(); }, ms + 50);
}

function toast(text, ms = 2000) {
  setToast(text, ms);
  repaint();
}

// 調整はサーバに差分で投げるだけで、確定値は WS ペイロードの view で受け取る (理由: docs/adr/0010-client-side-display-policy.md)
function calib(body) {
  fetch("/calib", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {}); // 一時的な失敗は次のキー操作で取り戻せるので握りつぶす
}

// 現在値をサーバ側のファイルへ保存する。保存できたかどうかは調整のやり直しに
// 直結するので、こちらは結果をHUDに出す。
function saveCalib() {
  fetch("/calib/save", { method: "POST" })
    .then((r) => r.json())
    .then((d) => {
      toast(d.status === "ok" ? `saved → ${d.file}` : "save failed");
    })
    .catch(() => toast("save failed"));
}

// --- 鑑定法・円盤・調整モード・選択/固定の操作 ------------------------------
// 選択(と固定)を捨てて、照準ベースの表示に戻す。
function clearSel() {
  sel = null;
  selLocked = false;
  selLostAt = 0;
  clearTimeout(selGraceTimer);
  // 窓は固定対象の説明なので、固定が外れたら一緒に閉じる。render() の中からも
  // 呼ばれるが、closeExplain の描き直しは sel=null の後なので再帰は1段で止まる。
  if (explainOpen) closeExplain();
}

function setMode(next) {
  // 選び直しただけで解除はしない。円盤を開いて確認し、そのまま確定して閉じる
  // (カーソルは今の鑑定法から始まるので Enter の空打ちになる) のは自然な操作で、
  // そこで固定が外れると「固定は明示的な解除でだけ外れる」前提が壊れる。
  if (next === mode) {
    toast(`${MODE_LABEL[next]} ── 変更なし`);
    return;
  }
  mode = next;
  // 保存に失敗しても鑑定そのものは成立するので、読み込みと同じく黙って諦める。
  try { localStorage.setItem(MODE_KEY, mode); } catch {}
  // 鑑定法をまたいで選択を残すと、広域鑑定中に見えない固定状態が残るため解除する。
  clearSel();
  currentTarget = null;
  toast(
    next === MODE_MULTI
      ? `${MODE_LABEL[next]} ── 最大${MULTI_MAX}件`
      : `${MODE_LABEL[next]} ── ←↑↓→で対象を選択  Enterで固定`
  );
}

// 円盤が「現在」として指すモード。鑑定法と調整は排他に扱うので常に1つに決まる
// (理由: docs/adr/0021-mode-wheel-with-calibration.md)
function activeMode() {
  return calibMode ? MODE_CALIB : mode;
}

// 円盤で選んだモードを適用する。調整を選べば鑑定法は覚えたまま調整へ入り、
// 鑑定法を選べば調整から抜ける。どちらも「選び直しただけ」なら解除はしない。
function selectMode(id) {
  if (id === MODE_CALIB) {
    if (calibMode) {
      toast("調整 ── 変更なし");
      return;
    }
    setCalibMode(true);
    return;
  }
  if (calibMode) {
    // 鑑定法を選んだので調整から抜ける。setCalibMode は終了の報せを出すため、
    // ここでは旗だけ倒して報せは鑑定法側にまとめる。
    calibMode = false;
    if (id === mode) {
      toast(`調整を終えて ${MODE_LABEL[id]} へ`);
      return;
    }
  }
  setMode(id);
}

// 円盤の項目を作って円周上に並べる。角度は項目数から出すので、MODES に足すだけで
// 配置が追従する。12時から時計回りに等間隔(壱=12時 / 弐=4時 / 参=8時)。
// 盤は → で左回りに回り、次の項目が12時へ滑ってくる。並びを優先して
// この向きに戻した (理由: docs/adr/0031-wheel-layout-restored.md)。
function buildWheel() {
  wheelRotor = document.createElement("div");
  wheelRotor.className = "w-rotor";
  MODES.forEach((m, i) => {
    const el = document.createElement("div");
    el.className = "w-item";
    const num = document.createElement("span");
    num.className = "w-num";
    num.textContent = m.num;
    const key = document.createElement("span");
    key.className = "w-key";
    key.textContent = m.key;
    const name = document.createElement("span");
    name.textContent = m.name;
    const now = document.createElement("span");
    now.className = "w-now";
    now.textContent = "現在";
    el.append(num, key, name, now);
    const a = ((-90 + (360 / MODES.length) * i) * Math.PI) / 180;
    el.style.left = (50 + Math.cos(a) * WHEEL_R) + "%";
    el.style.top = (50 + Math.sin(a) * WHEEL_R) + "%";
    wheelRotor.appendChild(el);
    wheelItems.push(el);
  });
  wheel.appendChild(wheelRotor);
}
buildWheel();

// 項目1つ分の角度と、その項目を 12時へ持ってくるための盤の角度。
const wheelStep = () => 360 / MODES.length;
const wheelAngleFor = (i) => -i * wheelStep();

// 隣へ1つ回す。delta は +1 が次、-1 が前。
function turnBy(delta) {
  const n = MODES.length;
  wheelCursor = (wheelCursor + delta + n) % n;
  wheelTurn -= delta * wheelStep();
}

// 円盤の見た目だけを更新する。WSペイロードに依存しないので、まだ1フレームも
// 受け取っていない状態で 'm' を押しても開ける (repaint() は payload 待ちのため)。
function renderWheel() {
  wheel.classList.toggle("on", wheelOpen);
  wheelRotor.style.transform = `rotate(${wheelTurn}deg)`;
  // 盤の回転を打ち消して、項目の文字は常に水平に保つ。
  const upright = `translate(-50%, -50%) rotate(${-wheelTurn}deg)`;
  for (let i = 0; i < wheelItems.length; i++) {
    wheelItems[i].style.transform = upright;
    wheelItems[i].classList.toggle("on", i === wheelCursor);
    wheelItems[i].classList.toggle("cur", MODES[i].id === activeMode());
  }
}

// 無操作での自動キャンセル。矢印でカーソルを動かしている間は延長する。
function bumpWheelTimer() {
  clearTimeout(wheelTimer);
  wheelTimer = setTimeout(() => {
    wheelOpen = false;
    renderWheel();
    repaint();
  }, WHEEL_MS);
}

function openWheel() {
  wheelOpen = true;
  // 今いるモードが指標に来た状態で開く。Enter を空打ちしても何も変わらない。
  wheelCursor = Math.max(0, MODES.findIndex((m) => m.id === activeMode()));
  // 閉じている間は transition が働かないので、ここで直に合わせれば回らずに出る。
  wheelTurn = wheelAngleFor(wheelCursor);
  bumpWheelTimer();
  renderWheel();
  repaint();  // 照準を消す(盤の中央見出しと重なるため)
}

function closeWheel() {
  wheelOpen = false;
  clearTimeout(wheelTimer);
  renderWheel();
}

// 円盤が開いている間のキー。ここで必ず握り潰し、本来の機能は動かさない
// (理由: docs/adr/0019-key-binding-policy-v2.md)
function onWheelKey(e) {
  e.preventDefault();
  // 盤そのものを回す。→↓ で次が、←↑ で前が、12時の指標へ滑ってくる。
  if (e.key === "ArrowRight" || e.key === "ArrowDown") {
    turnBy(1);
    bumpWheelTimer();
    renderWheel();
    return;
  }
  if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
    turnBy(-1);
    bumpWheelTimer();
    renderWheel();
    return;
  }
  if (e.key === "Enter") {
    closeWheel();
    selectMode(MODES[wheelCursor].id);
    return;
  }
  const i = MODES.findIndex((m) => m.key === e.key);
  if (i >= 0) {
    closeWheel();
    selectMode(MODES[i].id);
    return;
  }
  // 'm' や Esc を含め、それ以外のキーは取り消しにだけ使う。
  closeWheel();
  toast("モードの選択を取り消しました");
}

function setCalibMode(on) {
  calibMode = on;
  toast(
    on ? "調整モード ── ←↑↓→:位置  +/-:ズーム  f/v:文字サイズ  0:リセット  s:保存  m:モード  c/Esc:終了"
       : "調整モードを終了",
    on ? 3000 : 1600
  );
}

// /video の MJPEG はサーバの再起動やカメラ再接続に伴う配信の切れ目で死ぬことがある。
// img の error はストリーム1本につき1度しか上がらないため、死んだら付け直すまで
// 画面が真っ黒のまま残る。約1秒後に src を付け直して読み込ませ、復帰まで繰り返す。
// 同じ URL を付け直すとブラウザが再接続しないことがあるため、クエリに時刻を足して
// 毎回別の URL 扱いにする。
const VIDEO_SRC = "/video?annotate=0";
const VIDEO_RETRY_MS = 1000;
let videoRetryTimer = null;
let videoBroken = false;   // ストリームが死んだままか。'd' の再表示時に拾い直すかの判定に使う
function retryVideo() {
  // 連打でタイマーが積まれないよう、予約が無いときだけ1回予約する。
  if (videoRetryTimer) return;
  videoRetryTimer = setTimeout(() => {
    videoRetryTimer = null;
    // 'd' で非表示にされている間は試さない。死んだままの印(videoBroken)は残し、
    // 再表示したときに拾い直す。
    if (!showVideo) return;
    videoBroken = false;
    video.src = `${VIDEO_SRC}&r=${Date.now()}`;
  }, VIDEO_RETRY_MS);
}
video.addEventListener("error", () => {
  videoBroken = true;
  retryVideo();
});

function toggleVideo() {
  showVideo = !showVideo;
  video.style.display = showVideo ? "block" : "none";
  // 生きているストリームは付け直さない(映像が一瞬途切れるだけの無駄になる)。
  // 非表示中に切れていたときだけ、再表示に合わせて拾い直す。
  if (showVideo && videoBroken) retryVideo();
}

// 矢印で対象を選び替える。選び直した対象は「選択中」であって固定ではない。
// 固定中に矢印を押したときも選択中へ戻す(固定は Enter でやり直す)。
function navigate(key) {
  if (mode !== MODE_SINGLE) {
    toast("対象の選択は単体鑑定(m→1)のみ");
    return;
  }
  if (!lastPayload) return;
  const next = navTarget(lastPayload.detections, viewRect(), NAV_DIR[key]);
  if (!next) {
    toast("その向きに対象がありません");
    return;
  }
  sel = { ...next };
  selLocked = false;
  selLostAt = 0;
  clearTimeout(selGraceTimer);
  toast(`選択 ── ${next.label_ja || next.label}`);
}

function toggleLock() {
  if (mode !== MODE_SINGLE) {
    toast("鑑定の固定は単体鑑定(m→1)のみ");
    return;
  }
  if (sel && selLocked) {
    clearSel();
    toast("鑑定の固定を解除");
    return;
  }
  // 選択中ならその対象を、選択していなければ照準が指している対象を固定する。
  const target = sel || currentTarget;
  if (!target) {
    toast("固定する対象がありません");
    return;
  }
  sel = { ...target };
  selLocked = true;
  selLostAt = 0;
  clearTimeout(selGraceTimer);
  toast(`鑑定を固定 ── ${target.label_ja || target.label}`);
  // 固定した瞬間に説明ウィンドウを出す。解除のときは出さない
  // (理由: docs/adr/0024-llm-object-explanation.md)
  openExplain(target);
}

// Esc は「今かかっている状態を1つ外す」。円盤 → 調整モード → 選択/固定 の順に外すが、
// 円盤と調整モードの Esc は keydown の先頭でそれぞれ処理して return しているため、
// ここへ来るのは「どちらでもない」ときだけ。調整モードの Esc を消すなら
// keydown 側 (case "c": case "Escape":) を直すこと。ここには無い。
function onEscape() {
  if (sel) {
    const wasLocked = selLocked;
    clearSel();
    toast(wasLocked ? "鑑定の固定を解除" : "選択を解除 ── 照準に戻ります");
  }
}

document.addEventListener("keydown", (e) => {
  // 修飾キー付きはブラウザ標準ショートカットなので触らない
  // (Shift は '+' の入力に要るので除外しない。理由: docs/adr/0019-key-binding-policy-v2.md)
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // 円盤が開いている間は、すべてのキーを盤の操作として扱う。
  if (wheelOpen) {
    onWheelKey(e);
    return;
  }

  // 説明ウィンドウが開いている間は、閉じることだけを担う。Esc が「解除」に
  // 染まっていると窓を閉じるたびに固定まで外れてしまうため、窓を閉じても
  // 選択・固定の状態は保つ (理由: docs/adr/0019-key-binding-policy-v2.md)
  if (explainOpen) {
    e.preventDefault();
    if (e.key === "Enter" || e.key === "Escape") closeExplain();
    return;
  }

  // 調整モード中は調整のキーだけを見る。鑑定の操作(Enter/矢印の選択)は動かさない。
  // 例外は 'm' で、円盤から調整を抜けるために効かせる。
  if (calibMode) {
    switch (e.key) {
      // 切り抜き位置を動かす向き。↑ なら「より上」が見えるようになる
      // (check_camera の w/a/s/d と同じ意味)。
      case "ArrowUp":
        e.preventDefault(); calib({ dy: -STEP_OFFSET });
        break;
      case "ArrowDown":
        e.preventDefault(); calib({ dy: STEP_OFFSET });
        break;
      case "ArrowLeft":
        e.preventDefault(); calib({ dx: -STEP_OFFSET });
        break;
      case "ArrowRight":
        e.preventDefault(); calib({ dx: STEP_OFFSET });
        break;
      case "+": case "=":
        calib({ dzoom: STEP_ZOOM });
        break;
      case "-":
        calib({ dzoom: -STEP_ZOOM });
        break;
      case "0":
        calib({ reset: true });
        applyUiScale(1.0);
        toast("調整をリセットしました (文字サイズも既定へ)");
        break;
      // UIの文字サイズ。'+/-' はズームに取られているので隣の f/v を当てる。
      case "f":
        toast(`文字サイズ ${applyUiScale(uiScale + STEP_UI_SCALE)}`);
        break;
      case "v":
        toast(`文字サイズ ${applyUiScale(uiScale - STEP_UI_SCALE)}`);
        break;
      case "s":
        saveCalib();
        break;
      case "c": case "Escape":
        setCalibMode(false);
        break;
      // 円盤に調整を載せた以上、そこから戻れないと入口だけ片道になる。鑑定の操作で
      // 調整中も効くのは 'm' だけ (理由: docs/adr/0021-mode-wheel-with-calibration.md)
      case "m":
        openWheel();
        break;
      // 映像のON/OFFは調整と直交する(映像を見ながら合わせる)ので調整中も効かせる。
      case "d":
        toggleVideo();
        break;
      default:
        // [DEBUG] 意匠は調整中も見比べたい。意匠決定後はこの節ごと削除する。
        if (DEBUG_THEME_KEYS) {
          const i = THEMES.findIndex((t) => t.key === e.key);
          if (i >= 0) setTheme(i);
        }
    }
    return;
  }

  switch (e.key) {
    case "m":
      openWheel();
      break;
    case "c":
      setCalibMode(true);
      break;
    case "ArrowUp": case "ArrowDown": case "ArrowLeft": case "ArrowRight":
      e.preventDefault();
      navigate(e.key);
      break;
    case "Enter":
      e.preventDefault();
      toggleLock();
      break;
    case "Escape":
      onEscape();
      break;
    case "d":
      toggleVideo();
      break;
    // 調整は 'c' の中だけに置いた。押し間違いを黙って捨てず行き先を案内する。
    case "+": case "=": case "-": case "0": case "s": case "f": case "v":
      toast("ズーム・位置・文字サイズの調整は 'c' で調整モードへ");
      break;
    default:
      // [DEBUG] 意匠の切り替え。意匠決定後はこの節ごと削除する。
      if (DEBUG_THEME_KEYS) {
        const i = THEMES.findIndex((t) => t.key === e.key);
        if (i >= 0) setTheme(i);
      }
  }
});

const signed = (v) => (v < 0 ? "" : "+") + v.toFixed(2);

// 2つの正規化bboxの IoU(重なり具合)。ロック対象の追従に使う。
function iou(a, b) {
  const x1 = Math.max(a.x, b.x), y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w), y2 = Math.min(a.y + a.h, b.y + b.h);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const uni = a.w * a.h + b.w * b.h - inter;
  return uni > 0 ? inter / uni : 0;
}

// 映像が実際に占める矩形(レターボックスを除いた領域)。枠はこの矩形基準で配置する
// (理由・実測値: docs/adr/0013-overlay-coordinate-basis.md)
function viewRect() {
  const W = stage.clientWidth, H = stage.clientHeight;
  const s = lastPayload && lastPayload.source_size;
  if (!s || !s.w || !s.h) return { x: 0, y: 0, w: W, h: H };
  const scale = Math.min(W / s.w, H / s.h);
  const vw = s.w * scale, vh = s.h * scale;
  return { x: (W - vw) / 2, y: (H - vh) / 2, w: vw, h: vh };
}

// 画面中心からの距離を「表示ピクセル」で測る (理由: docs/adr/0011-gaze-target-selection.md)
function centerDistPx(d, r) {
  const dx = (d.x + d.w / 2 - 0.5) * r.w;
  const dy = (d.y + d.h / 2 - 0.5) * r.h;
  return Math.hypot(dx, dy);
}

// 照準に入らなかった場合に拾う範囲。表示領域の短辺に対する比
// (理由: docs/adr/0011-gaze-target-selection.md)
const GAZE_RADIUS = 0.25;

// 単体鑑定で表示する1件 = 「今見ているもの」。
//   1. 画面中心(照準)を含む検出。複数あれば面積の小さい方(より具体的な対象)
//   2. 無ければ、照準から GAZE_RADIUS 以内で最も近い検出
//   3. どちらも無ければ null (枠を出さない)
function gazeTarget(dets, r) {
  let hit = null;
  for (const d of dets) {
    if (d.x <= 0.5 && 0.5 <= d.x + d.w && d.y <= 0.5 && 0.5 <= d.y + d.h) {
      if (!hit || d.w * d.h < hit.w * hit.h) hit = d;
    }
  }
  if (hit) return hit;

  const limit = GAZE_RADIUS * Math.min(r.w, r.h);
  let best = null, bestDist = Infinity;
  for (const d of dets) {
    const dist = centerDistPx(d, r);
    if (dist > limit) continue;
    // 同距離なら信頼度の高い方。ちらつきの原因になる同点を残さない。
    if (dist < bestDist || (dist === bestDist && best && d.confidence > best.confidence)) {
      bestDist = dist;
      best = d;
    }
  }
  return best;
}

// --- 矢印による対象の選び替え -----------------------------------------------
// 画面座標での向き。↑ が -y。
const NAV_DIR = {
  ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
};
// 進む向きから横にずれた分をどれだけ嫌うか。大きいほど「まっすぐ隣」を選ぶ
const NAV_CROSS_WEIGHT = 2;
// 中心がほぼ同じものを「その向きにある」と扱わないための最小値(表示ピクセル)
const NAV_MIN_PX = 2;
// 横ずれを許す上限。前進量の何倍まで「その向きにある」と認めるか(2 = 約63°)。
// 減点だけで足切りしないと、真横に大きく離れた検出が「わずかに前」という理由で
// 勝ってしまう。前進 5px・横 278px の検出が、前進 400px の真上の検出に勝つ事象を
// テストで確認した (理由: docs/adr/0020-arrow-target-navigation.md)
const NAV_CONE = 2;

// 今の対象(無ければ照準)から見て、指定の向きにある最も近い検出を返す。
// 距離は表示ピクセルで測る (理由: docs/adr/0011-gaze-target-selection.md)。
// 半径 (GAZE_RADIUS) では絞らない。矢印は「意図して選びに行く」操作なので、
// 照準から遠い対象へも届かせる (理由: docs/adr/0020-arrow-target-navigation.md)
function navTarget(dets, r, dir) {
  if (!dir) return null;
  const ux = dir[0], uy = dir[1];
  const cur = currentTarget;
  const cx = (cur ? cur.x + cur.w / 2 : 0.5) * r.w;
  const cy = (cur ? cur.y + cur.h / 2 : 0.5) * r.h;
  let best = null, bestScore = Infinity;
  for (const d of dets) {
    const px = (d.x + d.w / 2) * r.w, py = (d.y + d.h / 2) * r.h;
    const main = (px - cx) * ux + (py - cy) * uy;             // 進む向きの成分
    if (main <= NAV_MIN_PX) continue;   // 真横・後ろ・自分自身は候補にしない
    const cross = Math.abs((px - cx) * uy - (py - cy) * ux);  // 横にずれた分
    if (cross > main * NAV_CONE) continue;   // 向きから外れすぎたものは候補にしない
    const score = main + cross * NAV_CROSS_WEIGHT;
    // 同点なら信頼度の高い方。ちらつきの原因になる同点を残さない。
    if (score < bestScore || (score === bestScore && best && d.confidence > best.confidence)) {
      bestScore = score;
      best = d;
    }
  }
  return best;
}

// 選択/固定中の枠に対応する検出を探す。ラベル一致を優先し、揺れたら重なりで拾い直す
// (理由: docs/adr/0012-lock-tracking-by-iou.md)
function trackSel(dets) {
  let same = null, sameIou = 0, any = null, anyIou = 0;
  for (const d of dets) {
    const v = iou(d, sel);
    if (d.label === sel.label && v > sameIou) { sameIou = v; same = d; }
    if (v > anyIou) { anyIou = v; any = d; }
  }
  if (same && sameIou >= SEL_IOU_SAME) return same;
  if (any && anyIou >= SEL_IOU_ANY) return any;
  return null;
}

// 実際に描く検出のリストを決める。_state は枠の見た目("picked"/"locked"/"lost")用。
function visibleBoxes(dets, r) {
  if (mode === MODE_MULTI) {
    currentTarget = null;
    return [...dets].sort((a, b) => b.confidence - a.confidence).slice(0, MULTI_MAX);
  }

  if (sel) {
    const hit = trackSel(dets);
    if (hit) {
      sel = { ...hit };   // 追従できたので枠を更新
      selLostAt = 0;
      clearTimeout(selGraceTimer);  // 戻ってきたので猶予切れの予約は不要
    } else if (!selLostAt) {
      selLostAt = Date.now();
      // 新フレームが来なくても猶予切れで解除されるよう、期限後の描き直しを予約する。
      selGraceTimer = setTimeout(repaint, SEL_GRACE_MS + 50);
    } else if (!explainOpen && Date.now() - selLostAt >= SEL_GRACE_MS) {
      // 窓(読み上げ中の説明ウィンドウ)が開いている間は自動解除しない。対象が
      // カメラから外れても、開いている間は鑑定を継続中として扱う
      // (理由: docs/adr/0028-readout-holds-the-target.md)。窓を閉じたときの
      // 解除は closeExplain() 側で行う。
      const wasLocked = selLocked;
      clearSel();
      // render 中なので描き直しはしない
      setToast(wasLocked
        ? "固定を解除 ── 対象を見失いました"
        : "選択を解除 ── 対象を見失いました");
    }
    if (sel) {
      currentTarget = sel;
      const state = selLostAt ? "lost" : selLocked ? "locked" : "picked";
      return [{ ...sel, _state: state }];
    }
  }

  currentTarget = gazeTarget(dets, r);
  return currentTarget ? [currentTarget] : [];
}

// 枠の要素は作り直さず使い回す (理由: docs/adr/0014-box-theme-and-dom-reuse.md)
// 子要素は [label, d1, d2] の順で固定し、余った枠は display:none で隠す。
// 使い回すのは「n番目の枠」であって物体ではないため、広域鑑定では同じスロットが
// 別の物体を指すことがある(表示自体はどちらでも壊れない)。
const boxPool = [];
function boxAt(i) {
  if (boxPool[i]) return boxPool[i];
  const box = document.createElement("div");
  const label = document.createElement("div");
  label.className = "label";
  box.appendChild(label);
  const d1 = document.createElement("div");
  d1.className = "d1";
  box.appendChild(d1);
  const d2 = document.createElement("div");
  d2.className = "d2";
  box.appendChild(d2);
  overlay.appendChild(box);
  boxPool[i] = box;
  return box;
}

function render(payload) {
  lastPayload = payload;
  const r = viewRect();
  const boxes = visibleBoxes(payload.detections, r);
  // 照準は単体鑑定でだけ意味を持つ(広域鑑定は中心で絞り込まない)。
  // 円盤や説明ウィンドウが中央にある間は重なるので消す。
  reticle.style.display = mode === MODE_SINGLE && !wheelOpen && !explainOpen ? "block" : "none";
  // 対象を選んでいる間、照準はもう対象を決めていないので薄くする
  // (理由: docs/adr/0020-arrow-target-navigation.md)
  reticle.classList.toggle("dim", !!sel);
  for (let i = 0; i < boxes.length; i++) {
    const d = boxes[i];
    const box = boxAt(i);
    box.className = d._state ? `box ${d._state}` : "box";
    box.style.display = "block";
    box.style.left = (r.x + d.x * r.w) + "px";
    box.style.top = (r.y + d.y * r.h) + "px";
    box.style.width = (d.w * r.w) + "px";
    box.style.height = (d.h * r.h) + "px";
    // 日本語表示名(label_ja)を優先。無ければ英語ラベル。
    const name = d.label_ja || d.label;
    const mark = d._state === "locked" ? " [固定]"
      : d._state === "picked" ? " [選択]"
      : d._state === "lost" ? " [見失い]" : "";
    box.children[0].textContent = `${name} ${(d.confidence * 100) | 0}%${mark}`;
  }
  for (let i = boxes.length; i < boxPool.length; i++) {
    boxPool[i].style.display = "none";
  }
  // --- 鑑定盤 ---
  // 状態は「今どう見ているか」を1語で出す (理由: docs/adr/0010-client-side-display-policy.md)
  pState.textContent = calibMode ? "調整中"
    : mode === MODE_MULTI ? "広域走査"
    : !sel ? "走査中"
    : selLostAt ? "見失い"
    : selLocked ? "固定" : "選択中";
  pModeName.textContent = MODE_LABEL[mode];
  // 調整モードの案内窓。左上の盤(#panel)ではなく中央の窓へ出す
  calibGuide.classList.toggle("on", calibMode);
  // カメラ切断検知中は動画の上に再接続中表示を重ねる。サーバは切断中、
  // 黒地に「再接続中」と焼いたプレースホルダー映像と一緒に camera_status を
  // "reconnecting" で配信する (理由: docs/adr/0035-camera-auto-reconnect.md)
  reconnect.classList.toggle("on", payload.camera_status === "reconnecting");
  const msg = Date.now() < toastUntil ? toastText : "";
  pMsg.textContent = msg;
  pMsg.style.display = msg ? "block" : "none";
  // 鑑定法が変わればカーソルと「現在」の印もずれるので、盤も描き直す。
  renderWheel();

  // --- デバッグ行 ---
  const v = payload.view;
  const calibText = v
    ? ` | zoom ${v.zoom.toFixed(2)} off(${signed(v.offset_x)},${signed(v.offset_y)})`
    : "";
  // [DEBUG] 意匠決定後はこの1行と themePart の連結を削除する。
  const themePart = DEBUG_THEME_KEYS
    ? ` | 意匠 ${THEMES[themeIndex].name}(1-${THEMES.length}で切替)` : "";
  const hint = calibMode
    ? ` | 調整中: ←↑↓→:位置  +/-:ズーム  f/v:文字サイズ  0:リセット  s:保存  m:モード  c/Esc:終了`
    : mode === MODE_SINGLE
    ? ` | m:モード  ←↑↓→:対象選択  Enter:固定  Esc:解除  c:調整  'd':映像`
    : ` | m:モード  c:調整  'd':映像`;
  // 表示は絞っても objects は検出総数を出す(絞り込みで見えていない数が分かる)。
  hud.textContent =
    `fps ${payload.fps} | objects ${payload.detections.length}` +
    `${calibText}${themePart}${hint}`;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => render(JSON.parse(ev.data));
  ws.onclose = () => { hud.textContent = "disconnected — retrying…";
    setTimeout(connect, 1000); };
}
connect();
