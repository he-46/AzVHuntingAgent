"""Session-scoped model and credential controls."""

from __future__ import annotations

import os

import streamlit as st

from job_agent.config import Settings
from job_agent.llm.client import LLMRuntimeConfig


_RUNTIME_KEY = "runtime_openai_api_key"
_COMPATIBLE_KEY = "runtime_compatible_api_key"
_PROVIDER_KEY = "llm_provider"
_OPENAI = "OpenAI"
_COMPATIBLE = "兼容接口"


def configured_llm() -> LLMRuntimeConfig:
    """Resolve the selected model without persisting secrets to disk."""
    provider = st.session_state.get(_PROVIDER_KEY, _OPENAI)
    if provider == _COMPATIBLE:
        return LLMRuntimeConfig(
            api_key=str(st.session_state.get(_COMPATIBLE_KEY) or "").strip() or None,
            model=str(st.session_state.get("llm_compatible_model") or "").strip(),
            api_style="chat_completions",
            base_url=str(st.session_state.get("llm_compatible_base_url") or "").strip(),
        )
    return LLMRuntimeConfig(
        api_key=(
            str(st.session_state.get(_RUNTIME_KEY) or os.environ.get("OPENAI_API_KEY", "")).strip()
            or None
        ),
        model=str(st.session_state.get("llm_openai_model", Settings.from_env().openai_model)).strip(),
    )


def configured_api_key() -> str | None:
    """Compatibility helper for callers that only need the selected key."""
    return configured_llm().api_key


def render_llm_settings() -> None:
    """Edit provider, model, endpoint and session-only API keys."""
    with st.expander("AI 服务配置", expanded=False):
        provider = st.selectbox("服务类型", [_OPENAI, _COMPATIBLE], key=_PROVIDER_KEY)
        if provider == _OPENAI:
            if "llm_openai_model" not in st.session_state:
                st.session_state["llm_openai_model"] = Settings.from_env().openai_model
            st.text_input("模型 ID", key="llm_openai_model", help="填写账号有权限调用的 OpenAI 模型 ID。")
            runtime_key = _RUNTIME_KEY
            environment_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
            st.caption("使用 OpenAI Responses 结构化输出。")
        else:
            if "llm_compatible_model" not in st.session_state:
                st.session_state["llm_compatible_model"] = ""
            if "llm_compatible_base_url" not in st.session_state:
                st.session_state["llm_compatible_base_url"] = ""
            st.text_input("模型 ID", key="llm_compatible_model", placeholder="服务商提供的模型 ID")
            st.text_input(
                "API Base URL", key="llm_compatible_base_url", placeholder="https://.../v1",
                help="填写兼容 OpenAI Chat Completions 的接口根地址；需要支持 JSON mode。",
            )
            runtime_key = _COMPATIBLE_KEY
            environment_configured = False
            st.caption("兼容接口使用 Chat Completions JSON mode；请确认模型支持该功能。")

        input_version_key = f"{provider}_api_key_input_version"
        input_version = int(st.session_state.get(input_version_key, 0))
        input_key = f"{provider}_api_key_input_{input_version}"
        session_configured = bool(str(st.session_state.get(runtime_key) or "").strip())
        if session_configured:
            st.success("当前会话已配置此服务的 API Key。")
        elif environment_configured:
            st.success("正在使用启动环境中的 OpenAI API Key。")
        else:
            st.warning("尚未配置此服务的 API Key，AI 分拣和简历生成暂不可用。")

        entered_key = st.text_input(
            "API Key",
            type="password",
            placeholder="仅当前会话使用",
            key=input_key,
            help="只保存在当前 Streamlit 会话内，不写入项目文件或数据库。",
        )
        apply_column, clear_column = st.columns(2)
        if apply_column.button("应用密钥", use_container_width=True, key=f"apply_{provider}_api_key"):
            if entered_key.strip():
                st.session_state[runtime_key] = entered_key.strip()
                st.session_state["api_key_notice"] = "API Key 已应用到当前会话。"
                st.rerun()
            else:
                st.warning("请先输入 API Key。")
        if clear_column.button("清除密钥", use_container_width=True, key=f"clear_{provider}_api_key"):
            st.session_state.pop(runtime_key, None)
            st.session_state.pop(input_key, None)
            st.session_state[input_version_key] = input_version + 1
            st.session_state["api_key_notice"] = "已清除当前会话中的 API Key。"
            st.rerun()

        notice = st.session_state.pop("api_key_notice", None)
        if notice:
            st.caption(notice)
        st.caption("模型切换立即生效。两个服务的会话密钥独立；本地用量限制合并计算。")
