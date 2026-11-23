"use strict";

const $ = (id) => document.getElementById(id);
const types = { manga: "Manga", manhwa: "Manhwa", manhua: "Manhua", novel: "Novela", other: "Otro" };
const statuses = {
    planned: { label: "Por leer", icon: "◷", color: "#c1a9ff" },
    reading: { label: "Leyendo", icon: "▸", color: "#86d8c2" },
    on_hold: { label: "En pausa", icon: "Ⅱ", color: "#f0cd8b" },
    completed: { label: "Completado", icon: "✓", color: "#a7d8f7" },
    dropped: { label: "Abandonado", icon: "−", color: "#f1a8bb" },
};
const collator = new Intl.Collator("es", { numeric: true, sensitivity: "base" });
const pageSize = 72;
const state = { works: [], status: "", view: "grid", limit: pageSize, ready: false, failed: false, remote: new Map() };
const workForm = $("work-form");
const importForm = $("import-form");
const backupForm = $("backup-form");
const tagFilter = $("tag-filter");
const folderFilter = $("folder-filter");
const typeFilter = $("type-filter");
let editing = null;
let editTags = [];
let preview = null;
let treeRoot = null;
let emptyAction = () => openImport();

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function field(form, name) {
    return form.elements.namedItem(name);
}

function fillSelect(select, entries, defaultValue = "") {
    const prior = select.value || defaultValue;
    select.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
    if (entries.some(([value]) => value === prior)) select.value = prior;
}

function action(text, handler, className = "button secondary") {
    const button = node("button", className, text);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
}

async function api(path, { method = "GET", data, signal } = {}) {
    const headers = {};
    const multipart = data instanceof FormData;
    if (method !== "GET") {
        headers["X-JMangaShelf"] = "1";
        const instance = document.querySelector('meta[name="jmangashelf-instance"]');
        if (instance) headers["X-JMangaShelf-Instance"] = instance.content;
    }
    if (data !== undefined && !multipart) headers["Content-Type"] = "application/json";
    let response;
    try {
        response = await fetch(path, { method, headers, signal, body: multipart ? data : JSON.stringify(data) });
    } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new Error("No se pudo conectar con JMangaShelf. Comprueba que la aplicación siga abierta.");
    }
    if (response.status === 204) return null;
    const result = await response.json().catch(() => null);
    if (!response.ok || result === null) {
        throw new Error(result?.error || "No se pudo completar la operación.");
    }
    return result;
}

function feedback(message, error = false) {
    const region = $("feedback");
    region.hidden = !message;
    region.classList.toggle("error", error);
    region.textContent = message;
}

function formError(form, message = "") {
    const error = form.querySelector(".form-error");
    error.textContent = message;
    error.hidden = !message;
    if (message) error.scrollIntoView({ block: "nearest" });
}

async function runForm(form, operation) {
    if (form.dataset.busy) return;
    formError(form);
    const buttons = Array.from(form.querySelectorAll("button:not(:disabled)"));
    form.dataset.busy = "1";
    buttons.forEach((button) => { button.disabled = true; });
    try {
        await operation();
    } catch (error) {
        formError(form, error.message);
    } finally {
        delete form.dataset.busy;
        buttons.forEach((button) => { button.disabled = false; });
        if (form === importForm && preview) updateSelection();
    }
}

const originLabel = value => value === "Edge" ? "Microsoft Edge" : value;
const displayPath = (path) => path.length ? path.join(" › ") : "Sin carpeta";
const normalizeSearch = (text) => text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es");
const sectionButtons = new Map();

let selecting = false;
const selected = new Set();
let filteredWorks = [];

function setupSections() {
    for (const [key, section] of [["", { label: "Todas las obras", icon: "▦" }], ["inbox", {label:"Sin clasificar", icon:"▤"}], ...Object.entries(statuses)]) {
        const button = action("", () => {
            state.status = key;
            if (key === "inbox") $("class-filter").value = "";
            feedback("");
            resetResults();
        }, "section-button");
        const icon = node("span", "section-icon", section.icon);
        icon.setAttribute("aria-hidden", "true");
        if (section.color) icon.style.setProperty("--section-color", section.color);
        const amount = node("span", "section-count", "0");
        button.append(icon, node("span", "", section.label), amount);
        sectionButtons.set(key, { button, amount });
        $("sections").append(button);
    }
}

