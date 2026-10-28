const $ = (s) => document.querySelector(s);
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => Math.round(n).toLocaleString("es-ES");
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };

// ---------------------------------------------------------------- routing: "" inicio | #generar | #archivo | #archivo/<id> | #continuar/<id>
function route() {
  const [name, id] = location.hash.slice(1).split("/");
  if (name === "continuar" && id) return seedFromArchive(+id, "threads");
  if (name === "continuar-gen" && id) return seedFromArchive(+id, "generated");
  const page = name === "archivo" || name === "generado" ? "archivo" : name === "generar" ? "generar" : "intro";
  for (const p of ["intro", "generar", "archivo"]) $(`#page-${p}`).hidden = p !== page;
  document.querySelectorAll(".tabs .nav").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  if (page === "intro" && !$("#i-total").textContent) intro();
  if (page === "generar") fitTitle();  // the textarea measures 0 while its page is hidden
  if (page === "archivo") {
    const reading = !!id;
    $("#b-filters").hidden = reading;
    $("#stats").hidden = reading;
    $("#page-archivo").classList.toggle("reading", reading);
    if (id) openThread(+id, name === "generado" ? "generated" : "threads");
    else { $("#b-view").hidden = true; $("#b-list").hidden = !$("#b-list").children.length; }  // threads only once asked for
    if (!$("#s-total").textContent) stats();
  }
}
window.addEventListener("hashchange", route);

async function seedFromArchive(id, src) {  // #continuar/<id>: the thread's start becomes the prompt
  const t = await (await fetch(`/api/${src}/${id}`)).json();
  state.title = t.title;
  state.posts = t.posts.slice(0, Math.min(3, t.posts.length));
  $("#tipo").value = t.tipo ?? ""; $("#calidad").value = t.calidad ?? "";
  $("#paginas").value = t.pages_bucket ?? ""; $("#etiquetas").value = t.tags ?? "";
  shared = null;
  history.replaceState(null, "", "#generar");
  route();
  redraw();
}

