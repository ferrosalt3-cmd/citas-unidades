import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import hashlib
import io

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="Citas de Unidades (por turnos)", layout="wide")

WORKSHEET_NAME = "citas"
ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]
TIPOS_OPERACION = ["Carga", "Descarga", "Importacion", "Exportacion"]

# Turnos fijos (puedes editar aquí)
TURNOS = [
    {"turno": "Turno 1", "horario": "08:00 - 10:00", "cupos": 4},
    {"turno": "Turno 2", "horario": "10:00 - 12:00", "cupos": 4},
    {"turno": "Turno 3", "horario": "13:00 - 15:00", "cupos": 4},
    {"turno": "Turno 4", "horario": "15:00 - 16:30", "cupos": 4},
]

# Columnas esperadas en Google Sheet
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


# -----------------------------
# GOOGLE SHEETS
# -----------------------------
@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet():
    sheet_id = st.secrets.get("SHEET_ID")
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
    # si faltan columnas, agregarlas
    missing = [c for c in COLUMNAS if c not in headers]
    if missing:
        new_headers = headers + missing
        ws.update("1:1", [new_headers])

def read_all(ws) -> pd.DataFrame:
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS)
    # asegurar columnas
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""
    return df

def add_row(ws, data: dict):
    headers = ws.row_values(1)
    row = [data.get(h, "") for h in headers]
    ws.append_row(row)

def update_estado_by_ticket(ws, ticket_id: str, nuevo_estado: str):
    headers = ws.row_values(1)
    col_estado = headers.index("estado") + 1

    cell = ws.find(ticket_id)
    ws.update_cell(cell.row, col_estado, nuevo_estado)

def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()


# -----------------------------
# FECHAS (SEMANA LUNES-SÁBADO)
# -----------------------------
def get_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

def is_monday_to_saturday(d: date) -> bool:
    return 0 <= d.weekday() <= 5  # 0=Lunes, 5=Sábado


# -----------------------------
# CUPOS POR TURNO
# -----------------------------
def cupos_disponibles(df: pd.DataFrame, fecha: date, turno: str) -> int:
    if df.empty:
        usados = 0
    else:
        usados = df[
            (df["fecha_cita"] == str(fecha)) &
            (df["turno"] == turno) &
            (df["estado"] != "CANCELADO")
        ].shape[0]

    cupos_total = next(t["cupos"] for t in TURNOS if t["turno"] == turno)
    return max(cupos_total - usados, 0)

def turnos_para_fecha(df: pd.DataFrame, fecha: date):
    opciones = []
    for t in TURNOS:
        disp = cupos_disponibles(df, fecha, t["turno"])
        if disp > 0:
            opciones.append(f'{t["turno"]} ({t["horario"]}) — Cupos: {disp}')
    return opciones

def parse_turno_label(label: str):
    # "Turno 1 (08:00 - 10:00) — Cupos: 3"
    turno = label.split(" (")[0].strip()
    horario = label.split("(")[1].split(")")[0].strip()
    return turno, horario


# -----------------------------
# ADMIN
# -----------------------------
def admin_ok() -> bool:
    admin_pass = st.secrets.get("ADMIN_PASSWORD", "")
    if not admin_pass:
        st.info("Admin sin contraseña configurada en Secrets (ADMIN_PASSWORD).")
        return True

    entered = st.sidebar.text_input("Contraseña admin", type="password")
    if entered and entered == admin_pass:
        st.sidebar.success("Admin activado")
        return True

    if entered and entered != admin_pass:
        st.sidebar.error("Contraseña incorrecta")

    return False


# -----------------------------
# UI
# -----------------------------
st.title("🧾 Citas de Unidades (por turnos)")

ws = get_sheet()
df = read_all(ws)

# Sidebar
st.sidebar.markdown("### 🔐 Admin")
is_admin = admin_ok()

# Tabs: chofer siempre; admin solo si ok
tabs = ["🧑‍✈️ Registrar cita"]
if is_admin:
    tabs.append("📊 Panel admin")

