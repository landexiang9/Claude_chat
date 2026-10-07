import re

# 1. Update dom.js
dom_path = "d:/28021/桌面/Learning_NOW/ai/Claude_chat/claude_chat/ui/dom.js"
with open(dom_path, "r", encoding="utf-8") as f:
    dom = f.read()

old_vars = [
    "claudeTempSlider", "claudeTempLabelTitle", "claudeMaxTokensInput", "claudeBudgetTokensInput", "claudeThinkingLevelGroup", "claudeThinkingLevelSelect",
    "deepseekTempSlider", "deepseekTempLabelTitle", "deepseekMaxTokensInput",
    "geminiTempSlider", "geminiTempLabelTitle", "geminiMaxTokensInput", "geminiThinkingEnabledInput", "geminiBudgetTokensInput", "geminiThinkingLevelGroup", "geminiThinkingLevelSelect", "claudeBudgetGroup", "geminiBudgetGroup"
]
for var in old_vars:
    dom = re.sub(fr'const {var} = document.getElementById\(".*?"\);\n?', '', dom)

new_vars = """
const modelTempSlider = document.getElementById("model-temp-slider");
const modelTempLabelTitle = document.getElementById("model-temp-label-title");
const modelMaxTokensInput = document.getElementById("model-max-tokens-input");
const modelThinkingContainer = document.getElementById("model-thinking-container");
const modelThinkingEnabledInput = document.getElementById("model-thinking-enabled-input");
const modelThinkingOptions = document.getElementById("model-thinking-options");
const modelThinkingTypeGroup = document.getElementById("model-thinking-type-group");
const modelThinkingBudgetGroup = document.getElementById("model-thinking-budget-group");
const modelThinkingBudgetInput = document.getElementById("model-thinking-budget-input");
const modelThinkingLevelGroup = document.getElementById("model-thinking-level-group");
const modelThinkingLevelSelect = document.getElementById("model-thinking-level-select");
const modelInfoContext = document.getElementById("model-info-context");
const modelInfoOutput = document.getElementById("model-info-output");
const modelInfoReasoning = document.getElementById("model-info-reasoning");
const currentModelIndicator = document.getElementById("current-model-indicator");
"""

dom = dom + new_vars
with open("d:/28021/桌面/Learning_NOW/ai/Claude_chat/claude_chat/ui/dom.js", "w", encoding="utf-8") as f:
    f.write(dom)

print("dom.js updated")