// ---------------------------------------------------------------- the forum dress: nicks, ranks and post times are invented,
// deterministic per (thread title, author), so a thread keeps its cast while it grows
const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };
const NICK_A = ["Pepe", "Manolo", "Rober", "Fran", "Nacho", "Dani", "Toni", "Javi", "Sergio", "Rafa", "Kike", "Ramon", "Lolo", "Juanjo", "Chema", "Xavi", "Pako", "Richy", "Ivan", "Borja"];
const NICK_B = ["Lugo", "Cabra", "Turbo", "Diesel", "Kebab", "Pelon", "Pocho", "Nocilla", "Zumo", "Bollo", "Pantoja", "Melon", "Choped", "Furgo", "Garrafon", "Chusta", "Canuto", "Pipas", "Bocata", "Betis"];
const NICK_PAT = [(a, b, n) => `${a}-${b}`, (a, b, n) => `${b}${n}`, (a, b, n) => `${a}_${b}`, (a, b, n) => `El${b}`, (a, b, n) => `${a.toLowerCase()}${n}`, (a, b, n) => `${b}iiii`, (a, b, n) => `Sr${b}`, (a, b, n) => `${b}${a}${n % 10}`];
const RANGOS = ["ForoCoches: Usuario", "ForoCoches: Usuario", "ForoCoches: Miembro", "ForoCoches: Miembro", "ForoCoches: Veterano", "ForoCoches: Forero"];
function persona(author, seed) {
  const h = hash(`${seed}|${author}`);
  const nick = NICK_PAT[h % NICK_PAT.length](NICK_A[(h >>> 3) % NICK_A.length], NICK_B[(h >>> 8) % NICK_B.length], 1 + ((h >>> 13) % 98));
  return { nick, rango: RANGOS[(h >>> 16) % RANGOS.length], hue: h % 360 };
}
function postDate(seed, i) {  // the thread opened yesterday evening; replies trail at random minutes, maybe into today
  const h = hash(`${seed}|t`);
  let m = (17 + ((h >>> 13) % 6)) * 60 + ((h >>> 16) % 60);
  for (let k = 1; k < i; k++) m += 1 + (hash(`${seed}|${k}`) % 40);
  const day = m >= 1440 ? "Hoy" : "Ayer";
  m %= 1440;
  return `${day} ${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

// ---------------------------------------------------------------- thread rendering (shared by the playground and the archive)
const nick = (a, seed) => `<span class="nk">${esc(persona(a, seed).nick)}</span>`;
const renderBody = (text, seed = "") => esc(text)
  .replace(/(?<!\[cita=)\b(OP|forero_[a-z])\b(?!\])/g, (a) => nick(a, seed))  // mentions inside the text
  .replace(/\[cita=([^\]]+)\]([\s\S]*?)\[\/cita\]\n?/g, (_, a, q) => `<blockquote${q.length > 400 ? ' class="long"' : ""}><b>${nick(a, seed)}</b> ${q}</blockquote>`);
// long quotes start collapsed, like the forum's; a click opens them
document.addEventListener("click", (e) => { const q = e.target.closest("blockquote.long"); if (q) q.classList.toggle("open"); });

function postEl(i, author, body, { live = false, citable = false, seed = "" } = {}) {
  const p = author.includes("?") || author === "…" ? { nick: "…", rango: "", hue: 0 } : persona(author, seed);
  return el(`<li class="post${author === "OP" ? " op" : ""}${live ? " live" : ""}">
    <div class="fh"><i class="ava" style="--h:${p.hue}"></i><div class="nm"><b class="nick">${esc(p.nick)}</b><span class="rango">${p.rango}</span></div>
      <div class="pt"><span>${postDate(seed, i)}</span><span class="pn">#${i}</span></div></div>
    <div class="body">${renderBody(body, seed)}</div>
    ${citable ? `<div class="pf"><button class="cite" data-n="${i}" type="button">Citar</button></div>` : ""}</li>`);
}

function renderPosts(list, posts, opts = {}) {
  list.innerHTML = "";
  const seed = opts.seed ?? state.title;
  posts.forEach(([a, b], i) => list.appendChild(postEl(i + 1, a, b, { ...opts, seed })));
  if (!posts.length) list.appendChild(el(`<li class="empty">Nada todavía. Escribe el primer post como OP, o deja que el modelo abra el hilo.</li>`));
}

// ---------------------------------------------------------------- inicio
const TIPO_DESC = {
  historia: "relato en primera persona, el tocho clásico", pregunta: "pide ayuda, datos u opinión", debate: "polémica para discutir: política, actualidad",
  hipotetico: "¿qué harías si…?", noticia: "noticia o enlace comentado", humor: "coña, memes, trolleo", compraventa: "anuncios; fuera del entrenamiento", otro: "lo demás",
};
const row = (k, cells, n, d = "") => `<tr><td class="k">${k}</td><td><div class="track">${cells}</div></td><td class="n">${n}</td>${d ? `<td class="d">${d}</td>` : ""}</tr>`;

async function intro() {
  const [s, rows, gen, curves] = await Promise.all(["/static/data/stats.json", "/api/threads?limit=5", "/api/generated?limit=5", "/static/data/curves.json"].map((u) => fetch(u).then((r) => r.json())));
  $("#i-total").innerHTML = `<b>${fmt(s.total.n)} hilos</b> ${fmt(s.total.posts)} posts, ${(s.total.chars / 1e6).toLocaleString("es-ES", { maximumFractionDigits: 0 })} millones de caracteres`;
  const trend = (list, href, extra) => list.map((r, i) => `<li><a href="${href(r)}"><b>${i + 1}</b><span class="tt">${esc(r.title)}</span>
    <span class="tm"><span>💬 ${r.n_posts}</span><span>@${esc(persona("OP", r.title).nick)}</span>${extra(r)}</span></a></li>`).join("");
  $("#trending").innerHTML = trend(rows, (r) => `#archivo/${r.thread_id}`, () => "");
  $("#trend-gen").innerHTML = gen.length ? trend(gen, (r) => `#generado/${r.id}`, (r) => `<span class="md">${esc(r.model || "modelo")}</span>`)
    : `<li class="dim small" style="padding:.6rem 0">Todavía nada. Genera un hilo y compártelo.</li>`;
  const tipos = {};
  for (const r of s.by_tipo_calidad) if (r.tipo !== "?") tipos[r.tipo] = (tipos[r.tipo] ?? 0) + r.n;
  const order = Object.entries(tipos).sort((a, b) => b[1] - a[1]), max = order[0][1];
  $("#i-tipos").innerHTML = order.map(([t, n]) => row(t, `<div class="seg${t === "compraventa" ? " b" : ""}" style="width:${(100 * n) / max}%"></div>`, `<b>${fmt(n)}</b>`, TIPO_DESC[t] ?? "")).join("");
  drawCurves(curves);
  if (Object.values(curves).some((r) => r.live)) setInterval(async () => drawCurves(await (await fetch("/static/data/curves.json", { cache: "no-cache" })).json()), 60000);
}

