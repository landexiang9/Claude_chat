const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
    if (!condition) throw new Error(message);
}

class FakeEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.bubbles = !!options.bubbles;
        this.defaultPrevented = false;
        this.target = null;
    }

    preventDefault() {
        this.defaultPrevented = true;
    }
}

function makeClassList(initial = []) {
    const classes = new Set(initial);
    return {
        add(...names) { names.forEach(name => classes.add(name)); },
        remove(...names) { names.forEach(name => classes.delete(name)); },
        contains(name) { return classes.has(name); },
        toggle(name, force) {
            if (force === true) {
                classes.add(name);
                return true;
            }
            if (force === false) {
                classes.delete(name);
                return false;
            }
            if (classes.has(name)) {
                classes.delete(name);
                return false;
            }
            classes.add(name);
            return true;
        }
    };
}

function makeEventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
        },
        dispatchEvent(event) {
            event.target = this;
            for (const handler of listeners.get(event.type) || []) handler.call(this, event);
            return !event.defaultPrevented;
        }
    };
}

function createElement(tagName, documentRef) {
    const eventTarget = makeEventTarget();
    const element = {
        ...eventTarget,
        tagName: String(tagName).toUpperCase(),
        type: "",
        className: "",
        classList: makeClassList(),
        dataset: {},
        style: {},
        children: [],
        parentNode: null,
        textContent: "",
        title: "",
        value: "",
        disabled: false,
        offsetHeight: 240,
        attributes: {},
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            return child;
        },
        replaceChildren(...children) {
            this.children.forEach(child => { child.parentNode = null; });
            this.children = [];
            children.forEach(child => this.appendChild(child));
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(this.attributes, name)
                ? this.attributes[name]
                : null;
        },
        removeAttribute(name) {
            delete this.attributes[name];
        },
        contains(target) {
            return target === this || this.children.some(child => child.contains(target));
        },
        querySelectorAll(selector) {
            const matches = [];
            const visit = node => {
                if (selector.startsWith(".") && node.className.split(/\s+/).includes(selector.slice(1))) {
                    matches.push(node);
                }
                node.children.forEach(visit);
            };
            this.children.forEach(visit);
            return matches;
        },
        querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
        },
        focus() {
            documentRef.activeElement = this;
        },
        getBoundingClientRect() {
            return { left: 100, top: 50, right: 420, bottom: 88, width: 320, height: 38 };
        }
    };
    return element;
}

const documentTarget = makeEventTarget();
const document = {
    ...documentTarget,
    activeElement: null,
    documentElement: { clientWidth: 1024, clientHeight: 768 }
};
document.createElement = tagName => createElement(tagName, document);

const windowTarget = makeEventTarget();
const window = {
    ...windowTarget,
    innerWidth: 1024,
    innerHeight: 768
};

const context = vm.createContext({
    console,
    document,
    window,
    Event: FakeEvent,
    requestAnimationFrame: callback => callback()
});

const source = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "model_picker.js"),
    "utf8"
);
vm.runInContext(source, context, { filename: "model_picker.js" });

const models = [
    { id: "claude-3-5-sonnet-20241022", display_name: "Claude Sonnet 3.5" },
    { id: "gpt-4o-mini", display_name: "OpenAI Fast Mini" },
    { id: "deepseek-reasoner", display_name: "DeepSeek R1" },
    "gemini-2.5-pro"
];

function filteredIds(query) {
    return window.ModelPicker.filterModels(models, query).map(model => model.id).join(",");
}

assert(filteredIds("4o-mini") === "gpt-4o-mini", "search should match a model id");
assert(filteredIds("sonnet 3.5") === "claude-3-5-sonnet-20241022", "search should match display_name");
assert(filteredIds("  oPeNaI   mInI  ") === "gpt-4o-mini", "search should ignore case and surrounding whitespace");
assert(filteredIds("fast gpt") === "gpt-4o-mini", "all search terms should match across display_name and id");
assert(filteredIds("") === models.map(model => typeof model === "string" ? model : model.id).join(","), "empty search should restore every model");

const select = document.createElement("select");
const trigger = document.createElement("button");
const valueLabel = document.createElement("span");
const panel = document.createElement("div");
const searchInput = document.createElement("input");
const results = document.createElement("div");
const emptyState = document.createElement("div");
panel.classList.add("hidden");
emptyState.classList.add("hidden");
valueLabel.textContent = "正在加载模型...";
select.value = "claude-3-5-sonnet-20241022";

let changeCount = 0;
select.addEventListener("change", () => { changeCount += 1; });

const picker = window.ModelPicker.create({
    select,
    trigger,
    valueLabel,
    panel,
    searchInput,
    results,
    emptyState
});
picker.setModels(models, select.value);
assert(valueLabel.textContent === "Claude Sonnet 3.5", "the trigger should show the selected model display name");
trigger.dispatchEvent(new FakeEvent("click"));

searchInput.value = "deepseek";
searchInput.dispatchEvent(new FakeEvent("input"));
assert(results.children.length === 1, "filtering should render only matching results");
assert(results.children[0].dataset.modelId === "deepseek-reasoner", "filtering rendered the wrong model");
assert(results.children[0].children[0].children[0].textContent === "DeepSeek R1", "results should preserve display names");
assert(changeCount === 0, "filtering must not dispatch a native select change event");
assert(select.value === "claude-3-5-sonnet-20241022", "filtering must not change the selected model");
const composingEnter = new FakeEvent("keydown");
composingEnter.key = "Enter";
composingEnter.isComposing = true;
searchInput.dispatchEvent(composingEnter);
assert(changeCount === 0, "confirming Chinese input must not select a model");