function populateFilters() {
    const tags = new Set();
    const folders = new Map();
    for (const work of state.works) {
        work.tags.forEach((tag) => tags.add(tag));
        for (const path of work.bookmark_paths) {
            if (!path.length) folders.set("[]", displayPath(path));
            for (let depth = 1; depth <= path.length; depth++) {
                const parent = path.slice(0, depth);
                folders.set(JSON.stringify(parent), displayPath(parent));
            }
        }
    }
    if (tagFilter.value) tags.add(tagFilter.value);
    if (folderFilter.value && !folders.has(folderFilter.value)) {
        folders.set(folderFilter.value, displayPath(JSON.parse(folderFilter.value)));
    }
    fillSelect(tagFilter, [["", "Todas las etiquetas"], ...Array.from(tags).sort(collator.compare).map((tag) => [tag, tag])]);
    fillSelect(folderFilter, [["", "Todas las carpetas"], ...Array.from(folders).sort((a, b) => collator.compare(a[1], b[1]))]);
}

async function refresh() {
    try {
        const incoming = await api("/api/works");
        const previous = new Map(state.works.map(work => [work.id, work]));
        state.works = incoming.map(work => {
            const cached = previous.get(work.id);
            return cached?.updated_at === work.updated_at && JSON.stringify(cached) === JSON.stringify(work) ? cached : work;
        });
        state.failed = false;
        const ids = new Set(state.works.map(work => work.id));
        for (const id of selected) if (!ids.has(id)) selected.delete(id);
        populateFilters();
    } catch (error) {
        state.failed = true;
        feedback(error.message, true);
    }
    state.ready = true;
    renderLibrary();
}

const cardData = new WeakMap();

function captureLibraryPosition(library) {
    const anchors = [];
    for (const [index, card] of Array.from(library.children).entries()) {
        const bounds = card.getBoundingClientRect();
        if (bounds.bottom > 0 && bounds.top < window.innerHeight) {
            anchors.push({id: card.dataset.workId, index, top: bounds.top});
        }
    }
    return {anchors, y: window.scrollY};
}

function restoreLibraryPosition(position, next, editedId) {
    if (!position) return;
    const positions = new Map(next.map((card, index) => [card.dataset.workId, {card, index}]));
    const edited = position.anchors.find(anchor => anchor.id === editedId && positions.get(editedId)?.index === anchor.index);
    const fallback = position.anchors.find(anchor => positions.has(anchor.id) && anchor.id !== editedId &&
        Math.abs(positions.get(anchor.id).index - anchor.index) <= position.anchors.length);
    const anchor = edited || fallback;
    const top = anchor ? window.scrollY + positions.get(anchor.id).card.getBoundingClientRect().top - anchor.top : position.y;
    if (Math.abs(window.scrollY - top) > 0.5) window.scrollTo({top, behavior:"instant"});
}

function syncCards(library, works) {
    const existing = new Map(Array.from(library.children, card => [card.dataset.workId, card]));
    const desired = works.map(work => {
        const card = existing.get(work.id);
        const data = cardData.get(card);
        if (data?.work === work && data.selecting === selecting) {
            const checkbox = card.querySelector(".pick-work input");
            if (checkbox) checkbox.checked = selected.has(work.id);
            return card;
        }
        return workCard(work);
    });
    const keep = new Set(desired);
    for (const card of existing.values()) if (!keep.has(card)) card.remove();
    for (const [index, card] of desired.entries()) {
        const current = library.children[index];
        if (current !== card) library.insertBefore(card, current || null);
    }
    return desired;
}

