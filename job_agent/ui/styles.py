"""Shared visual theme for the Streamlit application."""

import streamlit as st


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #172033;
            --muted: #657084;
            --line: #E5E9F2;
            --brand: #405DE6;
            --brand-dark: #283EBA;
            --surface: rgba(255, 255, 255, 0.92);
        }
        .stApp {
            background:
                radial-gradient(circle at 86% 2%, rgba(90, 110, 230, .12), transparent 28rem),
                linear-gradient(180deg, #F8FAFF 0%, #FFFFFF 32rem);
            color: var(--ink);
        }
        .block-container {
            max-width: 1320px;
            padding-top: 2.1rem;
            padding-bottom: 5rem;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #111A35 0%, #192444 100%);
            color: #EEF2FF;
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] h4,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] > p {
            color: #EEF2FF;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            opacity: 1 !important;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
            color: #B8C3DC;
        }
        [data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {
            background: rgba(255,255,255,.09) !important;
            border-color: rgba(255,255,255,.18) !important;
            color: #F4F6FF !important;
        }
        [data-testid="stSidebar"] .stButton > button:not([kind="primary"]) * {
            color: #F4F6FF !important;
        }
        [data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover {
            background: rgba(255,255,255,.16) !important;
        }
        [data-testid="stSidebar"] [data-baseweb="select"] > div {
            background: #FFFFFF !important;
            border-color: #DDE3EF !important;
        }
        [data-testid="stSidebar"] [data-baseweb="select"] input,
        [data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"] {
            color: #263047 !important;
            -webkit-text-fill-color: #263047 !important;
        }
        [data-testid="stSidebar"] [data-baseweb="select"] input::placeholder {
            color: #748096 !important;
            -webkit-text-fill-color: #748096 !important;
            opacity: 1;
        }
        [data-testid="stSidebar"] [data-baseweb="select"] svg {
            color: #59657D !important;
            fill: currentColor !important;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [role="group"] {
            background: #FFFFFF !important;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] input[role="combobox"] {
            color: #263047 !important;
            -webkit-text-fill-color: #263047 !important;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] input[role="combobox"]::placeholder {
            color: #748096 !important;
            -webkit-text-fill-color: #748096 !important;
            opacity: 1;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] button,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] svg {
            color: #59657D !important;
            fill: currentColor !important;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] {
            background: rgba(255,255,255,.055);
            border-color: rgba(255,255,255,.12);
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] > details > summary {
            background: transparent !important;
            color: #F7F9FF !important;
            transition: background-color .16s ease, color .16s ease;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] > details > summary:hover {
            background: rgba(255,255,255,.07) !important;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] > details[open] > summary {
            background: rgba(64,93,230,.23) !important;
            color: #FFFFFF !important;
            border-bottom: 1px solid rgba(255,255,255,.1);
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] > details > summary:focus-visible {
            outline: 2px solid #8EA0FF;
            outline-offset: -2px;
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] > details > summary * {
            color: inherit !important;
            fill: currentColor !important;
        }
        .hero {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 2rem;
            padding: 1.35rem 1.5rem;
            margin-bottom: 1.25rem;
            border: 1px solid rgba(82, 102, 210, .16);
            border-radius: 22px;
            background: linear-gradient(120deg, rgba(255,255,255,.98), rgba(240,243,255,.93));
            box-shadow: 0 16px 45px rgba(38, 52, 105, .08);
        }
        .hero-kicker, .section-kicker {
            color: var(--brand);
            font-size: .75rem;
            font-weight: 800;
            letter-spacing: .13em;
            text-transform: uppercase;
        }
        .hero h1 {
            color: var(--ink);
            font-size: clamp(2rem, 4vw, 3.15rem);
            line-height: 1.05;
            letter-spacing: -.045em;
            margin: .42rem 0 .55rem;
        }
        .hero p { color: var(--muted); margin: 0; font-size: 1rem; }
        .hero-badge {
            white-space: nowrap;
            padding: .55rem .85rem;
            color: #2841B7;
            background: #E8ECFF;
            border-radius: 999px;
            font-weight: 700;
            font-size: .83rem;
        }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .8rem;
            margin: .65rem 0 1rem;
        }
        .summary-card {
            padding: .95rem 1rem;
            border: 1px solid var(--line);
            border-radius: 16px;
            background: var(--surface);
            box-shadow: 0 8px 26px rgba(25, 39, 81, .045);
        }
        .summary-card span { color: var(--muted); font-size: .78rem; }
        .summary-card strong { display: block; margin-top: .22rem; font-size: 1.45rem; color: var(--ink); }
        .input-guide {
            min-height: 178px;
            padding: 1rem;
            border-radius: 15px;
            color: #4B5670;
            background: #F1F4FF;
            border: 1px solid #E1E6FF;
            font-size: .88rem;
            line-height: 1.65;
        }
        .input-guide b { color: #263A9F; }
        .resume-steps {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: .7rem;
            margin: .55rem 0 1rem;
        }
        .resume-step {
            padding: .8rem .9rem;
            border-radius: 13px;
            border: 1px solid #E1E6F3;
            background: #F8FAFF;
            color: #566178;
            font-size: .84rem;
        }
        .resume-step b { display: block; color: #2942B9; margin-bottom: .2rem; }
        [data-testid="stTextArea"] textarea,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-baseweb="select"] input,
        [data-baseweb="select"] [role="combobox"] {
            border-radius: 12px;
            border-color: #DDE3EF !important;
            background: #FFFFFF !important;
            color: #263047 !important;
            -webkit-text-fill-color: #263047 !important;
            caret-color: #263047;
        }
        [data-testid="stTextArea"] textarea::placeholder,
        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stNumberInput"] input::placeholder,
        [data-testid="stDateInput"] input::placeholder,
        [data-baseweb="select"] input::placeholder {
            color: #67748B !important;
            -webkit-text-fill-color: #67748B !important;
            opacity: 1;
        }
        [data-baseweb="select"] [aria-selected],
        [data-baseweb="popover"] [role="option"],
        [data-baseweb="menu"] [role="option"] {
            color: #263047 !important;
            -webkit-text-fill-color: #263047 !important;
        }
        [data-testid="stTextArea"] textarea:focus,
        [data-testid="stTextInput"] input:focus {
            border-color: #6679E8;
            box-shadow: 0 0 0 1px #6679E8;
        }
        .stButton > button, [data-testid="stFormSubmitButton"] > button {
            border-radius: 11px;
            font-weight: 700;
            min-height: 2.65rem;
        }
        .stButton > button[kind="primary"] {
            border: 0;
            background: linear-gradient(115deg, var(--brand-dark), #6378F2);
            box-shadow: 0 8px 18px rgba(64, 93, 230, .22);
        }
        [data-testid="stExpander"] {
            border-color: var(--line);
            border-radius: 14px;
            background: rgba(255,255,255,.78);
            overflow: hidden;
        }
        [data-testid="stVegaLiteChart"] {
            padding: .9rem .6rem .35rem;
            border: 1px solid var(--line);
            border-radius: 18px;
            background: rgba(255,255,255,.88);
            box-shadow: 0 12px 32px rgba(25, 39, 81, .05);
        }
        h2, h3 { color: var(--ink); letter-spacing: -.02em; }
        @media (max-width: 760px) {
            .hero { align-items: flex-start; flex-direction: column; gap: .85rem; }
            .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .resume-steps { grid-template-columns: 1fr; }
            .block-container { padding-top: 1rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