function drawCurves(runs) {
  const W = 720, H = 240, L = 36, R = 10, T = 10, B = 26;
  const colors = { v0: "#b5b5b5", v1: "#333", v2: "#ff5845", v3: "#3fafbc" };
  const names = Object.keys(runs).filter((k) => runs[k].train.length);
  const toM = (r, step) => (step * r.tokens_per_step) / 1e6;
  const xmax = Math.max(...names.map((k) => toM(runs[k], runs[k].max_steps ?? runs[k].train.at(-1)[0])));
  const ys = names.flatMap((k) => runs[k].train.slice(5).map((p) => p[1]));
  const ymin = Math.floor(Math.min(...ys) * 10) / 10, ymax = Math.min(Math.ceil(Math.max(...ys) * 10) / 10, ymin + 0.8);
  const X = (m) => L + ((W - L - R) * m) / xmax, Y = (v) => T + ((H - T - B) * (ymax - Math.min(Math.max(v, ymin), ymax))) / (ymax - ymin);
  let svg = "";
  for (let v = ymin; v <= ymax + 1e-9; v += 0.1) svg += `<line x1="${L}" y1="${Y(v)}" x2="${W - R}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${v.toFixed(1)}</text>`;
  for (let m = 0; m <= xmax; m += 100) svg += `<text x="${X(m)}" y="${H - 8}" text-anchor="middle">${m}M</text>`;
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  for (const k of names) {
    const r = runs[k], c = colors[k] ?? "#3fafbc";
    let ema = null;
    const train = r.train.map(([s, l]) => { ema = ema === null ? l : ema * 0.85 + l * 0.15; return [X(toM(r, s)), Y(ema)]; });
    svg += `<path d="${path(train)}" stroke="${c}" stroke-width="1.5"/>`;
  }
  $("#curves").innerHTML = svg;
  const live = (l) => l ? ` · <b>entrenando ahora</b>: ${(100 * l.step / l.max_steps).toFixed(0)}%, termina ~${new Date(l.eta * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })}` : "";
  $("#curves-legend").innerHTML = names.map((k) => `<span><i class="sw" style="background:${colors[k] ?? "#3fafbc"}"></i> ${k}: ${fmt(toM(runs[k], runs[k].train.at(-1)[0]))}M tokens${live(runs[k].live)}</span>`).join("");
}

// ---------------------------------------------------------------- playground
const state = { title: "", posts: [] };
try { Object.assign(state, JSON.parse(localStorage.getItem("fc-thread") || "{}")); } catch {}
const save = () => { try { localStorage.setItem("fc-thread", JSON.stringify(state)); } catch {} };
function fitTitle() { const t = $("#title"); t.style.height = "0"; t.style.height = t.scrollHeight + "px"; }
function redraw() { $("#title").value = state.title; fitTitle(); renderPosts($("#posts"), state.posts, { citable: true }); save(); shareState(); }

// only the header fields the selected version was trained with (v2: tipo; v3: tipo, año); the others are hidden
let models = { fields: {} };
const ALL_FIELDS = ["tipo", "calidad", "anio", "etiquetas", "paginas"];
const fieldsFor = (model) => models.fields[model.replace(/^gpu:/, "")] ?? ALL_FIELDS;
const header = () => Object.fromEntries(ALL_FIELDS.map((k) => [k, fieldsFor($("#model").value).includes(k) ? $("#" + k).value : ""]));
function showFields() {
  const f = fieldsFor($("#model").value);
  for (const k of ALL_FIELDS) $("#" + k).closest(".field").hidden = !f.includes(k);
}
const sampling = () => ({ temperature: +$("#temp").value, top_p: +$("#topp").value, repetition_penalty: +$("#rep").value, max_new_tokens: +$("#maxnew").value });
for (const id of ["temp", "topp", "rep", "maxnew", "nposts"]) {
  const inp = $(`#${id}`), out = $(`#${id}-out`);
  inp.oninput = () => { out.textContent = inp.value; if (id === "nposts") $("#nposts-lbl").textContent = inp.value; };
}

// sharing the current thread to the intro's Trending (server store, capped per client)
let shared = null;  // id once this thread was shared; reset when the thread changes
function shareState() {
  const ok = state.title.trim() && state.posts.length >= 2;
  $("#share").hidden = !ok;
  $("#share-note").textContent = shared ? "compartido ✓" : "";
  $("#btn-share").disabled = !!shared;
}
$("#btn-share").onclick = async () => {
  const res = await fetch("/api/generated", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: $("#model").value.replace(/^gpu:/, ""), tipo: $("#tipo").value, title: state.title, posts: state.posts }) });
  if (!res.ok) { $("#share-note").textContent = (await res.text()).slice(0, 80); return; }
  shared = (await res.json()).id;
  shareState();
};

