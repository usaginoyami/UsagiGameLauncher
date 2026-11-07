"use strict";
// Usagi Curador: edita catalog/catalog.json do repositório pelo celular e publica num único commit.
const REPO = "usaginoyami/UsagiGameLauncher", BRANCH = "main", DIR = "catalog";
const RAW = `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${DIR}/`;
const API = "https://api.github.com";
const PLATFORMS = ["N64","Xbox 360","Xbox One","Xbox","PS4","PS3","PS2","PS1","PSP","GameCube","Wii","Switch","Dreamcast","SNES","NES","Game Boy","GBA","DS","Outros"];
const TARGETS = ["Windows","Linux","macOS","Android","Switch","Portátil ARM64","Navegador"];
// "Portado para" aceita várias plataformas: no catálogo vira texto "Windows, Switch"; vazio = só Windows.
const KNOWN_TARGETS = new Map(TARGETS.map(t => [t.toLowerCase(), t]));
const splitTargets = v => {
  const out = [];
  for (const raw of Array.isArray(v) ? v : String(v || "").split(/[,;]/)) {
    const t = String(raw).trim().replace(/\s+/g, " ").slice(0, 30), name = KNOWN_TARGETS.get(t.toLowerCase()) || t;
    if (name && !out.some(o => o.toLowerCase() === name.toLowerCase())) out.push(name);
  }
  return out.slice(0, 8);
};
const joinTargets = v => { const t = splitTargets(v); return t.length === 1 && t[0] === "Windows" ? "" : t.join(", "); };
const GUESS = [["N64",/\bn64\b|nintendo ?64/i],["Xbox 360",/xbox ?360|xenon/i],["Xbox One",/xbox ?one/i],["PS4",/\bps4\b|playstation ?4/i],
  ["PS3",/\bps3\b|playstation ?3/i],["PSP",/\bpsp\b/i],["Dreamcast",/dreamcast/i],["SNES",/\bsnes\b|super nintendo/i],["NES",/\bnes\b|famicom/i],
  ["PS2",/\bps2\b|playstation ?2/i],["PS1",/\bps1\b|\bpsx\b|playstation ?1/i],["GameCube",/game ?cube|\bgcn\b/i],["Wii",/\bwii\b/i],
  ["Switch",/nintendo switch|\bswitch\b/i],["GBA",/\bgba\b/i],["Game Boy",/game ?boy/i],["DS",/\bnds\b|nintendo ds/i],["Xbox",/\bxbox\b/i]];

const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, ""); else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
};
const store = {
  get: (k, d = "") => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* modo privado */ } },
};

const S = { games: [], sha: "", dirty: new Map(), newCovers: new Map(), gone: new Set(), loaded: false };
const token = () => store.get("token").trim();
const keyOf = g => g.repo.toLowerCase();
const coverFile = key => "covers/" + key.replace(/[^a-z0-9._-]/g, "_").slice(0, 70) + ".jpg";
const isExt = g => g.repo.toLowerCase().startsWith("ext/");

function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.id); toast.id = setTimeout(() => { t.style.display = "none"; }, ms);
}

// ------------------------------------------------------------------ GitHub
async function gh(path, opts = {}) {
  const headers = { Accept: "application/vnd.github+json", ...(opts.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const r = await fetch(API + path, { ...opts, headers });
  if (r.status === 401 || r.status === 403) throw new Error("O GitHub recusou: confira o token e a permissão de escrita em Contents.");
  if (r.status === 404 && opts.allow404) return null;
  if (!r.ok) throw new Error(`GitHub respondeu ${r.status}.`);
  return r.status === 204 ? null : r.json();
}
const b64utf8 = s => { const b = new TextEncoder().encode(s); let bin = ""; b.forEach(x => bin += String.fromCharCode(x)); return btoa(bin); };
const fromB64utf8 = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), c => c.charCodeAt(0)));

async function fetchCatalog() {
  if (token()) {
    const f = await gh(`/repos/${REPO}/contents/${DIR}/catalog.json?ref=${BRANCH}`, { allow404: true });
    if (!f) return { games: [], sha: "" };
    const doc = JSON.parse(f.content ? fromB64utf8(f.content) : await (await fetch(f.download_url)).text());
    return { games: doc.games || [], sha: f.sha };
  }
  const r = await fetch(RAW + "catalog.json?t=" + Date.now());
  if (!r.ok) throw new Error("Não consegui ler o catálogo publicado.");
  return { games: (await r.json()).games || [], sha: "" };
}

