"""Session-scoped LLM credentials UI."""

from __future__ import annotations

import os

import streamlit as st


_RUNTIME_KEY = "runtime_openai_api_key"
_INPUT_VERSION_KEY = "openai_api_key_input_version"


def configured_api_key() -> str | None:
    """Return the session key first and the process environment key second."""
    session_key = str(st.session_state.get(_RUNTIME_KEY) or "").strip()
    environment_key = os.environ.get("OPENAI_API_KEY", "").strip()
    return session_key or environment_key or None


def render_llm_settings() -> None:
    """Let the user configure an API key without persisting it to disk."""
    with st.expander("AI 服务配置", expanded=False):
        input_version = int(st.session_state.get(_INPUT_VERSION_KEY, 0))
        input_key = f"openai_api_key_input_{input_version}"
        session_configured = bool(str(st.session_state.get(_RUNTIME_KEY) or "").strip())
        environment_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if session_configured:
            st.success("当前浏览器会话已配置 API Key。")
        elif environment_configured:
            st.success("正在使用启动环境中的 API Key。")
        else:
            st.warning("尚未配置 API Key，AI 分拣和简历生成暂不可用。")

        entered_key = st.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="sk-...",
            key=input_key,
            help="仅保存在当前 Streamlit 会话内，刷新连接或重启服务后可能需要重新输入。",
        )
        apply_column, clear_column = st.columns(2)
        if apply_column.button("应用", use_container_width=True, key="apply_openai_api_key"):
            if entered_key.strip():
                st.session_state[_RUNTIME_KEY] = entered_key.strip()
                st.session_state["api_key_notice"] = "API Key 已应用到当前会话。"
                st.rerun()
            else:
                st.warning("请先输入 API Key。")
        if clear_column.button("清除", use_container_width=True, key="clear_openai_api_key"):
            st.session_state.pop(_RUNTIME_KEY, None)
            st.session_state.pop(input_key, None)
            st.session_state[_INPUT_VERSION_KEY] = input_version + 1
            st.session_state["api_key_notice"] = "已清除当前会话中的 API Key。"
            st.rerun()

        notice = st.session_state.pop("api_key_notice", None)
        if notice:
            st.caption(notice)
        st.caption("密钥不会写入数据库、日志或项目文件。环境变量仍可作为备用配置。")
