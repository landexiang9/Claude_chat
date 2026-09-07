(function (global) {
    "use strict";
    const instances = new Map();
    function enhance(select) {
        if (instances.has(select)) return instances.get(select);
        const toolbar = select.closest(".toolbar-field");
        const label = select.getAttribute("aria-label") || select.labels?.[0]?.textContent.trim() || select.title || "选项";
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.id = `${select.id}-picker-trigger`;
        trigger.className = "model-picker-trigger" + (toolbar ? "" : " settings-picker-trigger");
        trigger.setAttribute("role", "combobox");
        trigger.setAttribute("aria-haspopup", "listbox");
        trigger.setAttribute("aria-expanded", "false");
        const valueLabel = document.createElement("span");
        trigger.appendChild(valueLabel);
        select.insertAdjacentElement("afterend", trigger);
        select.classList.add("unified-select-native");
        select.tabIndex = -1;
        select.setAttribute("aria-hidden", "true");
        if (toolbar) toolbar.classList.add("unified-toolbar-field");
        for (const fieldLabel of Array.from(select.labels || [])) {
            if (fieldLabel.htmlFor === select.id) fieldLabel.htmlFor = trigger.id;
        }
        const panel = document.createElement("div");
        panel.className = "model-picker-panel unified-picker-panel hidden";
        panel.setAttribute("aria-label", `选择${label}`);
        const searchBox = document.createElement("div");
        searchBox.className = "model-picker-search";
        const searchInput = document.createElement("input");
        searchInput.type = "search";
        searchInput.placeholder = `搜索${label}…`;
        searchInput.setAttribute("aria-label", `搜索${label}`);
        searchBox.appendChild(searchInput);
        const results = document.createElement("div");
        results.className = "model-picker-results";
        results.id = `${select.id}-picker-results`;
        results.setAttribute("role", "listbox");
        results.setAttribute("aria-label", label);
        trigger.setAttribute("aria-controls", results.id);
        const emptyState = document.createElement("div");
        emptyState.className = "model-picker-empty hidden";
        emptyState.textContent = "没有匹配的选项";
        emptyState.setAttribute("role", "status");
        panel.append(searchBox, results, emptyState);
        // Keep settings popups inside their modal's focus boundary.
        (select.closest(".modal-overlay") || document.body).appendChild(panel);
        let picker;
        const refresh = () => {
            const items = Array.from(select.options).filter(option => !option.hidden).map(option => ({
                id: option.value,
                label: option.parentElement?.tagName === "OPTGROUP"
                    ? `${option.parentElement.label} · ${option.textContent}` : option.textContent
            }));
            picker.setModels(items, select.value);
            if (select.disabled || !select.isConnected || select.closest(".hidden")) picker.close();
        };
        picker = ModelPicker.create({ select, trigger, valueLabel, panel, searchInput, results, emptyState,
            label, native: true, refresh });
        const observer = new MutationObserver(refresh);
        observer.observe(select, { childList: true, subtree: true, attributes: true, characterData: true });
        select.addEventListener("change", refresh);
        const instance = { refresh, close: picker.close, destroy() { observer.disconnect(); picker.destroy(); } };
        instances.set(select, instance);
        refresh();
        return instance;
    }
    function refreshAll() {
        instances.forEach((instance, select) => {
            if (!select.isConnected) { instance.destroy(); instances.delete(select); }
            else instance.refresh();
        });
    }
    function closeAll() { instances.forEach(instance => instance.close()); }
    function init() {
        document.querySelectorAll('#platform-select, #system-prompt-select, #settings-modal select').forEach(enhance);
    }
    global.SelectPicker = { enhance, refreshAll, closeAll, init };
    document.addEventListener("DOMContentLoaded", init);
})(window);