function renderLibrary({keepPosition = true, editedId} = {}) {
    if (!state.ready) return;
    const library = $("library");
    const position = keepPosition ? captureLibraryPosition(library) : null;
    const counts = {};
    for (const work of state.works) counts[work.status] = (counts[work.status] || 0) + 1;
    counts.inbox = state.works.filter(work => !work.classified).length;
    for (const [key, { button, amount }] of sectionButtons) {
        button.setAttribute("aria-current", key === state.status ? "true" : "false");
        amount.textContent = key ? counts[key] || 0 : state.works.length;
    }
    $("page-title").textContent = state.status === "inbox" ? "Sin clasificar" : (statuses[state.status]?.label || "Mi biblioteca");
    const query = normalizeSearch($("search").value.trim());
    const folder = folderFilter.value ? JSON.parse(folderFilter.value) : null;
    const visible = state.works.filter((work) =>
        (!state.status || work.status === state.status || (state.status === "inbox" && !work.classified)) &&
        (!$("class-filter").value || work.classified === ($("class-filter").value === "yes")) &&
        (!$("multi-filter").value || work.links.filter(link => link.role === "reading").length > 1) &&
        (!$("health-filter").value || work.links.some(link => link.health === $("health-filter").value)) &&
        (!typeFilter.value || work.type === typeFilter.value) &&
        (!tagFilter.value || work.tags.includes(tagFilter.value)) &&
        (!folder || work.bookmark_paths.some(path => folder.length ? folder.every((part, i) => part === path[i]) : !path.length)) &&
        (!query || [work.title, ...work.aliases].some(title => normalizeSearch(title).includes(query)))
    );
    const sort = $("sort").value;
    visible.sort((a, b) => {
        const order = sort === "title" ? 0 : sort === "rating" ? (b.rating ?? -1) - (a.rating ?? -1)
            : b[sort].localeCompare(a[sort]);
        return order || collator.compare(a.title, b.title);
    });
    filteredWorks = visible;
    updateSelectionBar();
    const count = visible.length;
    $("library-count").textContent = state.failed && !state.works.length ? "Biblioteca no disponible" :
        (count === state.works.length ? `${count} ${count === 1 ? "obra" : "obras"}` : `${count} de ${state.works.length} obras`);
    library.classList.toggle("list", state.view === "list");
    const cards = syncCards(library, visible.slice(0, state.limit));
    $("load-more").hidden = count <= state.limit;
    library.setAttribute("aria-busy", "false");
    $("empty").hidden = count > 0;
    restoreLibraryPosition(position, cards, editedId);
    if (count) return;
    if (state.failed && !state.works.length) {
        setEmpty("No se pudo abrir la biblioteca", "Comprueba que JMangaShelf esté abierto y vuelve a cargar la lista.", "Reintentar", refresh);
    } else if (!state.works.length) {
        setEmpty("Tu biblioteca está lista para empezar", "Importa tus marcadores del navegador o añade una obra para empezar a organizar tus lecturas.", "Importar marcadores", openImport);
    } else {
        setEmpty("No hay obras que coincidan", "Prueba otro título o cambia los filtros.", "Limpiar filtros", () => {
            state.status = "";
            $("class-filter").value = $("multi-filter").value = $("health-filter").value = "";
            $("search").value = typeFilter.value = tagFilter.value = folderFilter.value = "";
            resetResults();
            $("search").focus();
        });
    }
}

function resetResults() {
    state.limit = pageSize;
    renderLibrary({keepPosition:false});
}

function setEmpty(title, description, label, callback) {
    $("empty-title").textContent = title;
    $("empty-copy").textContent = description;
    $("empty-action").textContent = label;
    emptyAction = callback;
}

function workCard(work) {
    const status = statuses[work.status];
    const card = node("article", "work-card");
    cardData.set(card, {work, selecting});
    card.dataset.workId = work.id;
    card.style.setProperty("--section-color", status.color);
    const cover = node("div", "work-cover");
    const initials = work.title.match(/[\p{L}\p{N}]+/gu)?.slice(0, 2).map((word) => Array.from(word)[0]).join("");
    const placeholder = node("span", "cover-initial", initials?.toLocaleUpperCase("es") || "◇");
    placeholder.setAttribute("aria-hidden", "true");
    cover.append(placeholder);
    if (work.cover?.kind === "local") addCoverImage(cover, work.cover.url);
    if (work.cover?.kind === "url") {
        const url = work.cover.url;
        const loadButton = action("Mostrar portada", () => {
            state.remote.set(work.id, url);
            loadButton.hidden = true;
            showRemote();
        }, "button secondary show-cover");
        const showRemote = () => addCoverImage(cover, url, () => {
            state.remote.delete(work.id);
            loadButton.hidden = false;
            loadButton.textContent = "Reintentar portada";
            feedback("No se pudo cargar la portada remota.", true);
        });
        loadButton.hidden = state.remote.get(work.id) === url;
        cover.append(loadButton);
        if (loadButton.hidden) showRemote();
    }
    const info = node("div", "work-info");
    if (selecting) {
        const label = node("label", "pick-work");
        const check = node("input");
        check.type = "checkbox";
        check.checked = selected.has(work.id);
        check.onchange = () => {
            if (check.checked) selected.add(work.id); else selected.delete(work.id);
            updateSelectionBar();
        };
        label.append(check, "Seleccionar");
        info.append(label);
    }
    const heading = node("h2", "work-title");
    const title = action(work.title, () => openWork(work), "");
    title.title = work.title;
    heading.append(title);
    const meta = node("div", "work-meta");
    meta.append(node("span", "work-status", status.label), node("span", "", types[work.type]));
    const details = node("div", "work-meta work-rating");
    details.append(node("span", "", ratingText(work.rating)));
    if (!work.classified) details.append(node("span", "", "Sin clasificar"));
    const tags = node("div", "work-tags");
    for (const tag of work.tags) {
        const badge = node("span", "", tag);
        badge.title = tag;
        tags.append(badge);
    }
    info.append(heading, meta, details, tags);
    const actions = node("div", "work-actions");
    const preferred = work.links.find(link => link.role === "reading");
    if (preferred) {
        const link = sourceAnchor(preferred, work.status === "reading" ? "Continuar lectura" : "Abrir lectura", "button secondary");
        link.title = "Fuente preferida · " + (preferred.label || preferred.website);
        actions.append(link);
    }
    const chapter = action(work.chapter ? "Cap. " + work.chapter : "Anotar capítulo", () => openChapter(work));
    chapter.classList.add("chapter-action");
    chapter.title = work.chapter ? "Capítulo actual · " + work.chapter : "Actualizar capítulo";
    chapter.setAttribute("aria-label", "Actualizar capítulo" + (work.chapter ? " · Actual " + work.chapter : ""));
    actions.append(chapter);
    const sources = action("Fuentes (" + work.links.length + ")", () => openSources(work));
    sources.disabled = !work.links.length;
    sources.classList.add("sources-action");
    actions.append(sources);
    info.append(actions);
    card.append(cover, info);
    return card;
}

