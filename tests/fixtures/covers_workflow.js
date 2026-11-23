"use strict";

(async () => {
    const waitFor = async (check, label) => {
        for (let i = 0; i < 1000; i++) {
            if (check()) return;
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        throw Error(label + " · " + coverBody.innerText);
    };
    function click(scope, text) {
        const button = [...scope.querySelectorAll("button")].find(element => element.textContent === text);
        if (!button || button.disabled || button.hidden) throw Error("Botón no disponible · " + text);
        button.click();
    }
    try {
        await waitFor(() => state.works.length === 4, "Cargar biblioteca");
        const work = state.works.find(w => w.title === "Lectura principal");
        const card = () => document.querySelector('[data-work-id="' + work.id + '"]');
        const chapter = () => card().querySelector(".chapter-action");
        chapter().click();
        if (field(chapterForm, "chapter").value !== "24.5") throw Error("Capítulo precargado");
        field(chapterForm, "chapter").value = "Epílogo";
        chapterForm.requestSubmit();
        await waitFor(() => chapter().textContent === "Cap. Epílogo", "Capítulo visible");
        card().querySelector(".sources-action").click();
        const details = $("sources-body");
        if (!details.innerText.includes("Preferida") || !details.innerText.includes("Roto") || !details.innerText.includes("Referencia")) {
            throw Error("Fuentes y referencias");
        }
        const alternate = details.querySelector('a[href="https://reader.example/alt"]');
        if (!alternate || alternate.target !== "_blank") throw Error("Enlace alternativo");
        alternate.addEventListener("click", event => event.preventDefault(), {once:true});
        alternate.click();
        if (card().querySelector(".work-actions a").href !== "https://reader.example/primary") throw Error("Fuente preferida");
        $("close-sources").click();

        click(card(), "Lectura principal");
        $("cover-search").click();
        const titleQuery = coverBody.querySelector("input");
        if (titleQuery.value !== "Lectura principal") throw Error("Título precargado");
        await waitFor(() => coverBody.querySelectorAll(".cover-candidate").length === 2, "Mostrar candidatos");
        titleQuery.value = "Título alternativo";
        titleQuery.form.requestSubmit();
        await waitFor(() => coverBody.querySelectorAll(".cover-candidate").length === 2, "Buscar con título modificado");
        coverBody.querySelector('[data-candidate="2"]').click();
        await waitFor(() => coverBody.querySelector(".cover-preview img")?.naturalWidth > 0, "Previsualizar segunda portada");
        click(coverBody, "Usar esta portada");
        if (coverDialog.open || $("staged-cover").hidden || !state.works.every(w => !w.cover)) throw Error("Portada preparada");
        workForm.requestSubmit();
        await waitFor(() => !workForm.dataset.busy && !workForm.closest("dialog").open &&
            state.works.find(w => w.id === work.id).cover, "Guardar portada individual");

        $("cover-batch").click();
        if (!coverBody.innerText.includes("3 obras de la biblioteca sin portada")) throw Error("Conteo elegible");
        click(coverBody, "Empezar revisión de portadas");
        click(coverBody, "Omitir por ahora");
        const realFetch = window.fetch;
        let canceled = false;
        window.fetch = (path, options) => path === "/api/anilist/search"
            ? new Promise((_, reject) => options.signal.addEventListener("abort", () => {
                canceled = true;
                reject(new DOMException("Cancelado", "AbortError"));
            }, {once:true}))
            : realFetch(path, options);
        click(coverBody, "Buscar en AniList");
        click(coverBody, "Pausar lote");
        window.fetch = realFetch;
        if (coverDialog.open || !canceled) throw Error("Pausar sesión y cancelar búsqueda");
        $("cover-batch").click();
        if (!coverBody.innerText.includes("Obra 2 de 3") || !coverBody.innerText.includes("1 omitidas")) throw Error("Reanudar índice");
        click(coverBody, "Buscar en AniList");
        await waitFor(() => coverBody.querySelector('[data-candidate="1"]'), "Candidatos en el lote");
        click(coverBody, "Rechazar resultados");
        if (!coverBody.innerText.includes("Obra 3 de 3")) throw Error("Rechazar y avanzar");
        click(coverBody, "Buscar en AniList");
        await waitFor(() => coverBody.querySelector('[data-candidate="1"]'), "Candidatos de la última ficha");
        coverBody.querySelector('[data-candidate="1"]').click();
        click(coverBody, "Confirmar portada");
        await waitFor(() => coverBody.innerText.includes("final de la lista"), "Confirmar y terminar lote");
        click(coverBody, "Terminar sesión");
        if (coverDialog.open || state.works.filter(w => w.cover).length !== 2) throw Error("Resultado de la sesión");
        document.body.dataset.testResult = "ok";
    } catch (error) {
        document.body.dataset.testResult = error.message;
    }
})();
