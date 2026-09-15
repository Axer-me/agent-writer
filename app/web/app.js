const STAGES = [
  { id: "card", file: "card.md", label: "Карточка" },
  { id: "style", file: "style.md", label: "Стиль" },
  { id: "bible", file: "bible.md", label: "Библия" },
  { id: "plan", file: "plan.md", label: "План" },
  { id: "chapter", file: null, label: "Глава" },
];

const state = {
  projects: [],
  book: null,
  stage: "card",
  busy: false,
};

const $ = (id) => document.getElementById(id);

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 5000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || res.status));
  }
  return data;
}

async function refreshHealth() {
  const box = $("llm-status");
  try {
    const h = await api("/api/health/llm");
    const orOk = h.openrouter.ok;
    const gcOk = h.gigachat.ok;
    const orLimit = h.openrouter.rate_limited;
    box.className = "status " + (orOk || gcOk ? "ok" : "bad");
    box.textContent =
      `OpenRouter: ${orOk ? (orLimit ? "лимит :free" : "ok") : "ошибка"} (${h.openrouter.model || "?"})\n` +
      (orOk && !orLimit ? "" : `  ${h.openrouter.error || ""}\n`) +
      `GigaChat: ${gcOk ? "ok" : "ошибка"} (${h.gigachat.model || "?"})\n` +
      (gcOk ? "" : `  ${h.gigachat.error || ""}`);
  } catch (err) {
    box.className = "status bad";
    box.textContent = "Сервер недоступен: " + err.message;
  }
}

async function refreshBooks() {
  const data = await api("/api/projects");
  state.projects = data.projects;
  const ul = $("book-list");
  ul.innerHTML = "";
  for (const p of state.projects) {
    const li = document.createElement("li");
    li.textContent = p.title;
    if (state.book && state.book.id === p.id) li.classList.add("active");
    li.onclick = () => openBook(p.id);
    ul.appendChild(li);
  }
}

async function openBook(id) {
  state.book = await api(`/api/projects/${id}`);
  state.stage = state.book.stage || "card";
  $("empty").classList.add("hidden");
  $("workspace").classList.remove("hidden");
  renderWorkspace();
  refreshBooks();
}

function currentText() {
  if (!state.book) return "";
  if (state.stage === "chapter") return state.book.draft || "";
  const file = STAGES.find((s) => s.id === state.stage).file;
  return (state.book.files && state.book.files[file]) || "";
}

function renderWorkspace() {
  const b = state.book;
  $("book-title").textContent = b.title;
  $("meta").textContent = `этап ${b.stage} · глава ${b.chapter_number}`;
  const nav = $("stages");
  nav.innerHTML = "";
  for (const s of STAGES) {
    const btn = document.createElement("button");
    btn.textContent = s.label;
    if (s.id === state.stage) btn.classList.add("on");
    btn.onclick = () => {
      persistEditor();
      state.stage = s.id;
      renderWorkspace();
    };
    nav.appendChild(btn);
  }
  $("editor").value = currentText();
  const hints = $("hints");
  if (b.hints && b.hints.length) {
    hints.classList.remove("hidden");
    hints.textContent = b.hints.map((h) => h.message || h).join("\n");
  } else {
    hints.classList.add("hidden");
  }
  renderActions();
}

function renderActions() {
  const box = $("actions");
  box.innerHTML = "";
  const add = (label, fn, cls) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    if (cls) btn.className = cls;
    btn.onclick = fn;
    box.appendChild(btn);
  };
  add("Сохранить", saveCurrent, "secondary");
  if (state.stage === "style") {
    const file = document.createElement("input");
    file.type = "file";
    file.multiple = true;
    file.accept = ".txt,.md,.docx,.pdf";
    file.onchange = () => uploadStyle(file.files);
    box.appendChild(file);
    add("Стиль с URL", styleFromUrl);
    add("Переписать стиль", () => generateStage("style"));
    add("Пропустить / утвердить стиль", () => approve("style"));
  } else if (state.stage === "chapter") {
    add("Писать главу", generateChapter);
    add("Доработать выделенное", reviseSelection);
    add("Принять главу", acceptChapter);
    add("Сохранить курс", saveCourse);
  } else {
    add("Предложить / переписать", () => generateStage(state.stage));
    add("Утвердить", () => approve(state.stage));
  }
}

