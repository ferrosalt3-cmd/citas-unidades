import time
from io import BytesIO
from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

import plotly.express as px
import plotly.graph_objects as go

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Citas de Unidades (por turnos)", layout="wide")

ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]
CAPACIDAD_POR_TURNO = 4

# Turnos (ajústalos si quieres)
TURNOS = [
    {"turno": "Turno 1", "horario_turno": "08:00 - 10:00"},
    {"turno": "Turno 2", "horario_turno": "10:00 - 12:00"},
    {"turno": "Turno 3", "horario_turno": "13:00 - 15:00"},
    {"turno": "Turno 4", "horario_turno": "15:00 - 16:30"},
]

WORKSHEET_NAME = "citas"

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
]


# =========================
# UTIL: semana Lun-Sáb
# =========================
def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def week_saturday(d: date) -> date:
    return week_monday(d) + timedelta(days=5)  # Saturday

def week_days_lun_sab(d: date):
    start = week_monday(d)
    return [start + timedelta(days=i) for i in range(6)]  # 0..5


# =========================
# RETRY WRAPPER (evita caídas por 429/503)
# =========================
def with_retry(fn, *args, **kwargs):
    delays = [0.8, 1.6, 3.2]  # reintentos
    last_err = None
    for i, delay in enumerate([0] + delays):
        if i > 0:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            last_err = e
    raise last_err


# =========================
# GOOGLE SHEETS
# =========================
@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_resource
def get_sheet():
    sheet_id = st.secrets.get("SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Falta SHEET_ID en Secrets.")
    gc = get_client()
    sh = with_retry(gc.open_by_key, sheet_id)
    ws = with_retry(sh.worksheet, WORKSHEET_NAME)
    ensure_columns(ws)
    return ws

def ensure_columns(ws):
    headers = with_retry(ws.row_values, 1)
    if not headers:
        with_retry(ws.append_row, COLUMNAS)
        return
    if headers != COLUMNAS:
        # Si hay headers diferentes, los reescribimos (sin borrar datos)
        with_retry(ws.update, "A1", [COLUMNAS])

@st.cache_data(ttl=10)
def read_all_df():
    ws = get_sheet()
    values = with_retry(ws.get_all_values)
    if len(values) < 2:
        df = pd.DataFrame(columns=COLUMNAS)
        return df

    headers = values[0]
    rows = values[1:]

    df = pd.DataFrame(rows, columns=headers)
    # guardamos número real de fila (para updates rápidos)
    df.insert(0, "__rownum__", range(2, 2 + len(df)))

    # normalizamos
    if "fecha_cita" in df.columns:
        df["fecha_cita"] = pd.to_datetime(df["fecha_cita"], errors="coerce").dt.date

    return df

def append_ticket(row_dict: dict):
    ws = get_sheet()
    # armamos la fila EXACTA en orden COLUMNAS
    row = [row_dict.get(col, "") for col in COLUMNAS]
    with_retry(ws.append_row, row)
    # refrescar cache
    read_all_df.clear()

def batch_update_estados(changes: list):
    """
    changes: list of tuples (rownum, new_estado)
    """
    if not changes:
        return
    ws = get_sheet()
    estado_col_idx = COLUMNAS.index("estado") + 1  # 1-based
    requests = []
    for rownum, new_estado in changes:
        a1 = gspread.utils.rowcol_to_a1(rownum, estado_col_idx)
        requests.append({"range": a1, "values": [[new_estado]]})
    with_retry(ws.batch_update, requests)
    read_all_df.clear()


# =========================
# TICKET ID + PDF
# =========================
def generar_ticket_id():
    # corto y legible
    return "TKT-" + datetime.now().strftime("%H%M%S") + "-" + str(int(time.time()))[-4:]

def build_ticket_pdf(ticket_data: dict) -> bytes:
    bio = BytesIO()
    c = canvas.Canvas(bio, pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, h - 60, "Ticket de Cita - Unidades")

    c.setFont("Helvetica", 11)
    y = h - 100
    lines = [
        f"TICKET: {ticket_data.get('id_ticket','')}",
        f"FECHA: {ticket_data.get('fecha_cita','')}",
        f"TURNO: {ticket_data.get('turno','')} ({ticket_data.get('horario_turno','')})",
        f"PLACA TRACTO: {ticket_data.get('placa_tracto','')}",
        f"PLACA CARRETA: {ticket_data.get('placa_carreta','')}",
        f"CHOFER: {ticket_data.get('chofer_nombre','')}",
        f"LICENCIA: {ticket_data.get('licencia','')}",
        f"TRANSPORTE: {ticket_data.get('transporte','')}",
        f"OPERACIÓN: {ticket_data.get('tipo_operacion','')}",
        f"OBS: {ticket_data.get('observacion','')}",
        f"ESTADO: {ticket_data.get('estado','')}",
        f"CREADO: {ticket_data.get('creado_en','')}",
    ]
    for line in lines:
        c.drawString(50, y, line)
        y -= 18

    c.showPage()
    c.save()
    return bio.getvalue()