const ratingText = rating => rating === null ? "Sin puntuar" : rating + "/10";

function openSources(work) {
    const details = $("sources-body");
    const preferred = work.links.find(link => link.role === "reading");
    details.replaceChildren(node("p", "", work.title));
    for (const [role, label] of roles) {
        const sources = work.links.filter(item => item.role === role);
        if (!sources.length) continue;
        const list = node("ul");
        details.append(node("strong", "", label), list);
        for (const item of sources) {
            const entry = node("li");
            const linkName = item.label ? item.label + " · " + item.website : item.website;
            entry.append(sourceAnchor(item, linkName));
            const context = [item === preferred ? "Preferida" : "", healthLabels[item.health], referenceLabels[item.reference_hint]];
            entry.append(node("small", "source-context", context.filter(Boolean).join(" · ")));
            list.append(entry);
        }
    }
    $("sources-dialog").showModal();
}

$("close-sources").onclick = () => $("sources-dialog").close();

function sourceAnchor(item, name, className) {
    const link = node("a", className, name);
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
}

function updateCard(work) {
    const index = state.works.findIndex(item => item.id === work.id);
    if (index < 0) state.works.push(work);
    else state.works[index] = work;
    populateFilters();
    renderLibrary({editedId:work.id});
}

function closeAtCurrentPosition(dialog) {
    const top = window.scrollY;
    dialog.close();
    if (window.scrollY !== top) window.scrollTo({top, behavior:"instant"});
}

function focusCardControl(id, selector) {
    const control = $("library").querySelector('[data-work-id="' + id + '"] ' + selector);
    if (!control) return;
    const bounds = control.getBoundingClientRect();
    if (bounds.bottom > 0 && bounds.top < window.innerHeight) control.focus({preventScroll:true});
}

const chapterForm = $("chapter-form");
let chapterWork;
function openChapter(work) {
    chapterWork = work;
    formError(chapterForm);
    $("chapter-title").textContent = work.title;
    const value = field(chapterForm, "chapter");
    value.value = work.chapter;
    $("chapter-dialog").showModal();
    value.select();
}

bindForm(chapterForm, async () => {
    const {id, updated_at} = chapterWork;
    const work = await api("/api/works/" + id + "/chapter", {method: "PATCH",
        data: {updated_at, chapter: field(chapterForm, "chapter").value}});
    closeAtCurrentPosition($("chapter-dialog"));
    updateCard(work);
    feedback("Capítulo guardado.");
    focusCardControl(id, ".chapter-action");
});

function addCoverImage(container, source, onError) {
    const img = node("img");
    img.alt = "";
    img.loading = onError ? "eager" : "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => {
        img.remove();
        onError?.();
    }, { once: true });
    img.src = source;
    container.append(img);
}

