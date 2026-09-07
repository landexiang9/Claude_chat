(function (global) {
    "use strict";

    function normalizeSearchText(value) {
        return String(value == null ? "" : value).normalize("NFKC").trim().toLocaleLowerCase();
    }

    function normalizeModel(model) {
        if (typeof model === "string") {
            return { id: model, label: model };
        }
        const id = String(model && model.id != null ? model.id : "");
        const label = String(model && (model.display_name || model.label) ? (model.display_name || model.label) : id);
        return { id, label };
    }

    function filterModels(models, query, allowEmpty = false) {
        const terms = normalizeSearchText(query).split(/\s+/).filter(Boolean);
        const seen = new Set();
        return (Array.isArray(models) ? models : [])
            .map(normalizeModel)
            .filter(item => {
                if ((!allowEmpty && !item.id) || seen.has(item.id)) return false;
                seen.add(item.id);
                if (terms.length === 0) return true;
                const haystack = `${normalizeSearchText(item.label)} ${normalizeSearchText(item.id)}`;
                return terms.every(term => haystack.includes(term));
            });
    }

    function create(options) {
        const { select, trigger, valueLabel, panel, searchInput, results, emptyState } = options || {};
        if (!select || !trigger || !valueLabel || !panel || !searchInput || !results || !emptyState) {
            throw new Error("Searchable model picker is missing a required element");
        }

        const fieldLabel = options.label || "模型";
        const native = !!options.native;
        const subscriptions = [];
        function listen(target, name, handler, capture) {
            target.addEventListener(name, handler, capture);
            subscriptions.push(() => target.removeEventListener(name, handler, capture));
        }
        let models = [];
        let selectedId = String(select.value || "");
        let isOpen = false;

        function selectedItem() {
            return models.find(item => item.id === selectedId) || null;
        }

        function updateTrigger() {
            const item = selectedItem();
            const label = item ? item.label : (selectedId || `选择${fieldLabel}`);
            valueLabel.textContent = label;
            trigger.title = item && item.label !== item.id ? `${item.label} (${item.id})` : label;
            trigger.setAttribute("aria-label", `${fieldLabel}：${label}`);
        }

        function focusResult(offset) {
            const buttons = Array.from(results.querySelectorAll(".model-picker-option")).filter(button => !button.disabled);
            if (buttons.length === 0) return;
            const activeIndex = buttons.indexOf(document.activeElement);
            const nextIndex = activeIndex < 0
                ? (offset < 0 ? buttons.length - 1 : 0)
                : (activeIndex + offset + buttons.length) % buttons.length;
            buttons[nextIndex].focus();
        }

        function chooseModel(id) {
            const item = models.find(candidate => candidate.id === id);
            if (!item || select.disabled || (native && isDisabled(id))) return;
            const previousId = String(select.value || "");
            select.value = item.id;
            selectedId = item.id;
            updateTrigger();
            close(true);
            if (previousId !== item.id) {
                select.dispatchEvent(new Event("change", { bubbles: true }));
            }
        }

        function nativeOption(id) {
            return Array.from(select.options || []).find(option => option.value === id);
        }

        function isDisabled(id) {
            const option = nativeOption(id);
            return !!(option?.disabled || option?.parentElement?.disabled);
        }

        function createResult(item) {
            const option = document.createElement("button");
            option.type = "button";
            option.className = "model-picker-option";
            option.dataset.modelId = item.id;
            if (native) option.disabled = !!isDisabled(item.id);
            option.setAttribute("role", "option");
            option.setAttribute("aria-selected", item.id === selectedId ? "true" : "false");

            const text = document.createElement("span");
            text.className = "model-picker-option-text";
            const label = document.createElement("span");
            label.className = "model-picker-option-label";
            label.textContent = item.label;
            text.appendChild(label);
            if (!native && item.label !== item.id) {
                const idLabel = document.createElement("span");
                idLabel.className = "model-picker-option-id";
                idLabel.textContent = item.id;
                text.appendChild(idLabel);
            }
            option.appendChild(text);

            const check = document.createElement("span");
            check.className = "model-picker-option-check";
            check.textContent = "✓";
            check.setAttribute("aria-hidden", "true");
            option.appendChild(check);

            option.addEventListener("click", () => chooseModel(item.id));
            option.addEventListener("keydown", event => {
                if (event.key === "ArrowDown") {
                    event.preventDefault();
                    focusResult(1);
                } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    focusResult(-1);
                } else if (event.key === "Escape") {
                    event.preventDefault();
                    close(true);
                } else if (event.key === "/") {
                    event.preventDefault();
                    searchInput.focus();
                }
            });
            return option;
        }

        function render() {
            const visibleModels = filterModels(models, searchInput.value, native);
            results.replaceChildren(...visibleModels.map(createResult));
            emptyState.classList.toggle("hidden", visibleModels.length !== 0);
        }

        function positionPanel() {
            if (!isOpen || typeof trigger.getBoundingClientRect !== "function") return;
            const rect = trigger.getBoundingClientRect();
            const viewportWidth = global.innerWidth || document.documentElement.clientWidth;
            const viewportHeight = global.innerHeight || document.documentElement.clientHeight;
            const margin = 8;
            const panelWidth = Math.min(Math.max(rect.width, 320), Math.max(0, viewportWidth - margin * 2));
            const left = Math.min(Math.max(margin, rect.left), Math.max(margin, viewportWidth - panelWidth - margin));
            panel.style.width = `${panelWidth}px`;
            panel.style.left = `${left}px`;

            const below = viewportHeight - rect.bottom - margin * 2;
            const above = rect.top - margin * 2;
            panel.style.maxHeight = `${Math.max(80, Math.min(430, Math.max(below, above)))}px`;
            const panelHeight = panel.offsetHeight;
            let top = rect.bottom + margin;
            if (top + panelHeight > viewportHeight - margin && rect.top - panelHeight - margin >= margin) {
                top = rect.top - panelHeight - margin;
            }
            panel.style.top = `${Math.max(margin, top)}px`;
        }

        function open() {
            if (native) options.refresh?.();
            if (select.disabled || models.length === 0 || isOpen) return;
            document.dispatchEvent(new Event("pickeropen"));
            isOpen = true;
            searchInput.value = "";
            render();
            panel.classList.remove("hidden");
            trigger.setAttribute("aria-expanded", "true");
            positionPanel();
            requestAnimationFrame(() => searchInput.focus({ preventScroll: true }));
        }

        function close(returnFocus = false) {
            if (!isOpen) return;
            isOpen = false;
            panel.classList.add("hidden");
            trigger.setAttribute("aria-expanded", "false");
            searchInput.value = "";
            render();
            if (returnFocus) trigger.focus({ preventScroll: true });
        }

        function setModels(nextModels, nextSelectedId = select.value) {
            models = filterModels(nextModels, "", native);
            selectedId = String(nextSelectedId || "");
            searchInput.disabled = models.length === 0;
            trigger.disabled = select.disabled || models.length === 0;
            updateTrigger();
            render();
        }

        function setStatus(message) {
            models = [];
            selectedId = "";
            valueLabel.textContent = message;
            trigger.title = message;
            trigger.setAttribute("aria-label", `${fieldLabel}：${message}`);
            trigger.disabled = true;
            searchInput.disabled = true;
            close();
            render();
        }

        function setSelected(id) {
            selectedId = String(id || "");
            select.value = selectedId;
            updateTrigger();
            render();
        }

        trigger.addEventListener("click", () => {
            if (isOpen) close();
            else open();
        });
        trigger.addEventListener("keydown", event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                open();
            } else if (event.key === "Escape" && isOpen) {
                event.preventDefault();
                event.stopPropagation();
                close();
            }
        });
        searchInput.addEventListener("input", render);
        searchInput.addEventListener("keydown", event => {
            if (event.isComposing || event.keyCode === 229) return;
            if (event.key === "Escape") {
                event.preventDefault();
                close(true);
            } else if (event.key === "ArrowDown") {
                event.preventDefault();
                focusResult(1);
            } else if (event.key === "Enter") {
                const first = Array.from(results.querySelectorAll(".model-picker-option")).find(button => !button.disabled);
                if (first) {
                    event.preventDefault();
                    chooseModel(first.dataset.modelId);
                }
            }
        });
        listen(document, "pickeropen", () => close());
        panel.addEventListener("keydown", event => {
            if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close(true);
            }
        });
        listen(document, "pointerdown", event => {
            if (isOpen && !panel.contains(event.target) && !trigger.contains(event.target)) close();
        });
        listen(document, "focusin", event => {
            if (isOpen && !panel.contains(event.target) && !trigger.contains(event.target)) close();
        });
        listen(document, "scroll", positionPanel, true);
        listen(global, "resize", positionPanel);

        setStatus(valueLabel.textContent || "正在加载模型...");
        return { close, filter: render, open, setModels, setSelected, setStatus,
            destroy() { close(); subscriptions.forEach(unsubscribe => unsubscribe()); panel.remove(); } };
    }

    global.ModelPicker = { create, filterModels, normalizeModel };
})(window);