selected_tab = st.tabs(tabs)

# -----------------------------
# TAB CHOFER
# -----------------------------
with selected_tab[0]:
    st.subheader("Registro (Chofer)")

    hoy = date.today()
    lunes = get_monday(hoy)
    sabado = lunes + timedelta(days=5)

    fecha = st.date_input(
        "Fecha (Semana Lunes–Sábado)",
        value=hoy,
        min_value=lunes,
        max_value=sabado,
    )

    if not is_monday_to_saturday(fecha):
        st.error("Solo se permite agendar de Lunes a Sábado.")
        st.stop()

    opciones_turno = turnos_para_fecha(df, fecha)

    if not opciones_turno:
        st.warning("No hay cupos disponibles para esa fecha. Elige otro día.")
        st.stop()

    turno_label = st.selectbox("Selecciona turno disponible", opciones_turno)
    turno, horario_turno = parse_turno_label(turno_label)

    col1, col2 = st.columns(2)

    with col1:
        placa_tracto = st.text_input("Placa tracto *")
        placa_carreta = st.text_input("Placa carreta")
        chofer = st.text_input("Chofer *")
        licencia = st.text_input("Licencia")

    with col2:
        transporte = st.text_input("Transporte")
        tipo = st.selectbox("Tipo de operación", TIPOS_OPERACION)
        obs = st.text_area("Observación")

    # Mostrar cupos restantes
    disp = cupos_disponibles(df, fecha, turno)
    st.info(f"✅ Quedan {disp} cupos en {turno} ({horario_turno}) para {fecha}")

    if st.button("✅ Generar ticket y registrar"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios.")
        else:
            # revalidar cupos justo antes de registrar
            df_refresh = read_all(ws)
            disp2 = cupos_disponibles(df_refresh, fecha, turno)
            if disp2 <= 0:
                st.error("Lo sentimos: ese turno se llenó. Selecciona otro.")
                st.stop()

            ticket = generar_ticket()
            data = {
                "id_ticket": ticket,
                "turno": turno,
                "horario_turno": horario_turno,
                "placa_tracto": placa_tracto.strip().upper(),
                "placa_carreta": placa_carreta.strip().upper(),
                "chofer_nombre": chofer.strip(),
                "licencia": licencia.strip(),
                "transporte": transporte.strip(),
                "tipo_operacion": tipo,
                "fecha_cita": str(fecha),
                # hora_cita: guardamos el inicio del turno como referencia
                "hora_cita": horario_turno.split("-")[0].strip(),
                "observacion": obs.strip(),
                "estado": "EN COLA",
                "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            add_row(ws, data)
            st.success(f"🎫 Ticket creado: {ticket}")
            st.code(
                f"""TICKET: {ticket}
FECHA: {fecha}
TURNO: {turno} ({horario_turno})
PLACA TRACTO: {data["placa_tracto"]}
PLACA CARRETA: {data["placa_carreta"]}
CHOFER: {data["chofer_nombre"]}
OPERACIÓN: {tipo}
ESTADO: EN COLA
""",
                language="text",
            )

            st.info("📌 El ticket se muestra en pantalla. Si deseas PDF/QR, lo agregamos luego.")


# -----------------------------
# TAB ADMIN
# -----------------------------
if is_admin:
    with selected_tab[1]:
        st.subheader("Panel de control (Admin)")

        # Recargar datos
        df = read_all(ws)

        # Convertir fecha
        if not df.empty and "fecha_cita" in df.columns:
            df["fecha_cita"] = pd.to_datetime(df["fecha_cita"], errors="coerce")

        # ---------- KPI ----------
        st.markdown("### Resumen rápido")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("EN COLA", int((df["estado"] == "EN COLA").sum()) if not df.empty else 0)
        c2.metric("EN PROCESO", int((df["estado"] == "EN PROCESO").sum()) if not df.empty else 0)
        c3.metric("ATENDIDO", int((df["estado"] == "ATENDIDO").sum()) if not df.empty else 0)
        c4.metric("CANCELADO", int((df["estado"] == "CANCELADO").sum()) if not df.empty else 0)

        st.divider()

        # ---------- Filtros ----------
        st.markdown("### Filtros")
        f1, f2, f3 = st.columns(3)

        with f1:
            filtro_fecha = st.date_input("Filtrar fecha", value=None)

        with f2:
            filtro_estado = st.selectbox("Filtrar estado", ["(Todos)"] + ESTADOS, index=0)

        with f3:
            filtro_turno = st.selectbox("Filtrar turno", ["(Todos)"] + [t["turno"] for t in TURNOS], index=0)

        df_view = df.copy()

        if filtro_fecha:
            df_view = df_view[df_view["fecha_cita"].dt.date == filtro_fecha]

        if filtro_estado != "(Todos)":
            df_view = df_view[df_view["estado"] == filtro_estado]

        if filtro_turno != "(Todos)":
            df_view = df_view[df_view["turno"] == filtro_turno]

        # ---------- Tabla editable (solo estado) ----------
        st.markdown("### Gestión rápida (cambiar estado con click)")

        # Para editar, necesitamos fecha como string para no romper el editor
        df_edit = df_view.copy()
        if "fecha_cita" in df_edit.columns:
            df_edit["fecha_cita"] = df_edit["fecha_cita"].dt.strftime("%Y-%m-%d")

        edited_df = st.data_editor(
            df_edit,
            column_config={
                "estado": st.column_config.SelectboxColumn(
                    "Estado",
                    options=ESTADOS,
                    required=True,
                )
            },
            disabled=[c for c in df_edit.columns if c != "estado"],
            use_container_width=True,
            hide_index=True,
            key="editor_estados",
        )

        if st.button("💾 Guardar cambios de estado"):
            try:
                for _, row in edited_df.iterrows():
                    update_estado_by_ticket(ws, row["id_ticket"], row["estado"])
                st.success("✅ Estados actualizados correctamente")
            except Exception as e:
                st.error("Error guardando estados. Revisa si el ticket existe.")
                st.exception(e)

        st.divider()

        # ---------- Dashboard / Gráficos ----------
        st.markdown("### Dashboard (para gerencia)")

        # A) Estado actual (barras)
        st.markdown("**Estado actual (cantidad)**")
        if df.empty:
            st.info("Aún no hay registros.")
        else:
            estado_count = df["estado"].value_counts().reindex(ESTADOS, fill_value=0)
            st.bar_chart(estado_count)

        # B) Unidades por día (semana Lunes–Sábado)
        st.markdown("**Unidades por día (Lunes–Sábado)**")
        if not df.empty and df["fecha_cita"].notna().any():
            df2 = df.copy()
            df2 = df2[df2["fecha_cita"].notna()]
            df2 = df2[(df2["fecha_cita"].dt.weekday >= 0) & (df2["fecha_cita"].dt.weekday <= 5)]
            por_dia = df2.groupby(df2["fecha_cita"].dt.date).size()
            st.line_chart(por_dia)
        else:
            st.info("Sin fechas válidas para graficar.")

        # C) Ocupación por turno
        st.markdown("**Ocupación por turno**")
        if not df.empty:
            turnos_count = df.groupby("turno").size()
            st.bar_chart(turnos_count)

        st.divider()

        # ---------- Descargar reporte Excel ----------
        st.markdown("### 📥 Descargar reporte (Excel)")

        df_export = df.copy()
        if "fecha_cita" in df_export.columns:
            df_export["fecha_cita"] = pd.to_datetime(df_export["fecha_cita"], errors="coerce").dt.strftime("%Y-%m-%d")

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_export.to_excel(writer, index=False, sheet_name="Citas")

        st.download_button(
            label="📊 Descargar reporte .xlsx",
            data=buffer.getvalue(),
            file_name="reporte_citas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