let controller = null;
function busy(on) {
  document.querySelectorAll("#page-generar .btn, #btn-thread").forEach((b) => (b.disabled = on && b.id !== "btn-stop"));
  $("#btn-stop").hidden = !on;
  $("#btn-thread").hidden = on;
}

// ---------------------------------------------------------------- WebGPU: can this browser run the model?
let pub = false, gpu = { ok: false, why: "comprobando WebGPU…" };
async function webgpuCheck() {
  if (!navigator.gpu) return { ok: false, why: "este navegador no tiene WebGPU" };
  try {
    const a = await navigator.gpu.requestAdapter();
    if (!a) return { ok: false, why: "WebGPU está, pero el navegador no encuentra una GPU utilizable" };
    if (!a.features.has("shader-f16")) return { ok: false, why: "la GPU no ofrece shader-f16, que los pesos a 4 bits necesitan. En Linux, Chrome con --enable-unsafe-webgpu --use-angle=vulkan --enable-features=Vulkan suele arreglarlo" };
    return { ok: true, why: "WebGPU con shader-f16 disponible" };
  } catch (e) { return { ok: false, why: `WebGPU falló: ${String(e.message || e).slice(0, 80)}` }; }
}

// ---------------------------------------------------------------- generation: this browser's GPU (WebLLM over WebGPU), or the server's
// Both yield the same events: {text} while streaming, then {done, title} or {done, author, body}.
// On the public site only the browser path exists; on Tailscale the server is the fallback.
async function* sse(body, signal) {
  if (!body.model.startsWith("gpu:")) return yield* serverSse(body, signal);
  if (!llmError) {
    try { return yield* localSse(body, signal); }
    catch (e) {
      if (e.name === "AbortError" || signal.aborted || pub) throw e;
      dropEngine();
      llmError = `tu gpu falló (${String(e.message || e).slice(0, 70)}), sigue el servidor`;  // until another model is picked
      modelHint(llmError);
    }
  }
  return yield* serverSse({ ...body, model: body.model.slice(4) }, signal);
}

const modelHint = (t) => { $("#model-hint").textContent = t; };
const llmConfig = { model_list: [], useIndexedDBCache: false };
let llm = null, llmModel = "", llmWorker = null, llmError = null;
const abortError = () => Object.assign(new Error("parado"), { name: "AbortError" });
function dropEngine() { llmWorker?.terminate(); llm = llmWorker = null; llmModel = ""; }

function progress(r) {  // WebLLM: "Fetching param cache[3/18]: 120MB fetched. 20% completed, 5 secs elapsed." then "Loading…" then "Finish loading…"
  const dl = $("#dl");
  if (r.text.startsWith("Finish")) { dl.hidden = true; modelHint("modelo listo en tu navegador"); return; }
  dl.hidden = false;
  const mb = /(\d+)MB fetched/.exec(r.text);
  const fetching = r.text.startsWith("Fetching");
  dl.querySelector("i").style.width = `${Math.round(100 * (fetching ? r.progress : 1))}%`;
  dl.querySelector(".dl-text").textContent = fetching ? `descargando ${Math.round(100 * r.progress)}%${mb ? ` · ${mb[1]} de 575 MB` : ""}` : "cargando en la GPU…";
  modelHint("");
}

async function localEngine(version, signal) {
  const id = `fc-${version}`;
  if (llm && llmModel === id) return llm;
  if (!gpu.ok) throw new Error(gpu.why);
  llmModel = "";
  let last = Date.now();
  const onProgress = (r) => { last = Date.now(); progress(r); };
  const load = async () => {
    if (!llm) {
      const { CreateWebWorkerMLCEngine } = await import("/static/vendor/web-llm.js");
      llmWorker = new Worker("/static/llm-worker.js", { type: "module" });
      llm = await CreateWebWorkerMLCEngine(llmWorker, id, { appConfig: llmConfig, initProgressCallback: onProgress });
    } else await llm.reload(id);
  };
  // the download can take minutes, so the guard is silence, not time: a minute without progress, or "parar"
  let timer;
  const guard = new Promise((_, rej) => {
    timer = setInterval(() => {
      if (signal.aborted) rej(abortError());
      else if (Date.now() - last > 60000) rej(new Error("WebGPU no responde"));
    }, 1000);
  });
  try { await Promise.race([load(), guard]); }
  catch (e) { dropEngine(); $("#dl").hidden = true; throw e; }
  finally { clearInterval(timer); }
  llmModel = id;
  return llm;
}

