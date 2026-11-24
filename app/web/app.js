function stageList() {
  const original = !state.book || state.book.is_original !== false;
  const card = { id: "card", file: "card.md", label: "Описание" };
  const style = { id: "style", file: "style.md", label: "Стиль" };
  const drama = { id: "drama", file: "drama.md", label: "Развитие сюжета" };
  const bible = { id: "bible", file: "bible.md", label: "Персонажи" };
  const plan = { id: "plan", file: "plan.md", label: "План" };
  const chapter = { id: "chapter", file: null, label: "Глава" };
  if (original) return [card, style, drama, bible, plan, chapter];
  return [card, style, bible, drama, plan, chapter];
}

const state = {
  projects: [],
  book: null,
  stage: "card",
  screen: "write",
  readAt: 0,
  readToken: 0,
  busy: false,
};
let sheet = "";

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
  box.textContent = "Проверяю модели…";
  try {
    const h = await api("/api/health/llm");
    const orOk = h.openrouter.ok;
    const gcOk = h.gigachat.ok;
    const orLimit = h.openrouter.rate_limited;
    box.className = "status";
    box.replaceChildren(
      modelPill("OpenRouter", orOk, orLimit ? "лимит" : orOk ? "на связи" : "нет ключа", h.openrouter.error),
      modelPill("GigaChat", gcOk, gcOk ? "на связи" : "ошибка", h.gigachat.error)
    );
  } catch (err) {
    box.className = "status";
    box.replaceChildren(modelPill("Сервер", false, "недоступен", err.message));
  }
}

function modelPill(name, ok, stateLabel, detail) {
  const el = document.createElement("span");
  el.className = "pill " + (ok ? "ok" : "bad");
  el.textContent = name + " · " + stateLabel;
  if (detail) el.title = detail;
  return el;
}

async function refreshBooks() {
  const data = await api("/api/projects");
  state.projects = data.projects;
  const ul = $("book-list");
  ul.innerHTML = "";
  for (const p of state.projects) {
    const li = document.createElement("li");
    const main = document.createElement("div");
    main.className = "book-main";
    const name = document.createElement("span");
    name.className = "book-name";
    name.textContent = p.title;
    const sub = document.createElement("span");
    sub.className = "book-sub";
    sub.textContent = "Глава " + (p.chapter_number || 1);
    const dates = document.createElement("span");
    dates.className = "book-dates";
    dates.textContent = "создана " + formatWhen(p.created_at) + " · изменена " + formatWhen(p.updated_at);
    main.append(name, sub, dates);
    const more = document.createElement("button");
    more.type = "button";
    more.className = "book-more";
    more.setAttribute("aria-label", "Действия с книгой");
    more.textContent = "⋯";
    more.onclick = (event) => {
      event.stopPropagation();
      event.preventDefault();
      const opened = li.classList.contains("menu-open");
      closeBookMenus();
      if (!opened) li.classList.add("menu-open");
    };
    const menu = document.createElement("div");
    menu.className = "book-menu";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "book-delete";
    remove.textContent = "Удалить книгу";
    remove.onclick = (event) => deleteBook(p, event);
    menu.append(remove);
    li.append(main, more, menu);
    if (state.book && state.book.id === p.id) li.classList.add("active");
    li.onclick = () => openBook(p.id);
    ul.appendChild(li);
  }
}

function closeBookMenus() {
  document.querySelectorAll(".book-list li.menu-open").forEach((item) => item.classList.remove("menu-open"));
}

async function openBook(id) {
  state.book = await api(`/api/projects/${id}`);
  state.stage = state.book.stage || "card";
  state.screen = "write";
  state.readAt = 0;
  $("empty").classList.add("hidden");
  $("workspace").classList.remove("hidden");
  renderWorkspace();
  refreshBooks();
}

async function deleteBook(project, event) {
  event.stopPropagation();
  event.preventDefault();
  const sure = confirm("Удалить книгу «" + project.title + "»? Файлы будут стёрты.");
  if (!sure) return;
  await api("/api/projects/" + project.id, { method: "DELETE" });
  if (state.book && state.book.id === project.id) {
    state.book = null;
    state.screen = "write";
    $("workspace").classList.add("hidden");
    $("empty").classList.remove("hidden");
  }
  toast("Книга удалена");
  refreshBooks();
}

function stageMark(id) {
  const book = state.book || {};
  const files = book.files || {};
  const text = (name) => (files[name] || "").trim();
  const drama = book.drama || {};
  const dramaReady = ["want", "belief", "cost", "becomes", "act1", "act2", "act3"].every(
    (key) => (drama[key] || "").trim()
  );
  let written = false;
  if (id === "card") written = Boolean(text("card.md"));
  if (id === "style") written = Boolean(text("style.md"));
  if (id === "bible") written = Boolean(text("bible.md"));
  if (id === "drama") written = dramaReady || Boolean(text("drama.md"));
  if (id === "plan") written = (book.plan || []).some((row) => (row.event || row.title || "").trim());
  if (id === "chapter") {
    written = (book.chapter_index || []).some(
      (row) => row.status === "accepted" || (row.status === "draft" && row.words)
    );
  }
  const stale = Boolean((book.stale || {})[id]) && written;
  let title = "Этого текста ещё нет.";
  if (written && stale) title = "Текст есть, но он старше правки предыдущего шага.";
  else if (written) title = "Текст этого шага уже есть.";
  return { written, stale, title };
}