async function load() {
  $("#count").textContent = "Carregando…";
  try {
    const { games, sha } = await fetchCatalog();
    S.games = games; S.sha = sha; S.loaded = true;
    S.dirty.clear(); S.newCovers.clear(); S.gone.clear();
  } catch (e) { toast(e.message, 6000); $("#count").textContent = "Sem conexão com o catálogo"; }
  render();
}

async function publish() {
  if (!token()) { toast("Cole seu token do GitHub nas configurações (⚙)."); openSettings(); return; }
  const btn = $("#btnPublish"); btn.disabled = true; btn.textContent = "Publicando…";
  try {
    // junta as suas edições com o que estiver publicado agora (o desktop pode ter publicado enquanto isso)
    const latest = await fetchCatalog();
    const byKey = new Map(latest.games.map(g => [keyOf(g), g]));
    for (const [k, op] of S.dirty) {
      if (op === "delete") byKey.delete(k);
      else { const g = S.games.find(x => keyOf(x) === k); if (g) byKey.set(k, g); }
    }
    const games = [...byKey.values()].sort((a, b) => (a.name || "").localeCompare(b.name || "", "pt", { sensitivity: "base" }));
    const doc = { schema: 1, updated: new Date().toISOString().slice(0, 19) + "+00:00", games };
    const ref = await gh(`/repos/${REPO}/git/ref/heads/${BRANCH}`);
    const head = await gh(`/repos/${REPO}/git/commits/${ref.object.sha}`);
    const tree = [];
    const blob = async (content, encoding) => (await gh(`/repos/${REPO}/git/blobs`, { method: "POST", body: JSON.stringify({ content, encoding }) })).sha;
    tree.push({ path: `${DIR}/catalog.json`, mode: "100644", type: "blob", sha: await blob(b64utf8(JSON.stringify(doc, null, 2)), "base64") });
    for (const [key, dataUrl] of S.newCovers) {
      if (S.gone.has(key)) continue;
      tree.push({ path: `${DIR}/${coverFile(key)}`, mode: "100644", type: "blob", sha: await blob(dataUrl.split(",")[1], "base64") });
    }
    const n = S.dirty.size;
    const newTree = await gh(`/repos/${REPO}/git/trees`, { method: "POST", body: JSON.stringify({ base_tree: head.tree.sha, tree }) });
    const commit = await gh(`/repos/${REPO}/git/commits`, { method: "POST", body: JSON.stringify({ message: `Catálogo (celular): ${n} alteração(ões)`, tree: newTree.sha, parents: [ref.object.sha] }) });
    await gh(`/repos/${REPO}/git/refs/heads/${BRANCH}`, { method: "PATCH", body: JSON.stringify({ sha: commit.sha }) });
    toast("Publicado! O app do PC e o cliente pegam na próxima atualização.", 5000);
    await load();
  } catch (e) { toast(e.message, 7000); }
  btn.disabled = false; btn.textContent = "Publicar";
}

// ------------------------------------------------------------------ lista
function coverSrc(g) {
  const k = keyOf(g);
  if (S.newCovers.has(k)) return S.newCovers.get(k);
  if (!g.cover) return "";
  return /^https:/.test(g.cover) ? g.cover : RAW + g.cover;
}

function render() {
  const q = $("#search").value.trim().toLowerCase(), plat = $("#platformFilter").value;
  const sel = $("#platformFilter"), have = [...new Set(S.games.map(g => g.platform).filter(Boolean))].sort();
  if (sel.options.length !== have.length + 1 || [...sel.options].slice(1).some((o, i) => o.value !== have[i])) {
    sel.replaceChildren(el("option", { value: "" }, "Todas as plataformas"), ...have.map(p => el("option", { value: p }, p)));
    sel.value = have.includes(plat) ? plat : "";
  }
  const shown = S.games.filter(g => (!sel.value || g.platform === sel.value) &&
    (!q || `${g.name} ${g.repo} ${g.platform} ${g.description || ""}`.toLowerCase().includes(q)))
    .sort((a, b) => (a.name || "").localeCompare(b.name || "", "pt", { sensitivity: "base" }));
  $("#count").textContent = S.loaded ? `${shown.length} de ${S.games.length} jogos` : "";
  const list = $("#list");
  list.replaceChildren(...(shown.length ? shown.map(card) : [el("div", { class: "empty" }, S.loaded ? "Nenhum jogo encontrado." : "Carregando…")]));
  const n = S.dirty.size;
  $("#publishBar").hidden = n === 0;
  $("#publishText").textContent = `${n} alteração(ões) não publicadas`;
}

