"use strict";

const orgForm = $("org-form");
const orgFields = $("org-fields");
const origins = ["Manual", "Navegador", "WhatsApp", "X", "Otro"];
const roles = [["reading", "Lectura"], ["reference", "Referencia"]];
const healthLabels = {unchecked:"Sin revisar", working:"Funciona", broken:"Roto", suspicious:"Sospechoso"};
const referenceLabels = {catalog:"Posible referencia de catálogo", search:"Posible resultado de búsqueda"};
let submitOrganization;

function labelled(text, control) {
    const label = node("label", "", text);
    label.append(control);
    return label;
}

function selectControl(entries, value) {
    const select = node("select");
    fillSelect(select, entries, value);
    return select;
}

function inputControl(type, value = "", max = 300) {
    const input = node("input");
    input.type = type;
    input.maxLength = max;
    input.value = value;
    return input;
}

function addSourceControls(row, link) {
    const controls = node("div", "source-options");
    const language = inputControl("text", link.language || "", 60);
    language.className = "language";
    const origin = selectControl((link.origin === "Edge" ? [...origins, "Edge"] : origins).map(value => [value, originLabel(value)]), link.origin || "Manual");
    origin.className = "origin";
    const role = selectControl(roles, link.role || "reading");
    role.className = "role";
    const health = selectControl(Object.entries(healthLabels), link.health || "unchecked");
    health.className = "health";
    const preferred = inputControl("radio");
    preferred.name = "preferred";
    preferred.checked = !$("link-fields").querySelector("input[type=radio]:checked") && role.value === "reading";
    const updateRole = () => {
        preferred.disabled = role.value === "reference";
        if (preferred.disabled) preferred.checked = false;
    };
    role.onchange = updateRole;
    updateRole();
    controls.append(labelled("Idioma", language), labelled("Origen", origin), labelled("Función", role),
                    labelled("Estado del enlace", health), labelled("Preferida", preferred));
    row.append(controls);
}

function showOrganization(title, button, operation) {
    $("org-heading").textContent = title;
    orgFields.replaceChildren();
    formError(orgForm);
    $("org-submit").textContent = button || "";
    $("org-submit").hidden = !button;
    $("org-submit").disabled = false;
    submitOrganization = operation;
    if (!$("org-dialog").open) $("org-dialog").showModal();
}

function versions(works) {
    return Object.fromEntries(works.map(work => [work.id, work.updated_at]));
}

async function applyOrganization(path, data, message) {
    await api(path, {method: "POST", data});
    closeAtCurrentPosition($("org-dialog"));
    feedback(message);
    await refresh();
}

function updateSelectionBar() {
    $("batch-bar").hidden = !selecting;
    $("select-mode").setAttribute("aria-pressed", String(selecting));
    $("select-mode").textContent = selecting ? "Terminar selección" : "Seleccionar obras";
    const hidden = selected.size - filteredWorks.filter(w => selected.has(w.id)).length;
    $("selected-count").textContent = selected.size + " seleccionadas" + (hidden ? " (" + hidden + " fuera del filtro)" : "");
    $("batch").disabled = !selected.size;
    $("delete-selected").disabled = !selected.size;
    $("merge").disabled = selected.size < 2;
}

function captureLink() {
    const url = inputControl("url", "", 4096);
    url.required = true;
    const title = inputControl("text");
    const origin = selectControl([["", "Detectar por URL"], ...origins.map(v => [v, v])], "");
    const role = selectControl([["", "Detectar por URL"], ...roles], "");
    const targets = [...state.works].sort((a,b) => collator.compare(a.title, b.title));
    const target = selectControl([["", "Nueva obra en Sin clasificar"], ...targets.map(w => [w.id, w.title + (w.links[0] ? " · " + w.links[0].website : "")])], "");
    showOrganization("Guardar enlace rápido", "Guardar enlace", () => applyOrganization("/api/capture", {
        url: url.value, title: title.value, origin: origin.value, role: role.value, work_id: target.value,
    }, "Enlace guardado."));
    orgFields.append(labelled("URL", url), labelled("Obra de destino", target), labelled("Título o nombre opcional del enlace", title),
                     labelled("Origen de captura", origin), labelled("Función del enlace", role),
                     node("p", "muted", "Los enlaces de X y WhatsApp se guardan como referencias al detectar por URL."));
    url.focus();
}