// the prompt format of web/threads.py, built here because the model runs here
const MAX_PROMPT_CHARS = 12000;
function headerText(h) {
  const kept = [["tipo", h.tipo], ["calidad", h.calidad], ["año", h.anio], ["etiquetas", h.etiquetas.trim()], ["páginas", h.paginas]].filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`);
  return kept.length ? kept.join(" | ") + "\n\n" : "";
}
function threadText(title, posts) {
  const lines = posts.map(([a, t], i) => `#${i + 1} ${a}: ${t}`);
  while (lines.length > 1 && lines.reduce((n, l) => n + l.length + 2, 0) + title.length > MAX_PROMPT_CHARS) lines.splice(1, 1);
  return [`TÍTULO: ${title}`, ...lines].join("\n\n");
}

async function* localSse(body, signal) {
  const eng = await localEngine(body.model.slice(4), signal);
  const hdr = headerText(body.header), posts = body.posts;
  let prefix, stop, author = null;
  if (body.kind === "title") { prefix = hdr + "TÍTULO:"; stop = "\n\n"; }
  else {
    const who = posts.length ? body.who : "OP", n = posts.length + 1;
    if (who === "auto") prefix = `${hdr}${threadText(body.title, posts)}\n\n#${n}`;  // no trailing space, as in training
    else { author = who === "OP" ? "OP" : nextForero(); prefix = `${hdr}${threadText(body.title, posts)}\n\n#${n} ${author}:`; }
    stop = "\n\n#";
  }
  const s = body.sampling;
  const onAbort = () => eng.interruptGenerate();
  signal.addEventListener("abort", onAbort);
  let text = "";
  try {
    const stream = await eng.completions.create({ prompt: prefix, stream: true, stop: [stop], max_tokens: Math.min(s.max_new_tokens, 1500),
      temperature: s.temperature, top_p: s.top_p, repetition_penalty: s.repetition_penalty });
    for await (const chunk of stream) {
      text += chunk.choices[0]?.text ?? "";
      if (text.includes(stop)) { text = text.split(stop)[0]; yield { text }; eng.interruptGenerate(); break; }
      yield { text };
    }
  } finally { signal.removeEventListener("abort", onAbort); }
  if (signal.aborted) throw abortError();
  text = text.trim();
  if (body.kind === "title") return yield { done: true, title: text };
  if (author === null) {
    const m = /^\s*(OP|forero_[a-z]+):\s?/.exec(text);
    author = m ? m[1] : nextForero();
    if (m) text = text.slice(m[0].length);
  }
  yield { done: true, author, body: text.trim() };
}

async function* serverSse(body, signal) {
  const res = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok) throw new Error(await res.text());
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const p of parts) if (p.startsWith("data: ")) yield JSON.parse(p.slice(6));
  }
}

async function genTitle(signal) {
  for await (const ev of sse({ model: $("#model").value, kind: "title", header: header(), sampling: sampling() }, signal)) {
    if (ev.done) { state.title = ev.title; shared = null; save(); return true; }
    $("#title").value = ev.text; fitTitle();
  }
  return false;
}

async function genPost(who, signal) {
  const n = state.posts.length + 1;
  if (n === 1) $("#posts").innerHTML = "";
  const live = postEl(n, who === "forero" ? "forero_?" : who === "OP" || n === 1 ? "OP" : "…", "", { live: true, seed: state.title });
  $("#posts").appendChild(live);
  live.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const body = live.querySelector(".body"), nickEl = live.querySelector(".nick");
  const req = { model: $("#model").value, kind: "post", who, header: header(), title: state.title, posts: state.posts, sampling: sampling() };
  for await (const ev of sse(req, signal)) {
    if (ev.done) {
      if (!ev.body) { live.remove(); return false; } // the model closed the thread
      state.posts.push([ev.author, ev.body]);
      shared = null;
      redraw();
      return true;
    }
    let text = ev.text;
    if (who === "auto" && n > 1) {
      const m = /^\s*(OP|forero_[a-z]+):\s?/.exec(text);
      if (m) { nickEl.textContent = persona(m[1], state.title).nick; live.classList.toggle("op", m[1] === "OP"); text = text.slice(m[0].length); }
    }
    body.innerHTML = renderBody(text, state.title);
  }
  live.remove();
  return false;
}

async function run(fn) {
  if (controller) return;
  controller = new AbortController();
  busy(true);
  try { await fn(controller.signal); }
  catch (e) { if (e.name !== "AbortError") modelHint(String(e.message || e).slice(0, 160)); }
  finally { controller = null; busy(false); redraw(); gpuStatus(); }
}

