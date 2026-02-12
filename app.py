import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta
import hashlib
from io import BytesIO
import base64
from pathlib import Path
import re

import plotly.express as px

# PDF (ticket)
from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors


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
# Branding (LOGO + FONDO)
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

# CSS para que los inputs NO “se pierdan” con el fondo
st.markdown(
    f"""
    <style>
    .stApp {{
        background: url("{fondo_b64}") no-repeat center center fixed;
        background-size: cover;
    }}

    /* Overlay suave */
    .stApp::before {{
        content:"";
        position:fixed;
        inset:0;
        background:rgba(255,255,255,0.82);
        z-index:0;
        pointer-events:none;
    }}

    main, header, section[data-testid="stSidebar"] {{
        position:relative;
        z-index:1;
    }}

    section[data-testid="stSidebar"] > div {{
        background: rgba(255,255,255,0.94);
        border-right: 1px solid rgba(0,0,0,0.06);
    }}

    /* Inputs con fondo blanco (para que se lean SIEMPRE) */
    div[data-baseweb="input"] > div {{
        background: rgba(255,255,255,0.98) !important;
    }}
    div[data-baseweb="textarea"] textarea {{
        background: rgba(255,255,255,0.98) !important;
    }}
    div[data-baseweb="select"] > div {{
        background: rgba(255,255,255,0.98) !important;
    }}

    /* Cards */
    div[data-testid="stMetric"],
    div[data-testid="stDataFrame"],
    div[data-testid="stPlotlyChart"] {{
        background: rgba(255,255,255,0.94);
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
    # NO mostramos el logo grande arriba (solo en sidebar)
    st.markdown(
        """
        <div style="padding:8px 0 2px 0;">
          <h1 style="margin:0;">Citas de Unidades</h1>
          <div style="opacity:0.7;">Plataforma de registro y control por turnos</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()


# ----------------------------
# Utilidades
# ----------------------------
def safe_str(x):
    return "" if x is None else str(x)


def normalizar_texto(x: str) -> str:
    s = safe_str(x).strip()
    if s.startswith("'"):
        s = s[1:].strip()
    return s


def normalizar_fecha_texto(x: str) -> str:
    """
    Acepta:
      - '2026-02-14
      - 2026-02-14
      - 14/02/2026
    Devuelve YYYY-MM-DD si se puede, o el texto limpio si no.
    """
    s = normalizar_texto(x)
    if not s:
        return ""
    # intentos de parse
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return s
    return dt.strftime("%Y-%m-%d")


def extraer_sheet_id(posible_id_o_url: str) -> str:
    """
    Soporta:
      - ID puro
      - URL completa de Google Sheets
    """
    s = safe_str(posible_id_o_url).strip()
    if not s:
        return ""
    # si ya es id (sin '/')
    if "/" not in s and len(s) > 20:
        return s
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    # fallback: toma lo más parecido a id
    m2 = re.search(r"([a-zA-Z0-9-_]{25,})", s)
    return m2.group(1) if m2 else s


def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()


# ----------------------------
# Google Sheets helpers
# ----------------------------
@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def ensure_columns(ws):
    headers = ws.row_values(1)
    if not headers:
        ws.append_row(COLUMNAS)
        return

    missing = [c for c in COLUMNAS if c not in headers]
    if missing:
        ws.update("A1", [headers + missing])


def get_sheet():
    raw = st.secrets.get("SHEET_ID", "")
    sheet_id = extraer_sheet_id(raw)
    if not sheet_id:
        raise RuntimeError("Falta SHEET_ID en Secrets (debe ser el ID, no el link).")

    gc = get_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(WORKSHEET_NAME)
    ensure_columns(ws)
    return ws


def read_all(ws) -> pd.DataFrame:
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS)

    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""

    # NORMALIZAR campos clave para que el cupo funcione aunque venga con "'"
    df["fecha_cita"] = df["fecha_cita"].apply(normalizar_fecha_texto)
    df["creado_en"] = df["creado_en"].apply(normalizar_texto)
    df["telefono_registro"] = df["telefono_registro"].apply(normalizar_texto)
    df["turno"] = df["turno"].apply(normalizar_texto)
    df["estado"] = df["estado"].apply(normalizar_texto)

    return df[COLUMNAS]