function batchDialog(works) {
    const operation = selectControl([["classified","Organización"],["status","Estado de lectura"],["type","Tipo de obra"],
                                    ["folder_type","Sugerir tipo por carpetas del navegador"],
                                    ["add_tag","Añadir etiqueta"],["remove_tag","Quitar etiqueta"]], "classified");
    const values = node("div");
    let control;
    const change = () => {
        if (operation.value === "folder_type") {
            control = null;
            values.replaceChildren(node("p","muted","Se buscan nombres completos de carpetas. Manhwa, Manhua y Novela tienen prioridad sobre Manga. Las coincidencias ambiguas se omiten."));
            return;
        }
        if (operation.value === "classified") control = selectControl([["yes","Clasificada"],["no","Sin clasificar"]], "yes");
        else if (operation.value === "status") control = selectControl(Object.entries(statuses).map(([key,s]) => [key,s.label]));
        else if (operation.value === "type") control = selectControl(Object.entries(types));
        else control = inputControl("text", "", 60);
        control.required = true;
        values.replaceChildren(labelled("Valor que se aplicará", control));
    };
    const count = works.length;
    showOrganization("Organizar " + count + " obras", "Ver cambios", () =>
        previewBatch({versions: versions(works), operation: operation.value,
            value: operation.value === "classified" ? control.value === "yes" : control?.value}, () => batchDialog(works)));
    orgFields.append(labelled("Cambio para la selección", operation), values);
    operation.onchange = change;
    change();
}

function describeFields(fields) {
    const labels = {type:"Tipo", status:"Estado", classified:"Organización", title:"Título", chapter:"Capítulo", tags:"Etiquetas"};
    return Object.entries(fields).map(([key,value]) => {
        if (key === "type") value = types[value];
        if (key === "status") value = statuses[value].label;
        if (key === "classified") value = value ? "Clasificada" : "Sin clasificar";
        if (key === "tags") value = value.join(", ") || "Sin etiquetas";
        return labels[key] + " · " + value;
    }).join(" / ");
}

async function previewBatch(data, back) {
    const result = await api("/api/batch/preview", {method:"POST", data});
    showOrganization("Vista previa del lote", result.count ? "Confirmar cambios en " + result.count + " obras" : "",
        () => applyOrganization("/api/batch", {...data, confirm:true}, "Obras actualizadas: " + result.count));
    orgFields.append(node("p","",result.count + " de " + Object.keys(data.versions).length + " obras cambiarán."),
        action("Volver a la selección del cambio", back));
    for (const change of result.changes) {
        orgFields.append(node("p","",change.title + " — " + describeFields(change.fields)));
    }
}

function reviewTitles() {
    const candidates = state.works.filter(w => w.suggestion.title !== w.title);
    const checks = [];
    showOrganization("Revisar títulos", candidates.length ? "Aplicar títulos seleccionados" : "", () => {
        const chosen = checks.filter(([check]) => check.checked).map(([, work]) => work);
        if (!chosen.length) throw new Error("Selecciona alguna propuesta.");
        return previewBatch({versions: versions(chosen), operation: "clean_title"}, reviewTitles);
    });
    if (!candidates.length) {
        orgFields.append(node("p", "", "No hay títulos con una propuesta de limpieza."));
        return;
    }
    const toggle = inputControl("checkbox");
    toggle.onchange = () => { for (const [check] of checks) check.checked = toggle.checked; };
    orgFields.append(labelled("Seleccionar todas las propuestas", toggle));
    for (const work of candidates) {
        const check = inputControl("checkbox");
        const box = node("fieldset");
        checks.push([check, work]);
        box.append(labelled(work.title, check), node("p", "", "Título propuesto · " + work.suggestion.title));
        if (work.suggestion.chapter) box.append(node("p", "muted", "Capítulo detectado · " + work.suggestion.chapter +
            (work.chapter ? " (se conserva el progreso actual)" : "")));
        orgFields.append(box);
    }
}

