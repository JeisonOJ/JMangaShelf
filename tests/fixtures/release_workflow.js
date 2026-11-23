"use strict";

(async () => {
    const waitFor = async (check, failure) => {
        for (let i = 0; i < 1000; i++) {
            if (check()) return;
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        throw Error(failure + " · " + malForm.querySelector(".form-error").textContent);
    };
    const change = (control, value) => {
        control.value = value;
        control.dispatchEvent(new Event("change"));
    };
    try {
        await waitFor(() => state.works.length === 3, "Cargar biblioteca");
        const long = state.works.find(w => w.title.endsWith("FIN"));
        const short = state.works.find(w => w.title === "Pequeña");
        const card = work => document.querySelector('[data-work-id="' + work.id + '"]');
        const heading = card(long).querySelector(".work-title button");
        const titleStyle = getComputedStyle(heading);
        if (heading.title !== long.title || titleStyle.webkitLineClamp !== "3" ||
            heading.getBoundingClientRect().height > parseFloat(titleStyle.lineHeight) * 3 + 1) throw Error("Título de tres líneas");
        if (Math.abs(card(long).offsetHeight - card(short).offsetHeight) > 1) throw Error("Alturas de tarjeta");
        if (!card(long).textContent.includes("0/10") || !card(short).textContent.includes("Sin puntuar")) throw Error("Cero y sin puntuar");
        change($("sort"), "rating");
        if ($("library").firstElementChild.querySelector(".work-title").textContent !== "Torre local") throw Error("Orden por puntuación");
        card(short).querySelector(".work-title button").click();
        change(field(workForm, "rating"), "10");
        workForm.requestSubmit();
        await waitFor(() => card(short).textContent.includes("10/10"), "Puntuación visible");

        $("mal-import").click();
        const xml = "<myanimelist><myinfo><user_export_type>2</user_export_type></myinfo><manga>"
            + "<manga_mangadb_id>101</manga_mangadb_id><manga_title>Torre &amp; luna</manga_title>"
            + "<my_score>8</my_score><my_status>Completed</my_status><my_read_chapters>32</my_read_chapters></manga>"
            + "<manga><manga_mangadb_id>102</manga_mangadb_id><manga_title>Pequeña</manga_title><my_score>6</my_score></manga>"
            + "<manga><manga_mangadb_id>103</manga_mangadb_id><manga_title>Ficha nueva</manga_title></manga>"
            + "<manga><manga_title>Sin ID</manga_title></manga></myanimelist>";
        const upload = new DataTransfer();
        upload.items.add(new File([xml], "mal.xml", {type:"application/xml"}));
        field(malForm,"file").files = upload.files;
        $("mal-preview").click();
        await waitFor(() => $("mal-rows").textContent.includes("Coincidencia por ID"), "Vista previa MAL");
        const conflict = [...$("mal-rows").querySelectorAll("label")].find(label => label.firstChild.textContent === "Puntuación").querySelector("select");
        if (conflict.value !== "local" || !conflict.textContent.includes("4/10") || !conflict.textContent.includes("8/10")) throw Error("Conflicto de puntuación");
        if (!$("mal-apply").disabled) throw Error("Falta confirmación MAL");
        change(conflict,"mal");
        const filter = $("mal-filter");
        const search = $("mal-search");
        const visible = count => {
            if ($("mal-rows").querySelectorAll("fieldset").length !== count) throw Error("Cantidad visible del filtro " + filter.value);
        };
        const find = value => { search.value = value; search.dispatchEvent(new Event("input")); };
        const menu = () => $("mal-rows").querySelectorAll("select");
        visible(4);
        for (const [value,count] of [["review",1],["new",1],["linked",1],["skipped",2]]) {
            const option = [...filter.options].find(option => option.value === value);
            if (!option.text.endsWith("(" + count + ")")) throw Error("Conteo de " + value);
            change(filter,value);
            visible(count);
        }
        change(filter,"linked");
        find("TORRE LOCAL");
        visible(1);
        find("imposible");
        visible(0);
        if (!$("mal-rows").textContent.includes("No hay entradas")) throw Error("Estado vacío");
        find("");
        change(filter,"review");
        change(menu()[0],"link");
        change(menu()[1],short.id);
        change(menu()[2],"mal");
        visible(1);
        change(filter,"linked");
        visible(2);
        find("PEQUEÑA");
        visible(1);
        if (menu()[1].value !== short.id || menu()[2].value !== "mal") throw Error("Decisiones conservadas");
        change(menu()[0],"skip");
        visible(0);
        find("");
        change(filter,"new");
        change(menu()[0],"skip");
        visible(1);
        change(filter,"all");
        visible(4);
        if (menu()[1].value !== "mal") throw Error("Decisión original conservada");
        field(malForm,"confirm").click();
        $("mal-apply").click();
        await waitFor(() => !$("mal-dialog").open && state.works.some(w => w.title === "Torre local" && w.rating === 8), "Importar MAL");

        $("select-mode").click();
        card(long).querySelector("input[type=checkbox]").click();
        card(short).querySelector("input[type=checkbox]").click();
        $("delete-selected").click();
        if ($("org-fields").querySelectorAll("li").length !== 2 || !$("org-submit").disabled) throw Error("Revisión de eliminación");
        const phrase = $("org-fields").querySelector("input");
        phrase.value = "ELIMINAR";
        phrase.dispatchEvent(new Event("input"));
        $("org-submit").click();
        await waitFor(() => state.works.length === 1 && !$("org-dialog").open, "Eliminación en lote");
        if (selected.size || state.works[0].title !== "Torre local") throw Error("Conservar ficha ajena al lote");
        document.body.dataset.testResult = "ok";
    } catch (error) {
        document.body.dataset.testResult = error.message;
    }
})();