function currentText() {
  if (!state.book) return "";
  if (state.stage === "chapter") return state.book.draft || "";
  const file = stageList().find((s) => s.id === state.stage).file;
  return (state.book.files && state.book.files[file]) || "";
}

function renderWorkspace() {
  const b = state.book;
  $("book-title").textContent = b.title;
  const current = (b.chapter_index || []).find((item) => item.number === b.chapter_number);
  const sizeName = { mini: "Мини", medium: "Среднее", large: "Большое" }[b.book_size] || "Среднее";
  $("meta").textContent = "Глава " + b.chapter_number + " · " + wordsLabel(current ? current.words : 0) + " · " + sizeName;
  const sizePicker = $("book-size");
  if (sizePicker) sizePicker.value = b.book_size || "medium";
  const nav = $("stages");
  nav.innerHTML = "";
  for (const s of stageList()) {
    const btn = document.createElement("button");
    const name = document.createElement("span");
    name.textContent = s.label;
    btn.append(name);
    const mark = stageMark(s.id);
    if (mark.written) btn.classList.add("written");
    if (mark.stale) {
      btn.classList.add("stale");
      const age = document.createElement("span");
      age.className = "age";
      age.textContent = "устарело";
      btn.append(age);
    }
    btn.title = mark.title;
    if (s.id === state.stage && state.screen === "write") btn.classList.add("on");
    btn.onclick = async () => {
      const target = s.id;
      state.screen = "write";
      try {
        await persistEditor();
      } catch (err) {
        showRun(err.message, true);
      }
      state.stage = target;
      renderWorkspace();
    };
    nav.appendChild(btn);
  }
  const currentStep = nav.querySelector(".on");
  if (currentStep) currentStep.scrollIntoView({ inline: "nearest", block: "nearest" });
  if (state.screen !== "write") sheet = "";
  if (sheet === "story" && state.stage !== "chapter") sheet = "";
  if (sheet === "canon" && state.stage !== "bible") sheet = "";
  $("editor").value = currentText();
  renderPlan();
  renderCanon();
  renderStory();
  renderAccepted();
  const hints = $("hints");
  if (b.hints && b.hints.length) {
    hints.classList.remove("hidden");
    hints.textContent = b.hints.map((h) => h.message || h).join("\n");
  } else {
    hints.classList.add("hidden");
  }
  renderActions();
  applyScreen();
  renderSheet();
  markView();
}

function applyScreen() {
  const writing = state.screen === "write";
  $("composer").classList.toggle("hidden", !writing);
  $("stages").classList.toggle("hidden", !writing);
  $("library").classList.toggle("hidden", writing);
  if (!writing) {
    $("editor").classList.add("hidden-editor");
    $("plan-board").classList.add("hidden");
    $("accepted-view").classList.add("hidden");
    $("hints").classList.add("hidden");
    $("overlaps").classList.add("hidden");
    sheet = "";
  }
  if (state.screen === "read") renderReader();
  if (state.screen === "structure") renderStructure();
}

function markView() {
  for (const id of ["write", "read", "structure"]) {
    const button = $("view-" + id);
    if (button) button.classList.toggle("on", state.screen === id);
  }
}

function setScreen(screen) {
  if (!state.book) return;
  state.screen = screen;
  if (screen === "read") {
    const order = readingOrder();
    if (!order.some((item) => item.number === state.readAt)) {
      state.readAt = order.length ? order[0].number : 0;
    }
  }
  renderWorkspace();
}

function readingOrder() {
  const rows = (state.book && state.book.chapter_index) || [];
  const accepted = rows.filter((item) => item.status === "accepted");
  const draft = rows.find((item) => item.status === "draft");
  return draft ? accepted.concat(draft) : accepted;
}

function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function wordsLabel(count) {
  const n = Math.abs(Number(count) || 0);
  const mod100 = n % 100;
  const mod10 = n % 10;
  let word = "слов";
  if (mod100 < 11 || mod100 > 14) {
    if (mod10 === 1) word = "слово";
    else if (mod10 >= 2 && mod10 <= 4) word = "слова";
  }
  return n + " " + word;
}

function statusLabel(status) {
  if (status === "accepted") return "принята";
  if (status === "draft") return "черновик";
  return "пусто";
}

