import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import hashlib
from io import BytesIO

import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas

st.set_page_config(page_title="Citas de Unidades", layout="wide")

WORKSHEET_NAME = "citas"
ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]

TURNOS = [
    ("Turno 1", "08:00 - 10:00"),
    ("Turno 2", "10:00 - 12:00"),
    ("Turno 3", "13:00 - 15:00"),
    ("Turno 4", "15:00 - 16:30"),
]
CUPO_POR_TURNO = 4

COLUMNAS_BASE = [
    "id_ticket",
    "placa_tracto",
    "placa_carreta",
    "chofer_nombre",
    "licencia",
    "transporte",
    "tipo_operacion",
    "fecha_cita",
    "turno",
    "horario_turno",
    "observacion",
    "estado",
    "creado_en",
]

@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def ensure_columns(ws):
    headers = ws.row_values(1)
    if not headers:
        ws.update("1:1", [COLUMNAS_BASE])
        return
    missing = [c for c in COLUMNAS_BASE if c not in headers]
    if missing:
        ws.update("1:1", [headers + missing])

def get_sheet():
    gc = get_client()
    sheet_id = st.secrets.get("SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("Falta SHEET_ID en Secrets.")
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(WORKSHEET_NAME)
    ensure_columns(ws)
    return ws

def read_all(ws) -> pd.DataFrame:
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS_BASE)
    for c in COLUMNAS_BASE:
        if c not in df.columns:
            df[c] = ""
    return df

def append_row(ws, row_dict: dict):
    headers = ws.row_values(1)
    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")

def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()

def admin_ok() -> bool:
    admin_pwd = st.secrets.get("ADMIN_PASSWORD", "")
    if not admin_pwd:
        return False
    return st.session_state.get("admin_pwd", "") == admin_pwd

def turnos_disponibles(df: pd.DataFrame, fecha_iso: str):
    dff = df.copy()
    if not dff.empty:
        dff = dff[dff["fecha_cita"].astype(str) == fecha_iso]
        dff = dff[dff["estado"].astype(str) != "CANCELADO"]
    conteo = dff.groupby("turno").size().to_dict() if not dff.empty else {}

    disponibles = []
    for nombre, horario in TURNOS:
        usados = int(conteo.get(nombre, 0))
        restantes = CUPO_POR_TURNO - usados
        if restantes > 0:
            disponibles.append((nombre, horario, restantes))
    return disponibles

def pdf_ticket_bytes(row: dict) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A6)
    w, h = A6

    y = h - 20
    c.setFont("Helvetica-Bold", 12)
    c.drawString(14, y, "TICKET - Citas de Unidades")
    y -= 18

    c.setFont("Helvetica", 10)
    lines = [
        f"Ticket: {row['id_ticket']}",
        f"Fecha: {row['fecha_cita']}",
        f"Turno: {row['turno']} ({row['horario_turno']})",
        f"Placa tracto: {row['placa_tracto']}",
        f"Placa carreta: {row['placa_carreta']}",
        f"Chofer: {row['chofer_nombre']}",
        f"Tipo: {row['tipo_operacion']}",
        f"Estado: {row['estado']}",
        f"Creado: {row['creado_en']}",
    ]
    obs = row.get("observacion", "").strip()
    if obs:
        lines.append("Obs: " + (obs[:45]))
        if len(obs) > 45:
            lines.append("     " + obs[45:90])

    for ln in lines:
        c.drawString(14, y, ln)
        y -= 14

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(14, 14, "Presentar este ticket al llegar.")
    c.showPage()
    c.save()

    buf.seek(0)
    return buf.read()

st.title("🧾 Citas de Unidades (por turnos)")

with st.sidebar:
    st.markdown("### 🔐 Admin")
    st.text_input("Contraseña admin", type="password", key="admin_pwd")
    if admin_ok():
        st.success("Admin activado")
    else:
        st.caption("Choferes: no usar.")

ws = get_sheet()
df = read_all(ws)

tabs = ["📲 Registrar cita"]
if admin_ok():
    tabs.append("📊 Panel admin")

tab_objs = st.tabs(tabs)

