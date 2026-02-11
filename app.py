import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import hashlib
from io import BytesIO

import altair as alt
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# -----------------------------
# Config
# -----------------------------
st.set_page_config(page_title="Citas de Unidades (por turnos)", layout="wide")

WORKSHEET_NAME = "citas"

ESTADOS = ["EN COLA", "EN PROCESO", "ATENDIDO", "CANCELADO"]
TIPOS_OPERACION = ["Carga", "Descarga", "Importacion", "Exportacion"]

# Turnos fijos (edítalos aquí si cambian)
TURNOS = [
    {"turno": "Turno 1", "horario": "08:00 - 10:00"},
    {"turno": "Turno 2", "horario": "10:00 - 12:00"},
    {"turno": "Turno 3", "horario": "13:00 - 15:00"},
    {"turno": "Turno 4", "horario": "15:00 - 16:30"},
]
CUPOS_POR_TURNO = 4

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
# Google Sheets
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
        raise RuntimeError("Falta SHEET_ID en Secrets (fuera de [gcp_service_account]).")

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

    # si faltan columnas, las agregamos al final
    missing = [c for c in COLUMNAS if c not in headers]
    if missing:
        new_headers = headers + missing
        ws.update("1:1", [new_headers])


def read_all(ws) -> pd.DataFrame:
    rows = ws.get_all_records()
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS)

    # asegurar columnas
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""

    # parse fechas
    if "fecha_cita" in df.columns:
        df["fecha_cita_dt"] = pd.to_datetime(df["fecha_cita"], errors="coerce").dt.date
    else:
        df["fecha_cita_dt"] = pd.NaT

    return df


def append_row(ws, data: dict):
    headers = ws.row_values(1)
    row = [data.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")


def update_estado_by_ticket(ws, ticket_id: str, nuevo_estado: str) -> bool:
    # Busca ticket en la columna A (id_ticket)
    try:
        cell = ws.find(ticket_id)
        row_idx = cell.row
    except Exception:
        return False

    headers = ws.row_values(1)
    if "estado" not in headers:
        return False

    col_estado = headers.index("estado") + 1
    ws.update_cell(row_idx, col_estado, nuevo_estado)
    return True


def update_estado_batch(ws, cambios: dict):
    """
    cambios: {ticket_id: nuevo_estado}
    """
    if not cambios:
        return

    headers = ws.row_values(1)
    col_estado = headers.index("estado") + 1

    # mapeo ticket->fila
    all_ids = ws.col_values(1)  # id_ticket columna A
    id_to_row = {}
    for i, val in enumerate(all_ids, start=1):
        if val and val != "id_ticket":
            id_to_row[val] = i

    updates = []
    for tid, est in cambios.items():
        r = id_to_row.get(tid)
        if r:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, col_estado), "values": [[est]]})

    if updates:
        ws.batch_update(updates)


# -----------------------------
# Helpers
# -----------------------------
def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()


def semana_lunes_sabado(fecha: date):
    # lunes = 0
    lunes = fecha - timedelta(days=fecha.weekday())
    sabado = lunes + timedelta(days=5)
    return lunes, sabado


def cupos_disponibles(df: pd.DataFrame, fecha: date):
    # cuenta cuántos registros ya hay por turno en esa fecha (excepto cancelados si quieres permitir reuso)
    dfd = df[df["fecha_cita_dt"] == fecha].copy()
    # si quieres que CANCELADO libere cupo, descomenta:
    dfd = dfd[dfd["estado"] != "CANCELADO"]

    usados = dfd.groupby("turno")["id_ticket"].count().to_dict()
    disponibles = []
    for t in TURNOS:
        turno = t["turno"]
        usados_turno = int(usados.get(turno, 0))
        disp = max(CUPOS_POR_TURNO - usados_turno, 0)
        disponibles.append({"turno": turno, "horario": t["horario"], "disponibles": disp})
    return disponibles


