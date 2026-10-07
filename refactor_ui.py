import os
import re

html_path = "d:/28021/桌面/Learning_NOW/ai/Claude_chat/claude_chat/ui/index.html"

with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()

# I will use a simple Python script to do the string replacement to build the new UI structure
def remove_between(text, start, end):
    pattern = re.escape(start) + r'.*?' + re.escape(end)
    return re.sub(pattern, end, text, flags=re.DOTALL)

# Claude
html = remove_between(html, '<!-- Claude Parameters -->', '<!-- Claude Web Search -->')
# DeepSeek
html = remove_between(html, '<!-- DeepSeek Parameters -->', '<!-- DeepSeek Web Search -->')
# Gemini
html = remove_between(html, '<!-- Gemini Parameters -->', '<!-- Gemini Web Search Grounding -->')

# Now wait, I notice DeepSeek might not have <!-- DeepSeek Parameters -->
# Let's replace the raw form groups using regex
html = re.sub(r'<div class="form-group">\s*<label for="deepseek-temp-slider".*?</label>\s*<input type="range" id="deepseek-temp-slider".*?>\s*</div>', '', html, flags=re.DOTALL)
html = re.sub(r'<div class="form-group">\s*<label for="deepseek-max-tokens-input">Max Tokens:</label>\s*<input type="number" id="deepseek-max-tokens-input" value="4096">\s*</div>', '', html, flags=re.DOTALL)
# And deepseek doesn't have deepseek thinking mode in html, it's just temp and tokens.

new_model_settings_html = """
                <!-- Group 2: Model Specific Settings -->
                <div class="settings-section-card" id="model-specific-settings-card">
                    <div class="settings-section-title">
                        🧠 当前模型专属设置 (Per-Model Settings)
                        <span id="current-model-indicator" style="float: right; color: var(--blue); font-size: 13px;"></span>
                    </div>
                    
                    <div id="model-registry-info" style="margin-top: 10px; padding: 10px; background-color: var(--crust); border: 1px solid var(--surface0); border-radius: 6px; font-size: 12px; display: flex; gap: 15px; flex-wrap: wrap;">
                        <div>上下文长度: <span id="model-info-context" style="color: var(--green); font-weight: bold;">--</span></div>
                        <div>最大输出: <span id="model-info-output" style="color: var(--peach); font-weight: bold;">--</span></div>
                        <div id="model-info-reasoning-container">思考支持: <span id="model-info-reasoning" style="color: var(--mauve); font-weight: bold;">--</span></div>
                    </div>

                    <div class="form-group" style="margin-top: 15px;">
                        <label for="model-temp-slider" id="model-temp-label-title">Temperature: 0.70</label>
                        <input type="range" id="model-temp-slider" min="0.0" max="2.0" step="0.05" value="0.7">
                    </div>
                    
                    <div class="form-group">
                        <label for="model-max-tokens-input">Max Tokens:</label>
                        <input type="number" id="model-max-tokens-input" value="4096">
                    </div>

                    <!-- Extended Thinking (Dynamic) -->
                    <div class="form-group" style="margin-top: 15px; border-top: 1px dashed var(--surface0); padding-top: 10px;" id="model-thinking-container">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                            <input type="checkbox" id="model-thinking-enabled-input" style="cursor: pointer; width: 14px; height: 14px; accent-color: var(--blue);"> 
                            <span style="font-weight: 500;">启用思维模式 (Thinking Mode)</span>
                        </label>
                        
                        <div id="model-thinking-options" class="hidden" style="margin-top: 10px; padding-left: 22px;">
                            <div class="form-group" id="model-thinking-type-group">
                                <label>思维模式 (Thinking Type):</label>
                                <div class="radio-group" style="margin-bottom: 8px;">
                                    <label class="radio-label">
                                        <input type="radio" name="model-thinking-type" value="adaptive"> Adaptive (自适应)
                                    </label>
                                    <label class="radio-label">
                                        <input type="radio" name="model-thinking-type" value="enabled"> Enabled (强制开启)
                                    </label>
                                </div>
                            </div>
                            
                            <div class="form-group" id="model-thinking-budget-group">
                                <label for="model-thinking-budget-input">思维 Token 预算 (Thinking Budget):</label>
                                <input type="number" id="model-thinking-budget-input" value="1024">
                            </div>
                            
                            <div class="form-group" id="model-thinking-level-group">
                                <label for="model-thinking-level-select">思维深度等级 (Thinking Effort):</label>
                                <select id="model-thinking-level-select">
                                    <option value="low">Low (低)</option>
                                    <option value="medium">Medium (中)</option>
                                    <option value="high" selected>High (高)</option>
                                    <option value="xhigh">X-High (极高)</option>
                                    <option value="max">Max (最大)</option>
                                </select>
                            </div>
                        </div>
                    </div>
                </div>
"""

# Insert new settings card after Group 1
if "<!-- Group 1: Base Parameters & Platform Settings -->" in html:
    html = html.replace('<!-- Custom Provider Management -->', new_model_settings_html + '\n                <!-- Custom Provider Management -->')

with open("d:/28021/桌面/Learning_NOW/ai/Claude_chat/ui_refactored.html", "w", encoding="utf-8") as f:
    f.write(html)
print("done")
