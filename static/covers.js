"use strict";

const coverDialog = $("cover-dialog");
const coverBody = $("cover-body");
const batchChunk = 20;
const formats = {MANGA: "Manga", NOVEL: "Novela", ONE_SHOT: "Historia autoconclusiva"};
const candidateCountries = {JP: "Japón", KR: "Corea del Sur", CN: "China", TW: "Taiwán", HK: "Hong Kong"};
let stagedCover = null;
let coverBatch = null;
let activeSearch = null;
let savingCover = false;

function clearStagedCover() {
    stagedCover = null;
    const option = field(workForm, "cover_kind").querySelector('[value="anilist"]');
    option.hidden = option.disabled = true;
    $("staged-cover").replaceChildren();
}

function candidateTitle(candidate) {
    return candidate.titles.romaji || candidate.titles.english || candidate.titles.native || "Ficha de AniList " + candidate.id;
}

function previewImage(candidate, large = false) {
    const frame = node("div", large ? "candidate-image large" : "candidate-image");
    const image = node("img");
    const fallback = node("small", "", "Vista no disponible");
    image.alt = "Portada de " + candidateTitle(candidate);
    image.referrerPolicy = "no-referrer";
    image.loading = large ? "eager" : "lazy";
    image.addEventListener("error", () => { image.hidden = true; fallback.hidden = false; }, {once:true});
    const url = large ? candidate.image : candidate.thumbnail;
    if (url) {
        fallback.hidden = true;
        image.src = url;
        frame.append(image);
    }
    frame.append(fallback);
    return frame;
}

function showCoverDialog(title) {
    activeSearch?.abort();
    activeSearch = null;
    $("cover-heading").textContent = title;
    coverBody.replaceChildren();
    if (!coverDialog.open) coverDialog.showModal();
    coverBody.scrollTop = 0;
}

function closeCoverDialog() {
    if (savingCover) return;
    activeSearch?.abort();
    activeSearch = null;
    coverDialog.close();
    coverBody.replaceChildren();
}

function selectForEditor(candidate) {
    stagedCover = candidate;
    const select = field(workForm, "cover_kind");
    const option = select.querySelector('[value="anilist"]');
    option.hidden = option.disabled = false;
    select.value = "anilist";
    $("staged-cover").replaceChildren(previewImage(candidate),
        node("p", "", "Portada pendiente · " + candidateTitle(candidate) + ". Se aplicará al guardar la obra."));
    setCoverFields();
    closeCoverDialog();
}

function renderCoverSearch(work, queryText, batch = false) {
    const form = node("form", "cover-query");
    const query = inputControl("search", queryText, 300);
    query.required = true;
    const search = node("button", "button secondary", "Buscar en AniList");
    search.type = "submit";
    form.append(labelled("Título de búsqueda", query), search);
    const notice = node("p", "muted");
    notice.setAttribute("role", "status");
    const results = node("div", "cover-candidates");
    const preview = node("div", "cover-preview");
    const applyError = node("p", "form-error");
    applyError.setAttribute("role", "alert");
    applyError.hidden = true;
    const reject = action("Rechazar resultados", () => advanceCoverBatch("rechazadas"));
    reject.hidden = true;

    function choose(candidate) {
        for (const button of results.querySelectorAll("button")) {
            button.setAttribute("aria-pressed", String(button.dataset.candidate === String(candidate.id)));
        }
        const description = node("div", "cover-confirmation");
        const confirm = action(batch ? "Confirmar portada" : "Usar esta portada", async () => {
            if (!batch) {
                selectForEditor(candidate);
                return;
            }
            savingCover = true;
            applyError.hidden = true;
            const buttons = [...coverDialog.querySelectorAll("button:not(:disabled)")];
            for (const button of buttons) button.disabled = true;
            try {
                const saved = await api("/api/works/" + work.id + "/cover", {method:"PATCH",
                    data:{token:candidate.token, updated_at:work.updated_at, confirm:true}});
                updateCard(saved);
                advanceCoverBatch("guardadas");
            } catch (error) {
                applyError.textContent = error.message;
                applyError.hidden = false;
            } finally {
                savingCover = false;
                for (const button of buttons) button.disabled = false;
            }
        }, "button primary");
        description.append(node("strong", "", candidateTitle(candidate)),
            node("p", "muted", batch ? "La portada se guardará en esta obra." :
                "La portada se aplicará cuando guardes la ficha."), confirm, applyError);
        applyError.hidden = true;
        preview.replaceChildren(previewImage(candidate, true), description);
        preview.scrollIntoView({block:"nearest"});
        confirm.focus();
    }

    async function lookup() {
        if (savingCover || search.disabled || !query.value.trim()) return;
        activeSearch?.abort();
        const controller = new AbortController();
        activeSearch = controller;
        search.disabled = true;
        reject.hidden = true;
        notice.textContent = "Consultando AniList…";
        results.replaceChildren();
        preview.replaceChildren();
        try {
            const response = await api("/api/anilist/search", {method:"POST", signal:controller.signal,
                data:{query:query.value, work_id:work?.id}});
            if (controller.signal.aborted) return;
            const {candidates, suggested} = response;
            const available = candidates.some(candidate => candidate.token);
            notice.textContent = !candidates.length ? "Sin resultados. Prueba otro título o alias." :
                !available ? "Estos resultados no tienen una portada disponible." :
                suggested ? "Hay una coincidencia exacta de título o alias. Verifica la edición." :
                    "Sin sugerencia clara. Compara los títulos, el país y el formato antes de elegir.";
            reject.hidden = !batch || !candidates.length;
            for (const candidate of [...candidates].sort((a,b) => Number(b.id === suggested) - Number(a.id === suggested))) {
                const card = node("article", "cover-candidate");
                const name = candidateTitle(candidate);
                card.append(previewImage(candidate), node("h3", "", name));
                const info = [formats[candidate.format], candidateCountries[candidate.country] || candidate.country,
                    candidate.year].filter(Boolean);
                if (info.length) card.append(node("small", "", info.join(" · ")));
                const titles = [...new Set([...Object.values(candidate.titles), ...candidate.aliases])].filter(t => t !== name);
                if (titles.length) {
                    const aliases = node("details");
                    aliases.append(node("summary","","Otros títulos"), node("small","",titles.join(" / ")));
                    card.append(aliases);
                }
                if (candidate.id === suggested) card.append(node("p", "candidate-suggested", "Coincidencia de título"));
                if (candidate.token) {
                    const pick = action("Ver portada", () => choose(candidate));
                    pick.dataset.candidate = String(candidate.id);
                    pick.setAttribute("aria-pressed", "false");
                    pick.setAttribute("aria-label", "Ver portada de " + name);
                    card.append(pick);
                } else {
                    card.append(node("small", "", "Sin portada disponible"));
                }
                results.append(card);
            }
        } catch (error) {
            if (!controller.signal.aborted) notice.textContent = error.message;
        } finally {
            if (activeSearch === controller) {
                activeSearch = null;
                search.disabled = false;
            }
        }
    }
    form.addEventListener("submit", event => { event.preventDefault(); lookup(); });
    coverBody.append(form);
    if (batch && work.aliases.length) {
        const aliases = node("div", "org-actions");
        for (const title of work.aliases.slice(0, 4)) {
            aliases.append(action(title, () => { query.value = title; form.requestSubmit(); }));
        }
        coverBody.append(aliases);
    }
    coverBody.append(notice, results, preview);
    if (batch) {
        const actions = node("div", "org-actions");
        actions.append(reject, action("Omitir por ahora", () => advanceCoverBatch("omitidas")),
            action("Pausar lote", closeCoverDialog), action("Terminar sesión", finishCoverBatch));
        coverBody.append(actions);
    }
    return {query, form};
}