async function renderReader() {
  const box = $("library");
  const order = readingOrder();
  const token = ++state.readToken;
  box.replaceChildren();
  if (!order.length) {
    const empty = document.createElement("p");
    empty.className = "plan-empty";
    empty.textContent = "Пока нечего читать. Принятых глав ещё нет.";
    box.appendChild(empty);
    return;
  }
  let index = order.findIndex((item) => item.number === state.readAt);
  if (index < 0) index = 0;
  const row = order[index];
  state.readAt = row.number;
  const head = document.createElement("div");
  head.className = "reader-head";
  const kicker = document.createElement("p");
  kicker.className = "kicker";
  kicker.textContent = "Глава " + row.number + " из " + order.length;
  const title = document.createElement("h3");
  title.textContent = row.title || "Без названия";
  const meta = document.createElement("p");
  meta.className = "book-dates";
  meta.textContent = [
    statusLabel(row.status),
    wordsLabel(row.words),
    "создана " + formatWhen(row.created_at),
    "изменена " + formatWhen(row.updated_at),
  ].join(" · ");
  const nav = document.createElement("div");
  nav.className = "reader-nav";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "secondary";
  prev.textContent = "Назад";
  prev.disabled = index === 0;
  prev.onclick = () => {
    state.readAt = order[index - 1].number;
    renderReader();
  };
  const next = document.createElement("button");
  next.type = "button";
  next.className = "secondary";
  next.textContent = "Дальше";
  next.disabled = index === order.length - 1;
  next.onclick = () => {
    state.readAt = order[index + 1].number;
    renderReader();
  };
  nav.append(prev, next);
  const prose = document.createElement("article");
  prose.className = "reader-prose";
  prose.textContent = "Открываю главу…";
  head.append(kicker, title, meta);
  box.append(head, nav, prose);
  try {
    const data = await api(
      "/api/projects/" + state.book.id + "/chapters/" + row.number + "?draft=" + (row.status === "draft")
    );
    if (token !== state.readToken || state.screen !== "read") return;
    prose.textContent = (data.text || "").trim() || "В этой главе пока нет текста.";
  } catch (err) {
    if (token !== state.readToken) return;
    prose.textContent = err.message;
  }
}

function dramaActSpans(drama) {
  if (!drama) return [];
  const ready = ["want", "belief", "cost", "becomes", "act1", "act2", "act3"].every(
    (key) => (drama[key] || "").trim()
  );
  if (!ready) return [];
  const spans = [];
  for (let index = 0; index < 3; index += 1) {
    const match = String(drama["act" + (index + 1)] || "").match(/(\d+)\s*[–—-]\s*(\d+)/);
    if (!match) return [];
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start < 1 || end > 100 || start > end) return [];
    spans.push({ label: "Акт " + (index + 1), start, end });
  }
  return spans;
}

function renderStructure() {
  const box = $("library");
  const book = state.book;
  box.replaceChildren();
  const lead = document.createElement("p");
  lead.className = "book-dates";
  lead.textContent = "Книга создана " + formatWhen(book.created_at) + " · изменена " + formatWhen(book.updated_at);
  box.appendChild(lead);
  const drama = renderDramaCard(book.drama);
  const spine = renderSpineCard(book.spine || {});
  const world = worldHasText(book.world) ? renderWorldCard(book.world, book.is_original !== false) : null;
  if (drama) box.appendChild(drama);
  if (spine) box.appendChild(spine);
  if (world) box.appendChild(world);
  const written = (book.chapter_index || []).filter((row) => row.status !== "empty");
  if (!written.length) {
    const empty = document.createElement("p");
    empty.className = "plan-empty";
    empty.textContent = "Написанных глав пока нет.";
    box.appendChild(empty);
    return;
  }
  const spans = dramaActSpans(book.drama);
  if (!spans.length) {
    box.appendChild(structureSection("", written));
    return;
  }
  const placed = new Set();
  for (const span of spans) {
    const group = written.filter((row) => row.number >= span.start && row.number <= span.end);
    group.forEach((row) => placed.add(row.number));
    if (group.length) box.appendChild(structureSection(span.label + " · главы " + span.start + "–" + span.end, group));
  }
  const rest = written.filter((row) => !placed.has(row.number));
  if (rest.length) box.appendChild(structureSection("", rest));
}

function structureSection(label, rows) {
  const section = document.createElement("section");
  section.className = "act-block";
  if (label) {
    const heading = document.createElement("h3");
    heading.textContent = label;
    section.appendChild(heading);
  }
  for (const row of rows) {
    const line = document.createElement("button");
    line.type = "button";
    line.className = "structure-row " + row.status;
    const num = document.createElement("span");
    num.className = "plan-num";
    num.textContent = String(row.number).padStart(2, "0");
    const name = document.createElement("span");
    name.className = "structure-title";
    name.textContent = row.title || "Без названия";
    const stateName = document.createElement("span");
    stateName.textContent = statusLabel(row.status) + (row.words ? " · " + wordsLabel(row.words) : "");
    const when = document.createElement("span");
    when.className = "book-dates";
    when.textContent = row.updated_at
      ? "создана " + formatWhen(row.created_at) + " · изменена " + formatWhen(row.updated_at)
      : "";
    line.append(num, name, stateName, when);
    line.onclick = () => openFromStructure(row);
    section.appendChild(line);
  }
  return section;
}

function openFromStructure(row) {
  if (row.status === "draft") {
    state.stage = "chapter";
    state.screen = "write";
    renderWorkspace();
    return;
  }
  if (row.status !== "accepted") return;
  state.readAt = row.number;
  state.screen = "read";
  renderWorkspace();
}