function workSummary(work) {
    const box = node("details", "review-work");
    box.append(node("summary","", work.title + " · " + (work.links[0]?.website || "Sin fuentes") +
        " · " + (work.chapter ? "Cap. " + work.chapter : "Sin progreso")));
    box.append(node("p","", describeFields({type: work.type, status: work.status, classified: work.classified})));
    if (work.aliases.length) box.append(node("p","","Títulos alternativos · " + work.aliases.join(" / ")));
    if (work.tags.length) box.append(node("p","","Etiquetas · " + work.tags.join(", ")));
    if (work.notes) box.append(node("p","review-notes",work.notes));
    if (work.bookmark_paths.length) box.append(node("p","muted",work.bookmark_paths.map(displayPath).join(" / ")));
    for (const [i,link] of work.links.entries()) {
        box.append(node("p","", [link.role === "reading" ? (i === 0 ? "Lectura preferida" : "Lectura") : "Referencia",
            link.url, referenceLabels[link.reference_hint], link.label, link.language, originLabel(link.origin), healthLabels[link.health], ...link.original_titles,
            ...link.paths.map(displayPath)].filter(Boolean).join(" · ")));
    }
    if (work.cover) {
        if (work.cover.kind === "url") box.append(node("p","","Portada · " + work.cover.url));
        else {
            const img = node("img","merge-cover");
            img.alt = "Portada principal"; img.src = work.cover.url;
            box.append(img);
        }
    }
    return box;
}

function evidenceText(e) {
    const labels = {url:"Variantes de la misma URL", title:"Coincidencia de título o alias",
        folder:"Pista del navegador · Misma carpeta específica", folder_title:"El nombre de la carpeta coincide con el título o alias"};
    return labels[e.kind] + (e.kind === "folder_title" ? "" : " · " + (e.path ? displayPath(e.path) : e.value));
}

async function reviewDecision(path, group, message) {
    await runForm(orgForm, async () => {
        await api("/api/review/" + path, {method:"POST",data:{signature:group.signature}});
        await reviewDuplicates();
        feedback(message);
    });
}

async function reviewDuplicates() {
    try {
        const {groups, dismissed} = await api("/api/review");
        const byId = new Map(state.works.map(w => [w.id,w]));
        showOrganization("Revisar posibles duplicados", "", null);
        if (!groups.length) orgFields.append(node("p","muted","No hay grupos pendientes. También puedes seleccionar obras en la biblioteca y compararlas."));
        for (const group of groups) {
            const works = group.ids.map(id => byId.get(id)).filter(Boolean);
            if (works.length !== group.ids.length) continue;
            const box = node("fieldset");
            box.append(node("legend","", group.title), node("p","muted",works.length + " obras"));
            for (const e of group.evidence.filter(e => e.kind !== "folder_title")) box.append(node("p","",evidenceText(e)));
            box.append(action("Ver grupo", () => inspectGroup(group, works)));
            orgFields.append(box);
        }
        if (dismissed.length) {
            const list = node("details","rejected-groups");
            list.append(node("summary","","Sugerencias descartadas"));
            list.append(node("p","muted","Al volver a considerar un grupo, también pueden reaparecer sugerencias con las mismas obras."));
            for (const group of dismissed) {
                const box = node("fieldset");
                box.append(node("legend","",group.title),
                    node("p","",group.ids.map(id => byId.get(id)?.title).filter(Boolean).join(" · ")),
                    action("Volver a considerar", () => reviewDecision("reconsider", group, "La sugerencia vuelve a estar pendiente.")));
                list.append(box);
            }
            orgFields.append(list);
        }
    } catch (error) { feedback(error.message, true); }
}