def build_ticket_pdf(ticket_data: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 60
    c.setFont("Helvetica-Bold", 18)
    c.drawString(60, y, "TICKET DE CITA - UNIDADES")
    y -= 30

    c.setFont("Helvetica", 12)
    items = [
        ("Ticket", ticket_data.get("id_ticket", "")),
        ("Fecha", ticket_data.get("fecha_cita", "")),
        ("Turno", f'{ticket_data.get("turno","")} ({ticket_data.get("horario_turno","")})'),
        ("Placa tracto", ticket_data.get("placa_tracto", "")),
        ("Placa carreta", ticket_data.get("placa_carreta", "")),
        ("Chofer", ticket_data.get("chofer_nombre", "")),
        ("Licencia", ticket_data.get("licencia", "")),
        ("Transporte", ticket_data.get("transporte", "")),
        ("Operación", ticket_data.get("tipo_operacion", "")),
        ("Estado", ticket_data.get("estado", "")),
        ("Creado", ticket_data.get("creado_en", "")),
    ]

    for k, v in items:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(60, y, f"{k}:")
        c.setFont("Helvetica", 11)
        c.drawString(170, y, str(v))
        y -= 18
        if y < 80:
            c.showPage()
            y = height - 60

    c.setFont("Helvetica-Oblique", 10)
    c.drawString(60, 60, "Presentar este ticket al llegar a la instalación.")
    c.save()
    return buffer.getvalue()


def admin_ok() -> bool:
    admin_pw = st.secrets.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        st.sidebar.warning("Falta ADMIN_PASSWORD en Secrets.")
        return False

    pw = st.sidebar.text_input("Contraseña admin", type="password")
    if not pw:
        st.sidebar.caption("Choferes: no usar.")
        return False
    if pw == admin_pw:
        st.sidebar.success("Admin activado")
        return True
    st.sidebar.error("Clave incorrecta")
    return False


def export_excel(df: pd.DataFrame) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="reporte")
    return out.getvalue()


# -----------------------------
# UI
# -----------------------------
st.title("🧾 Citas de Unidades (por turnos)")

ws = get_sheet()
df = read_all(ws)

# Tabs: Chofer siempre / Admin solo si clave correcta
admin_enabled = admin_ok()

tabs = ["🧍 Registrar cita"]
if admin_enabled:
    tabs.append("🛠 Panel admin")
    tabs.append("📊 Dashboard gerencia")

tab_objs = st.tabs(tabs)