function renderPlan() {
  const board = $("plan-board");
  const editor = $("editor");
  if (state.stage !== "plan") {
    board.classList.add("hidden");
    editor.classList.remove("hidden-editor");
    return;
  }
  editor.classList.add("hidden-editor");
  board.classList.remove("hidden");
  board.innerHTML = "";
  const drama = renderDramaCard(state.book.drama);
  if (drama) board.appendChild(drama);
  const spineCard = renderSpineCard(state.book.spine || {});
  if (spineCard) board.appendChild(spineCard);
  board.appendChild(renderWorldCard(state.book.world, state.book.is_original !== false));
  const rows = state.book.plan || [];
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "plan-empty";
    empty.textContent = "Плана ещё нет. Его можно предложить кнопкой внизу.";
    board.appendChild(empty);
    return;
  }
  for (const row of rows) {
    const article = document.createElement("article");
    article.className = "plan-row";
    article.dataset.planNumber = String(row.number);
    const head = document.createElement("div");
    head.className = "plan-head";
    const num = document.createElement("span");
    num.className = "plan-num";
    num.textContent = String(row.number).padStart(2, "0");
    head.append(num, field("title", row.title || "", "Название главы"));
    article.append(
      head,
      labeled(field("tags", row.tags || "", "Какой это ход: разговор, погоня, торг"), "Метки"),
      labeled(area("event", row.event || "", ""), "Что происходит"),
      labeled(area("reveal", row.reveal || "", ""), "Что узнаёт читатель")
    );
    board.appendChild(article);
  }
}

function renderDramaCard(drama) {
  return sheetEditor("drama-sheet", "Развитие сюжета", "drama", [
    ["Герой", "hero"],
    ["Хочет", "want"],
    ["Верит", "belief"],
    ["Цена", "cost"],
    ["Становится", "becomes"],
    ["Акт 1", "act1"],
    ["Акт 2", "act2"],
    ["Акт 3", "act3"],
    ["Мир акта 1", "world1"],
    ["Мир акта 2", "world2"],
    ["Мир акта 3", "world3"],
  ], drama || {});
}

function renderWorldCard(world, original) {
  const card = sheetEditor("world-sheet", "Мир", "world", [
    ["Когда и где", "when_where"],
    ["Обычная жизнь", "daily"],
    ["Первый час", "first_hour"],
    ["Пока скрыто", "hidden"],
  ], world || {});
  const hint = document.createElement("p");
  hint.className = "plan-empty";
  hint.textContent = original
    ? "Своя книга: эти поля задают мир, которого ещё не было. У каждой главы ниже — одна новая подробность."
    : "Чужая книга: здесь только то, что уже написано в заметках и на карточке. Новых фактов лист не добавляет.";
  card.insertBefore(hint, card.children[1]);
  return card;
}

function worldHasText(world) {
  return Boolean(world && Object.values(world).some((value) => String(value || "").trim()));
}

function renderSpineCard(spine) {
  return sheetEditor("spine", "События книги", "spine", [
    ["Финал", "finale"],
    ["Акт 1", "act1"],
    ["Акт 2", "act2"],
    ["Акт 3", "act3"],
  ], spine || {});
}

function sheetEditor(className, title, sheet, pairs, data) {
  const block = document.createElement("section");
  block.className = className;
  const heading = document.createElement("h3");
  heading.textContent = title;
  const grid = document.createElement("div");
  grid.className = className === "spine" ? "spine-grid" : "drama-grid";
  for (const [label, key] of pairs) {
    const card = document.createElement("article");
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = label;
    const area = document.createElement("textarea");
    area.dataset.sheet = sheet;
    area.dataset.key = key;
    area.value = data[key] || "";
    area.rows = 2;
    area.spellcheck = true;
    card.append(kicker, area);
    grid.appendChild(card);
  }
  block.append(heading, grid);
  return block;
}

function sheetPayload(name) {
  const payload = {};
  for (const field of document.querySelectorAll(`[data-sheet="${name}"]`)) {
    payload[field.dataset.key] = field.value;
  }
  return payload;
}

function sheetUntouched(name, stored) {
  const fields = [...document.querySelectorAll(`[data-sheet="${name}"]`)];
  if (!fields.length) return true;
  return fields.every((field) => sameText(field.value, (stored && stored[field.dataset.key]) || ""));
}

function renderCanon() {
  const notes = $("canon-notes");
  if (!notes || !state.book) return;
  if (notes.dataset.bookId !== state.book.id) {
    notes.dataset.bookId = state.book.id;
    notes.value = state.book.canon_notes || "";
  }
}

function setSheet(name) {
  sheet = sheet === name ? "" : name;
  renderSheet();
}

function renderSheet() {
  const drawer = $("drawer");
  if (!drawer) return;
  const writing = state.screen === "write";
  if (!writing) sheet = "";
  if (sheet === "story" && state.stage !== "chapter") sheet = "";
  if (sheet === "canon" && state.stage !== "bible") sheet = "";
  const titles = {
    story: "Ближайшие главы",
    note: "Заметка",
    canon: "Заметки о мире",
  };
  drawer.classList.toggle("open", Boolean(sheet));
  drawer.setAttribute("aria-hidden", sheet ? "false" : "true");
  $("drawer-title").textContent = titles[sheet] || "";
  $("pane-story").classList.toggle("hidden", sheet !== "story");
  $("pane-note").classList.toggle("hidden", sheet !== "note");
  $("pane-canon").classList.toggle("hidden", sheet !== "canon");
  document.querySelectorAll("#actions button[data-sheet]").forEach((button) => {
    button.classList.toggle("on", button.dataset.sheet === sheet);
  });
}