function inspectGroup(group, members) {
    const picked = new Map(members.map(w => [w.id,w]));
    const rows = node("div","review-entries");
    const extra = selectControl([]);
    const compare = action("Revisar y fusionar", () => mergeDialog([...picked.values()], () => inspectGroup(group, [...picked.values()])),
        "button primary");
    const add = action("Añadir otra obra", () => {
        const work = state.works.find(w => w.id === extra.value);
        if (work) picked.set(work.id, work);
        render();
    });
    function render() {
        rows.replaceChildren();
        for (const work of picked.values()) {
            const box = node("fieldset");
            box.append(workSummary(work));
            if (group.folder_additions.includes(work.id)) box.append(node("small","muted","Entrada adicional sugerida por la carpeta del navegador."));
            box.append(action("Quitar de esta comparación", () => {picked.delete(work.id); render();}));
            rows.append(box);
        }
        fillSelect(extra, state.works.filter(w => !picked.has(w.id)).sort((a,b) => collator.compare(a.title,b.title))
            .map(w => [w.id,w.title + " · " + (w.links[0]?.website || "Sin fuentes")]));
        add.disabled = !extra.options.length;
        compare.disabled = picked.size < 2;
    }
    showOrganization("Revisar grupo", "", null);
    orgFields.append(node("h3","", group.title),
        node("p","muted","Quitar una obra solo afecta a esta comparación. «No son la misma obra» descarta la sugerencia inicial. Todas las fichas siguen en tu biblioteca."));
    const evidence = node("details","review-evidence");
    evidence.open = true;
    evidence.append(node("summary","","Por qué se sugirió este grupo"));
    for (const e of group.evidence) {
        const detail = node("details");
        detail.append(node("summary","", evidenceText(e)));
        for (const [id, values] of Object.entries(e.matches)) {
            detail.append(node("p","", (state.works.find(w => w.id === id)?.title || "") + " · " + values.join(" / ")));
            for (const url of (e.uncertain?.[id] || [])) {
                detail.append(node("p","muted", "Formato por verificar · " + url));
            }
        }
        evidence.append(detail);
    }
    if (group.evidence.some(e => e.uncertain && Object.keys(e.uncertain).length)) {
        orgFields.append(node("p","muted","Algunas fuentes tienen el formato por verificar. Comprueba la edición antes de fusionar."));
    }
    const reject = action("No son la misma obra", () => reviewDecision("reject", group, "Sugerencia descartada. Las obras siguen en la biblioteca."));
    const actions = node("div","org-actions");
    actions.append(compare, reject, action("Volver", reviewDuplicates));
    const addRow = node("div","review-add");
    addRow.append(labelled("Buscar otra ficha por título",extra), add);
    orgFields.append(evidence, rows, addRow, actions);
    render();
}

function mergeDialog(works, back, prior = {}) {
    if (works.length < 2) return;
    const title = inputControl("text", prior.title ?? works[0].suggestion.title);
    title.required = true;
    const choices = {};
    const links = works.flatMap(work => work.links);
    const reading = links.filter(link => link.role === "reading");
    const suggestedSource = reading.find(link => !link.reference_hint);
    const preferred = selectControl(reading.map(link => [link.url, link.url +
        (link.reference_hint ? " · " + referenceLabels[link.reference_hint] : "")]), prior.preferred ?? suggestedSource?.url);
    showOrganization("Preparar fusión de " + works.length + " obras", "Revisar resultado", async () => {
        const data = {versions:versions(works), title:title.value, preferred:preferred.value};
        for (const [key, select] of Object.entries(choices)) data[key] = select.value;
        const result = await api("/api/merge/preview", {method:"POST", data});
        const merged = result.work;
        const consent = inputControl("checkbox");
        consent.required = true;
        showOrganization("Confirmar fusión de " + works.length + " obras", "Fusionar en una obra", () =>
            applyOrganization("/api/merge", {...data, confirm:consent.checked}, "Fusión guardada. Se conservaron " + merged.links.length + " enlaces."));
        const preserved = node("details","review-evidence");
        preserved.append(node("summary","","Detalles que se conservarán"));
        for (const [label, value] of [
            ["Puntuación personal", ratingText(merged.rating)],
            ["Títulos alternativos", merged.aliases.join(" / ")],
            ["Etiquetas", merged.tags.join(", ")],
            ["Carpetas del navegador", merged.bookmark_paths.map(displayPath).join(" / ")],
            ["Notas", merged.notes],
            ["Portada principal", merged.cover ? works.find(w => w.id === data.cover).title : "Sin portada"],
            ["Portadas alternativas", result.alternate_count || ""],
        ]) {
            if (value) preserved.append(node("p","",label + " · " + value));
        }
        const sources = node("fieldset");
        sources.append(node("legend","","Enlaces que se conservarán"));
        for (const link of merged.links) {
            const saved = links.find(item => item.url === link.url);
            const label = link.url === data.preferred ? "Fuente preferida" : link.role === "reading" ? "Fuente alternativa" : "Referencia";
            sources.append(node("p","",[label, link.url, referenceLabels[saved?.reference_hint], healthLabels[link.health]].filter(Boolean).join(" · ")));
        }
        if (!merged.links.length) sources.append(node("p","muted","Esta obra quedará sin enlaces."));
        orgFields.append(node("h3","",merged.title),
            node("p","","Obra principal · " + works.find(w => w.id === data.canonical).title),
            node("p","",describeFields({type:merged.type,status:merged.status,classified:merged.classified,chapter:merged.chapter || "Sin progreso"})),
            sources, preserved, action("Volver", () => mergeDialog(works,back,data)),
            labelled("Confirmo que estas " + works.length + " entradas corresponden a la misma obra.", consent));
    });
    const inputs = node("fieldset");
    inputs.append(node("legend","","Obras que se combinarán"));
    for (const work of works) inputs.append(workSummary(work));
    if (back) inputs.append(action("Volver a la comparación",back));
    orgFields.append(inputs);
    const fields = node("div","merge-fields");
    for (const [key,label,format] of [
        ["canonical","Obra principal",w=>w.title],
        ["status","Estado de lectura",w=>statuses[w.status].label],
        ["chapter","Capítulo actual",w=>w.chapter || "Sin progreso"],
        ["type","Tipo",w=>types[w.type]],
        ["classified","Organización",w=>w.classified ? "Clasificada" : "Sin clasificar"],
        ["rating","Puntuación personal",w=>ratingText(w.rating)],
        ["cover","Portada principal",w=>w.cover ? (w.cover.kind === "local" ? "Imagen local" : w.cover.url) : "Sin portada"]
    ]) {
        const options = works.map(w => [w.id,[format(w), key === "canonical" ? "" : w.title,
            w.links[0]?.website || "Sin fuentes"].filter(Boolean).join(" · ")]);
        const fallback = works.find(w => key === "cover" ? w.cover : key === "chapter" ? w.chapter : key === "rating" ? w.rating !== null : true);
        choices[key] = selectControl(options, prior[key] || fallback?.id || works[0].id);
        fields.append(labelled(label,choices[key]));
        if (key === "canonical") fields.append(labelled("Título definitivo",title));
    }
    orgFields.append(fields);
    if (reading.length) orgFields.append(labelled("Fuente preferida",preferred));
    orgFields.append(node("p","muted","La obra principal quedará en la biblioteca con el título que elijas y los enlaces de las fichas de esta comparación."));
}

