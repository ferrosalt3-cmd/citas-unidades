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
def _img_to_base64(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    b = p.read_bytes()
    ext = p.suffix.lower().replace(".", "")
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64," + base64.b64encode(b).decode("utf-8")


# ✅ Coloca tus archivos en el repo (misma carpeta del app.py o en /assets)
# Recomendado:
#   assets/logo.jpg
#   assets/fondo.png
LOGO_PATH = "assets/logo.jpg"
FONDO_PATH = "assets/fondo.png"  # renombra tu fondo a "fondo.png" para evitar espacios

logo_b64 = _img_to_base64(LOGO_PATH)
fondo_b64 = _img_to_base64(FONDO_PATH)

# CSS: fondo + look & feel Ferrosalt
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
            content: "";
            position: fixed;
            inset: 0;
            background: rgba(255,255,255,0.78);
            z-index: 0;
            pointer-events: none;
        }}

        /* Todo el contenido por encima del overlay */
        section[data-testid="stSidebar"],
        main,
        header {{
            position: relative;
            z-index: 1;
        }}

        /* Sidebar un poco más elegante */
        section[data-testid="stSidebar"] > div {{
            background: rgba(255,255,255,0.90);
            border-right: 1px solid rgba(0,0,0,0.06);
        }}

        /* Cards */
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"] {{
            background: rgba(255,255,255,0.90);
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
    colL, colR = st.columns([1, 4])
    with colL:
        if Path(LOGO_PATH).exists():
            st.image(LOGO_PATH, use_container_width=True)
    with colR:
        st.markdown(
            """
            <div style="margin-top:6px">
              <h1 style="margin:0; font-size:40px;">Citas de Unidades</h1>
              <div style="margin-top:4px; font-size:15px; opacity:0.8;">
                Plataforma de registro y control por turnos
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ----------------------------
# Google Sheets helpers
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
        raise RuntimeError("Falta SHEET_ID en Secrets (debe ir FUERA del bloque [gcp_service_account]).")

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
        new_headers = headers + missing
        ws.update("A1", [new_headers])


def read_all(ws) -> pd.DataFrame:
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


def safe_str(x):
    return "" if x is None else str(x)


def append_cita(ws, row_dict: dict):
    headers = ws.row_values(1)
    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")


def update_estado_por_row(ws, row_number: int, nuevo_estado: str):
    headers = ws.row_values(1)
    if "estado" not in headers:
        raise RuntimeError("No existe columna 'estado' en la hoja.")
    col_estado = headers.index("estado") + 1
    ws.update_cell(row_number, col_estado, nuevo_estado)


# ----------------------------
# Fechas: Semana Lunes-Sábado
# ----------------------------
def lunes_de_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def semana_lun_sab(d: date):
    start = lunes_de_semana(d)
    days = [start + timedelta(days=i) for i in range(6)]  # lunes..sábado
    return start, days


def fmt_fecha(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def nombre_dia_es(d: date) -> str:
    dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    return dias[d.weekday()]


# ----------------------------
# Turnos / cupos
# ----------------------------
def cupos_disponibles(df: pd.DataFrame, fecha: str, turno: str, capacidad: int) -> int:
    if df.empty:
        return capacidad
    sub = df[(df["fecha_cita"] == fecha) & (df["turno"] == turno)]
    sub = sub[sub["estado"] != "CANCELADO"]  # cancelados no consumen cupo
    usados = len(sub)
    return max(0, capacidad - usados)


def turnos_disponibles(df: pd.DataFrame, fecha: str):
    opciones = []
    for t in TURNOS_BASE:
        libres = cupos_disponibles(df, fecha, t["turno"], t["capacidad"])
        if libres > 0:
            opciones.append((t["turno"], t["horario"], libres, t["capacidad"]))
    return opciones


# ----------------------------
# PDF Ticket
# ----------------------------
def make_ticket_pdf_bytes(ticket_data: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A6)
    _, height = A6

    y = height - 28
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20, y, "TICKET DE CITA")
    y -= 18

    c.setFont("Helvetica", 9.6)
    lines = [
        f"Ticket: {ticket_data.get('id_ticket','')}",
        f"Fecha: {ticket_data.get('fecha_cita','')}",
        f"Turno: {ticket_data.get('turno','')} ({ticket_data.get('horario_turno','')})",
        f"Placa tracto: {ticket_data.get('placa_tracto','')}",
        f"Placa carreta: {ticket_data.get('placa_carreta','')}",
        f"Chofer: {ticket_data.get('chofer_nombre','')}",
        f"Licencia: {ticket_data.get('licencia','')}",
        f"Transporte: {ticket_data.get('transporte','')}",
        f"Operación: {ticket_data.get('tipo_operacion','')}",
        f"Estado: {ticket_data.get('estado','')}",
        f"Registrado por: {ticket_data.get('registrado_por','')}",
        f"Teléfono: {ticket_data.get('telefono_registro','')}",
    ]
    for line in lines:
        c.drawString(20, y, line)
        y -= 13

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


# ----------------------------
# UI
# ----------------------------
header_brand()

# Sidebar admin + logo pequeño
if Path(LOGO_PATH).exists():
    st.sidebar.image(LOGO_PATH, use_container_width=True)

st.sidebar.subheader("🔒 Admin")
admin_password_input = st.sidebar.text_input("Contraseña admin", type="password")
admin_ok = (admin_password_input != "") and (admin_password_input == st.secrets.get("ADMIN_PASSWORD", ""))

if admin_ok:
    st.sidebar.success("Admin activado")
else:
    st.sidebar.caption("Choferes: no usar.")

tabs = ["🧑‍✈️ Registrar cita"]
if admin_ok:
    tabs += ["🛠️ Panel admin", "📊 Dashboard gerencia"]

tab_selected = st.tabs(tabs)

# Cargar sheet/df
try:
    ws = get_sheet()
    df = read_all(ws)
except Exception:
    st.error("No se pudo conectar a Google Sheets. Revisa permisos del Service Account y el SHEET_ID.")
    st.stop()


# ----------------------------
# TAB 1: Registro (Chofer)
# ----------------------------
with tab_selected[0]:
    st.subheader("Registro (Chofer)")

    hoy = date.today()
    _, days = semana_lun_sab(hoy)
    opciones_fecha = {f"{nombre_dia_es(d)} {d.strftime('%d/%m/%Y')}": d for d in days}

    fecha_label = st.selectbox("Fecha (Semana Lunes–Sábado)", list(opciones_fecha.keys()), index=0)
    fecha_sel = opciones_fecha[fecha_label]
    fecha_str = fmt_fecha(fecha_sel)

    disponibles = turnos_disponibles(df, fecha_str)
    if not disponibles:
        st.warning("No hay cupos disponibles para esa fecha (todos los turnos están llenos).")
        st.stop()

    turno_labels = [f"{t} ({h}) — Cupos: {libres}" for (t, h, libres, _cap) in disponibles]
    choice = st.selectbox("Selecciona turno disponible", turno_labels)
    idx = turno_labels.index(choice)
    turno_sel, horario_sel, libres_sel, cap_sel = disponibles[idx]

    col1, col2 = st.columns(2)
    with col1:
        placa_tracto = st.text_input("Placa tracto *")
        placa_carreta = st.text_input("Placa carreta")
        chofer = st.text_input("Chofer *")
        licencia = st.text_input("Licencia")
    with col2:
        transporte = st.text_input("Transporte")
        tipo = st.selectbox("Tipo de operación", ["Carga", "Descarga", "Importación", "Exportación"])
        obs = st.text_area("Observación")

    st.markdown("### Información de registro")
    registrado_por = st.text_input("Registrado por *")
    telefono_registro = st.text_input("Teléfono (opcional)")

    st.info(f"✅ Quedan *{libres_sel} cupos* en *{turno_sel} ({horario_sel})* para *{fecha_str}*")

    if st.button("✅ Generar ticket y registrar"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios.")
        elif not registrado_por.strip():
            st.error("El campo *Registrado por* es obligatorio.")
        else:
            df_now = read_all(ws)
            libres_now = cupos_disponibles(df_now, fecha_str, turno_sel, cap_sel)
            if libres_now <= 0:
                st.error("Se agotó el cupo justo ahora. Elige otro turno.")
            else:
                ticket = generar_ticket()
                data = {
                    "id_ticket": ticket,
                    "turno": turno_sel,
                    "horario_turno": horario_sel,
                    "placa_tracto": placa_tracto.strip().upper(),
                    "placa_carreta": placa_carreta.strip().upper(),
                    "chofer_nombre": chofer.strip(),
                    "licencia": safe_str(licencia).strip(),
                    "transporte": safe_str(transporte).strip(),
                    "tipo_operacion": tipo,
                    "fecha_cita": fecha_str,
                    "hora_cita": "",
                    "observacion": safe_str(obs).strip(),
                    "estado": "EN COLA",
                    "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "registrado_por": registrado_por.strip(),
                    "telefono_registro": telefono_registro.strip(),
                }
                append_cita(ws, data)

                st.success(f"🎟️ Ticket creado: *{ticket}*")

                st.code(
                    f"TICKET: {ticket}\n"
                    f"FECHA: {fecha_str}\n"
                    f"TURNO: {turno_sel} ({horario_sel})\n"
                    f"PLACA TRACTO: {data['placa_tracto']}\n"
                    f"PLACA CARRETA: {data['placa_carreta']}\n"
                    f"CHOFER: {data['chofer_nombre']}\n"
                    f"OPERACIÓN: {data['tipo_operacion']}\n"
                    f"ESTADO: {data['estado']}\n"
                    f"REGISTRADO POR: {data['registrado_por']}\n"
                    f"TELÉFONO: {data['telefono_registro'] or '-'}\n",
                    language="text",
                )

                pdf_bytes = make_ticket_pdf_bytes(data)
                st.download_button(
                    "⬇️ Descargar ticket (PDF)",
                    data=pdf_bytes,
                    file_name=f"{ticket}.pdf",
                    mime="application/pdf",
                )


# ----------------------------
# TAB 2: Panel admin
# ----------------------------
if admin_ok:
    with tab_selected[1]:
        st.subheader("Panel de control (Admin)")

        df_admin = df.copy()

        colf1, colf2, colf3 = st.columns(3)
        with colf1:
            filtro_fecha = st.text_input("Filtrar fecha (YYYY-MM-DD)", "")
        with colf2:
            filtro_estado = st.selectbox("Filtrar estado", ["(Todos)"] + ESTADOS, index=0)
        with colf3:
            filtro_turno = st.selectbox("Filtrar turno", ["(Todos)"] + [t["turno"] for t in TURNOS_BASE], index=0)

        if filtro_fecha:
            df_admin = df_admin[df_admin["fecha_cita"].astype(str).str.contains(filtro_fecha.strip())]
        if filtro_estado != "(Todos)":
            df_admin = df_admin[df_admin["estado"] == filtro_estado]
        if filtro_turno != "(Todos)":
            df_admin = df_admin[df_admin["turno"] == filtro_turno]

        st.caption("✅ Cambia el estado directamente en la tabla y luego pulsa *Guardar cambios*.")

        df_full = read_all(ws).reset_index(drop=True)
        df_full["__row"] = df_full.index + 2  # header=1

        df_admin = df_admin.merge(df_full[["id_ticket", "__row"]], on="id_ticket", how="left")

        edited = st.data_editor(
            df_admin,
            use_container_width=True,
            hide_index=True,
            column_config={
                "estado": st.column_config.SelectboxColumn("estado", options=ESTADOS, required=True),
                "_row": st.column_config.NumberColumn("_row", disabled=True),
            },
            disabled=[c for c in df_admin.columns if c not in ["estado"]],
            key="editor_estado",
        )

        if st.button("💾 Guardar cambios de estado"):
            try:
                merged = edited.merge(df_admin[["id_ticket", "estado"]], on="id_ticket", suffixes=("_new", "_old"))
                cambios = merged[merged["estado_new"] != merged["estado_old"]]

                if cambios.empty:
                    st.info("No hay cambios.")
                else:
                    for _, r in cambios.iterrows():
                        update_estado_por_row(ws, int(r["__row"]), r["estado_new"])
                    st.success(f"Estados actualizados: {len(cambios)}")
                    st.rerun()
            except Exception:
                st.error("Error actualizando estados. Revisa permisos y vuelve a intentar.")


# ----------------------------
# TAB 3: Dashboard gerencia
# ----------------------------
if admin_ok:
    with tab_selected[2]:
        st.subheader("Dashboard (Gerencia)")

        df_dash = df.copy()
        df_dash["fecha_cita_dt"] = pd.to_datetime(df_dash["fecha_cita"], errors="coerce").dt.date

        hoy = date.today()
        start_week, _days = semana_lun_sab(hoy)
        min_d = start_week - timedelta(days=7 * 12)
        max_d = start_week + timedelta(days=7 * 52)

        semana_base = st.date_input(
            "Semana (elige cualquier día de esa semana)",
            value=hoy,
            min_value=min_d,
            max_value=max_d,
        )
        start_week, days = semana_lun_sab(semana_base)
        set_days = set(days)

        df_w = df_dash[df_dash["fecha_cita_dt"].isin(set_days)].copy()

        total = len(df_w)
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Total", total)
        k2.metric("En cola", int((df_w["estado"] == "EN COLA").sum()))
        k3.metric("En proceso", int((df_w["estado"] == "EN PROCESO").sum()))
        k4.metric("Atendido", int((df_w["estado"] == "ATENDIDO").sum()))
        k5.metric("Cancelado", int((df_w["estado"] == "CANCELADO").sum()))

        st.markdown("### Distribución de estados (rápido)")
        estado_counts = (
            df_w["estado"].value_counts()
            .reindex(ESTADOS)
            .fillna(0)
            .astype(int)
            .reset_index()
        )
        estado_counts.columns = ["estado", "cantidad"]

        fig_donut = px.pie(estado_counts, names="estado", values="cantidad", hole=0.55)
        fig_donut.update_traces(textinfo="label+percent+value")
        st.plotly_chart(fig_donut, use_container_width=True)

        st.markdown("### Unidades por día (Lunes–Sábado) y por estado")
        cal = pd.DataFrame([{"fecha": d, "dia": f"{nombre_dia_es(d)} {d.strftime('%d/%m')}"} for d in days])

        grp = df_w.groupby(["fecha_cita_dt", "estado"]).size().reset_index(name="cantidad")
        grp = grp.merge(cal, left_on="fecha_cita_dt", right_on="fecha", how="right")
        grp["cantidad"] = grp["cantidad"].fillna(0).astype(int)

        full = []
        for d in days:
            dia_label = f"{nombre_dia_es(d)} {d.strftime('%d/%m')}"
            for est in ESTADOS:
                sub = grp[(grp["fecha"] == d) & (grp["estado"] == est)]
                full.append({"dia": dia_label, "estado": est, "cantidad": int(sub["cantidad"].iloc[0]) if not sub.empty else 0})
        full_df = pd.DataFrame(full)

        fig_stack = px.bar(full_df, x="dia", y="cantidad", color="estado", barmode="stack", text="cantidad")
        fig_stack.update_traces(textposition="outside")
        fig_stack.update_layout(yaxis_title="Cantidad", xaxis_title="Día")
        st.plotly_chart(fig_stack, use_container_width=True)

        st.markdown("### Citas por turno (semanal)")
        turno_counts = (
            df_w["turno"].value_counts()
            .reindex([t["turno"] for t in TURNOS_BASE])
            .fillna(0)
            .astype(int)
            .reset_index()
        )
        turno_counts.columns = ["turno", "cantidad"]

        fig_turnos = px.bar(turno_counts, x="turno", y="cantidad", text="cantidad")
        fig_turnos.update_traces(textposition="outside")
        fig_turnos.update_layout(xaxis_title="Turno", yaxis_title="Cantidad")
        st.plotly_chart(fig_turnos, use_container_width=True)

        st.markdown("### Operación (tipo) — semanal")
        op_counts = df_w["tipo_operacion"].value_counts().reset_index()
        op_counts.columns = ["tipo_operacion", "cantidad"]
        fig_ops = px.bar(op_counts, x="tipo_operacion", y="cantidad", text="cantidad")
        fig_ops.update_traces(textposition="outside")
        fig_ops.update_layout(xaxis_title="Operación", yaxis_title="Cantidad")
        st.plotly_chart(fig_ops, use_container_width=True)

        st.markdown("### Descargar reporte (Excel)")
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df_w.drop(columns=["fecha_cita_dt"], errors="ignore").to_excel(writer, index=False, sheet_name="Citas_semana")
            estado_counts.to_excel(writer, index=False, sheet_name="Resumen_estados")
            turno_counts.to_excel(writer, index=False, sheet_name="Turnos")
            op_counts.to_excel(writer, index=False, sheet_name="Operaciones")
        out.seek(0)

        st.download_button(
            "⬇️ Descargar reporte semana (XLSX)",
            data=out.getvalue(),
            file_name=f"reporte_citas_{start_week.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
