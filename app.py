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

    /* Inputs con fondo blanco */
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
    # NO mostramos el logo grande arriba
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
# Google Sheets helpers
# ----------------------------
@st.cache_resource
def get_client():
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def get_sheet():
    sheet_id = st.secrets.get("SHEET_ID", "").strip()
    # IMPORTANTE: aquí debe ir SOLO el ID, no la URL
    # Ej: 1PXe0YAYrGttH_8AfGZqK64_FtSwJapHI5-_JqChmWXo
    if (not sheet_id) or ("/" in sheet_id) or ("edit" in sheet_id):
        raise RuntimeError("SHEET_ID inválido. Debe ser SOLO el ID del spreadsheet (sin URL).")

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


def _norm_str(x) -> str:
    if x is None:
        return ""
    return str(x).strip()


def _norm_date_text(x) -> str:
    # Para comparar aunque venga "'2026-02-14" o "2026-02-14"
    s = _norm_str(x)
    return s.lstrip("'").strip()


def read_all(ws) -> pd.DataFrame:
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS)

    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""

    # Normalizaciones útiles
    df["fecha_cita"] = df["fecha_cita"].astype(str).apply(_norm_date_text)
    df["turno"] = df["turno"].astype(str).apply(_norm_str)
    df["estado"] = df["estado"].astype(str).apply(_norm_str)

    return df[COLUMNAS]


def generar_ticket():
    return "TKT-" + hashlib.sha1(str(datetime.now()).encode()).hexdigest()[:8].upper()


def safe_str(x):
    return "" if x is None else str(x)


def append_cita(ws, row_dict: dict):
    """
    RAW para evitar que Sheets convierta datetimes a números.
    """
    headers = ws.row_values(1)
    row_dict = row_dict.copy()

    # Mantener texto (por seguridad)
    for k in ["fecha_cita", "creado_en", "telefono_registro"]:
        if k in row_dict and row_dict[k] != "":
            row_dict[k] = str(row_dict[k]).lstrip("'")  # no duplicar apóstrofes

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
    if df.empty:
        return capacidad
    fecha = _norm_date_text(fecha)
    turno = _norm_str(turno)

    sub = df[(df["fecha_cita"].astype(str).apply(_norm_date_text) == fecha) & (df["turno"].astype(str).apply(_norm_str) == turno)]
    sub = sub[sub["estado"] != "CANCELADO"]  # cancelados no consumen cupo
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
# PDF Ticket (PRO con logo + fondo)  ✅ LOGO CORREGIDO
# ----------------------------
def make_ticket_pdf_bytes(ticket_data: dict) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A6)
    width, height = A6

    # 1) Fondo
    if FONDO_PATH.exists():
        try:
            c.drawImage(ImageReader(str(FONDO_PATH)), 0, 0, width=width, height=height, mask="auto")
        except Exception:
            pass

    # 2) Tarjeta blanca con bordes redondeados (simulada)
    margin = 10
    card_x = margin
    card_y = margin
    card_w = width - 2 * margin
    card_h = height - 2 * margin

    # “sombra” ligera
    try:
        c.saveState()
        c.setFillColor(colors.black)
        c.setFillAlpha(0.08)
        c.roundRect(card_x + 2, card_y - 2, card_w, card_h, 12, fill=1, stroke=0)
        c.restoreState()
    except Exception:
        pass

    # tarjeta blanca
    try:
        c.saveState()
        c.setFillColor(colors.white)
        c.setFillAlpha(0.90)
        c.roundRect(card_x, card_y, card_w, card_h, 12, fill=1, stroke=0)
        c.restoreState()
    except Exception:
        c.setFillColor(colors.white)
        c.roundRect(card_x, card_y, card_w, card_h, 12, fill=1, stroke=0)

    # 3) Header: logo + título sin chocar
    header_top = height - margin - 10
    header_h = 42
    header_y = header_top - header_h

    # Logo (ajuste proporcional)
    logo_drawn_w = 0
    if LOGO_PATH.exists():
        try:
            img = ImageReader(str(LOGO_PATH))
            iw, ih = img.getSize()

            max_w = 120
            max_h = 34
            scale = min(max_w / float(iw), max_h / float(ih))

            lw = iw * scale
            lh = ih * scale

            lx = card_x + 12
            ly = header_y + (header_h - lh) / 2

            c.drawImage(img, lx, ly, width=lw, height=lh, mask="auto")
            logo_drawn_w = lw + 10
        except Exception:
            logo_drawn_w = 0

    # Título centrado (o corrido si hay logo)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)

    title = "TICKET DE CITA"
    title_y = header_y + header_h / 2 + 6

    # si hay logo, centramos título en el espacio restante
    left_limit = card_x + 12 + logo_drawn_w
    right_limit = card_x + card_w - 12
    available_w = max(10, right_limit - left_limit)

    title_w = c.stringWidth(title, "Helvetica-Bold", 12)
    if title_w <= available_w:
        tx = left_limit + (available_w - title_w) / 2
    else:
        tx = left_limit

    c.drawString(tx, title_y, title)

    # Línea separadora bajo header
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.line(card_x + 12, header_y - 2, card_x + card_w - 12, header_y - 2)

    # 4) Cuerpo (dos columnas tipo “Ticket: valor”)
    y = header_y - 16
    label_x = card_x + 16
    value_x = card_x + 92

    c.setFont("Helvetica-Bold", 9.5)

    def row(lbl: str, val: str):
        nonlocal y
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(label_x, y, f"{lbl}:")
        c.setFont("Helvetica", 9.5)
        c.drawString(value_x, y, safe_str(val))
        y -= 13

    row("Ticket", ticket_data.get("id_ticket", ""))
    row("Fecha", ticket_data.get("fecha_cita", ""))
    row("Turno", f"{ticket_data.get('turno','')} ({ticket_data.get('horario_turno','')})")
    row("Placa tracto", ticket_data.get("placa_tracto", ""))
    row("Placa carreta", ticket_data.get("placa_carreta", ""))
    row("Chofer", ticket_data.get("chofer_nombre", ""))
    row("Licencia", ticket_data.get("licencia", ""))
    row("Transporte", ticket_data.get("transporte", ""))
    row("Operación", ticket_data.get("tipo_operacion", ""))
    row("Estado", ticket_data.get("estado", ""))
    row("Registrado por", ticket_data.get("registrado_por", ""))
    row("Teléfono", ticket_data.get("telefono_registro", ""))

    # Línea separadora
    c.setLineWidth(0.8)
    c.line(card_x + 12, y - 2, card_x + card_w - 12, y - 2)
    y -= 18

    # IMPORTANTE
    c.setFont("Helvetica-Bold", 10)
    c.drawString(label_x, y, "IMPORTANTE:")
    y -= 12
    c.setFont("Helvetica", 10)
    c.drawString(label_x, y, "Presentarse 30 minutos antes de su cita.")

    # footer
    creado = ticket_data.get("creado_en", "")
    creado = safe_str(creado).split(".")[0]
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawString(label_x, card_y + 10, f"Generado: {creado}")

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