def append_cita(ws, row_dict: dict):
    """
    ✅ RAW para que Sheets no convierta fechas/horas a números.
    ✅ Igual guardamos texto, pero luego normalizamos al leer (para cupos/dashboard).
    """
    headers = ws.row_values(1)
    row_dict = row_dict.copy()

    # Forzar texto (opcional pero útil)
    for k in ["fecha_cita", "creado_en", "telefono_registro"]:
        if k in row_dict and row_dict[k] != "":
            row_dict[k] = "'" + str(row_dict[k])

    row = [row_dict.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="RAW")


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
def turnos_para_fecha(fecha_dt: date):
    # Sábado (weekday 5) solo 2 turnos
    if fecha_dt.weekday() == 5:
        return TURNOS_BASE[:2]
    return TURNOS_BASE


def cupos_disponibles(df: pd.DataFrame, fecha: str, turno: str, capacidad: int) -> int:
    """
    Cuenta todo menos CANCELADO.
    """
    if df.empty:
        return capacidad

    fecha = normalizar_fecha_texto(fecha)
    turno = normalizar_texto(turno)

    sub = df[(df["fecha_cita"] == fecha) & (df["turno"] == turno)]
    sub = sub[sub["estado"] != "CANCELADO"]
    usados = len(sub)
    return max(0, capacidad - usados)


def turnos_disponibles(df: pd.DataFrame, fecha_dt: date, fecha_str: str):
    opciones = []
    for t in turnos_para_fecha(fecha_dt):
        libres = cupos_disponibles(df, fecha_str, t["turno"], t["capacidad"])
        if libres > 0:
            opciones.append((t["turno"], t["horario"], libres, t["capacidad"]))
    return opciones


# ----------------------------
# PDF Ticket (PRO con logo + fondo)
# ----------------------------
def make_ticket_pdf_bytes(ticket_data: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A6)
    width, height = A6

    # Fondo (más visible)
    if FONDO_PATH.exists():
        try:
            c.saveState()
            c.setFillAlpha(1)
            c.drawImage(ImageReader(str(FONDO_PATH)), 0, 0, width=width, height=height, mask="auto")
            c.restoreState()
        except Exception:
            pass

    # Tarjeta blanca redondeada (overlay)
    card_x, card_y = 12, 12
    card_w, card_h = width - 24, height - 24
    try:
        c.saveState()
        c.setFillColor(colors.white)
        c.setFillAlpha(0.88)
        c.roundRect(card_x, card_y, card_w, card_h, 12, fill=1, stroke=0)
        c.restoreState()
    except Exception:
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(card_x, card_y, card_w, card_h, 12, fill=1, stroke=0)

    # Logo (arriba izquierda)
    if LOGO_PATH.exists():
        try:
            c.drawImage(ImageReader(str(LOGO_PATH)), card_x + 8, height - 55, width=110, height=35, mask="auto")
        except Exception:
            pass

    # Título centrado
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, height - 28, "TICKET DE CITA")

    # Línea separadora
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(card_x + 10, height - 62, width - (card_x + 10), height - 62)

    # Contenido en 2 columnas (etiqueta/valor)
    left_x = card_x + 16
    label_x = left_x
    value_x = left_x + 72
    y = height - 82

    c.setFont("Helvetica-Bold", 9.6)
    labels = [
        ("Ticket:", ticket_data.get("id_ticket", "")),
        ("Fecha:", ticket_data.get("fecha_cita", "")),
        ("Turno:", f"{ticket_data.get('turno','')} ({ticket_data.get('horario_turno','')})"),
        ("Placa tracto:", ticket_data.get("placa_tracto", "")),
        ("Placa carreta:", ticket_data.get("placa_carreta", "")),
        ("Chofer:", ticket_data.get("chofer_nombre", "")),
        ("Licencia:", ticket_data.get("licencia", "")),
        ("Transporte:", ticket_data.get("transporte", "")),
        ("Operación:", ticket_data.get("tipo_operacion", "")),
        ("Estado:", ticket_data.get("estado", "")),
        ("Registrado por:", ticket_data.get("registrado_por", "")),
        ("Teléfono:", ticket_data.get("telefono_registro", "")),
    ]

    c.setFillColor(colors.black)
    for lab, val in labels:
        c.setFont("Helvetica-Bold", 9.6)
        c.drawString(label_x, y, lab)
        c.setFont("Helvetica", 9.6)
        c.drawString(value_x, y, safe_str(val))
        y -= 14

    # Línea separadora + IMPORTANT
    y -= 2
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(card_x + 10, y, width - (card_x + 10), y)
    y -= 18

    c.setFont("Helvetica-Bold", 10)
    c.drawString(label_x, y, "IMPORTANTE:")
    y -= 14
    c.setFont("Helvetica", 9.6)
    c.drawString(label_x, y, "Presentarse 30 minutos antes de su cita.")

    # Footer
    c.setFont("Helvetica-Oblique", 8.5)
    generado = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.drawString(card_x + 12, card_y + 10, f"Generado: {generado}")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