async function persistEditor() {
  if (!state.book) return;
  const content = $("editor").value;
  if (state.stage === "chapter") {
    await api(`/api/projects/${state.book.id}/chapter/save-draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    state.book.draft = content;
    return;
  }
  const file = STAGES.find((s) => s.id === state.stage).file;
  await api(`/api/projects/${state.book.id}/files/${file}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  state.book.files[file] = content;
}

async function saveCurrent() {
  try {
    await persistEditor();
    toast("Сохранено");
  } catch (err) {
    toast(err.message);
  }
}

function setBusy(v) {
  state.busy = v;
  document.querySelectorAll("button").forEach((b) => {
    b.disabled = v;
  });
}

async function generateStage(stage) {
  const comment = $("comment").value;
  setBusy(true);
  toast("Генерация… это может занять минуту");
  try {
    await persistEditor();
    let data;
    if (stage === "style") {
      data = await api(`/api/projects/${state.book.id}/style/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment }),
      });
    } else {
      data = await api(`/api/projects/${state.book.id}/generate/${stage}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment }),
      });
    }
    state.book = data.project;
    $("editor").value = data.text;
    toast("Готово. Проверьте текст и утвердите.");
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(false);
  }
}

async function approve(stage) {
  try {
    await persistEditor();
    state.book = await api(`/api/projects/${state.book.id}/approve/${stage}`, { method: "POST" });
    state.stage = state.book.stage || stage;
    renderWorkspace();
    toast("Утверждено");
  } catch (err) {
    toast(err.message);
  }
}

async function uploadStyle(fileList) {
  const fd = new FormData();
  fd.append("comment", $("comment").value);
  [...fileList].slice(0, 10).forEach((f) => fd.append("files", f));
  setBusy(true);
  try {
    const data = await api(`/api/projects/${state.book.id}/style/files`, { method: "POST", body: fd });
    state.book = data.project;
    $("editor").value = data.text;
    toast("Стиль собран из файлов");
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(false);
  }
}

async function styleFromUrl() {
  const url = prompt("URL страницы (Rulate, QQ и т.п.):");
  if (!url) return;
  const cookies = prompt("Куки (можно пусто):") || "";
  setBusy(true);
  try {
    const data = await api(`/api/projects/${state.book.id}/style/url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, cookies, comment: $("comment").value }),
    });
    state.book = data.project;
    $("editor").value = data.text;
    toast("Стиль с URL");
  } catch (err) {
    toast(err.message + " — можно загрузить txt/docx/pdf");
  } finally {
    setBusy(false);
  }
}

async function generateChapter() {
  setBusy(true);
  $("overlaps").classList.add("hidden");
  toast("Пишем главу (несколько проходов)…");
  try {
    await persistEditor();
    const res = await fetch(`/api/projects/${state.book.id}/chapter/generate`, { method: "POST" });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const block of parts) {
        const ev = block.match(/^event:\s*(\w+)/m);
        const dataLine = block.match(/^data:\s*(.*)$/m);
        if (!ev || !dataLine) continue;
        const payload = JSON.parse(dataLine[1]);
        if (ev[1] === "progress") toast(payload.message || payload.step);
        if (ev[1] === "error") throw new Error(payload.error || "ошибка главы");
        if (ev[1] === "done") {
          $("editor").value = payload.text;
          state.book.draft = payload.text;
          showOverlaps(payload.overlaps || [], payload.word_count);
          toast(`Черновик главы ${payload.chapter}, ~${payload.word_count} слов`);
        }
      }
    }
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(false);
  }
}

function showOverlaps(items, words) {
  const box = $("overlaps");
  if (!items.length) {
    box.classList.remove("hidden");
    box.textContent = `Повторов выше порога не найдено. Слов: ${words ?? "?"}`;
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = items
    .map((o) =>
      o.error
        ? o.error
        : `сходство ${o.score} с гл. ${o.chapter}: «${o.query_preview}» ≈ «${o.stored_preview}»`
    )
    .join("<br/>");
}

async function reviseSelection() {
  const ta = $("editor");
  const sel = ta.value.substring(ta.selectionStart, ta.selectionEnd);
  if (!sel.trim()) {
    toast("Выделите фрагмент в тексте главы");
    return;
  }
  setBusy(true);
  try {
    const data = await api(`/api/projects/${state.book.id}/chapter/revise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection: sel, comment: $("comment").value }),
    });
    $("editor").value = data.text;
    state.book.draft = data.text;
    showOverlaps(data.overlaps || [], data.word_count);
    toast("Фрагмент переписан");
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(false);
  }
}

async function acceptChapter() {
  try {
    await persistEditor();
    const data = await api(`/api/projects/${state.book.id}/chapter/accept`, { method: "POST" });
    state.book = await api(`/api/projects/${state.book.id}`);
    $("editor").value = "";
    renderWorkspace();
    toast(`Глава ${data.accepted} принята. Дальше ${data.next}.`);
  } catch (err) {
    toast(err.message);
  }
}

async function saveCourse() {
  try {
    state.book = await api(`/api/projects/${state.book.id}/chapter/course`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: $("comment").value }),
    });
    toast("Курс сохранён. Пустое поле = агент сам по плану.");
  } catch (err) {
    toast(err.message);
  }
}

$("new-book").onsubmit = async (e) => {
  e.preventDefault();
  const title = e.target.title.value;
  const book = await api("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  e.target.reset();
  await refreshBooks();
  await openBook(book.id);
};

refreshHealth();
refreshBooks();