function card(g) {
  const src = coverSrc(g), k = keyOf(g);
  const thumb = el("div", { class: "thumb" }, src ? "" : (g.name || "?").slice(0, 1).toUpperCase());
  if (src) thumb.style.backgroundImage = `url("${src.replace(/"/g, "%22")}")`;
  return el("button", { class: "game", onclick: () => openEditor(g) }, thumb,
    el("div", { class: "info" }, el("div", { class: "name" }, g.name || g.repo),
      el("div", { class: "repo" }, isExt(g) ? (g.link || "Fora do GitHub") : g.repo.replace(/^gl:/i, "")),
      el("div", { class: "repo", hidden: !/^gl:/i.test(g.repo) }, "GitLab"),
      el("div", { class: "chips" }, el("span", { class: "chip" }, g.platform || "Outros"),
        joinTargets(g.target) ? el("span", { class: "chip" }, "→ " + splitTargets(g.target).join(", ")) : null,
        isExt(g) ? el("span", { class: "chip ext" }, "Fora do GitHub") : null,
        g.adult ? el("span", { class: "chip adult" }, "Adulto") : null,
        S.dirty.has(k) ? el("span", { class: "chip dirty" }, "Alterado") : null)));
}

// ------------------------------------------------------------------ editor
function field(label, input, hint) { return el("div", { class: "field" }, el("label", {}, label), input, hint ? el("div", { class: "hint" }, hint) : null); }
const select = (opts, value) => { const s = el("select"); for (const o of opts) s.append(el("option", { value: o }, o)); if (value && !opts.includes(value)) s.append(el("option", { value }, value)); s.value = value || opts[0]; return s; };

// Caixas de marcar para "Portado para" (uma ou mais plataformas) + campo para as que não estão na lista.
function targetPicker(value) {
  const have = splitTargets(value); if (!have.length) have.push("Windows");
  const boxes = TARGETS.map(t => el("label", { class: "tcheck" }, el("input", { type: "checkbox", checked: have.includes(t) }), t));
  const extra = el("input", { placeholder: "Outras (separe por vírgula)", value: have.filter(h => !KNOWN_TARGETS.has(h.toLowerCase())).join(", ") });
  return {
    node: el("div", {}, el("div", { class: "tgrid" }, boxes), extra),
    get: () => joinTargets([...boxes.filter(b => b.firstChild.checked).map(b => b.textContent), ...splitTargets(extra.value)]),
  };
}

function closeSheet() { $("#sheet").hidden = true; $("#sheet").replaceChildren(); document.body.style.overflow = ""; }
function showSheet(...kids) { const s = $("#sheet"); s.replaceChildren(...kids); s.hidden = false; s.scrollTop = 0; document.body.style.overflow = "hidden"; }

function resizeToJpeg(file, max = 640) {
  return new Promise((resolve, reject) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const r = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * r); c.height = Math.round(img.height * r);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url); resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Não consegui abrir essa imagem.")); };
    img.src = url;
  });
}