$("#btn-title").onclick = () => run(genTitle);
$("#btn-auto").onclick = () => run((s) => genPost("auto", s));
$("#btn-forero").onclick = () => run((s) => genPost("forero", s));
$("#btn-op").onclick = () => run((s) => genPost("OP", s));
$("#btn-stop").onclick = () => controller?.abort();
$("#btn-thread").onclick = () => run(async (s) => {
  if (!state.title.trim() && !(await genTitle(s))) return;
  const target = +$("#nposts").value;
  while (state.posts.length < target && !s.aborted) if (!(await genPost("auto", s))) break;
});
$("#btn-undo").onclick = () => { state.posts.pop(); shared = null; redraw(); };
$("#btn-new").onclick = () => { state.title = ""; state.posts = []; shared = null; clearQuote(); redraw(); };
$("#title").oninput = (e) => { state.title = e.target.value.replace(/\n/g, " "); fitTitle(); save(); };
$("#title").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("#user-text").focus(); } };  // titles are one line in the format
window.addEventListener("resize", fitTitle);

let quote = null;
const clearQuote = () => { quote = null; $("#quote").hidden = true; };
$("#posts").onclick = (e) => {
  const b = e.target.closest(".cite");
  if (!b) return;
  const [a, t] = state.posts[+b.dataset.n - 1];
  quote = { author: a, text: t.replace(/\[cita=[^\]]+\][\s\S]*?\[\/cita\]\n?/g, "").trim().slice(0, 200) };
  $("#quote").hidden = false;
  $("#quote").innerHTML = `<span><b>${esc(persona(a, state.title).nick)}</b> ${esc(quote.text)}</span><button type="button" id="unquote">quitar la cita</button>`;
  $("#unquote").onclick = clearQuote;
  $("#user-text").focus();
};
let asWho = "OP";
$("#as-who").onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  asWho = b.dataset.v;
  $("#as-who").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
};
function nextForero() {
  const used = new Set(state.posts.map(([a]) => a));
  for (const c of "abcdefghijklmnopqrstuvwxyz") if (!used.has(`forero_${c}`)) return `forero_${c}`;
  return "forero_z";
}
$("#compose").onsubmit = (e) => {
  e.preventDefault();
  let text = $("#user-text").value.trim();
  if (!text) return;
  const author = asWho === "OP" || !state.posts.length ? "OP" : nextForero();
  if (quote) text = `[cita=${quote.author}]${quote.text}[/cita]\n${text}`;
  state.posts.push([author, text]);
  shared = null;
  $("#user-text").value = ""; clearQuote();
  redraw();
};
$("#user-text").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#compose").requestSubmit(); };

// header: the server GPU's state (Tailscale only). Under the model selector: what the chosen model needs.
async function gpuStatus() {
  const sel = $("#model").value;
  if (!sel) { $("#gpu").textContent = ""; return; }
  if (sel.startsWith("gpu:")) {
    $("#gpu").textContent = "";
    if (!$("#dl").hidden) return;
    modelHint(llmError ?? (llmModel === `fc-${sel.slice(4)}` ? "modelo listo en tu navegador" : "575 MB, se descargan al primer post y quedan en caché"));
    return;
  }
  modelHint("");
  const m = await (await fetch("/api/models")).json();
  const loaded = Object.keys(m.loaded);
  $("#gpu").textContent = loaded.length ? `gpu del servidor: ${loaded.join(", ")}` : "gpu del servidor: libre";
}

// ---------------------------------------------------------------- archivo
const filter = { tipo: "", calidad: "", year: "" };
function segs(container, key, values, all) {
  container.innerHTML = [["", all], ...values.map((v) => [v, v])].map(([v, t]) => `<button type="button" data-v="${v}" class="${v === filter[key] ? "on" : ""}">${t}</button>`).join("");
  container.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    filter[key] = b.dataset.v;
    container.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    browse();
  };
}

async function browse() {
  const q = new URLSearchParams({ ...filter, q: $("#b-q").value });
  const rows = await (await fetch(`/api/threads?${q}`)).json();
  $("#b-view").hidden = true; $("#b-list").hidden = false;
  $("#b-list").innerHTML = rows.length ? "" : `<li class="empty">Ningún título con eso.</li>`;
  for (const r of rows) {
    $("#b-list").appendChild(el(`<li><a href="#archivo/${r.thread_id}"><span class="t">${esc(r.title)}</span>
      <span class="m">${r.tipo ?? "?"} · ${r.calidad ?? "?"}${r.year ? " · " + r.year : ""} · <b>${r.n_posts}</b> posts</span></a></li>`));
  }
}

