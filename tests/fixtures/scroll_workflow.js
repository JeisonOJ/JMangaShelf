"use strict";

(async () => {
    const waitFor = async (check, failure) => {
        for (let i = 0; i < 1000; i++) {
            if (check()) return;
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        throw Error(failure + " · " + workForm.querySelector(".form-error").textContent);
    };
    const layout = () => new Promise(resolve => setTimeout(resolve, 50));
    const check = (condition, label) => { if (!condition) throw Error(label); };
    const change = (control, value) => {
        control.value = value;
        control.dispatchEvent(new Event("change"));
    };
    const cards = $("library");
    const card = id => cards.querySelector('[data-work-id="' + id + '"]');
    const submit = (form) => form.requestSubmit();
    const edit = id => card(id).querySelector(".work-title button").click();
    const at = id => card(id).getBoundingClientRect().top;
    const near = (id, top, label) => check(Math.abs(at(id) - top) <= 3, label + " · " + at(id) + " / " + top);
    try {
        await waitFor(() => state.works.length === 240, "Cargar biblioteca");
        change($("sort"), "title");
        sectionButtons.get("planned").button.click();
        change(typeFilter, "other");
        const firstCard = cards.firstElementChild;
        const focus = document.activeElement;
        for (const expected of [144, 216]) {
            $("load-more").scrollIntoView({block:"center"});
            await waitFor(() => cards.children.length === expected, "Carga automática de " + expected);
            await layout();
        }
        check(firstCard === cards.firstElementChild && focus === document.activeElement, "Añadir tarjetas sin mover el foco");
        check(cards.children.length === 216, "Cargar dos tandas adicionales");
        const work = state.works.find(item => item.title === "Serie 199");
        const anchor = state.works.find(item => item.title === "Serie 198");
        const neighbor = card(anchor.id);
        card(work.id).scrollIntoView({block:"center"});
        await layout();
        const fetching = window.fetch;
        let refreshes = 0;
        window.fetch = (path, options) => {
            if (path === "/api/works") refreshes++;
            return fetching(path, options);
        };

        let top = at(work.id);
        edit(work.id);
        field(workForm,"rating").value = "9";
        submit(workForm);
        await waitFor(() => card(work.id).textContent.includes("9/10"), "Guardar puntuación");
        await layout();
        check(cards.children.length === 216 && card(anchor.id) === neighbor && refreshes === 0, "Guardar solo la tarjeta afectada");
        near(work.id, top, "Mantener la obra editada en pantalla");

        card(work.id).querySelector(".chapter-action").click();
        field(chapterForm,"chapter").value = "28.5";
        submit(chapterForm);
        await waitFor(() => card(work.id).textContent.includes("28.5"), "Guardar capítulo");
        near(work.id, top, "Mantener posición tras guardar capítulo");

        edit(work.id);
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 10;
        const blob = await new Promise(resolve => canvas.toBlob(resolve));
        const transfer = new DataTransfer();
        transfer.items.add(new File([blob],"portada.png",{type:"image/png"}));
        change(field(workForm,"cover_kind"), "local");
        field(workForm,"cover_file").files = transfer.files;
        $("link-fields").querySelector('input[type="url"]').value = "https://alt.example/manga";
        submit(workForm);
        await waitFor(() => card(work.id).querySelector("img")?.naturalWidth > 0, "Cargar la portada guardada");
        await layout();
        check(card(work.id).querySelector(".work-actions a").href === "https://alt.example/manga", "Actualizar fuente preferida");
        near(work.id, top, "Mantener posición al cargar la imagen");
        check(cards.children.length === 216, "Mantener el tramo cargado");

        // Force a narrower layout with the modal open, then cancel it.
        edit(work.id);
        cards.style.maxWidth = "520px";
        await layout();
        const beforeCancel = window.scrollY;
        $("work-dialog").querySelector("[data-close]").click();
        await layout();
        check(Math.abs(window.scrollY - beforeCancel) <= 3, "Conservar desplazamiento al cancelar con otro ancho");

        card(work.id).scrollIntoView({block:"center"});
        await layout();
        top = at(anchor.id);
        edit(work.id);
        check(field(workForm,"rating").value === "9" && field(workForm,"chapter").value === "28.5" &&
            field(workForm,"cover_kind").value === "keep", "Abrir ficha actualizada");
        field(workForm,"title").value = "AAA";
        submit(workForm);
        await waitFor(() => cards.firstElementChild.dataset.workId === work.id, "Reordenar por título");
        await layout();
        // A changed row break can put the original neighbor one row lower.
        check(Math.abs(at(anchor.id) - top) < card(anchor.id).offsetHeight + 20, "Conservar la zona al reordenar");

        const exiting = state.works.find(item => item.title === "Serie 200");
        card(exiting.id).scrollIntoView({block:"center"});
        await layout();
        let lastScroll = window.scrollY;
        edit(exiting.id);
        field(workForm,"status").value = "completed";
        submit(workForm);
        await waitFor(() => !card(exiting.id), "Quitar del filtro al cambiar estado");
        check(cards.children.length === 216 && $("sort").value === "title" && state.status === "planned" &&
            typeFilter.value === "other" && Math.abs(window.scrollY - lastScroll) < 2 * card(anchor.id).offsetHeight, "Restaurar contexto al salir del filtro");

        const removing = state.works.find(item => item.title === "Serie 201");
        card(removing.id).scrollIntoView({block:"center"});
        await layout();
        lastScroll = window.scrollY;
        edit(removing.id);
        $("delete-work").click();
        submit($("delete-form"));
        await waitFor(() => !card(removing.id), "Eliminar obra");
        check(cards.children.length === 216 && refreshes === 0 &&
            Math.abs(window.scrollY - lastScroll) < 2 * card(anchor.id).offsetHeight, "Mantener contexto al eliminar");

        $("select-mode").click();
        card(anchor.id).querySelector(".pick-work input").click();
        const second = state.works.find(item => item.title === "Serie 197");
        card(second.id).querySelector(".pick-work input").click();
        lastScroll = window.scrollY;
        $("batch").click();
        $("org-submit").click();
        await waitFor(() => $("org-heading").textContent === "Vista previa del lote", "Previsualizar organización");
        $("org-submit").click();
        await waitFor(() => [anchor,second].every(work => state.works.find(item => item.id === work.id).classified), "Organizar obras");
        check(cards.children.length === 216 && selected.has(anchor.id) && selected.has(second.id) && refreshes === 1 &&
            Math.abs(window.scrollY - lastScroll) < 2 * card(anchor.id).offsetHeight, "Mantener selección y tramo tras organizar");
        $("more-works").click();
        check(cards.childElementCount === filteredWorks.length && $("load-more").hidden, "Última tanda manual");
        check(new Set(Array.from(cards.children, el => el.dataset.workId)).size === cards.childElementCount, "Sin tarjetas repetidas");
        document.body.dataset.testResult = "ok";
    } catch (error) {
        document.body.dataset.testResult = error.message;
    }
})();