# =========================
# UI
# =========================
st.title("🧾 Citas de Unidades (por turnos)")

admin_password = st.sidebar.text_input("Contraseña admin", type="password")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

admin_ok = bool(ADMIN_PASSWORD) and (admin_password == ADMIN_PASSWORD)
if not ADMIN_PASSWORD:
    st.sidebar.info("Configura ADMIN_PASSWORD en Secrets.")
if not admin_ok:
    st.sidebar.caption("Choferes: no usar.")

tabs = ["🧑‍✈️ Registrar cita"]
if admin_ok:
    tabs += ["🛠️ Panel admin", "📊 Dashboard gerencia"]

tab = st.tabs(tabs)


# =========================
# TAB 1 - REGISTRO CHOFER
# =========================
with tab[0]:
    st.subheader("Registro (Chofer)")

    hoy = date.today()
    lun = week_monday(hoy)
    sab = week_saturday(hoy)
    dias_semana = week_days_lun_sab(hoy)

    fecha = st.date_input(
        "Fecha (Semana Lunes–Sábado)",
        value=hoy if lun <= hoy <= sab else lun,
        min_value=lun,
        max_value=sab,
    )

    df = read_all_df()

    # disponibilidad por turno en esa fecha
    def cupos_disponibles(turno_name: str):
        if df.empty:
            usados = 0
        else:
            usados = df[
                (df["fecha_cita"] == fecha)
                & (df["turno"] == turno_name)
                & (df["estado"] != "CANCELADO")
            ].shape[0]
        return max(0, CAPACIDAD_POR_TURNO - usados)

    turnos_labels = []
    for t in TURNOS:
        cupos = cupos_disponibles(t["turno"])
        turnos_labels.append(f'{t["turno"]} ({t["horario_turno"]}) — Cupos: {cupos}')

    selected_label = st.selectbox("Selecciona turno disponible", turnos_labels)
    idx = turnos_labels.index(selected_label)
    turno_sel = TURNOS[idx]["turno"]
    horario_sel = TURNOS[idx]["horario_turno"]
    cupos = cupos_disponibles(turno_sel)

    c1, c2 = st.columns(2)
    with c1:
        placa_tracto = st.text_input("Placa tracto *")
        placa_carreta = st.text_input("Placa carreta")
        chofer = st.text_input("Chofer *")
        licencia = st.text_input("Licencia")
    with c2:
        transporte = st.text_input("Transporte")
        tipo = st.selectbox("Tipo de operación", ["Carga", "Descarga", "Importacion", "Exportacion"])
        obs = st.text_area("Observación")

    st.info(f"✅ Quedan **{cupos}** cupos en **{turno_sel}** ({horario_sel}) para **{fecha}**")

    if st.button("✅ Generar ticket y registrar", use_container_width=False):
        if cupos <= 0:
            st.error("Este turno ya no tiene cupos disponibles. Elige otro turno.")
        elif not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios.")
        else:
            ticket = generar_ticket_id()
            data = {
                "id_ticket": ticket,
                "turno": turno_sel,
                "horario_turno": horario_sel,
                "placa_tracto": placa_tracto.strip().upper(),
                "placa_carreta": placa_carreta.strip().upper(),
                "chofer_nombre": chofer.strip(),
                "licencia": licencia.strip(),
                "transporte": transporte.strip(),
                "tipo_operacion": tipo,
                "fecha_cita": str(fecha),
                "hora_cita": "",  # ya viene “implícito” por turno
                "observacion": obs.strip(),
                "estado": "EN COLA",
                "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            append_ticket(data)

            st.success(f"🎫 Ticket creado: {ticket}")

            # Mostrar ticket + descarga PDF
            st.code(
                "\n".join(
                    [
                        f"TICKET: {data['id_ticket']}",
                        f"FECHA: {data['fecha_cita']}",
                        f"TURNO: {data['turno']} ({data['horario_turno']})",
                        f"PLACA TRACTO: {data['placa_tracto']}",
                        f"PLACA CARRETA: {data['placa_carreta']}",
                        f"CHOFER: {data['chofer_nombre']}",
                        f"OPERACIÓN: {data['tipo_operacion']}",
                        f"ESTADO: {data['estado']}",
                    ]
                )
            )

            pdf_bytes = build_ticket_pdf(data)
            st.download_button(
                "⬇️ Descargar ticket (PDF)",
                data=pdf_bytes,
                file_name=f"{ticket}.pdf",
                mime="application/pdf",
            )


# =========================
# TAB 2 - PANEL ADMIN
# =========================
if admin_ok:
    with tab[1]:
        st.subheader("Panel de control (Admin)")

        df = read_all_df().copy()
        if df.empty:
            st.warning("No hay registros aún.")
            st.stop()

        # filtros
        colf1, colf2, colf3 = st.columns(3)
        with colf1:
            f_fecha = st.date_input("Filtrar fecha", value=None)
        with colf2:
            f_estado = st.selectbox("Filtrar estado", ["(Todos)"] + ESTADOS)
        with colf3:
            f_turno = st.selectbox("Filtrar turno", ["(Todos)"] + [t["turno"] for t in TURNOS])

        dff = df
        if f_fecha:
            dff = dff[dff["fecha_cita"] == f_fecha]
        if f_estado != "(Todos)":
            dff = dff[dff["estado"] == f_estado]
        if f_turno != "(Todos)":
            dff = dff[dff["turno"] == f_turno]

        st.markdown("### Gestión rápida (cambiar estado con click)")
        show_cols = [c for c in COLUMNAS if c != "hora_cita"]  # opcional
        table = dff[["__rownum__"] + show_cols].copy()

        # editor: SOLO estado editable
        edited = st.data_editor(
            table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "__rownum__": st.column_config.NumberColumn("Fila", disabled=True),
                "estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS),
            },
            disabled=[c for c in table.columns if c not in ["estado"]],
        )

        if st.button("💾 Guardar cambios de estado"):
            # detectar cambios comparando vs original
            changes = []
            merged = edited.merge(
                table[["__rownum__", "estado"]],
                on="__rownum__",
                suffixes=("_new", "_old"),
            )
            for _, r in merged.iterrows():
                if r["estado_new"] != r["estado_old"]:
                    changes.append((int(r["__rownum__"]), r["estado_new"]))
            if not changes:
                st.info("No hay cambios para guardar.")
            else:
                batch_update_estados(changes)
                st.success(f"Listo. Actualizados: {len(changes)}")
                st.rerun()

        # export excel
        st.markdown("### ⬇️ Descargar reporte (Excel)")
        export_df = dff[show_cols].copy()
        out = BytesIO()
        export_df.to_excel(out, index=False, sheet_name="reporte")
        st.download_button(
            "Descargar reporte .xlsx",
            data=out.getvalue(),
            file_name=f"reporte_citas_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# =========================
# TAB 3 - DASHBOARD GERENCIA
# =========================
if admin_ok:
    with tab[2]:
        st.subheader("Dashboard (Gerencia)")

        df = read_all_df().copy()
        if df.empty:
            st.warning("No hay registros para graficar.")
            st.stop()

        # Convertimos fecha a texto para plots
        df["fecha_str"] = df["fecha_cita"].astype(str)

        # KPIs
        k1, k2, k3, k4 = st.columns(4)
        total = len(df)
        k1.metric("Total", total)
        k2.metric("En cola", int((df["estado"] == "EN COLA").sum()))
        k3.metric("En proceso", int((df["estado"] == "EN PROCESO").sum()))
        k4.metric("Atendido", int((df["estado"] == "ATENDIDO").sum()))

        st.markdown("### Distribución de estados (rápido)")
        estado_counts = df["estado"].value_counts().reindex(ESTADOS).fillna(0).astype(int).reset_index()
        estado_counts.columns = ["estado", "cantidad"]

        fig_donut = px.pie(
            estado_counts,
            names="estado",
            values="cantidad",
            hole=0.5,
        )
        fig_donut.update_traces(textinfo="percent+label", pull=[0.03]*len(estado_counts))
        st.plotly_chart(fig_donut, use_container_width=True)

        st.markdown("### Unidades por día (Lunes–Sábado) y por estado")
        # mostramos solo semana actual (lun-sab) para gerencia
        hoy = date.today()
        dias = week_days_lun_sab(hoy)
        dias_str = [str(d) for d in dias]
        df_week = df[df["fecha_str"].isin(dias_str)].copy()

        if df_week.empty:
            st.info("Aún no hay datos en la semana actual (Lunes–Sábado).")
        else:
            grp = (
                df_week.groupby(["fecha_str", "estado"])
                .size()
                .reset_index(name="cantidad")
            )
            # asegurar orden de fechas y estados
            grp["fecha_str"] = pd.Categorical(grp["fecha_str"], categories=dias_str, ordered=True)
            grp["estado"] = pd.Categorical(grp["estado"], categories=ESTADOS, ordered=True)
            grp = grp.sort_values(["fecha_str", "estado"])

            fig_stacked = px.bar(
                grp,
                x="fecha_str",
                y="cantidad",
                color="estado",
                text="cantidad",
                barmode="stack",
                labels={"fecha_str": "Fecha", "cantidad": "Cantidad", "estado": "Estado"},
            )
            fig_stacked.update_traces(textposition="inside")
            st.plotly_chart(fig_stacked, use_container_width=True)

        st.markdown("### Operación por turno (semanal)")
        if not df_week.empty:
            turno_grp = (
                df_week.groupby(["turno"])
                .size()
                .reset_index(name="cantidad")
            )
            fig_turno = px.bar(
                turno_grp,
                x="turno",
                y="cantidad",
                text="cantidad",
                labels={"turno": "Turno", "cantidad": "Cantidad"},
            )
            fig_turno.update_traces(textposition="outside")
            st.plotly_chart(fig_turno, use_container_width=True)

        st.caption("✅ Todo el dashboard está en español y con etiquetas (data labels).")