function openEditor(g, isNew = false) {
  const draft = { ...g }, key = keyOf(g);
  let cover = coverSrc(g), newCover = null, removeCover = false;
  const f = {
    name: el("input", { value: g.name || "", maxlength: 80 }),
    platform: select(PLATFORMS, g.platform || "Outros"),
    target: targetPicker(g.target),
    description: el("textarea", { maxlength: 600 }, g.description || ""),
    notes: el("textarea", { maxlength: 400 }, g.notes || ""),
    link: el("input", { type: "url", value: g.link || "", placeholder: "https://…" }),
    adult: el("input", { type: "checkbox" }),
  };
  f.adult.checked = !!g.adult;
  const thumb = el("div", { class: "thumb" }, cover ? "" : "?");
  const paint = () => { thumb.style.backgroundImage = cover ? `url("${cover}")` : ""; thumb.textContent = cover ? "" : "?"; };
  paint();
  const file = el("input", { type: "file", accept: "image/*", hidden: true });
  file.addEventListener("change", async () => {
    if (!file.files[0]) return;
    try { newCover = await resizeToJpeg(file.files[0]); cover = newCover; removeCover = false; paint(); }
    catch (e) { toast(e.message); }
  });
  const save = () => {
    const name = f.name.value.trim();
    if (!name) { toast("Dê um nome ao jogo."); return; }
    if (f.link.value.trim() && !/^https:\/\//i.test(f.link.value.trim())) { toast("O link precisa começar com https://"); return; }
    Object.assign(draft, { name, platform: f.platform.value, target: f.target.get(),
      description: f.description.value.trim(), notes: f.notes.value.trim(), link: f.link.value.trim(), adult: f.adult.checked });
    if (newCover) { S.newCovers.set(key, newCover); draft.cover = coverFile(key); }
    else if (removeCover) { S.newCovers.delete(key); delete draft.cover; }
    const i = S.games.findIndex(x => keyOf(x) === key);
    if (i < 0 && !draft.added_at) draft.added_at = new Date().toISOString().slice(0, 19);
    if (i >= 0) S.games[i] = draft; else S.games.push(draft);
    S.dirty.set(key, "upsert"); S.gone.delete(key);
    closeSheet(); render();
  };
  const remove = () => {
    if (!confirm(`Remover “${draft.name}” do catálogo? Só some para todos quando você publicar.`)) return;
    S.games = S.games.filter(x => keyOf(x) !== key);
    S.dirty.set(key, "delete"); S.newCovers.delete(key); S.gone.add(key);
    closeSheet(); render();
  };
  showSheet(
    el("div", { class: "top" }, el("button", { class: "ghost", onclick: closeSheet }, "✕"), el("h2", {}, isNew ? "Novo jogo" : "Editar jogo"), el("button", { class: "primary", onclick: save }, "Salvar")),
    isExt(g) ? el("p", { class: "hint" }, "Jogo de fora do GitHub: o link é a página para baixar.") : el("p", { class: "hint" }, g.repo),
    field("Nome", f.name), field("Plataforma original", f.platform),
    field("Portado para", f.target.node, "Marque todas as plataformas onde o port roda. Sem nenhuma marcada, vale Windows."),
    field("Descrição", f.description), field("Aviso (opcional)", f.notes, "Aparece em laranja no painel do jogo."),
    field("Link da página", f.link, isExt(g) ? "Obrigatório para o app abrir a página." : "Opcional."),
    el("div", { class: "field check" }, f.adult, el("label", {}, "Conteúdo adulto ou sugestivo (fica escondido por padrão)")),
    el("div", { class: "field" }, el("label", {}, "Capa / print"),
      el("div", { class: "cover-edit" }, thumb, el("div", {},
        el("button", { onclick: () => file.click() }, "Escolher imagem"), " ",
        el("button", { onclick: () => { cover = ""; newCover = null; removeCover = true; paint(); } }, "Sem capa"),
        el("p", { class: "hint" }, "Tire um print ou escolha da galeria. A imagem é reduzida antes de enviar."))), file),
    isNew ? null : el("button", { class: "danger", onclick: remove }, "Remover do catálogo"));
}

// ------------------------------------------------------------------ adicionar
function guessPlatform(text) { for (const [p, re] of GUESS) if (re.test(text)) return p; return "Outros"; }
const pretty = s => s.replace(/[_-]/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/\s+/g, " ").trim();

function parseGitlab(text) {
  const t = text.trim();
  if (!/^gl:|gitlab\.com/i.test(t)) return null;
  let path = /^gl:/i.test(t) ? t.slice(3) : t.split(/gitlab\.com[/:]/i)[1] || "";
  path = path.split(/[?#]/)[0].split("/-/")[0];
  const parts = path.split("/").filter(Boolean);
  if (parts.length && /\.git$/i.test(parts[parts.length - 1])) parts[parts.length - 1] = parts[parts.length - 1].slice(0, -4);
  if (parts.length < 2 || !parts.every(p => /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(p))) return "invalid";
  return parts.join("/");
}

function openAdd() {
  const repo = el("input", { placeholder: "dono/repositório ou link do GitHub ou GitLab", autocapitalize: "none", autocorrect: "off" });
  const go = el("button", { class: "primary" }, "Buscar");
  go.addEventListener("click", async () => {
    const gl = parseGitlab(repo.value);
    if (gl) {
      if (gl === "invalid") { toast("Link do GitLab inválido."); return; }
      const id = "gl:" + gl;
      if (S.games.some(g => keyOf(g) === id.toLowerCase())) { toast("Esse projeto já está no catálogo."); return; }
      go.disabled = true; go.textContent = "Buscando…";
      try {
        // sem o token do GitHub: ele só vai para o GitHub
        const r = await fetch("https://gitlab.com/api/v4/projects/" + encodeURIComponent(gl));
        if (!r.ok) throw new Error("Não achei esse projeto no GitLab (precisa ser público).");
        const info = await r.json();
        const text = `${info.path} ${info.description || ""} ${(info.topics || []).join(" ")}`;
        openEditor({ repo: id, name: pretty(info.path || gl.split("/").pop()), platform: guessPlatform(text), target: "", description: info.description || "", notes: "", asset_hint: "", homepage: "", cover_title: "", link: "", adult: false }, true);
      } catch (e) { toast(e.message); go.disabled = false; go.textContent = "Buscar"; }
      return;
    }
    const m = repo.value.trim().match(/(?:github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git|\/.*)?$/);
    if (!m) { toast("Use o formato dono/repositório."); return; }
    const full = `${m[1]}/${m[2]}`;
    if (S.games.some(g => keyOf(g) === full.toLowerCase())) { toast("Esse repositório já está no catálogo."); return; }
    go.disabled = true; go.textContent = "Buscando…";
    try {
      const info = await gh(`/repos/${full}`);
      const text = `${info.name} ${info.description || ""} ${(info.topics || []).join(" ")}`;
      openEditor({ repo: info.full_name, name: pretty(info.name), platform: guessPlatform(text), target: "", description: info.description || "", notes: "", asset_hint: "", homepage: "", cover_title: "", link: "", adult: false }, true);
    } catch (e) { toast(e.message); go.disabled = false; go.textContent = "Buscar"; }
  });
  const name = el("input", { placeholder: "Nome do jogo" });
  const ext = el("button", {}, "Continuar");
  ext.addEventListener("click", () => {
    const n = name.value.trim(); if (!n) { toast("Dê um nome ao jogo."); return; }
    const slug = n.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "jogo";
    let id = "ext/" + slug, i = 2; while (S.games.some(g => keyOf(g) === id)) id = `ext/${slug}-${i++}`;
    openEditor({ repo: id, name: n, platform: "Outros", target: "", description: "", notes: "", asset_hint: "", homepage: "", cover_title: "", link: "", adult: false }, true);
  });
  showSheet(el("div", { class: "top" }, el("button", { class: "ghost", onclick: closeSheet }, "✕"), el("h2", {}, "Adicionar jogo")),
    el("h3", {}, "Do GitHub ou GitLab"), field("Repositório", repo), go,
    el("h3", {}, "De fora do GitHub (itch.io, site…)"), field("Nome", name), ext);
}

// ------------------------------------------------------------------ configurações
function openSettings() {
  const t = el("input", { type: "password", value: token(), placeholder: "github_pat_…", autocapitalize: "none", autocomplete: "off" });
  showSheet(el("div", { class: "top" }, el("button", { class: "ghost", onclick: closeSheet }, "✕"), el("h2", {}, "Configurações"),
    el("button", { class: "primary", onclick: () => { store.set("token", t.value.trim()); closeSheet(); toast(token() ? "Token salvo neste aparelho." : "Token removido."); load(); } }, "Salvar")),
    field("Token do GitHub", t, "Fine-grained, só para o repositório " + REPO + ", com Contents: leitura e escrita. Fica salvo apenas neste navegador."),
    el("p", { class: "warn" }, "Sem token você só consulta o catálogo. Não use um token com mais permissões que isso, e não instale o app em aparelho que não é seu."),
    el("button", { class: "danger", onclick: () => { store.set("token", ""); t.value = ""; toast("Token removido."); } }, "Apagar token deste aparelho"));
}

// ------------------------------------------------------------------ início
$("#search").addEventListener("input", render);
$("#platformFilter").addEventListener("change", render);
$("#fab").addEventListener("click", openAdd);
$("#btnSettings").addEventListener("click", openSettings);
$("#btnPublish").addEventListener("click", publish);
$("#btnSync").addEventListener("click", () => {
  if (S.dirty.size && !confirm("Recarregar descarta as alterações não publicadas. Continuar?")) return;
  load();
});
window.addEventListener("beforeunload", e => { if (S.dirty.size) { e.preventDefault(); e.returnValue = ""; } });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
load();
if (!token()) setTimeout(() => toast("Modo leitura. Abra ⚙ e cole o token para editar e publicar.", 6000), 800);