function field(name, value, placeholder) {
  const input = document.createElement("input");
  input.dataset.field = name;
  input.value = value;
  input.placeholder = placeholder;
  return input;
}

function area(name, value, placeholder) {
  const input = document.createElement("textarea");
  input.dataset.field = name;
  input.value = value;
  input.placeholder = placeholder;
  return input;
}

function labeled(control, caption) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const span = document.createElement("span");
  span.textContent = caption;
  wrap.append(span, control);
  return wrap;
}

function renderAccepted() {
  const box = $("accepted-view");
  const text = (state.book && state.book.accepted_text) || "";
  if (state.stage !== "chapter" || !text.trim()) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  const number = Math.max(1, (state.book.chapter_number || 1) - 1);
  box.classList.remove("hidden");
  box.replaceChildren();
  const kicker = document.createElement("p");
  kicker.className = "kicker";
  kicker.textContent = "Принятая глава " + number;
  const prose = document.createElement("div");
  prose.className = "prose";
  prose.textContent = text;
  box.append(kicker, prose);
}

function renderStory() {
  const box = $("story-strip");
  if (!box) return;
  box.replaceChildren();
  if (state.stage !== "chapter" || !state.book) return;
  const arc = state.book.arc;
  const open = (state.book.events || []).filter((item) => item.status === "открыто");
  if (open.length) {
    const block = document.createElement("p");
    block.textContent = open
      .map((item) => "Глава " + item.chapter + " · " + item.kind + " · " + item.body)
      .join("\n");
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = "Открытые события";
    box.append(kicker, block);
  }
  if (arc && (arc.promise || arc.steps || arc.payoff_chapter)) {
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = "Обещание";
    const promise = document.createElement("p");
    const promiseText = String(arc.promise || "Обещание ещё не записано").replace(/[.!?…]\s*$/, "");
    promise.textContent = promiseText + ". Не раскрывать раньше главы " + (arc.payoff_chapter || "—") + ".";
    box.append(kicker, promise);
    if (arc.steps) {
      const steps = document.createElement("p");
      steps.textContent = arc.steps;
      box.appendChild(steps);
    }
  }
  if (!box.childElementCount) {
    const empty = document.createElement("p");
    empty.textContent = "Пока нет открытых событий и обещаний на ближайшие главы.";
    box.appendChild(empty);
  }
}

const STATUS_LINES = {
  card: ["Собираем описание", "Записываем, о чём книга", "Собираем замысел в описание"],
  style: ["Подбираем манеру фразы", "Собираем стиль", "Смотрим, как должна звучать проза"],
  drama: ["Намечаем развитие сюжета", "Ищем, чего хочет герой и чем это кончится", "Раскладываем перемену по актам"],
  bible: ["Описываем персонажей", "Собираем, кто давит на героя", "Записываем лица книги"],
  plan: ["Составляем план", "Раскладываем события по главам", "Отмечаем, что в каждой главе узнаёт читатель"],
  chapter: ["Договариваемся, что будет в главе", "Намечаем ход этой главы", "Пишем главу"],
};

let statusTimer = 0;
let statusStage = "";
let statusIndex = -1;
let statusPercent = null;

function statusPhrases(id) {
  const list = (STATUS_LINES[id] || ["Собираем шаг"]).slice();
  if (id === "chapter" && statusPercent != null) {
    return list.map((line) => line + ", уже готово " + statusPercent + "%");
  }
  return list;
}

function nextStatusDelay() {
  return 3000 + Math.floor(Math.random() * 2001);
}

function haltStatusCycle() {
  if (statusTimer) window.clearTimeout(statusTimer);
  statusTimer = 0;
  statusStage = "";
}

function paintRun(message, isError, animate) {
  const line = $("run-line");
  if (!line) return;
  const current = line.querySelector(".run-text.current");
  const next = line.querySelector(".run-text.next");
  if (!current || !next) {
    line.textContent = message || "";
    line.classList.toggle("hidden", !message);
    line.classList.toggle("error", Boolean(isError));
    return;
  }
  if (line._swapDone) {
    line.removeEventListener("transitionend", line._swapDone);
    line._swapDone = null;
  }
  if (line._swapTimer) {
    window.clearTimeout(line._swapTimer);
    line._swapTimer = 0;
  }
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const visible = !line.classList.contains("hidden") && current.textContent;
  line.classList.toggle("error", Boolean(isError));
  if (!message) {
    line.classList.add("hidden");
    line.classList.remove("swap", "error");
    current.textContent = "";
    next.textContent = "";
    return;
  }
  line.classList.remove("hidden");
  if (!animate || isError || reduce || !visible) {
    line.classList.add("no-motion");
    line.classList.remove("swap");
    current.textContent = message;
    next.textContent = "";
    window.requestAnimationFrame(() => line.classList.remove("no-motion"));
    return;
  }
  next.textContent = message;
  line.classList.add("swap");
  let settled = false;
  const commit = () => {
    if (settled) return;
    settled = true;
    window.clearTimeout(line._swapTimer);
    line.removeEventListener("transitionend", done);
    line._swapDone = null;
    line.classList.add("no-motion");
    current.textContent = message;
    next.textContent = "";
    line.classList.remove("swap");
    window.requestAnimationFrame(() => line.classList.remove("no-motion"));
  };
  const done = (event) => {
    if (event.target !== next || event.propertyName !== "opacity") return;
    commit();
  };
  line._swapDone = done;
  line._swapTimer = window.setTimeout(commit, 700);
  line.addEventListener("transitionend", done);
}