# ----------------------------
# UI
# ----------------------------
header_brand()

# Logo SOLO en sidebar
if LOGO_PATH.exists():
    st.sidebar.image(str(LOGO_PATH), use_container_width=True)

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

# Conectar sheet
try:
    ws = get_sheet()
except Exception:
    st.error("No se pudo conectar a Google Sheets. Revisa permisos del Service Account y el SHEET_ID.")
    st.stop()

# Session state para que NO se borre el resumen y SI salga el PDF
if "last_ticket_data" not in st.session_state:
    st.session_state["last_ticket_data"] = None
if "last_pdf_bytes" not in st.session_state:
    st.session_state["last_pdf_bytes"] = None


# ----------------------------
# TAB 1: Registro
# ----------------------------
with tab_selected[0]:
    df = read_all(ws)

    st.subheader("Registro (Chofer)")

    hoy = date.today()
    _, days = semana_lun_sab(hoy)
    opciones_fecha = {f"{nombre_dia_es(d)} {d.strftime('%d/%m/%Y')}": d for d in days}

    fecha_label = st.selectbox("Fecha (Semana Lunes–Sábado)", list(opciones_fecha.keys()), index=0)
    fecha_sel = opciones_fecha[fecha_label]
    fecha_str = fmt_fecha(fecha_sel)

    disponibles = turnos_disponibles(df, fecha_sel, fecha_str)
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

    st.info(f"✅ Quedan **{libres_sel} cupos** en **{turno_sel} ({horario_sel})** para **{fecha_str}**")

    if st.button("✅ Generar ticket y registrar"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios.")
        elif not registrado_por.strip():
            st.error("El campo **Registrado por** es obligatorio.")
        else:
            # Releer justo antes de guardar (anti colisión)
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

                # Guardar para mostrar después del rerun
                st.session_state["last_ticket_data"] = data
                st.session_state["last_pdf_bytes"] = make_ticket_pdf_bytes(data)

                # Rerun para refrescar cupos/turnos, pero SIN perder resumen
                st.rerun()

    # Mostrar el último ticket si existe (no se borra)
    if st.session_state["last_ticket_data"]:
        data = st.session_state["last_ticket_data"]
        st.success(f"🎟️ Ticket creado: **{data['id_ticket']}**")

        st.code(
            f"TICKET: {data['id_ticket']}\n"
            f"FECHA: {data['fecha_cita']}\n"
            f"TURNO: {data['turno']} ({data['horario_turno']})\n"
            f"PLACA TRACTO: {data['placa_tracto']}\n"
            f"PLACA CARRETA: {data['placa_carreta']}\n"
            f"CHOFER: {data['chofer_nombre']}\n"
            f"LICENCIA: {data['licencia']}\n"
            f"TRANSPORTE: {data['transporte']}\n"
            f"OPERACIÓN: {data['tipo_operacion']}\n"
            f"ESTADO: {data['estado']}\n"
            f"REGISTRADO POR: {data['registrado_por']}\n"
            f"TELÉFONO: {data['telefono_registro'] or '-'}\n",
            language="text",
        )

        if st.session_state["last_pdf_bytes"]:
            st.download_button(
                "⬇️ Descargar ticket (PDF)",
                data=st.session_state["last_pdf_bytes"],
                file_name=f"{data['id_ticket']}.pdf",
                mime="application/pdf",
            )


# ----------------------------
# TAB 2: Panel admin
# ----------------------------
if admin_ok:
    with tab_selected[1]:
        df = read_all(ws)

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

        st.caption("✅ Cambia el estado directamente en la tabla y luego pulsa **Guardar cambios**.")

        df_full = read_all(ws).reset_index(drop=True)
        df_full["__row"] = df_full.index + 2  # header=1

        df_admin = df_admin.merge(df_full[["id_ticket", "__row"]], on="id_ticket", how="left")

        edited = st.data_editor(
            df_admin,
            use_container_width=True,
            hide_index=True,
            column_config={
                "estado": st.column_config.SelectboxColumn("estado", options=ESTADOS, required=True),
                "__row": st.column_config.NumberColumn("__row", disabled=True),
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
        df = read_all(ws)

        st.subheader("Dashboard (Gerencia)")

        df_dash = df.copy()

        # Parse robusto
        df_dash["fecha_cita_dt"] = pd.to_datetime(
            df_dash["fecha_cita"].astype(str),
            errors="coerce",
            dayfirst=True,
        ).dt.date

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

        if df_w.empty and not df_dash.empty:
            st.warning("⚠️ No hay registros en la semana seleccionada. Mostrando resumen general (todos los registros).")
            df_w = df_dash.copy()

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

        if estado_counts["cantidad"].sum() == 0:
            st.info("No hay datos para graficar en esta selección.")
        else:
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
                full.append(
                    {"dia": dia_label, "estado": est, "cantidad": int(sub["cantidad"].iloc[0]) if not sub.empty else 0}
                )
        full_df = pd.DataFrame(full)

        if full_df["cantidad"].sum() == 0:
            st.info("No hay datos para graficar por día en esta selección.")
        else:
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

        if turno_counts["cantidad"].sum() == 0:
            st.info("No hay datos para graficar por turnos en esta selección.")
        else:
            fig_turnos = px.bar(turno_counts, x="turno", y="cantidad", text="cantidad")
            fig_turnos.update_traces(textposition="outside")
            fig_turnos.update_layout(xaxis_title="Turno", yaxis_title="Cantidad")
            st.plotly_chart(fig_turnos, use_container_width=True)

        st.markdown("### Operación (tipo) — semanal")
        op_counts = df_w["tipo_operacion"].value_counts().reset_index()
        op_counts.columns = ["tipo_operacion", "cantidad"]

        if op_counts["cantidad"].sum() == 0:
            st.info("No hay datos para graficar por operación en esta selección.")
        else:
            fig_ops = px.bar(op_counts, x="tipo_operacion", y="cantidad", text="cantidad")
            fig_ops.update_traces(textposition="outside")
            fig_ops.update_layout(xaxis_title="Operación", yaxis_title="Cantidad")
            st.plotly_chart(fig_ops, use_container_width=True)

        st.markdown("### Descargar reporte (Excel)")
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df_w.drop(columns=["fecha_cita_dt"], errors="ignore").to_excel(writer, index=False, sheet_name="Citas")
            estado_counts.to_excel(writer, index=False, sheet_name="Resumen_estados")
            turno_counts.to_excel(writer, index=False, sheet_name="Turnos")
            op_counts.to_excel(writer, index=False, sheet_name="Operaciones")
        out.seek(0)

        st.download_button(
            "⬇️ Descargar reporte (XLSX)",
            data=out.getvalue(),
            file_name=f"reporte_citas_{start_week.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