function batchSummary() {
    const {guardadas, rechazadas, omitidas} = coverBatch.counts;
    return guardadas + " guardadas · " + rechazadas + " rechazadas · " + omitidas + " omitidas";
}

function finishCoverBatch() {
    const message = batchSummary();
    coverBatch = null;
    closeCoverDialog();
    feedback("Sesión de portadas · " + message);
}

function advanceCoverBatch(result) {
    coverBatch.counts[result]++;
    coverBatch.index++;
    showNextCoverWork();
}

function showNextCoverWork() {
    const {ids} = coverBatch;
    const workById = new Map(state.works.map(work => [work.id,work]));
    while (coverBatch.index < ids.length) {
        const work = workById.get(ids[coverBatch.index]);
        if (work && !work.cover) break;
        coverBatch.index++;
        coverBatch.counts.omitidas++;
    }
    showCoverDialog("Buscar portadas en lote");
    const {index, stopAt} = coverBatch;
    coverBody.append(node("p", "muted", batchSummary()));
    if (index >= ids.length) {
        coverBody.append(node("p", "", "Llegaste al final de la lista."), action("Terminar sesión", finishCoverBatch, "button primary"));
        return;
    }
    if (index >= stopAt) {
        coverBody.append(node("p", "", "Quedan " + (ids.length - index) + " obras en la lista de esta sesión."),
            action("Revisar el siguiente bloque", () => {
                coverBatch.stopAt += batchChunk;
                showNextCoverWork();
            }, "button primary"), action("Pausar lote", closeCoverDialog), action("Terminar sesión", finishCoverBatch));
        return;
    }
    const work = workById.get(ids[index]);
    coverBody.append(node("p", "muted", "Obra " + (index + 1) + " de " + ids.length), node("h3", "", work.title));
    const search = renderCoverSearch(work, work.suggestion.title, true);
    search.query.focus();
}

function openCoverBatch() {
    if (coverBatch) {
        showNextCoverWork();
        return;
    }
    const eligible = state.works.filter(work => !work.cover);
    const filtered = filteredWorks.filter(work => !work.cover);
    showCoverDialog("Buscar portadas en lote");
    coverBody.append(node("p", "", eligible.length ? eligible.length + " obras de la biblioteca sin portada." :
        "No hay obras sin portada en la biblioteca."));
    if (!eligible.length) return;
    coverBody.append(node("p", "muted", "Revisa hasta " + batchChunk + " obras por bloque. Tú inicias cada búsqueda y decides qué portada guardar."));
    function begin(works) {
        coverBatch = {ids:works.map(work => work.id), index:0, stopAt:batchChunk, counts:{guardadas:0, rechazadas:0, omitidas:0}};
        showNextCoverWork();
    }
    const controls = node("div","org-actions");
    if (filtered.length && filtered.length < eligible.length) {
        controls.append(action("Revisar resultados filtrados (" + filtered.length + ")", () => begin(filtered), "button primary"));
    }
    controls.append(action("Empezar revisión de portadas", () => begin(eligible.sort((a,b) =>
        Number(b.status === "reading") - Number(a.status === "reading") || b.updated_at.localeCompare(a.updated_at))),
        controls.children.length ? "button secondary" : "button primary"));
    coverBody.append(controls);
}

$("cover-search").onclick = () => {
    const title = field(workForm, "title").value.trim();
    showCoverDialog("Buscar portada en AniList");
    const search = renderCoverSearch(editing, title);
    if (title) search.form.requestSubmit();
    else search.query.focus();
};
$("cover-batch").onclick = openCoverBatch;
$("close-cover").onclick = closeCoverDialog;
coverDialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeCoverDialog();
});