function addLink(link = { label: "", url: "", paths: [] }) {
    if ($("link-fields").children.length >= 100) {
        formError(workForm, "Puedes guardar hasta 100 enlaces por obra.");
        return;
    }
    const row = node("div", "link-fields");
    row.dataset.previousUrl = link.url;
    const nameLabel = node("label", "", "Nombre opcional");
    const name = node("input");
    name.maxLength = 100;
    name.value = link.label;
    nameLabel.append(name);
    const urlLabel = node("label", "", "URL de lectura");
    const url = node("input");
    url.type = "url";
    url.maxLength = 4096;
    url.required = true;
    url.value = link.url;
    url.placeholder = "https://…";
    urlLabel.append(url);
    const remove = action("Quitar", () => row.remove(), "button secondary");
    remove.setAttribute("aria-label", "Quitar enlace");
    row.append(nameLabel, urlLabel, remove);
    addSourceControls(row, link);
    if (link.url) {
        const open = node("a", "", "Abrir enlace");
        open.href = link.url;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        const details = node("small");
        details.append(open, " · " + link.website + " · " + originLabel(link.origin));
        if (link.reference_hint) details.append(node("p", "muted", referenceLabels[link.reference_hint] + ". Puedes ajustar su función arriba."));
        for (const title of link.original_titles) details.append(node("p", "", "Marcador original · " + title));
        if (link.original_url !== link.url) details.append(node("p", "", "URL original · " + link.original_url));
        if (link.paths.length) details.append(` · ${link.paths.map(displayPath).join(" · ")}`);
        row.append(details);
    }
    $("link-fields").append(row);
    return url;
}

function renderTags() {
    $("edit-tags").replaceChildren(...editTags.map((tag) => {
        const remove = action(`${tag} ×`, () => {
            editTags = editTags.filter((item) => item !== tag);
            renderTags();
            field(workForm, "tag").focus();
        }, "");
        remove.setAttribute("aria-label", `Quitar etiqueta ${tag}`);
        return remove;
    }));
}

function addTag() {
    const input = field(workForm, "tag");
    const tag = input.value.trim();
    if (!tag) return;
    if (editTags.some((value) => value.toLocaleLowerCase("es") === tag.toLocaleLowerCase("es"))) {
        throw new Error("Esa etiqueta ya existe en la obra.");
    }
    if (editTags.length >= 40) throw new Error("Puedes guardar hasta 40 etiquetas por obra.");
    editTags.push(tag);
    input.value = "";
    renderTags();
}

function setCoverFields() {
    const kind = field(workForm, "cover_kind").value;
    $("cover-file-label").hidden = kind !== "local";
    $("cover-url-label").hidden = kind !== "url";
    field(workForm, "cover_file").required = kind === "local";
    field(workForm, "cover_url").required = kind === "url";
    field(workForm, "cover_file").disabled = kind !== "local";
    field(workForm, "cover_url").disabled = kind !== "url";
    $("staged-cover").hidden = kind !== "anilist";
}

function openWork(work = null) {
    editing = work;
    workForm.reset();
    clearStagedCover();
    formError(workForm);
    $("work-heading").textContent = work ? "Editar obra" : "Nueva obra";
    for (const name of ["title", "type", "status", "chapter", "notes"]) {
        if (work) field(workForm, name).value = work[name];
    }
    field(workForm, "classified").value = work?.classified === false ? "no" : "yes";
    field(workForm, "rating").value = work?.rating ?? "";
    $("work-mal-ids").hidden = !work?.mal_ids?.length;
    $("work-mal-ids").textContent = work?.mal_ids?.length ? "ID de MyAnimeList · " + work.mal_ids.join(", ") : "";
    field(workForm, "aliases").value = (work?.aliases || []).join("\n");
    $("alternate-covers").replaceChildren(...(work?.alternate_covers || []).map((cover, i) => {
        const link = node("a", "", "Ver portada alternativa " + (i + 1));
        link.href = cover.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        return link;
    }));
    editTags = work ? [...work.tags] : [];
    renderTags();
    const folders = $("work-folders");
    folders.hidden = !work?.bookmark_paths.length;
    folders.querySelector("ul").replaceChildren(...(work?.bookmark_paths || []).map((path) => node("li", "", displayPath(path))));
    $("link-fields").replaceChildren();
    work?.links.forEach(addLink);
    const keep = field(workForm, "cover_kind").querySelector('[value="keep"]');
    keep.hidden = !work?.cover;
    keep.disabled = !work?.cover;
    field(workForm, "cover_kind").value = work?.cover ? "keep" : "none";
    if (work?.cover?.kind === "url") field(workForm, "cover_url").value = work.cover.url;
    setCoverFields();
    $("delete-work").hidden = !work;
    $("work-dialog").showModal();
    field(workForm, "title").focus();
}

