"use strict";

(async () => {
    const waitFor = async (check, label) => {
        for (let i = 0; i < 1000; i++) {
            if (check()) return;
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        throw Error(label + " · " + (orgForm.querySelector(".form-error")?.textContent || ""));
    };
    const clickText = (scope, text) => {
        const button = [...scope.querySelectorAll("button")].find(b => b.textContent === text);
        if (!button || button.disabled) throw Error("Botón no disponible · " + text);
        button.click();
    };
    const change = (element, value) => {
        element.value = value;
        element.dispatchEvent(new Event("change", {bubbles:true}));
    };
    const heading = prefix => waitFor(() => $("org-heading").textContent.startsWith(prefix), prefix);
    const saved = () => waitFor(() => !$("org-dialog").open && !orgForm.dataset.busy, "Guardar operación");
    try {
        await waitFor(() => state.works.length === 75, "Cargar biblioteca");
        change(folderFilter, JSON.stringify(["Manga"]));
        $("select-mode").click();
        $("select-results").click();
        if (selected.size !== 75 || $("library").children.length !== 72) throw Error("Selección paginada");
        $("batch").click();
        change(orgFields.querySelector("select"), "folder_type");
        $("org-submit").click();
        await heading("Vista previa del lote");
        if (!$("org-submit").textContent.includes("75")) throw Error("Conteo del lote");
        $("org-submit").click();
        await saved();
        await waitFor(() => state.works.every(w => w.type === "manhua"), "Clasificación por carpeta");
        change($("sort"), "title");
        for (const title of ["Serie 0","Serie 1"]) {
            clickText($("library"), title);
            field(workForm, "aliases").value = "Torre compartida";
            change(workForm.querySelector(".health"), "suspicious");
            workForm.requestSubmit();
            await waitFor(() => !workForm.dataset.busy && !workForm.closest("dialog").open, "Guardar edición");
        }
        change($("health-filter"), "suspicious");
        $("search").value = "torre compartida";
        $("search").dispatchEvent(new Event("input"));
        if (filteredWorks.length !== 2) throw Error("Buscar por alias y estado de fuente");
        $("review").click();
        await heading("Revisar posibles");
        if (!orgFields.textContent.includes("Manga › Manhua › Torre compartida")) throw Error("Ruta de Edge");
        clickText(orgFields, "Ver grupo");
        if (!orgFields.textContent.includes("Posible referencia de catálogo")) throw Error("Indicador de catálogo");
        clickText(orgFields, "No son la misma obra");
        await waitFor(() => orgFields.textContent.includes("Sugerencias descartadas") && !orgForm.dataset.busy, "Descartar");
        if (state.works.length !== 75) throw Error("Conservación de obras al descartar");
        clickText(orgFields, "Volver a considerar");
        await waitFor(() => orgFields.textContent.includes("Ver grupo") && !orgForm.dataset.busy, "Volver a considerar");
        clickText(orgFields, "Ver grupo");
        clickText(orgFields, "Quitar de esta comparación");
        const add = orgFields.querySelector("select");
        for (const title of ["Serie 0","Serie 1","Serie 2"]) {
            const id = state.works.find(w => w.title === title).id;
            if ([...add.options].some(o => o.value === id)) {
                change(add, id);
                clickText(orgFields,"Añadir otra obra");
            }
        }
        clickText(orgFields, "Revisar y fusionar");
        if (!orgFields.textContent.includes("Obra principal")) throw Error("Elección de la obra principal");
        orgFields.querySelector("input[type=text]").value = "La torre";
        $("org-submit").click();
        await heading("Confirmar fusión de 3");
        if (!orgFields.textContent.includes("Enlaces que se conservarán")) throw Error("Vista previa de enlaces");
        if (orgForm.checkValidity()) throw Error("Falta confirmación explícita");
        orgFields.querySelector("input[type=checkbox]").click();
        $("org-submit").click();
        await saved();
        await waitFor(() => state.works.length === 73, "Fusión de tres obras");
        const merged = state.works.find(w => w.title === "La torre");
        if (merged?.links.length !== 3 || !merged.aliases.includes("Torre compartida")) throw Error("Resultado de fusión");
        document.body.dataset.testResult = "ok";
    } catch (error) {
        document.body.dataset.testResult = error.message;
    }
})();
