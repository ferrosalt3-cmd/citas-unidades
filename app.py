import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import hashlib
from io import BytesIO
import base64
from pathlib import Path

import plotly.express as px

# PDF (ticket)
from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas


# ----------------------------
# Config
# ----------------------------
st.set_page_config(page_title="Citas de Unidades (por turnos)", layout="wide")

WORKSHEET_NAME = "citas"

ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]

TURNOS_BASE = [
    {"turno": "Turno 1", "horario": "08:00 - 10:00", "capacidad": 4},
    {"turno": "Turno 2", "horario": "10:00 - 12:00", "capacidad": 4},
    {"turno": "Turno 3", "horario": "13:00 - 15:00", "capacidad": 4},
    {"turno": "Turno 4", "horario": "15:00 - 16:30", "capacidad": 4},
]

COLUMNAS = [
    "id_ticket",
    "turno",
    "horario_turno",
    "placa_tracto",
    "placa_carreta",
    "chofer_nombre",
    "licencia",
    "transporte",
    "tipo_operacion",
    "fecha_cita",
    "hora_cita",
    "observacion",
    "estado",
    "creado_en",
    # ✅ NUEVO (auditoría)
    "registrado_por",
    "telefono_registro",
]


# ----------------------------
# Branding (Logo + Fondo)
# ----------------------------
ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "logo.png"
FONDO_PATH = ASSETS_DIR / "fondo.png"


def _img_to_base64(path: Path) -> str:
    if not path.exists():
        return ""
    b = path.read_bytes()
    ext = path.suffix.lower().replace(".", "")
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64," + base64.b64encode(b).decode("utf-8")


logo_b64 = _img_to_base64(LOGO_PATH)
fondo_b64 = _img_to_base64(FONDO_PATH)

if fondo_b64:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: url("{fondo_b64}") no-repeat center center fixed;
            background-size: cover;
        }}

        /* Overlay suave para legibilidad */
        .stApp::before {{
            content:"";
            position:fixed;
            inset:0;
            background:rgba(255,255,255,0.80);
            z-index:0;
            pointer-events:none;
        }}

        /* Todo por encima del overlay */
        main, header, section[data-testid="stSidebar"] {{
            position:relative;
            z-index:1;
        }}

        /* Sidebar */
        section[data-testid="stSidebar"] > div {{
            background: rgba(255,255,255,0.92);
            border-right: 1px solid rgba(0,0,0,0.06);
        }}

        /* Cards */
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"] {{
            background: rgba(255,255,255,0.92);
            border: 1px solid rgba(0,0,0,0.06);
            border-radius: 12px;
            padding: 10px;
        }}

        /* Tabs */
        button[role="tab"] {{
            border-radius: 10px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def header_brand():
    colL, colR = st