function advanceStatus() {
  if (!statusStage) return;
  const list = statusPhrases(statusStage);
  let next = Math.floor(Math.random() * list.length);
  if (list.length > 1 && next === statusIndex) next = (next + 1) % list.length;
  statusIndex = next;
  paintRun(list[statusIndex], false, true);
  statusTimer = window.setTimeout(advanceStatus, nextStatusDelay());
}

function startStatusCycle(id) {
  haltStatusCycle();
  statusPercent = null;
  statusStage = id;
  const list = statusPhrases(id);
  statusIndex = Math.floor(Math.random() * list.length);
  paintRun(list[statusIndex], false, false);
  statusTimer = window.setTimeout(advanceStatus, nextStatusDelay());
}

function noteProgress(message) {
  const found = String(message || "").match(/(\d+)\s*%/);
  if (found) statusPercent = Number(found[1]);
  paintRun(message, false, true);
  if (!statusStage) return;
  if (statusTimer) window.clearTimeout(statusTimer);
  statusTimer = window.setTimeout(advanceStatus, nextStatusDelay());
}

function showRun(message, isError) {
  haltStatusCycle();
  paintRun(message, Boolean(isError), false);
}

function renderActions() {
  const box = $("actions");
  box.innerHTML = "";
  const add = (label, fn, cls, hint) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    if (cls) btn.className = cls;
    if (hint) {
      btn.title = hint;
      btn.dataset.hint = hint;
    }
    btn.onclick = fn;
    box.appendChild(btn);
    return btn;
  };
  const sheetButton = (label, name, hint) => {
    const button = add(label, () => setSheet(name), sheet === name ? "on" : "", hint);
    button.dataset.sheet = name;
  };
  if (state.stage === "chapter") {
    sheetButton(
      "Ближайшие главы",
      "story",
      "Открытые события и обещание, которое нельзя раскрывать раньше указанной главы."
    );
  }
  sheetButton("Заметка", "note", "Замечание к переписи или текст, из которого собрать книгу.");
  if (state.stage === "bible") {
    sheetButton(
      "Заметки о мире",
      "canon",
      "Факты чужой истории. Для своей книги можно не заполнять. Сохраняются вместе с кнопкой «Сохранить»."
    );
  }
  const rewriteLabel = {
    card: "Переписать описание",
    style: "Переписать стиль",
    drama: "Переписать развитие сюжета",
    bible: "Переписать персонажей",
    plan: "Переписать план",
  };
  const rewriteHint = "Переписывает только этот шаг. Текст из «Заметки» попадёт в запрос. Остальные шаги не запускаются.";
  add(
    "Собрать по заметке",
    assembleFromNote,
    "secondary",
    "По тексту из «Заметки» собирает описание, стиль, развитие, персонажей и план, если их ещё нет, и пишет главу. Готовые шаги не трогает."
  );
  add("Сохранить", saveCurrent, "secondary", "Записывает текст, который сейчас на экране, включая ячейки плана. Модель для этого не нужна.");
  if (state.stage === "style") {
    const file = document.createElement("input");
    file.type = "file";
    file.multiple = true;
    file.accept = ".txt,.md,.docx,.pdf";
    file.title = "До десяти файлов с текстом автора. Из них соберётся стиль.";
    file.onchange = () => uploadStyle(file.files);
    box.appendChild(file);
    add("Стиль с URL", styleFromUrl, "secondary", "Снимает манеру автора со страницы по ссылке.");
    add("Переписать стиль", () => generateStage("style"), "primary", rewriteHint);
  } else if (state.stage === "chapter") {
    add("Доработать выделенное", reviseSelection, "secondary", "Переписывает выделенный фрагмент с учётом замечания.");
    add("Принять главу", acceptChapter, "", "Принимает черновик готовой главой и открывает следующую. Скачок времени это остановит.");
    add("Запомнить направление", saveCourse, "secondary", "Записывает заметку как направление следующих глав. Пустое поле оставляет направление из плана.");
    add(
      "Писать главу",
      generateChapter,
      "primary",
      "Пишет главу четырьмя событиями одного дня и соединяет их. Готовый черновик заменяется. Если прошлый запуск оборвался, уже записанные куски продолжаются."
    );
  } else {
    add(
      rewriteLabel[state.stage] || "Переписать этот шаг",
      () => generateStage(state.stage),
      "primary",
      rewriteHint
    );
  }
}

function sameText(left, right) {
  return String(left || "").replace(/\r\n/g, "\n") === String(right || "").replace(/\r\n/g, "\n");
}