function imageData(file) {
    if (!file || file.size > 5 * 1024 * 1024) throw new Error("Selecciona una imagen de hasta 5 MB.");
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener("load", () => resolve(reader.result.split(",", 2)[1]));
        reader.addEventListener("error", () => reject(new Error("No se pudo leer la imagen.")));
        reader.readAsDataURL(file);
    });
}

async function storeWork() {
    const data = {};
    for (const name of ["title", "type", "status", "chapter", "notes"]) data[name] = field(workForm, name).value;
    addTag();
    data.tags = editTags;
    data.aliases = field(workForm, "aliases").value.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
    data.classified = field(workForm, "classified").value === "yes";
    const rating = field(workForm, "rating").value;
    if (rating && !/^(?:[0-9]|10)$/.test(rating)) throw new Error("Selecciona una puntuación entre 0 y 10.");
    data.rating = rating === "" ? null : Number(rating);
    data.links = Array.from($("link-fields").children, (row) => {
        const [label, url] = row.querySelectorAll("input");
        return { label: label.value, url: url.value, previous_url: row.dataset.previousUrl || undefined, language: row.querySelector(".language").value,
            origin: row.querySelector(".origin").value, role: row.querySelector(".role").value, health: row.querySelector(".health").value,
            preferred: row.querySelector("[type=radio]").checked };

    });
    data.links.sort((a,b) => Number(b.preferred) - Number(a.preferred));
    const cover = field(workForm, "cover_kind").value;
    if (cover === "none") data.cover = null;
    if (cover === "url") data.cover = { kind: "url", url: field(workForm, "cover_url").value };
    if (cover === "local") data.cover = { kind: "local", data: await imageData(field(workForm, "cover_file").files[0]) };
    if (cover === "anilist") data.cover = { kind: "anilist", token: stagedCover.token, confirm: true };
    if (editing) data.updated_at = editing.updated_at;
    const path = editing ? `/api/works/${editing.id}` : "/api/works";
    const saved = await api(path, { method: editing ? "PUT" : "POST", data });
    closeAtCurrentPosition($("work-dialog"));
    feedback(editing ? "Cambios guardados." : "Obra añadida a la biblioteca.");
    updateCard(saved);
    if (editing) focusCardControl(saved.id, ".work-title button");
}

function openImport() {
    importForm.reset();
    clearPreview();
    $("import-dialog").showModal();
}

function clearPreview() {
    preview = treeRoot = null;
    $("import-preview").hidden = true;
    $("commit-import").disabled = true;
    $("bookmark-tree").replaceChildren();
    formError(importForm);
}

function makeTreeNode() {
    return { folders: new Map(), rows: [], checkbox: null };
}

function selectedBookmarks() {
    return preview.rows.filter((row) => row.checkbox.checked);
}

function markFolder(folder, checked) {
    for (const row of folder.rows) row.checkbox.checked = checked;
    for (const nested of folder.folders.values()) markFolder(nested, checked);
}

function syncFolders(folder) {
    let total = folder.rows.length;
    let checked = folder.rows.filter((row) => row.checkbox.checked).length;
    for (const nested of folder.folders.values()) {
        const count = syncFolders(nested);
        total += count.total;
        checked += count.checked;
    }
    if (folder.checkbox) {
        folder.checkbox.checked = checked === total;
        folder.checkbox.indeterminate = checked > 0 && checked < total;
    }
    return { total, checked };
}

function updateSelection() {
    syncFolders(treeRoot);
    const selected = selectedBookmarks();
    const newUrls = new Set(selected.filter((row) => !row.existing).map((row) => row.url)).size;
    const existing = new Set(selected.filter((row) => row.existing).map((row) => row.url)).size;
    $("preview-count").textContent = `${selected.length} de ${preview.rows.length} marcadores seleccionados · ${newUrls} URLs nuevas · ${existing} ya guardadas`;
    $("commit-import").disabled = selected.length === 0 || Boolean(importForm.dataset.busy);
}