# ===== TAB CHOFER =====
with tab_objs[0]:
    st.subheader("Registro (Chofer)")

    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    sabado = lunes + timedelta(days=5)

    fecha = st.date_input(
        "Fecha (Semana Lunes–Sábado)",
        min_value=lunes,
        max_value=sabado,
        value=hoy if (lunes <= hoy <= sabado) else lunes
    )
    fecha_iso = fecha.isoformat()

    disp = turnos_disponibles(df, fecha_iso)
    if not disp:
        st.error("No hay turnos disponibles para esa fecha. Elige otro día.")
    else:
        opciones = [f"{n} ({h}) — Cupos: {r}" for n, h, r in disp]
        idx = st.selectbox("Selecciona turno disponible", list(range(len(opciones))), format_func=lambda i: opciones[i])
        turno_sel, horario_sel, restantes_sel = disp[idx]

        c1, c2 = st.columns(2)
        with c1:
            placa_tracto = st.text_input("Placa tracto *")
            placa_carreta = st.text_input("Placa carreta")
            chofer = st.text_input("Chofer *")
            licencia = st.text_input("Licencia")
        with c2:
            transporte = st.text_input("Transporte")
            tipo = st.selectbox("Tipo de operación", ["Carga", "Descarga", "Importacion", "Exportacion"])
            obs = st.text_area("Observación", height=120)

        st.info(f"✅ Quedan **{restantes_sel}** cupos en {turno_sel} ({horario_sel}) para {fecha_iso}")

        if st.button("✅ Generar ticket y registrar"):
            if not placa_tracto.strip() or not chofer.strip():
                st.error("Placa tracto y chofer son obligatorios.")
            else:
                df2 = read_all(ws)
                disp2 = turnos_disponibles(df2, fecha_iso)
                ok_turno = [d for d in disp2 if d[0] == turno_sel]
                if not ok_turno:
                    st.error("Ese turno ya se llenó. Elige otro turno/fecha.")
                else:
                    ticket = generar_ticket()
                    row = {
                        "id_ticket": ticket,
                        "placa_tracto": placa_tracto.strip().upper(),
                        "placa_carreta": placa_carreta.strip().upper(),
                        "chofer_nombre": chofer.strip(),
                        "licencia": licencia.strip(),
                        "transporte": transporte.strip(),
                        "tipo_operacion": tipo,
                        "fecha_cita": fecha_iso,
                        "turno": turno_sel,
                        "horario_turno": horario_sel,
                        "observacion": obs.strip(),
                        "estado": "EN COLA",
                        "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    append_row(ws, row)

                    st.success(f"✅ Ticket creado: {ticket}")
                    pdf_bytes = pdf_ticket_bytes(row)
                    st.download_button(
                        "⬇️ Descargar ticket (PDF)",
                        pdf_bytes,
                        file_name=f"{ticket}.pdf",
                        mime="application/pdf",
                    )

# ===== TAB ADMIN =====
if admin_ok():
    with tab_objs[1]:
        st.subheader("Panel de control (Admin)")

        df = read_all(ws)

        c1, c2, c3 = st.columns(3)
        with c1:
            f_fecha = st.date_input("Filtrar fecha (opcional)", value=None)
        with c2:
            f_estado = st.selectbox("Filtrar estado", ["(Todos)"] + ESTADOS)
        with c3:
            f_turno = st.selectbox("Filtrar turno", ["(Todos)"] + [t[0] for t in TURNOS])

        dff = df.copy()
        if f_fecha:
            dff = dff[dff["fecha_cita"].astype(str) == f_fecha.isoformat()]
        if f_estado != "(Todos)":
            dff = dff[dff["estado"].astype(str) == f_estado]
        if f_turno != "(Todos)":
            dff = dff[dff["turno"].astype(str) == f_turno]

        st.dataframe(dff.sort_values(["fecha_cita", "turno"]), use_container_width=True, height=420)

        csv_bytes = dff.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Descargar reporte (CSV)",
            csv_bytes,
            file_name="reporte_citas.csv",
            mime="text/csv",
        )

        st.markdown("### 📈 Gráficos (clic por estado)")

        df_plot = df.copy()
        df_plot["fecha_cita"] = pd.to_datetime(df_plot["fecha_cita"], errors="coerce").dt.date

        hoy = date.today()
        lunes = hoy - timedelta(days=hoy.weekday())
        sabado = lunes + timedelta(days=5)

        colA, colB = st.columns(2)
        with colA:
            r_ini = st.date_input("Desde", value=lunes, key="r_ini")
        with colB:
            r_fin = st.date_input("Hasta", value=sabado, key="r_fin")

        df_plot = df_plot[(df_plot["fecha_cita"] >= r_ini) & (df_plot["fecha_cita"] <= r_fin)]

        estado_click = st.radio(
            "Ver gráfico por estado:",
            ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO", "TODOS"],
            horizontal=True,
        )

        if estado_click != "TODOS":
            df_estado = df_plot[df_plot["estado"].astype(str) == estado_click]
        else:
            df_estado = df_plot.copy()

        if df_estado.empty:
            st.info("No hay datos para ese filtro.")
        else:
            if estado_click == "TODOS":
                por_dia = df_plot.groupby(["fecha_cita", "estado"]).size().reset_index(name="cantidad")
                piv = por_dia.pivot(index="fecha_cita", columns="estado", values="cantidad").fillna(0).sort_index()
                fig = plt.figure()
                for col in piv.columns:
                    plt.plot(piv.index, piv[col].values, marker="o", label=col)
                plt.xticks(rotation=45)
                plt.title("Citas por día (todos los estados)")
                plt.xlabel("Fecha")
                plt.ylabel("Cantidad")
                plt.legend()
                st.pyplot(fig)
            else:
                por_dia_simple = df_estado.groupby("fecha_cita").size().sort_index()
                fig = plt.figure()
                plt.plot(por_dia_simple.index, por_dia_simple.values, marker="o")
                plt.xticks(rotation=45)
                plt.title(f"{estado_click} por día")
                plt.xlabel("Fecha")
                plt.ylabel("Cantidad")
                st.pyplot(fig)

        st.markdown("### Cambiar estado por Ticket")
        ticket = st.text_input("ID Ticket (ej: TKT-XXXXXXXX)")
        nuevo = st.selectbox("Nuevo estado", ESTADOS)

        if st.button("Actualizar estado"):
            if not ticket.strip():
                st.error("Escribe un ticket.")
            else:
                headers = ws.row_values(1)
                id_col = headers.index("id_ticket") + 1
                estado_col = headers.index("estado") + 1
                ids = ws.col_values(id_col)
                try:
                    row_idx = ids.index(ticket.strip().upper()) + 1
                    ws.update_cell(row_idx, estado_col, nuevo)
                    st.success("Estado actualizado.")
                    st.rerun()
                except ValueError:
                    st.error("No encontré ese ticket.")