function planUntouched(chapters) {
  const stored = state.book.plan || [];
  if (stored.length !== chapters.length) return false;
  return chapters.every((row, index) => {
    const old = stored[index] || {};
    return (
      Number(old.number) === row.number &&
      sameText(old.title, row.title) &&
      sameText(old.tags, row.tags) &&
      sameText(old.event, row.event) &&
      sameText(old.reveal, row.reveal)
    );
  });
}

async function persistEditor() {
  if (!state.book) return;
  if (state.stage === "plan") {
    const chapters = [...document.querySelectorAll(".plan-row")].map((row) => ({
      number: Number(row.dataset.planNumber),
      title: row.querySelector("[data-field=title]").value,
      tags: row.querySelector("[data-field=tags]").value,
      event: row.querySelector("[data-field=event]").value,
      reveal: row.querySelector("[data-field=reveal]").value,
    }));
    const drama = sheetPayload("drama");
    const spine = sheetPayload("spine");
    const world = sheetPayload("world");
    const sheetsSame =
      sheetUntouched("drama", state.book.drama) &&
      sheetUntouched("spine", state.book.spine) &&
      sheetUntouched("world", state.book.world);
    if ((!chapters.length || planUntouched(chapters)) && sheetsSame) return;
    state.book = await api(`/api/projects/${state.book.id}/plan`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapters, drama, spine, world }),
    });
    return;
  }
  const content = $("editor").value;
  if (state.stage === "bible") {
    const notes = $("canon-notes").value;
    if (!sameText(notes, state.book.canon_notes || "")) {
      state.book = await api(`/api/projects/${state.book.id}/canon`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: notes }),
      });
    }
  }
  if (state.stage === "chapter") {
    if (sameText(content, state.book.draft || "")) return;
    await api(`/api/projects/${state.book.id}/chapter/save-draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    state.book.draft = content;
    return;
  }
  const file = stageList().find((s) => s.id === state.stage).file;
  const stored = (state.book.files && state.book.files[file]) || "";
  if (sameText(content, stored)) return;
  const data = await api(`/api/projects/${state.book.id}/files/${file}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  state.book = data.project || state.book;
}

async function saveCurrent() {
  try {
    const before = JSON.stringify(state.book.stale || {});
    await persistEditor();
    renderWorkspace();
    if (JSON.stringify(state.book.stale || {}) !== before) {
      toast("Сохранено. Шаги ниже отмечены: их текст старше этой правки.");
      return;
    }
    toast("Сохранено");
  } catch (err) {
    showRun(err.message, true);
  }
}

function setBusy(v) {
  state.busy = v;
  document.querySelectorAll("button").forEach((b) => {
    if (b.dataset.keep) return;
    b.disabled = v;
  });
}

async function requestStage(stage, comment) {
  let note = comment || "";
  if (stage === "style" && !note && !((state.book.files || {})["style.md"] || "").trim()) {
    note = "Собери манеру прозы по описанию этой книги. Чужого автора нет.";
  }
  const path = stage === "style"
    ? `/api/projects/${state.book.id}/style/generate`
    : `/api/projects/${state.book.id}/generate/${stage}`;
  const data = await api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment: note }),
  });
  state.book = data.project;
  renderWorkspace();
  if (state.stage !== "plan" && state.stage === stage) $("editor").value = data.text || "";
  return data;
}

async function generateStage(stage) {
  setBusy(true);
  startStatusCycle(stage);
  try {
    await persistEditor();
    await requestStage(stage, $("comment").value);
    const step = stageList().find((item) => item.id === stage);
    showRun("Готово: " + (step ? step.label : stage));
  } catch (err) {
    showRun(err.message, true);
  } finally {
    haltStatusCycle();
    setBusy(false);
  }
}

async function assembleFromNote() {
  if (!state.book || state.busy) return;
  const note = $("comment").value.trim();
  const steps = stageList().filter((item) => item.id !== "chapter" && !stageMark(item.id).written);
  if (steps.some((item) => item.id === "card") && !note) {
    showRun("Напишите заметку внизу. Из неё соберутся описание, персонажи, план и глава.", true);
    return;
  }
  setBusy(true);
  try {
    await persistEditor();
    for (const step of steps) {
      startStatusCycle(step.id);
      await requestStage(step.id, note);
    }
    startStatusCycle("chapter");
    await streamChapter();
  } catch (err) {
    showRun(err.message, true);
  } finally {
    haltStatusCycle();
    setBusy(false);
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
    showRun(err.message, true);
  } finally {
    setBusy(false);
  }
}

async function styleFromUrl() {
  const url = prompt("Ссылка на страницу с текстом:");
  if (!url) return;
  const cookies = prompt("Если страница не открывается без данных входа, вставьте их сюда. Можно оставить пустым:") || "";
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
    showRun(err.message + " — можно загрузить txt/docx/pdf", true);
  } finally {
    setBusy(false);
  }
}

function clearStopButtons() {
  document.querySelectorAll("#actions [data-keep]").forEach((node) => node.remove());
}