const SOURCES = { archive: "archivo 2019–2020", "crawl-2026": "crawl 2026", public: "crawl público 2026" };
let current = null, currentSrc = "threads";
async function openThread(id, src = "threads") {
  current = await (await fetch(`/api/${src}/${id}`)).json();
  currentSrc = src;
  $("#b-list").hidden = true; $("#b-view").hidden = false;
  $("#b-title").textContent = current.title;
  $("#b-back").textContent = src === "generated" ? "‹ inicio" : "‹ archivo";
  $("#b-back").href = src === "generated" ? "#" : "#archivo";
  $("#b-meta").textContent = src === "generated" ? `generado por ${current.model || "el modelo"}${current.tipo ? " · " + current.tipo : ""} · ${current.posts.length} posts`
    : `${current.tipo ?? "?"} · ${current.calidad ?? "?"}${current.year ? " · " + current.year : ""} · ${current.posts.length} posts${current.tags ? " · " + current.tags : ""} · ${SOURCES[current.source] ?? "archivo"}`;
  renderPosts($("#b-posts"), current.posts, { seed: current.title });
  window.scrollTo({ top: 0 });
}
$("#b-random").onclick = () => { $("#b-q").value = ""; browse(); };
let t; $("#b-q").oninput = () => { clearTimeout(t); t = setTimeout(browse, 250); };
$("#b-play").onclick = () => (location.hash = currentSrc === "generated" ? `continuar-gen/${current.id}` : `continuar/${current.thread_id}`);