function renderTree(folder, root = false) {
    const content = node("div");
    for (const [title, nested] of folder.folders) {
        const details = node("details");
        details.open = root && preview.rows.length <= 80;
        const summary = node("summary");
        const checkbox = node("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        checkbox.setAttribute("aria-label", `Seleccionar carpeta ${title}`);
        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", () => {
            markFolder(nested, checkbox.checked);
            updateSelection();
        });
        nested.checkbox = checkbox;
        summary.append(checkbox, title);
        details.append(summary, renderTree(nested));
        content.append(details);
    }
    for (const row of folder.rows) {
        const label = node("label");
        const checkbox = node("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        row.checkbox = checkbox;
        checkbox.addEventListener("change", updateSelection);
        const text = node("span", "", row.title);
        text.append(node("span", "bookmark-url", row.url));
        const hints = [];
        if (row.existing) hints.push("URL ya guardada");
        if (row.repeated) hints.push("URL repetida en el archivo");
        if (row.similar) hints.push("Posible duplicado por título");
        if (hints.length) text.append(node("small", "duplicate-hint", hints.join(" · ")));
        label.append(checkbox, text);
        content.append(label);
    }
    return content;
}

async function loadPreview() {
    const file = field(importForm, "file").files[0];
    clearPreview();
    if (!file) throw new Error("Selecciona el archivo HTML del navegador.");
    if (file.size > 16 * 1024 * 1024) throw new Error("El archivo HTML supera los 16 MB.");
    const formData = new FormData();
    formData.append("file", file);
    const result = await api("/api/bookmarks/preview", { method: "POST", data: formData });
    if (field(importForm, "file").files[0] !== file) return;
    preview = result;
    treeRoot = makeTreeNode();
    for (const row of preview.rows) {
        let position = treeRoot;
        for (const part of row.path) {
            if (!position.folders.has(part)) position.folders.set(part, makeTreeNode());
            position = position.folders.get(part);
        }
        position.rows.push(row);
    }
    const container = $("bookmark-tree");
    container.replaceChildren(preview.rows.length ? renderTree(treeRoot, true) : node("p", "muted", "No se encontraron marcadores HTTP o HTTPS."));
    const ignored = preview.ignored === 1 ? "1 enlace omitido" : `${preview.ignored} enlaces omitidos`;
    $("preview-help").textContent = `${ignored} por URL no válida. Los títulos parecidos se guardan como obras separadas. Las URLs repetidas conservan las carpetas seleccionadas.`;
    $("import-preview").hidden = false;
    updateSelection();
}

async function commitImport() {
    if (!preview) return;
    const rows = selectedBookmarks().map(({ title, url, path, original_url, original_title }) => ({ title, url, path, original_url, original_title }));
    if (!rows.length) return;
    const data = { rows, type: field(importForm, "type").value, status: field(importForm, "status").value };
    const result = await api("/api/bookmarks/import", { method: "POST", data });
    $("import-dialog").close();
    feedback(`Importación completada. Obras nuevas: ${result.created}. URLs con carpetas añadidas: ${result.enriched}. URLs ya registradas: ${result.existing}.`);
    await refresh();
}

function setBackupMode() {
    const replace = field(backupForm, "mode").value === "replace";
    $("replace-confirm").hidden = !replace;
    field(backupForm, "confirm").required = replace;
    $("backup-mode-help").textContent = replace
        ? "La copia sustituirá todas las obras actuales. Puedes descargar una copia de la biblioteca actual antes de continuar."
        : "Si coincide un ID de ficha, un ID de MyAnimeList o un enlace, se conserva la obra actual.";
}

async function restoreBackup() {
    const mode = field(backupForm, "mode").value;
    const confirm = field(backupForm, "confirm").value.trim();
    if (mode === "replace" && confirm !== "REEMPLAZAR") throw new Error("Escribe REEMPLAZAR para confirmar.");
    const file = field(backupForm, "file").files[0];
    if (file.size >= 128 * 1024 * 1024) throw new Error("La copia supera el límite de 128 MB.");
    const data = new FormData();
    data.append("mode", mode);
    data.append("confirm", confirm);
    data.append("file", file);
    const result = await api("/api/backup/restore", { method: "POST", data });
    $("backup-dialog").close();
    feedback(`Restauración completada. Obras añadidas: ${result.added}. Obras existentes conservadas: ${result.skipped}.`);
    await refresh();
}

function bindForm(form, operation) {
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        runForm(form, operation);
    });
    const dialog = form.closest("dialog");
    dialog.setAttribute("aria-labelledby", dialog.querySelector("h2").id || `${dialog.id}-heading`);
    dialog.querySelector("h2").id = dialog.getAttribute("aria-labelledby");
    form.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => closeAtCurrentPosition(dialog)));
    dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        if (!form.dataset.busy) closeAtCurrentPosition(dialog);
    });
}