function ensureStopButton() {
  clearStopButtons();
  const stop = document.createElement("button");
  stop.textContent = "Остановить";
  stop.className = "danger";
  stop.dataset.keep = "1";
  stop.title = "Останавливает письмо. Уже записанные куски остаются.";
  stop.dataset.hint = stop.title;
  stop.onclick = () => {
    fetch(`/api/projects/${state.book.id}/chapter/cancel`, { method: "POST" });
    showRun("Останавливаю главу. Готовые куски сохранятся.");
  };
  $("actions").appendChild(stop);
}

async function generateChapter() {
  if (state.busy) return;
  setBusy(true);
  startStatusCycle("chapter");
  try {
    await persistEditor();
    await streamChapter();
  } catch (err) {
    showRun(err.message, true);
  } finally {
    haltStatusCycle();
    setBusy(false);
  }
}

async function streamChapter() {
  ensureStopButton();
  try {
    await readChapterStream();
  } finally {
    clearStopButtons();
  }
}

async function readChapterStream() {
  $("overlaps").classList.add("hidden");
  const res = await fetch(`/api/projects/${state.book.id}/chapter/generate`, { method: "POST" });
  if (!res.ok) {
    let detail = "Не удалось начать главу";
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || detail);
    } catch (_) {
      /* ответ без JSON */
    }
    throw new Error(detail);
  }
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
      if (ev[1] === "progress") noteProgress(payload.message || payload.step);
      if (ev[1] === "error") throw new Error(payload.error || "Глава не собралась");
      if (ev[1] === "done") {
        $("editor").value = payload.text;
        state.book.draft = payload.text;
        showOverlaps(payload.overlaps || [], payload.word_count, payload.bounds || []);
        const problems = payload.bounds || [];
        const boundNote = problems.length ? " " + problems.join(" ") : "";
        showRun(
          `Черновик главы ${payload.chapter}, ${wordsLabel(payload.word_count)}.${boundNote}`,
          problems.length > 0
        );
      }
    }
  }
}

function showOverlaps(items, words, bounds) {
  const box = $("overlaps");
  const lines = [];
  if (!items.length) lines.push(`Повторов выше порога не найдено. Слов: ${words ?? "?"}`);
  for (const item of items) {
    lines.push(
      item.error
        ? item.error
        : `сходство ${item.score} с гл. ${item.chapter}: «${item.query_preview}» ≈ «${item.stored_preview}»`
    );
  }
  for (const issue of bounds || []) lines.push(issue);
  box.classList.remove("hidden");
  box.textContent = lines.join("\n");
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
    showRun(err.message, true);
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
    showRun(err.message, true);
  }
}

async function saveCourse() {
  try {
    state.book = await api(`/api/projects/${state.book.id}/chapter/course`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: $("comment").value }),
    });
    toast("Направление следующих глав записано. Пустое поле оставляет план.");
  } catch (err) {
    showRun(err.message, true);
  }
}

document.addEventListener("click", closeBookMenus);

$("drawer-close").onclick = () => setSheet("");
document.querySelectorAll(".themes button").forEach((button) => {
  button.onclick = () => {
    document.body.classList.toggle("night", button.dataset.theme === "night");
    document.querySelectorAll(".themes button").forEach((item) => {
      item.classList.toggle("on", item === button);
    });
    try {
      localStorage.setItem("room-theme", button.dataset.theme);
    } catch (_) {
      /* тема просто не запомнится */
    }
  };
});
try {
  if (localStorage.getItem("room-theme") === "night") {
    document.querySelector('.themes button[data-theme="night"]').click();
  }
} catch (_) {
  /* нет хранилища */
}

$("view-write").onclick = () => setScreen("write");
$("view-read").onclick = () => setScreen("read");
$("view-structure").onclick = () => setScreen("structure");

let newBookSize = "medium";
document.querySelectorAll(".size-pick button").forEach((button) => {
  button.onclick = () => {
    newBookSize = button.dataset.size;
    document.querySelectorAll(".size-pick button").forEach((item) => {
      item.classList.toggle("on", item === button);
    });
  };
});

$("book-size").onchange = async () => {
  if (!state.book) return;
  const bookSize = $("book-size").value;
  if (bookSize === (state.book.book_size || "medium")) return;
  const hadPlan = stageMark("drama").written || stageMark("plan").written;
  try {
    state.book = await api(`/api/projects/${state.book.id}/size`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_size: bookSize }),
    });
    renderWorkspace();
    showRun(
      hadPlan
        ? "Размер записан. Развитие сюжета и план отмечены: их номера глав были для другого размера."
        : "Размер записан."
    );
  } catch (err) {
    showRun(err.message, true);
  }
};

$("new-book").onsubmit = async (e) => {
  e.preventDefault();
  const title = e.target.title.value;
  const book = await api("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, book_size: newBookSize }),
  });
  e.target.reset();
  newBookSize = "medium";
  document.querySelectorAll(".size-pick button").forEach((item) => {
    item.classList.toggle("on", item.dataset.size === "medium");
  });
  await refreshBooks();
  await openBook(book.id);
};

$("llm-status").onclick = () => refreshHealth();
$("llm-status").title = "Проверить связь с моделями. Само при открытии страницы не спрашивает.";
$("llm-status").textContent = "Нажмите, чтобы проверить модели";
refreshBooks();