// ---------------------------------------------------------------- the numbers
async function stats() {
  const s = await (await fetch("/static/data/stats.json")).json();
  $("#s-total").textContent = `${fmt(s.total.n)} hilos, ${fmt(s.total.posts)} posts, ${(s.total.chars / 1e6).toLocaleString("es-ES", { maximumFractionDigits: 0 })} millones de caracteres.`;

  const tipos = {};
  for (const r of s.by_tipo_calidad) { const t = (tipos[r.tipo] ??= { n: 0, seg: {} }); t.n += r.n; t.seg[r.calidad] = r.n; }
  const order = Object.entries(tipos).sort((a, b) => b[1].n - a[1].n);
  const max = order[0][1].n;
  $("#s-tipos").innerHTML = order.map(([t, { n, seg }]) => row(t,
    [["alta", "a"], ["media", "m"], ["baja", "b"], ["?", "b"]].filter(([c]) => seg[c]).map(([c, k]) => `<div class="seg ${k}" style="width:${(100 * seg[c]) / max}%" title="${c}: ${fmt(seg[c])}"></div>`).join(""),
    `<b>${fmt(n)}</b>`)).join("");

  const names = Object.keys(s.mixes);
  const showMix = (name) => {
    const m = s.mixes[name];
    const rows = Object.entries(m).filter(([k, v]) => !k.startsWith("_") && v.mtok >= 0.1).sort((a, b) => b[1].mtok - a[1].mtok);
    const mx = rows[0][1].mtok;
    $("#s-mix").innerHTML = rows.map(([t, v]) => row(t, `<div class="seg" style="width:${(100 * v.mtok) / mx}%"></div>`, `<b>${v.mtok.toFixed(1)}</b> M`)).join("");
    $("#s-mix-note").textContent = `${name}: ${m._total_mtok} M tokens en total · ${fmt(m._val_threads)} hilos reservados para validación${m._dedup_dropped ? ` · ${fmt(m._dedup_dropped)} duplicados fuera` : ""}`;
    $("#s-mix-tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.textContent === name));
  };
  $("#s-mix-tabs").innerHTML = names.map((n) => `<button type="button">${n}</button>`).join("");
  $("#s-mix-tabs").onclick = (e) => { const b = e.target.closest("button"); if (b) showMix(b.textContent); };
  if (names.length) showMix(names[names.length - 1]);

  // posts per thread, archive only: it keeps up to 30 posts per thread, the crawl only the long ones
  const top = Math.max(...s.posts_hist.map((r) => r.n_posts));
  const hist = Array(top + 1).fill(0);
  for (const r of s.posts_hist) hist[r.n_posts] += r.n;
  const hm = Math.max(...hist), W = 600, H = 140, base = 118, gap = 2, bw = (W - gap * (top - 1)) / top;
  let svg = `<line x1="0" y1="${base + 0.5}" x2="${W}" y2="${base + 0.5}"/>`;
  for (let i = 1; i <= top; i++) {
    const h = (100 * hist[i]) / hm, x = (i - 1) * (bw + gap);
    svg += `<rect x="${x}" y="${base - h}" width="${bw}" height="${h}"><title>${i} posts: ${fmt(hist[i])} hilos</title></rect>`;
    if (i === 1 || i % 5 === 0) svg += `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${i}</text>`;
  }
  $("#s-hist").innerHTML = svg;

  // threads per year in the 2026 crawl: one selection (long posts) over 2003–2026. The archive is every thread of a
  // 2019–2020 id range, so drawing it in would show the sampling, not the forum
  const archive = s.years.filter((r) => r.source === "archive").reduce((a, r) => a + r.n, 0);
  const yk = Object.keys(s.crawl_years), ym = Math.max(...Object.values(s.crawl_years));
  const yw = (600 - 2 * (yk.length - 1)) / yk.length;
  let ysvg = `<line x1="0" y1="118.5" x2="600" y2="118.5"/>`;
  yk.forEach((y, i) => {
    const n = s.crawl_years[y], h = (100 * n) / ym, x = i * (yw + 2);
    ysvg += `<rect x="${x}" y="${118 - h}" width="${yw}" height="${h}"><title>${y}: ${fmt(n)} hilos</title></rect>`;
    if (y % 5 === 0 || i === 0) ysvg += `<text x="${x + yw / 2}" y="134" text-anchor="middle">${y}</text>`;
  });
  $("#s-years-note").textContent = `El archivo (${fmt(archive)} hilos) es de 2019–2020 y no entra en la gráfica.`;
  $("#s-years").innerHTML = ysvg;

  const porder = ["1", "2", "3-5", "6-10", "11-30", "31+"];
  // the 2026 crawl kept only the long posts, so its threads have no page count ("unknown")
  const pages = s.pages.filter((p) => p.pages_bucket !== "unknown").sort((a, b) => porder.indexOf(a.pages_bucket) - porder.indexOf(b.pages_bucket));
  const pm = Math.max(...pages.map((p) => p.n));
  $("#s-pages").innerHTML = pages.map((p) => row(p.pages_bucket, `<div class="seg" style="width:${(100 * p.n) / pm}%"></div>`, `<b>${fmt(p.n)}</b>`)).join("");
}

// ---------------------------------------------------------------- init
(async () => {
  const [m, g] = await Promise.all([fetch("/api/models").then((r) => r.json()), webgpuCheck()]);
  pub = m.public; gpu = g; models = m;
  $("#i-gpu").textContent = `Tu navegador: ${gpu.why}.`;
  $("#i-gpu").classList.add(gpu.ok ? "ok" : "bad");
  // versions converted for the browser (scripts/export_webllm.py) run locally when WebGPU works;
  // on the public site they are the only option, and the weights come from Hugging Face
  const local = (gpu.ok ? m.webgpu : []).filter((v) => !pub || m.hf[v]);
  llmConfig.model_list = local.map((v) => ({
    model: pub ? `https://huggingface.co/${m.hf[v]}/resolve/main/` : `${location.origin}/mlc/${v}/resolve/main/`, model_id: `fc-${v}`,
    model_lib: `${location.origin}/static/vendor/gemma3-1b-q4f16_1-webgpu.wasm`,
    // WebLLM wants exactly one of the two window sizes; the kernels use a plain 4k KV cache
    vram_required_MB: 711, low_resource_required: true, overrides: { context_window_size: 4096, sliding_window_size: -1 },
  }));
  $("#model").innerHTML = m.versions.map((v) => `<option value="${v}">${v}</option>`).join("")
    + local.map((v) => `<option value="gpu:${v}">${v} · en tu navegador</option>`).join("");
  const prefer = ["v3", "gpu:v3", "v2", "gpu:v2", `gpu:${local[local.length - 1]}`, m.versions[m.versions.length - 1]];
  $("#model").value = prefer.find((v) => [...$("#model").options].some((o) => o.value === v)) ?? "";
  showFields();
  $("#model").onchange = () => { llmError = null; showFields(); gpuStatus(); };
  if (!$("#model").options.length) {  // public site without WebGPU: reading only; the ⓘ tooltip says why
    document.querySelectorAll("#page-generar .controls button, #page-generar .controls select, #page-generar .controls input, #page-generar .controls textarea").forEach((x) => (x.disabled = true));
    modelHint("Sin WebGPU en este navegador no se puede generar.");
  }
  const opts = (vals) => `<option value="">—</option>` + vals.map((v) => `<option>${v}</option>`).join("");
  $("#tipo").innerHTML = opts(m.tipos); $("#calidad").innerHTML = opts(m.calidades); $("#paginas").innerHTML = opts(m.paginas); $("#anio").innerHTML = opts(m.anios ?? []);
  $("#tipo").value = "historia"; $("#calidad").value = "alta";
  segs($("#f-tipo"), "tipo", m.tipos, "todos los tipos");
  segs($("#f-calidad"), "calidad", m.calidades, "toda calidad");
  $("#b-year").innerHTML = [["", "todos los años"], ...(m.anios ?? []).map((y) => [y, y])].map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
  $("#b-year").onchange = () => { filter.year = $("#b-year").value; browse(); };
  redraw();
  gpuStatus();
  route();
})();
