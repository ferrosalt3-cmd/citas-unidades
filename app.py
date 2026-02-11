import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import hashlib

st.set_page_config(page_title="Citas de Unidades", layout="wide")

SHEET_NAME = "Citas Unidades DB"
WORKSHEET_NAME = "citas"

ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]

COLUMNAS = [
    "id_ticket",
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
]

@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet():
    gc = get_client()
    sh = gc.open(SHEET_NAME)
    ws = sh.worksheet(WORKSHEET_NAME)
    return ws

def add_row(ws, data):
    headers = ws.row_values(1)
    row = [data.get(h, "") for h in headers]
    ws.append_row(row)

def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()

st.title("🧾 Citas de Unidades")

tab1, tab2 = st.tabs(["📲 Registro Chofer", "📊 Panel"])

with tab1:
    st.subheader("Registrar cita")

    placa_tracto = st.text_input("Placa tracto *")
    placa_carreta = st.text_input("Placa carreta")
    chofer = st.text_input("Chofer *")
    licencia = st.text_input("Licencia")
    transporte = st.text_input("Transporte")
    tipo = st.selectbox("Tipo de operación", ["Carga", "Descarga", "Importacion", "Exportacion"])
    fecha = st.date_input("Fecha cita")
    hora = st.time_input("Hora cita")
    obs = st.text_area("Observación")

    if st.button("Generar ticket"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios")
        else:
            ws = get_sheet()
            ticket = generar_ticket()
            data = {
                "id_ticket": ticket,
                "placa_tracto": placa_tracto,
                "placa_carreta": placa_carreta,
                "chofer_nombre": chofer,
                "licencia": licencia,
                "transporte": transporte,
                "tipo_operacion": tipo,
                "fecha_cita": str(fecha),
                "hora_cita": str(hora),
                "observacion": obs,
                "estado": "EN COLA",
                "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            add_row(ws, data)
            st.success(f"Ticket creado: {ticket}")

with tab2:
    ws = get_sheet()
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)