# -----------------------------
# TAB 1: Registro Chofer
# -----------------------------
with tab_objs[0]:
    st.subheader("Registro (Chofer)")

    hoy = date.today()
    lunes, sabado = semana_lunes_sabado(hoy)

    fecha = st.date_input(
        "Fecha (Semana Lunes–Sábado)",
        value=hoy,
        min_value=lunes,
        max_value=sabado,
    )

    # recalcular semana según fecha elegida
    lunes, sabado = semana_lunes_sabado(fecha)

    disp = cupos_disponibles(df, fecha)
    opciones = []
    for d in disp:
        if d["disponibles"] > 0:
            opciones.append(f'{d["turno"]} ({d["horario"]}) — Cupos: {d["disponibles"]}')

    if not opciones:
        st.error("No hay cupos disponibles para esa fecha. Prueba otra fecha/turno.")
        st.stop()

    sel = st.selectbox("Selecciona turno disponible", opciones)

    # Parse turno/horario
    turno_sel = sel.split(" (")[0].strip()
    horario_sel = sel.split("(")[1].split(")")[0].strip()

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

    # info cupos
    disp_map = {d["turno"]: d["disponibles"] for d in disp}
    st.info(f"✅ Quedan **{disp_map.get(turno_sel, 0)} cupos** en **{turno_sel} ({horario_sel})** para **{fecha}**")

    if st.button("✅ Generar ticket y registrar"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios")
        else:
            ticket = generar_ticket()
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
                "hora_cita": "",  # no se usa (turnos mandan)
                "observacion": obs.strip(),
                "estado": "EN COLA",
                "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            append_row(ws, data)

            st.success(f"🎫 Ticket creado: {ticket}")

            # Mostrar ticket
            st.code(
                "\n".join(
                    [
                        f"TICKET: {ticket}",
                        f"FECHA: {fecha}",
                        f"TURNO: {turno_sel} ({horario_sel})",
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

            st.caption("💡 Guarda este PDF y preséntalo al llegar.")

# -----------------------------
# TAB 2: Panel Admin
# -----------------------------
if admin_enabled:
    with tab_objs[1]:
        st.subheader("Panel de control (Admin)")

        # Recargar (por si ya registraron)
        ws = get_sheet()
        df = read_all(ws)

        # Filtros básicos
        c1, c2, c3 = st.columns(3)
        with c1:
            fecha_f = st.date_input("Filtrar fecha", value=date.today())
        with c2:
            estado_f = st.selectbox("Filtrar estado", ["(Todos)"] + ESTADOS, index=0)
        with c3:
            turno_f = st.selectbox("Filtrar turno", ["(Todos)"] + [t["turno"] for t in TURNOS], index=0)

        dff = df.copy()
        dff = dff[dff["fecha_cita_dt"] == fecha_f]
        if estado_f != "(Todos)":
            dff = dff[dff["estado"] == estado_f]
        if turno_f != "(Todos)":
            dff = dff[dff["turno"] == turno_f]

        st.markdown("### Gestión rápida (cambiar estado sin escribir ticket)")
        st.caption("Edita la columna **estado** y luego presiona **Guardar cambios**.")

        show_cols = [
            "id_ticket", "turno", "horario_turno",
            "placa_tracto", "placa_carreta", "chofer_nombre",
            "licencia", "transporte", "tipo_operacion",
            "fecha_cita", "observacion", "estado", "creado_en"
        ]

        dff_show = dff[show_cols].copy()

        edited = st.data_editor(
            dff_show,
            use_container_width=True,
            hide_index=True,
            column_config={
                "estado": st.column_config.SelectboxColumn(
                    "estado",
                    options=ESTADOS,
                    required=True,
                )
            },
            disabled=[c for c in dff_show.columns if c != "estado"],
            key="admin_editor",
        )

        if st.button("💾 Guardar cambios"):
            # Detectar cambios estado
            cambios = {}
            original = dff_show.set_index("id_ticket")["estado"].to_dict()
            nuevo = edited.set_index("id_ticket")["estado"].to_dict()
            for tid, est_new in nuevo.items():
                if original.get(tid) != est_new:
                    cambios[tid] = est_new

            update_estado_batch(ws, cambios)

            if cambios:
                st.success(f"✅ Cambios guardados: {len(cambios)}")
            else:
                st.info("No hubo cambios.")
            st.rerun()

# -----------------------------
# TAB 3: Dashboard Gerencia
# -----------------------------
if admin_enabled:
    with tab_objs[2]:
        st.subheader("Dashboard (para gerencia)")

        ws = get_sheet()
        df = read_all(ws).copy()

        # Elegir semana (lunes-sábado)
        base = st.date_input("Semana (elige un día)", value=date.today())
        lunes, sabado = semana_lunes_sabado(base)

        dfw = df[(df["fecha_cita_dt"] >= lunes) & (df["fecha_cita_dt"] <= sabado)].copy()

        # KPIs
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total semana", int(len(dfw)))
        k2.metric("EN COLA", int((dfw["estado"] == "EN COLA").sum()))
        k3.metric("EN PROCESO", int((dfw["estado"] == "EN PROCESO").sum()))
        k4.metric("ATENDIDO", int((dfw["estado"] == "ATENDIDO").sum()))

        st.markdown("### Distribución por estado (semana)")
        estado_counts = (
            dfw.groupby("estado")["id_ticket"].count().reindex(ESTADOS, fill_value=0).reset_index()
        )
        estado_counts.columns = ["estado", "cantidad"]

        chart_estado = (
            alt.Chart(estado_counts)
            .mark_bar()
            .encode(
                x=alt.X("estado:N", title="Estado"),
                y=alt.Y("cantidad:Q", title="Cantidad"),
                tooltip=["estado", "cantidad"],
            )
        )

        text_estado = (
            alt.Chart(estado_counts)
            .mark_text(dy=-8)
            .encode(x="estado:N", y="cantidad:Q", text="cantidad:Q")
        )

        st.altair_chart((chart_estado + text_estado).properties(height=320), use_container_width=True)

        st.markdown("### Unidades por día (semana Lunes–Sábado)")
        if dfw["fecha_cita_dt"].notna().any():
            by_day = dfw.groupby("fecha_cita_dt")["id_ticket"].count().reset_index()
            by_day.columns = ["fecha", "cantidad"]
            by_day["fecha"] = by_day["fecha"].astype(str)

            chart_day = (
                alt.Chart(by_day)
                .mark_bar()
                .encode(
                    x=alt.X("fecha:N", title="Día", sort=None),
                    y=alt.Y("cantidad:Q", title="Cantidad"),
                    tooltip=["fecha", "cantidad"],
                )
            )
            text_day = (
                alt.Chart(by_day)
                .mark_text(dy=-8)
                .encode(x="fecha:N", y="cantidad:Q", text="cantidad:Q")
            )

            st.altair_chart((chart_day + text_day).properties(height=320), use_container_width=True)
        else:
            st.info("Aún no hay datos de fechas para esa semana.")

        st.markdown("### Ocupación por turno (semana)")
        by_turno = dfw.groupby("turno")["id_ticket"].count().reindex([t["turno"] for t in TURNOS], fill_value=0).reset_index()
        by_turno.columns = ["turno", "cantidad"]

        chart_turno = (
            alt.Chart(by_turno)
            .mark_bar()
            .encode(
                x=alt.X("turno:N", title="Turno", sort=None),
                y=alt.Y("cantidad:Q", title="Cantidad"),
                tooltip=["turno", "cantidad"],
            )
        )
        text_turno = (
            alt.Chart(by_turno)
            .mark_text(dy=-8)
            .encode(x="turno:N", y="cantidad:Q", text="cantidad:Q")
        )
        st.altair_chart((chart_turno + text_turno).properties(height=320), use_container_width=True)

        st.markdown("### Operación (semana)")
        by_op = dfw.groupby("tipo_operacion")["id_ticket"].count().reset_index()
        by_op.columns = ["operacion", "cantidad"]

        chart_op = (
            alt.Chart(by_op)
            .mark_bar()
            .encode(
                x=alt.X("operacion:N", title="Operación"),
                y=alt.Y("cantidad:Q", title="Cantidad"),
                tooltip=["operacion", "cantidad"],
            )
        )
        text_op = (
            alt.Chart(by_op)
            .mark_text(dy=-8)
            .encode(x="operacion:N", y="cantidad:Q", text="cantidad:Q")
        )
        st.altair_chart((chart_op + text_op).properties(height=320), use_container_width=True)

        st.markdown("### Descargar reporte (Excel)")
        reporte_cols = [
            "id_ticket", "fecha_cita", "turno", "horario_turno", "estado",
            "placa_tracto", "placa_carreta", "chofer_nombre", "licencia",
            "transporte", "tipo_operacion", "observacion", "creado_en"
        ]
        reporte = dfw[reporte_cols].copy()
        excel_bytes = export_excel(reporte)

        st.download_button(
            "⬇️ Descargar reporte (.xlsx)",
            data=excel_bytes,
            file_name=f"reporte_citas_{lunes}_a_{sabado}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
