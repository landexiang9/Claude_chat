import logging
from datetime import datetime
from copy import deepcopy
from claude_chat.request_params import preview_generation_params, validate_request_params

from claude_chat.config import DEFAULT_MAX_TOKENS, get_sensitive_api_keys
from claude_chat.custom_params import validate_custom_params
from claude_chat.platform_params import PlatformParamMapper
from claude_chat.sandbox import normalize_execution_timeout
from claude_chat.services.base import AppService

logger = logging.getLogger("claude_chat")


class ConfigService(AppService):
    """Configuration, parser capability, and application log operations."""

    def check_parsers(self):
        """
        检查本地可选解析依赖库安装状态
        """
        status = {
            "docx": False,
            "openpyxl": False,
            "pptx": False,
            "pypdf": False,
            "easyocr": False
        }
        try:
            import docx
            status["docx"] = True
        except ImportError: pass
        try:
            import openpyxl
            status["openpyxl"] = True
        except ImportError: pass
        try:
            import pptx
            status["pptx"] = True
        except ImportError: pass
        try:
            import pypdf
            status["pypdf"] = True
        except ImportError: pass
        try:
            import easyocr
            status["easyocr"] = True
        except ImportError: pass
        return status

    def preview_model_request(self, data):
        """Build a draft without saving settings, opening clients or making requests."""
        try:
            if not isinstance(data, dict):
                raise ValueError("参数预览请求必须是对象")
            model = data.get("model")
            platform = data.get("platform", "claude")
            if not isinstance(model, str) or not model:
                raise ValueError("请先选择模型")
            if not isinstance(platform, str) or not (platform in ("claude", "deepseek", "gemini") or platform.startswith("custom:")):
                raise ValueError("平台无效")
            with self._app.lock:
                draft = deepcopy(self._app.config.data)
            if "model_config" in data:
                if not isinstance(data["model_config"], dict):
                    raise ValueError("模型配置必须是对象")
                draft.setdefault("model_configs", {})[model] = deepcopy(data["model_config"])
            params = preview_generation_params(platform, model, draft)
            params = validate_request_params(params, platform)
            return {"params": params, "platform": platform, "model": model}
        except (ValueError, TypeError, AttributeError) as exc:
            return {"error": str(exc)}

    def get_config(self):
        """
        获取当前系统的全部配置字典数据，将敏感密钥替换为 has_xxx 标志返回给前端，保护密钥安全。
        """
        with self._app.lock:
            cfg = dict(self._app.config.data)
            api_keys = get_sensitive_api_keys(self._app.config.data)
            for key in api_keys:
                cfg[f"has_{key}"] = bool(cfg.get(key, "").strip())
                cfg[key] = ""
            return cfg

    def save_config(self, new_config):
        """
        更新并存储系统配置信息
        M6: 通过 config.set_many() 批量写入，持有 config._lock 保证线程安全，
        避免直接操作 config.data 字典时与并发 config.get() 产生竞态。
        """
        with self._app.lock:
            logger.info(f"保存系统配置，键名列表: {list(new_config.keys())}")

            try:
                for model_config in new_config.get("model_configs", {}).values():
                    validate_custom_params(model_config.get("custom_params", {}))
                    if "request_params" in model_config:
                        validate_request_params(model_config["request_params"], model_config.get("request_platform", "generic"))
            except (ValueError, TypeError, AttributeError):
                logger.warning("拒绝保存无效的模型自定义参数")
                return False

            sensitive_keys = get_sensitive_api_keys(self._app.config.data)

            # custom_providers 只能由专用 CRUD 端点管理，绝不允许 save_config 覆盖，
            # 否则前端旧快照会把后端刚新增的供应商整体清空。
            PROTECTED_KEYS = {"custom_providers"}

            # 1. 构造批量更新字典，跳过 has_xxx / clear_xxx 标志和受保护字段
            updates = {}
            for k, v in new_config.items():
                if k.startswith("has_") or k.startswith("clear_"):
                    continue
                if k in PROTECTED_KEYS:
                    continue
                # 对于敏感 Key 字段，仅当其有值时才保存；空值由 clear_xxx 逻辑处理，防止前端空值覆盖
                if k in sensitive_keys:
                    if v:
                        updates[k] = v
                else:
                    updates[k] = v
            if "code_sandbox_timeout" in updates:
                updates["code_sandbox_timeout"] = normalize_execution_timeout(
                    updates["code_sandbox_timeout"]
                )

            # security_token 不允许被保存为空:空 token 会让服务端拒绝所有 /api 请求(401),
            # 前端将永久卡在认证弹窗且只能重启恢复。空值一律忽略,保留原 token。
            if "security_token" in updates and not str(updates.get("security_token") or "").strip():
                del updates["security_token"]

            # 2. 显式处理清除敏感 Key 的请求
            for key in sensitive_keys:
                if new_config.get(f"clear_{key}"):
                    updates[key] = ""

            # Only changes that affect model discovery should trigger a network refresh.
            # A normal model selection or unrelated settings save must stay local/fast.
            changed_keys = {
                key for key, value in updates.items()
                if self._app.config.get(key) != value
            }

            # 通过 ConfigManager.set_many 一次性加锁写入 + 持久化
            if not self._app.config.set_many(updates):
                logger.error("配置文件写入失败，未应用本次前端配置更新")
                return False

            model_source_keys = {
                "api_key", "deepseek_api_key", "gemini_api_key",
                "deepseek_api_url", "gemini_api_url",
                "proxy_mode", "proxy_url", "model_configs",
            }
            custom_model_source_changed = any(
                key.startswith("custom_") and key.endswith("_api_key")
                for key in changed_keys
            )
            if changed_keys & model_source_keys or custom_model_source_changed:
                self._app._refresh_models_async()

            # 联调:如果存在活跃的对话且属于全局配置范畴,同步修改对话内置的模型参数以防数据割裂
            # M-fix#2:使用 save_conversation_metadata(仅 UPDATE)而非 save_conversation,
            # 后者会 INSERT OR REPLACE 触发 ON DELETE CASCADE + 显式 DELETE messages,
            # 与流式 reader 线程并发时会抹除刚写入的助手消息并重置 token 计数。
            if self._app.current_conv:
                conv_platform = updates.get("active_platform") or self._app.current_conv.get("platform") or "claude"
                conv_model = updates.get("model") or self._app.current_conv.get("model") or self._app.config.get("model")
                mapped = PlatformParamMapper.map_params(conv_platform, self._app.config.data, model_id=conv_model)

                self._app.current_conv["model"] = conv_model
                self._app.current_conv["platform"] = conv_platform
                self._app.current_conv["temperature"] = mapped.get("temperature", 0.7)
                self._app.current_conv["max_tokens"] = mapped.get("max_tokens", DEFAULT_MAX_TOKENS)

                # Persist the same model-aware thinking configuration used for generation.
                self._app.current_conv["thinking"] = None
                if mapped.get("thinking_config"):
                    self._app.current_conv["thinking"] = dict(mapped["thinking_config"])
                    if (mapped.get("output_config") or {}).get("effort"):
                        self._app.current_conv["thinking"]["effort"] = mapped["output_config"]["effort"]
                self._app.conv_manager.save_conversation_metadata(self._app.current_conv)
            return True

    def get_logs(self, max_lines=200):
        """
        读取本地最新的运行日志（反向读取最新 max_lines 行）
        """
        from claude_chat.config import LOG_PATH
        if not LOG_PATH.exists():
            return "暂无运行日志。"
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            last_lines = lines[-max_lines:] if len(lines) > max_lines else lines
            return "".join(last_lines)
        except Exception as e:
            logger.error(f"读取本地日志失败: {e}")
            return f"读取日志出错: {e}"

    def clear_logs(self):
        """
        清空日志内容
        """
        from claude_chat.config import LOG_PATH
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} - claude_chat - INFO - Log cleared by user.\n")
            logger.info("系统日志已被用户成功清除。")
            return True
        except Exception as e:
            logger.error(f"清空日志失败: {e}")
            return False