# Estado para mantener resumen + pdf (evita que se borre)
if "last_ticket_data" not in st.session_state:
    st.session_state["last_ticket_data"] = None
if "last_pdf_bytes" not in st.session_state:
    st.session_state["last_pdf_bytes"] = None


# ----------------------------
# TAB 1: Registro
# ----------------------------
with tab_selected[0]:
    st.subheader("Registro (Chofer)")

    # Leer siempre fresco
    df = read_all(ws)

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

    # Recalcular cupos “en vivo” con data fresca
    df_now_for_cupos = read_all(ws)
    libres_live = cupos_disponibles(df_now_for_cupos, fecha_str, turno_sel, cap_sel)

    st.info(f"✅ Quedan **{libres_live} cupos** en **{turno_sel} ({horario_sel})** para **{fecha_str}**")

    if st.button("✅ Generar ticket y registrar"):
        if not placa_tracto or not chofer:
            st.error("Placa tracto y chofer son obligatorios.")
        elif not registrado_por.strip():
            st.error("El campo **Registrado por** es obligatorio.")
        else:
            # validar cupo justo antes de registrar
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

                # Guardar en session_state para que NO se borre
                st.session_state["last_ticket_data"] = data
                st.session_state["last_pdf_bytes"] = make_ticket_pdf_bytes(data)

                st.success(f"🎟️ Ticket creado: **{ticket}**")

    # Mostrar resumen + PDF SIEMPRE que exista (aunque se recargue la página)
    if st.session_state["last_ticket_data"]:
        d = st.session_state["last_ticket_data"]
        st.code(
            f"TICKET: {d.get('id_ticket','')}\n"
            f"FECHA: {d.get('fecha_cita','')}\n"
            f"TURNO: {d.get('turno','')} ({d.get('horario_turno','')})\n"
            f"PLACA TRACTO: {d.get('placa_tracto','')}\n"
            f"PLACA CARRETA: {d.get('placa_carreta','')}\n"
            f"CHOFER: {d.get('chofer_nombre','')}\n"
            f"LICENCIA: {d.get('licencia','')}\n"
            f"TRANSPORTE: {d.get('transporte','')}\n"
            f"OPERACIÓN: {d.get('tipo_operacion','')}\n"
            f"ESTADO: {d.get('estado','')}\n"
            f"REGISTRADO POR: {d.get('registrado_por','')}\n"
            f"TELÉFONO: {d.get('telefono_registro','') or '-'}\n",
            language="text",
        )

        st.download_button(
            "⬇️ Descargar ticket (PDF)",
            data=st.session_state["last_pdf_bytes"],
            file_name=f"{d.get('id_ticket','TICKET')}.pdf",
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

        # parse robusto de fechas
        df_dash["fecha_cita_dt"] = pd.to_datetime(
            df_dash["fecha_cita"].astype(str).apply(_norm_date_text),
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
