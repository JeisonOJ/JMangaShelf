"use strict";

const malForm = $("mal-form");
const malPageSize = 30;
const malCategories = {strong:"Coincidencia por ID", ambiguous:"Requiere revisión", new:"Nueva", skipped:"Omitida"};
let malReview = null;
let malPage = 0;
const malFilters = {
    all: ["Todas", () => true],
    review: ["Para revisar", row => row.category === "ambiguous"],
    new: ["Nuevas", row => row.category === "new"],
    linked: ["Vinculadas", (_, choice) => choice?.action === "link" && Boolean(choice.target)],
    skipped: ["Omitidas", (row, choice) => row.category === "skipped" || choice?.action === "skip"],
};

function malMatches(row, filter, query = "") {
    const choice = malReview.choices.get(row.row);
    if (!malFilters[filter][1](row, choice)) return false;
    return !query || normalizeSearch(row.title).includes(query) ||
        [...row.matches, choice?.target].some(id => normalizeSearch(malReview.works.get(id)?.title || "").includes(query));
}

function clearMalReview() {
    malReview = null;
    malPage = 0;
    $("mal-navigation").hidden = true;
    $("mal-filter").replaceChildren();
    $("mal-search").value = "";
    $("mal-rows").replaceChildren();
    $("mal-summary").textContent = "";
    $("mal-consent").hidden = true;
    field(malForm, "confirm").disabled = true;
    field(malForm, "confirm").checked = false;
    $("mal-apply").disabled = true;
    formError(malForm);
}

function malSummary() {
    const counts = Object.fromEntries(Object.keys(malCategories).map(key => [key, 0]));
    for (const row of malReview.rows) counts[row.category]++;
    fillSelect($("mal-filter"), Object.entries(malFilters).map(([key, [label]]) =>
        [key, label + " (" + malReview.rows.filter(row => malMatches(row, key)).length + ")"]), "all");
    const choices = [...malReview.choices.values()];
    const links = choices.filter(choice => choice.action === "link");
    const creates = choices.filter(choice => choice.action === "create").length;
    const conflictingTargets = new Set(links.map(choice => choice.target).filter(Boolean)).size < links.filter(choice => choice.target).length;
    $("mal-summary").textContent = [counts.strong + " por ID", counts.ambiguous + " para revisar",
        counts.new + " nuevas", counts.skipped + " no importables"].join(" · ") + ". "
        + "Selección actual · " + links.length + " para vincular, " + creates + " para crear."
        + (conflictingTargets ? " Elige una ficha distinta para cada entrada." : "");
    const ready = (links.length || creates) && !conflictingTargets && links.every(choice => choice.target);
    $("mal-consent").hidden = !ready;
    field(malForm, "confirm").disabled = !ready;
    $("mal-apply").disabled = !ready || !field(malForm, "confirm").checked;
}

function malChoicesChanged(row) {
    field(malForm, "confirm").checked = false;
    if (row && !malMatches(row, $("mal-filter").value, normalizeSearch($("mal-search").value.trim()))) {
        renderMalReview();
        $("mal-filter").focus();
        return;
    }
    malSummary();
}

function renderMalRow(row) {
    const box = node("fieldset");
    box.append(node("legend", "", row.title || "Entrada " + row.row),
        node("p", "muted", malCategories[row.category] + " · " + row.reason));
    if (row.error) return box;
    const status = value => statuses[value]?.label || "Sin dato";
    const chapter = value => value === null ? "Sin dato" : value || "Sin progreso";
    box.append(node("p", "", ["MAL " + row.id, ratingText(row.rating), status(row.status), "Cap. " + chapter(row.chapter),
        row.started && "Inicio " + row.started, row.finished && "Fin " + row.finished].filter(Boolean).join(" · ")));
    const choice = malReview.choices.get(row.row);
    const actions = [["skip","Omitir"],["link","Vincular a una ficha"]];
    if (row.allow_create) actions.push(["create","Crear una obra"]);
    const choiceField = selectControl(actions, choice.action);
    const matchFields = node("div");
    box.append(labelled("Acción para esta entrada", choiceField), matchFields);
    function renderLink() {
        matchFields.replaceChildren();
        if (choice.action !== "link") return;
        if (!row.target) {
            const candidates = [...malReview.works.values()]
                .filter(work => !work.mal_ids.length || work.mal_ids.includes(row.id))
                .filter(work => row.allow_create || row.matches.includes(work.id))
                .sort((a,b) => Number(row.matches.includes(b.id)) - Number(row.matches.includes(a.id))
                    || collator.compare(a.title,b.title));
            const target = selectControl([["","Elige una obra"], ...candidates.map(work =>
                [work.id, work.title + " · " + types[work.type] + (row.matches.includes(work.id) ? " · Coincidencia" : "")])], choice.target);
            target.onchange = () => {
                choice.target = target.value;
                Object.assign(choice, defaultMalValues(row, choice.target));
                renderLink();
                matchFields.querySelector("select")?.focus();
                malChoicesChanged(row);
            };
            matchFields.append(labelled("Ficha de destino", target));
        }
        const work = malReview.works.get(choice.target);
        if (!work) return;
        if (row.target) matchFields.append(node("p", "", "Ficha local · " + work.title + " · " + types[work.type]));
        const fields = node("div","mal-values");
        for (const [key,label,format] of [["rating","Puntuación",ratingText],["status","Estado de lectura",status],["chapter","Capítulo",chapter]]) {
            if (row[key] === null || work[key] === row[key]) {
                fields.append(node("p", "muted", label + " local · " + format(work[key])));
                continue;
            }
            const select = selectControl([["local","Local · " + format(work[key])], ["mal","MyAnimeList · " + format(row[key])]],
                choice[key] ? "mal" : "local");
            select.onchange = () => { choice[key] = select.value === "mal"; malChoicesChanged(); };
            fields.append(labelled(label,select));
        }
        matchFields.append(fields);
    }
    choiceField.onchange = () => {
        choice.action = choiceField.value;
        renderLink();
        malChoicesChanged(row);
    };
    renderLink();
    return box;
}

