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
    "registrado_por",
    "telefono_registro",
]


# ----------------------------
# Branding (LOGO + FONDO) ✅ (ya listo)
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

        /* Todo encima del overlay */
        main, header, section[data-testid="stSidebar"] {{
            position:relative;
            z-index:1;
        }}

        section[data-testid="stSidebar"] > div {{
            background: rgba(255,255,255,0.92);
            border-right: 1px solid rgba(0,0,0,0.06);
        }}

        /* Cards básicas */
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"] {{
            background: rgba(255,255,255,0.92);
            border-radius: 12px;
            padding: 10px;
            border: 1px solid rgba(0,0,0,0.06);
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
    col1, col2 = st.columns([1, 4])
    with col1:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
        else:
            st.caption("⚠️ Falta el logo en assets/logo.png")
    with col2:
        st.markdown(
            """
            <h1 style="margin:0;">Citas de Unidades</h1>
            <div style="opacity:0.7;">Plataforma de registro y control por turnos</div>
            """,
            unsafe_allow_html=True,
        )


# ----------------------------
# Google Sheets
# ----------------------------
@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def get_sheet():
    sheet_id = st.secrets.get("SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Falta SHEET_ID en Secrets.")
    gc = get_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(WORKSHEET_NAME)
    ensure_columns(ws)
    return ws


def ensure_columns(ws):
    headers = ws.row_values(1)
    if not headers:
        ws.append_row(COLUMNAS)
        return
    missing = [c for c in COLUMNAS if c not in headers]
    if missing:
        ws.update("A1", [headers + missing])


def read_all(ws):
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS)
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNAS]


def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()


def append_cita(ws, data):
    headers = ws.row_values(1)
    ws.append_row([data.get(h, "") for h in headers], value_input_option="USER_ENTERED")


# ----------------------------
# UI
# ----------------------------
header_brand()

if LOGO_PATH.exists():
    st.sidebar.image(str(LOGO_PATH), use_container_width=True)

st.sidebar.subheader("🔒 Admin")
admin_password = st.sidebar.text_input("Contraseña", type="password")
admin_ok = admin_password == st.secrets.get("ADMIN_PASSWORD", "")

tabs = ["🧑‍✈️ Registrar"]
if admin_ok:
    tabs += ["📊 Dashboard"]

tab = st.tabs(tabs)

ws = get_sheet()
df = read_all(ws)

# ----------------------------
# REGISTRO
# ----------------------------
with tab[0]:
    st.subheader("Registrar cita")

    fecha = st.date_input("Fecha")
    fecha_str = fecha.strftime("%Y-%m-%d")

    turno = st.selectbox("Turno", [t["turno"] for t in TURNOS_BASE])
    horario = next(t["horario"] for t in TURNOS_BASE if t["turno"] == turno)

    col1, col2 = st.columns(2)
    with col1:
        placa_tracto = st.text_input("Placa tracto *")
        placa_carreta = st.text_input("Placa carreta")
        chofer = st.text_input("Chofer *")
    with col2:
        transporte = st.text_input("Transporte")
        tipo = st.selectbox("Operación", ["Carga", "Descarga", "Importación", "Exportación"])
        obs = st.text_area("Observación")

    st.markdown("### Información de registro")
    registrado_por = st.text_input("Registrado por *")
    telefono = st.text_input("Teléfono (opcional)")

    if st.button("Generar ticket"):
        if not placa_tracto or not chofer or not registrado_por:
            st.error("Completa los campos obligatorios.")
        else:
            ticket = generar_ticket()
            data = {
                "id_ticket": ticket,
                "turno": turno,
                "horario_turno": horario,
                "placa_tracto": placa_tracto.strip().upper(),
                "placa_carreta": placa_carreta.strip().upper(),
                "chofer_nombre": chofer.strip(),
                "licencia": "",
                "transporte": transporte.strip(),
                "tipo_operacion": tipo,
                "fecha_cita": fecha_str,
                "hora_cita": "",
                "observacion": obs.strip(),
                "estado": "EN COLA",
                "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "registrado_por": registrado_por.strip(),
                "telefono_registro": telefono.strip(),
            }
            append_cita(ws, data)
            st.success(f"Ticket creado: {ticket}")

# ----------------------------
# DASHBOARD
# ----------------------------
if admin_ok:
    with tab[1]:
        st.subheader("Dashboard")

        if df.empty:
