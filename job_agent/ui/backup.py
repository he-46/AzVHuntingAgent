"""Local SQLite backup and restore controls."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import streamlit as st

import database


def render_backup_controls(db_path: str) -> None:
    with st.expander("数据备份与恢复", expanded=False):
        st.caption("备份包含简历、岗位和原始输入，请妥善保管下载的文件。")
        st.download_button(
            "下载完整备份",
            data=database.backup_database(db_path),
            file_name=f"求职时间线备份-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db",
            mime="application/vnd.sqlite3",
            use_container_width=True,
        )
        uploaded = st.file_uploader("恢复 SQLite 备份", type="db", key="restore_database_upload")
        if uploaded is None:
            return
        data = uploaded.getvalue()
        try:
            summary = database.inspect_backup(data)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.caption(f"备份包含 {summary['applications']} 个岗位、{summary['events']} 条事件。")
        confirmed = st.checkbox("确认用备份替换当前数据", key="confirm_database_restore")
        if st.button("恢复备份", disabled=not confirmed, use_container_width=True):
            try:
                database.restore_database(data, db_path)
            except (ValueError, OSError, sqlite3.DatabaseError) as exc:
                st.error(f"恢复失败：{exc}")
                return
            st.session_state.clear()
            st.session_state["flash_success"] = "备份已恢复。恢复前的数据已保存到 data/backups。"
            st.rerun()