function reviewBulkDelete() {
    const works = state.works.filter(w => selected.has(w.id)).sort((a,b) => collator.compare(a.title,b.title));
    if (!works.length) return;
    const phrase = inputControl("text", "", 8);
    phrase.required = true;
    phrase.pattern = "ELIMINAR";
    showOrganization("Eliminar " + works.length + " obras seleccionadas", "Eliminar las obras", async () => {
        const result = await api("/api/batch/delete", {method:"POST", data:{versions:versions(works), confirm:phrase.value}});
        closeAtCurrentPosition($("org-dialog"));
        selected.clear();
        selecting = false;
        feedback(result.count + " obras eliminadas. Copia local · " + result.backup);
        await refresh();
    });
    const list = node("ul","delete-review");
    for (const work of works) list.append(node("li","",work.title));
    orgFields.append(node("p","","Se quitarán de JMangaShelf estas fichas, con sus fuentes, portadas asignadas y demás datos. Se guardará una copia SQLite antes de eliminarlas."),
        list, labelled("Escribe ELIMINAR para confirmar", phrase));
    $("org-submit").disabled = true;
    phrase.oninput = () => { $("org-submit").disabled = phrase.value !== "ELIMINAR"; };
}

$("select-mode").onclick = () => { selecting = !selecting; selected.clear(); renderLibrary(); };
$("select-results").onclick = () => { selected.clear(); for (const work of filteredWorks) selected.add(work.id); renderLibrary(); };
$("clear-selection").onclick = () => { selected.clear(); renderLibrary(); };
$("batch").onclick = () => batchDialog(state.works.filter(w => selected.has(w.id)));
$("merge").onclick = () => mergeDialog(state.works.filter(w => selected.has(w.id)));
$("delete-selected").onclick = reviewBulkDelete;
$("review").onclick = reviewDuplicates;
$("suggestions").onclick = reviewTitles;
$("capture").onclick = captureLink;
bindForm(orgForm, async () => { if (submitOrganization) await submitOrganization(); });
refresh();