for (const form of [workForm, importForm]) {
    fillSelect(field(form, "type"), Object.entries(types), form === workForm ? "manga" : "other");
    fillSelect(field(form, "status"), Object.entries(statuses).map(([key, value]) => [key, value.label]), "planned");
    // Make form.reset() return to the same choices shown on first open.
    for (const name of ["type", "status"]) {
        const select = field(form, name);
        select.options[select.selectedIndex].defaultSelected = true;
    }
}
fillSelect(typeFilter, [["", "Todos los tipos"], ...Object.entries(types)]);
fillSelect($("work-rating"), [["", "Sin puntuar"], ...Array.from({length:11}, (_, i) => [String(i), ratingText(i)])]);
populateFilters();
setupSections();

$("search").addEventListener("input", resetResults);
for (const control of [typeFilter, tagFilter, folderFilter, $("sort"), $("multi-filter"), $("class-filter"), $("health-filter")]) control.addEventListener("change", resetResults);
function loadNextBatch(manual) {
    const first = $("library").childElementCount;
    if (first >= filteredWorks.length) return;
    const end = Math.min(first + pageSize, filteredWorks.length);
    const cards = filteredWorks.slice(first, end).map(workCard);
    $("library").append(...cards);
    state.limit = end;
    $("load-more").hidden = end === filteredWorks.length;
    if (manual) cards[0].querySelector(".work-title button").focus();
}

$("more-works").addEventListener("click", () => loadNextBatch(true));
if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(([entry]) => {
        const sentinel = entry.target;
        if (entry.isIntersecting && !sentinel.hidden &&
            sentinel.getBoundingClientRect().top <= window.innerHeight + 400) {
            observer.unobserve(sentinel);
            loadNextBatch(false);
            observer.observe(sentinel);
        }
    }, {rootMargin: "0px 0px 400px"});
    observer.observe($("load-more"));
}

$("shutdown")?.addEventListener("click", async (event) => {
    if (!window.confirm("¿Cerrar JMangaShelf en todas las pestañas?")) return;
    try {
        await api(event.currentTarget.dataset.endpoint || "/api/portable/shutdown", {method: "POST"});
        const message = node("main", "workspace");
        message.append(node("h1", "", "JMangaShelf se ha cerrado"), node("p", "", "Puedes cerrar esta pestaña."));
        document.body.replaceChildren(message);
    } catch (error) {
        feedback(error.message, true);
    }
});
for (const view of ["grid", "list"]) {
    $(`${view}-view`).addEventListener("click", () => {
        state.view = view;
        $("grid-view").setAttribute("aria-pressed", String(view === "grid"));
        $("list-view").setAttribute("aria-pressed", String(view === "list"));
        renderLibrary();
    });
}
$("new-work").addEventListener("click", () => openWork());
$("top-import").addEventListener("click", openImport);
$("empty-action").addEventListener("click", () => emptyAction());
$("add-link").addEventListener("click", () => addLink()?.focus());
$("add-tag").addEventListener("click", () => {
    try {
        addTag();
        formError(workForm);
        field(workForm, "tag").focus();
    } catch (error) {
        formError(workForm, error.message);
    }
});
field(workForm, "tag").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        $("add-tag").click();
    }
});
field(workForm, "cover_kind").addEventListener("change", setCoverFields);
$("preview-import").addEventListener("click", () => runForm(importForm, loadPreview));
field(importForm, "file").addEventListener("change", clearPreview);
for (const [id, checked] of [["select-all", true], ["select-none", false]]) {
    $(id).addEventListener("click", () => {
        markFolder(treeRoot, checked);
        updateSelection();
    });
}
$("backup-button").addEventListener("click", () => {
    backupForm.reset();
    setBackupMode();
    formError(backupForm);
    $("backup-dialog").showModal();
});
field(backupForm, "mode").addEventListener("change", setBackupMode);
$("delete-work").addEventListener("click", () => {
    $("delete-message").textContent = `¿Eliminar «${editing.title}» y todos sus enlaces de la biblioteca?`;
    formError($("delete-form"));
    $("delete-dialog").showModal();
    $("delete-form").querySelector("[data-close].button").focus();
});

bindForm(workForm, storeWork);
bindForm(importForm, commitImport);
bindForm(backupForm, restoreBackup);
bindForm($("delete-form"), async () => {
    const {id} = editing;
    await api(`/api/works/${id}`, { method: "DELETE" });
    closeAtCurrentPosition($("delete-dialog"));
    closeAtCurrentPosition($("work-dialog"));
    feedback("Obra eliminada.");
    state.works = state.works.filter(work => work.id !== id);
    selected.delete(id);
    state.remote.delete(id);
    populateFilters();
    renderLibrary({editedId:id});
});