searchInput.value = "not-a-real-model";
searchInput.dispatchEvent(new FakeEvent("input"));
assert(results.children.length === 0, "an unmatched search should clear the result list");
assert(!emptyState.classList.contains("hidden"), "an unmatched search should show the empty state");
assert(changeCount === 0, "showing the empty state must not dispatch a change event");

searchInput.value = "";
searchInput.dispatchEvent(new FakeEvent("input"));
assert(results.children.length === models.length, "clearing the search should restore every result");
assert(emptyState.classList.contains("hidden"), "clearing the search should hide the empty state");
assert(changeCount === 0, "clearing the search must not dispatch a change event");

searchInput.value = "deepseek";
searchInput.dispatchEvent(new FakeEvent("input"));
const chosenResult = results.children[0];
chosenResult.dispatchEvent(new FakeEvent("click"));
assert(select.value === "deepseek-reasoner", "choosing a result should update the native select value");
assert(changeCount === 1, "choosing a different result should dispatch exactly one change event");
assert(document.activeElement === trigger, "choosing a result should return focus to the model trigger");

chosenResult.dispatchEvent(new FakeEvent("click"));
assert(changeCount === 1, "choosing the already-selected result should not dispatch another change event");

const uiSource = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "ui.js"),
    "utf8"
);
const fallbackStart = uiSource.indexOf("function createSearchableModelPicker()");
const fallbackEnd = uiSource.indexOf("const searchableModelPicker", fallbackStart);
assert(fallbackStart >= 0 && fallbackEnd > fallbackStart, "model picker fallback source could not be located");

const fallbackSelect = document.createElement("select");
fallbackSelect.classList.add("model-select-native");
fallbackSelect.setAttribute("tabindex", "-1");
fallbackSelect.setAttribute("aria-hidden", "true");
const fallbackTrigger = document.createElement("button");
const fallbackContext = vm.createContext({
    window: {},
    modelSelect: fallbackSelect,
    modelPickerTrigger: fallbackTrigger,
    modelPickerValue: document.createElement("span"),
    modelPickerPanel: document.createElement("div"),
    modelSearchInput: document.createElement("input"),
    modelPickerResults: document.createElement("div"),
    modelPickerEmpty: document.createElement("div")
});
vm.runInContext(
    `${uiSource.slice(fallbackStart, fallbackEnd)}\ncreateSearchableModelPicker();`,
    fallbackContext,
    { filename: "ui-model-picker-fallback.js" }
);

assert(!fallbackSelect.classList.contains("model-select-native"), "fallback should reveal the native model select");
assert(fallbackSelect.classList.contains("select-menu"), "fallback should apply the standard select styling");
assert(fallbackSelect.getAttribute("tabindex") === null, "fallback should restore native select keyboard access");
assert(fallbackSelect.getAttribute("aria-hidden") === null, "fallback should expose the native select to assistive technology");
assert(fallbackTrigger.classList.contains("hidden"), "fallback should hide the unavailable picker trigger");

// Manually entered request IDs survive discovery refreshes and stay provider-scoped.
const listStart = uiSource.indexOf("function updateModelList(");
const listEnd = uiSource.indexOf("// 绑定模型列表更新", listStart);
let renderedModels;
const manualConfig = {
    active_platform: "custom:a", model: "private/model-v2",
    manual_model_ids: { "custom:a": ["private/model-v2", "remote"], "custom:b": ["other"] }
};
const listContext = vm.createContext({
    config: manualConfig, document, modelSelect: document.createElement("select"),
    searchableModelPicker: { setModels(models) { renderedModels = models; } },
    apiBridge: { save_config() { throw new Error("refresh must preserve the selected request ID"); } },
    currentConvId: null, window: {},
    setModelListStatus() { throw new Error("manual IDs should remain available without discovery"); }
});
vm.runInContext(uiSource.slice(listStart, listEnd), listContext);
listContext.updateModelList([{ id: "remote", display_name: "Remote" }]);
assert(renderedModels.filter(m => m.id === "remote").length === 1, "manual IDs must not duplicate discovery results");
assert(renderedModels.some(m => m.id === "private/model-v2"), "custom request ID must be retained exactly");
assert(!renderedModels.some(m => m.id === "other"), "manual IDs must not leak across providers");
listContext.updateModelList([]);
assert(renderedModels.some(m => m.id === "private/model-v2"), "empty discovery must retain custom IDs");
manualConfig.active_platform = "custom:b";
manualConfig.model = "other";
listContext.updateModelList([]);
assert(renderedModels.length === 1 && renderedModels[0].id === "other", "switching providers must restore their own IDs");

picker.setModels([{ id: "builtin" }, { id: "custom:a", label: "Provider A", group: "custom-providers" },
    { id: "custom:b", label: "Provider B", group: "custom-providers" }], "builtin");
searchInput.value = "";
picker.filter();
assert(results.children[1].style.borderTop === "1px solid var(--surface1)", "custom provider group must start with a divider");
assert(!results.children[2].style.borderTop, "divider must not repeat before each provider");

console.log("model picker regression tests passed");