function defaultMalValues(row, target) {
    return {rating: Boolean(target && malReview.works.get(target)?.rating === null && row.rating !== null),
        status:false, chapter:false};
}

function renderMalReview() {
    const rows = $("mal-rows");
    rows.replaceChildren();
    malSummary();
    $("mal-navigation").hidden = false;
    const query = normalizeSearch($("mal-search").value.trim());
    const visible = malReview.rows.filter(row => malMatches(row, $("mal-filter").value, query));
    $("mal-visible").textContent = visible.length + " de " + malReview.rows.length + " entradas";
    if (!visible.length) {
        rows.append(node("p", "muted", "No hay entradas que coincidan con el filtro y la búsqueda."));
        return;
    }
    const pages = Math.ceil(visible.length / malPageSize);
    malPage = Math.min(malPage, pages - 1);
    const start = malPage * malPageSize;
    for (const row of visible.slice(start, start + malPageSize)) rows.append(renderMalRow(row));
    if (pages > 1) {
        const navigation = node("div", "org-actions");
        const previous = action("Página anterior", () => { malPage--; renderMalReview(); $("mal-rows").scrollIntoView(); });
        const next = action("Página siguiente", () => { malPage++; renderMalReview(); $("mal-rows").scrollIntoView(); });
        previous.disabled = !malPage;
        next.disabled = malPage + 1 >= pages;
        navigation.append(previous, node("span","muted","Página " + (malPage + 1) + " de " + pages), next);
        rows.append(navigation);
    }
}

function updateMalNavigation() {
    malPage = 0;
    renderMalReview();
    $("mal-navigation").scrollIntoView({block:"nearest"});
}

$("mal-filter").onchange = updateMalNavigation;
$("mal-search").oninput = updateMalNavigation;
$("mal-import").onclick = () => {
    malForm.reset();
    clearMalReview();
    $("mal-dialog").showModal();
};
field(malForm, "file").onchange = clearMalReview;
field(malForm, "confirm").onchange = malSummary;
$("mal-preview").onclick = async () => {
    $("mal-apply").disabled = true;
    await runForm(malForm, async () => {
        clearMalReview();
        const file = field(malForm, "file").files[0];
        if (!file) throw new Error("Selecciona un archivo de MyAnimeList.");
        const data = new FormData();
        data.append("file", file);
        const result = await api("/api/mal/preview", {method:"POST", data});
        if (field(malForm, "file").files[0] !== file) return;
        malReview = {...result, works:new Map(result.works.map(work => [work.id,work])), choices:new Map(), file};
        for (const row of result.rows.filter(row => !row.error)) {
            malReview.choices.set(row.row, {action:row.target ? "link" : row.allow_create && row.category === "new" ? "create" : "skip",
                target:row.target || "", ...defaultMalValues(row,row.target)});
        }
        renderMalReview();
    });
};

bindForm(malForm, async () => {
    if (!malReview || !field(malForm, "confirm").checked) throw new Error("Revisa y confirma la importación.");
    const data = new FormData();
    data.append("file",malReview.file);
    data.append("version",malReview.version);
    data.append("confirm","1");
    data.append("choices",JSON.stringify(Object.fromEntries([...malReview.choices].filter(([,choice]) => choice.action !== "skip"))));
    const result = await api("/api/mal/import", {method:"POST", data});
    $("mal-dialog").close();
    clearMalReview();
    feedback(result.created + " obras creadas · " + result.linked + " fichas vinculadas o actualizadas · " + result.ignored + " sin cambios."
        + (result.backup ? " Copia local · " + result.backup : ""));
    await refresh();
});
