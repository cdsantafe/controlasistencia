"""
Dashboard de Control - TAREA CRÍTICA: NOTIFICACIÓN AL CLIENTE (BEES Deliver)
Ejecutar local:  streamlit run app.py
"""
from html import escape
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

# ----------------------------------------------------------------------
# Configuración general
# ----------------------------------------------------------------------
OBJETIVO = 88.0                      # % objetivo de notificación > 2 min
ARCHIVO_DEFECTO = "routes-data_2026-10-19_14h1m.csv"
COLUMNAS_REQ = ["driver_name", "driver_notification_time", "arrived_at"]
ZONA_LOCAL = "America/Argentina/Buenos_Aires"   # las marcas ISO8601 vienen en UTC

st.set_page_config(page_title="Notificación al Cliente", page_icon="📲", layout="wide")

# ----------------------------------------------------------------------
# CSS personalizado (imita la planilla de control)
# ----------------------------------------------------------------------
st.markdown(
    """
    <style>
    .titulo {color:#D32F2F; font-size:2.4rem; font-weight:800; margin-bottom:0; letter-spacing:.5px;}
    .subtitulo {color:#444; font-size:1.2rem; font-weight:600; margin-top:0;}
    .resumen {width:100%; border-collapse:collapse; margin:10px 0 20px 0;}
    .resumen th {background:#FFC933; color:#222; padding:10px; text-align:center;
                 border:1px solid #d9a800; font-size:.95rem;}
    .resumen td {padding:12px; text-align:center; font-size:1.5rem; font-weight:700;
                 border:1px solid #ddd;}
    .ok  {background:#C8E6C9; color:#1B5E20;}
    .bad {background:#FFCDD2; color:#B71C1C;}
    .neu {background:#FFF8E1; color:#333;}
                      border-top:4px solid #D32F2F; border-bottom:4px solid #D32F2F;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------
# Carga y preparación de datos
# ----------------------------------------------------------------------
def leer_archivo(origen) -> pd.DataFrame:
    """Lee CSV o Excel desde un uploader o una ruta."""
    nombre = getattr(origen, "name", str(origen)).lower()
    if hasattr(origen, "seek"):
        origen.seek(0)  # los archivos del uploader se releen en cada ejecución
    if nombre.endswith((".xlsx", ".xls")):
        return pd.read_excel(origen)
    return pd.read_csv(origen, sep=None, engine="python")  # detecta separador


CLAVE_VISITA = ["tour_display_id", "visit_order", "driver_name"]


def unir_archivos(archivos) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lee y concatena varios archivos. Devuelve (datos, resumen por archivo).
    Si una visita aparece en más de un archivo, se conserva la del último archivo cargado."""
    partes, resumen = [], []
    for a in archivos:
        try:
            df = leer_archivo(a)
        except Exception as e:  # archivo ilegible: se informa y se sigue con los demás
            st.warning(f"No se pudo leer {a.name}: {e}")
            continue
        df["archivo_origen"] = a.name
        partes.append(df)
        resumen.append({"Archivo": a.name, "Filas": len(df)})
    if not partes:
        return pd.DataFrame(), pd.DataFrame(resumen)

    datos = pd.concat(partes, ignore_index=True)
    antes = len(datos)
    if all(c in datos.columns for c in CLAVE_VISITA):
        datos = datos.drop_duplicates(subset=CLAVE_VISITA, keep="last")
    else:
        datos = datos.drop_duplicates(subset=[c for c in datos.columns if c != "archivo_origen"], keep="last")
    st.session_state["duplicadas"] = antes - len(datos)
    return datos.reset_index(drop=True), pd.DataFrame(resumen)


def datos_demo() -> pd.DataFrame:
    """Datos de demostración cuando no hay archivo."""
    rng = np.random.default_rng(7)
    choferes = ["Chofer A", "Chofer B", "Chofer C", "Chofer D", "Chofer E"]
    filas = []
    for dia in pd.date_range("2026-10-12", periods=7):
        for ch in choferes:
            for _ in range(rng.integers(15, 30)):
                llegada = dia + pd.Timedelta(hours=int(rng.integers(8, 17)), minutes=int(rng.integers(0, 60)))
                anticipo = max(rng.normal(240 if ch < "D" else 100, 90), 0)
                filas.append({
                    "driver_name": ch,
                    "tour_display_id": f"P{choferes.index(ch)+1:03d}",
                    "tour_date": dia.date().isoformat(),
                    "driver_notification_time": (llegada - pd.Timedelta(seconds=anticipo)).isoformat() + "Z",
                    "arrived_at": llegada.isoformat() + "Z" if rng.random() > 0.05 else None,
                })
    return pd.DataFrame(filas)


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula anticipación y banderas de cumplimiento."""
    faltan = [c for c in COLUMNAS_REQ if c not in df.columns]
    if faltan:
        st.error(f"Faltan columnas en el archivo: {', '.join(faltan)}")
        st.stop()

    df = df.copy()
    # format="ISO8601": acepta marcas con y sin milisegundos en la misma columna
    df["arrived_at"] = pd.to_datetime(df["arrived_at"], utc=True, errors="coerce", format="ISO8601")
    df["driver_notification_time"] = pd.to_datetime(df["driver_notification_time"], utc=True, errors="coerce", format="ISO8601")

    # Fecha de la ruta: tour_date > trip_start_timestamp > fecha de llegada
    if "tour_date" in df.columns:
        fecha = pd.to_datetime(df["tour_date"], errors="coerce")
    elif "trip_start_timestamp" in df.columns:
        fecha = pd.to_datetime(df["trip_start_timestamp"], utc=True, errors="coerce", format="ISO8601").dt.tz_localize(None)
    else:
        fecha = df["arrived_at"].dt.tz_localize(None)
    df["fecha"] = fecha.dt.normalize()

    if "tour_display_id" not in df.columns:
        df["tour_display_id"] = "N/D"

    # Regla 1: excluir visitas sin llegada
    df = df[df["arrived_at"].notna() & df["fecha"].notna()].copy()

    # Reglas 2 y 3: anticipación en segundos (sin notificación => no cumple)
    df["anticipacion_seg"] = (df["arrived_at"] - df["driver_notification_time"]).dt.total_seconds()
    df["cumple_2"] = df["anticipacion_seg"] >= 120
    df["cumple_5"] = df["anticipacion_seg"] >= 300

    # Hora local de la visita (para analizar la evolución durante el día)
    df["hora"] = df["arrived_at"].dt.tz_convert(ZONA_LOCAL).dt.hour
    return df


def asignar_periodo(df: pd.DataFrame, frecuencia: str) -> pd.DataFrame:
    """Agrega la columna 'periodo' según la frecuencia elegida."""
    df = df.copy()
    if frecuencia == "Diario":
        df["periodo"] = df["fecha"].dt.strftime("%Y-%m-%d")
    elif frecuencia == "Semanal":
        iso = df["fecha"].dt.isocalendar()
        df["periodo"] = iso["year"].astype(str) + "-S" + iso["week"].astype(str).str.zfill(2)
    else:
        df["periodo"] = df["fecha"].dt.strftime("%Y-%m")
    return df


def pct(serie: pd.Series) -> float:
    return float(serie.mean() * 100) if len(serie) else np.nan


# ----------------------------------------------------------------------
# Estilos de tabla y tarjetas
# ----------------------------------------------------------------------
def color_celda(v):
    if pd.isna(v):
        return ""
    return "background-color:#C8E6C9;color:#1B5E20" if v >= OBJETIVO else "background-color:#FFCDD2;color:#B71C1C"


PLANTILLA_TABLA = """
<style>
  body {font-family: "Source Sans Pro", sans-serif; margin:0;}
  #wrap {background:#fff; padding:4px;}
  #wrap:fullscreen {padding:24px; overflow:auto;}
  .barra {display:flex; gap:8px; justify-content:flex-end; margin-bottom:8px;}
  .barra button {border:1px solid #d9a800; background:#FFF8E1; border-radius:6px; padding:6px 12px;
                 cursor:pointer; font-size:.9rem;}
  .barra button:hover {background:#FFC933;}
  table {width:100%; border-collapse:collapse;}
  th {background:#FFC933; color:#222; padding:10px; text-align:center; border:1px solid #d9a800;
      cursor:pointer; user-select:none;}
  th:hover {background:#FFB300;}
  td {padding:8px 10px; text-align:center; border:1px solid #e0e0e0; font-size:1rem;}
  td.nom {text-align:left;}
  td.ok  {background:#C8E6C9; color:#1B5E20;}
  td.bad {background:#FFCDD2; color:#B71C1C;}
  tr.gral td {font-size:1.6rem; font-weight:800; padding:16px 10px;
              border-top:4px solid #D32F2F; border-bottom:4px solid #D32F2F;}
  tr.gral td.nom {background:#FFB300; color:#222; letter-spacing:1px;}
  tr.gral td.cli {background:#FFE082; color:#222;}
  tr.gral td.ok  {background:#2E7D32; color:#fff;}
  tr.gral td.bad {background:#C62828; color:#fff;}
  #wrap:fullscreen td {font-size:1.2rem;} #wrap:fullscreen tr.gral td {font-size:2rem;}
</style>
<div id="wrap">
  <div class="barra">
    <button onclick="fsTabla()">⛶ Maximizar tabla</button>
    <button onclick="fsDash()">🖥️ Dashboard en pantalla completa</button>
  </div>
  <table id="t">__CABECERA__<tbody>__FILAS__</tbody></table>
</div>
<script>
  const dirs = {};
  function ordenar(i) {
    const tb = document.querySelector('#t tbody');
    const asc = dirs[i] === undefined ? false : !dirs[i];   // primer clic: mayor a menor
    dirs[i] = asc;
    const filas = Array.from(tb.rows);
    filas.sort((a, b) => {
      const x = a.cells[i], y = b.cells[i];
      const vx = x.dataset.v !== undefined ? parseFloat(x.dataset.v) : x.textContent.toLowerCase();
      const vy = y.dataset.v !== undefined ? parseFloat(y.dataset.v) : y.textContent.toLowerCase();
      return (vx > vy ? 1 : vx < vy ? -1 : 0) * (asc ? 1 : -1);
    });
    filas.forEach(f => tb.appendChild(f));
    document.querySelectorAll('#t th').forEach((th, k) => {
      th.textContent = th.dataset.t + (k === i ? (asc ? ' ▲' : ' ▼') : '');
    });
  }
  document.querySelectorAll('#t th').forEach((th, k) => {
    th.dataset.t = th.textContent; th.onclick = () => ordenar(k);
  });
  function fsTabla() {
    const w = document.getElementById('wrap');
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    w.requestFullscreen().catch(() => alert('Tu navegador bloqueó la pantalla completa.'));
  }
  function fsDash() {
    try {
      const d = window.parent.document;
      if (d.fullscreenElement) { d.exitFullscreen(); return; }
      d.documentElement.requestFullscreen().catch(() => alert('Usá F11 para pantalla completa.'));
    } catch (e) { alert('Usá F11 para pantalla completa.'); }
  }
</script>
"""


def tabla_componente(tabla: pd.DataFrame) -> tuple[str, int]:
    """Tabla HTML ordenable (clic en encabezados) con la fila GENERAL fija arriba."""
    c2, c5 = "Notificaciones mayores a 2 minutos", "Notificaciones mayores a 5 minutos"

    def fila(r, clase_tr=""):
        k2, k5 = ("ok" if r[c2] >= OBJETIVO else "bad"), ("ok" if r[c5] >= OBJETIVO else "bad")
        return (
            f'<tr class="{clase_tr}"><td class="nom">{escape(str(r["CHOFER"]))}</td>'
            f'<td class="cli" data-v="{int(r["Clientes"])}">{int(r["Clientes"])}</td>'
            f'<td class="{k2}" data-v="{r[c2]:.4f}">{r[c2]:.0f}%</td>'
            f'<td class="{k5}" data-v="{r[c5]:.4f}">{r[c5]:.0f}%</td></tr>'
        )

    gral = tabla[tabla["CHOFER"] == "GENERAL"].iloc[0]
    choferes = tabla[tabla["CHOFER"] != "GENERAL"]
    cabecera = (f"<thead><tr><th>CHOFER</th><th>Clientes</th><th>{c2}</th><th>{c5}</th></tr>"
                f"{fila(gral, 'gral')}</thead>")
    html = (PLANTILLA_TABLA.replace("__CABECERA__", cabecera)
            .replace("__FILAS__", "".join(fila(r) for _, r in choferes.iterrows())))
    alto = min(190 + 38 * len(choferes), 800)
    return html, alto


def clase(v):
    return "neu" if pd.isna(v) else ("ok" if v >= OBJETIVO else "bad")


def a_excel(tabla: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        tabla.to_excel(w, index=False, sheet_name="Notificación")
    return buf.getvalue()


# ----------------------------------------------------------------------
# Encabezado
# ----------------------------------------------------------------------
st.markdown('<p class="titulo">TAREA CRÍTICA: NOTIFICACIÓN AL CLIENTE</p>', unsafe_allow_html=True)
st.markdown(f'<p class="subtitulo">Objetivo: {OBJETIVO:.0f}% notificado con más de 2 minutos</p>',
            unsafe_allow_html=True)

# ----------------------------------------------------------------------
# Carga de archivo (principal + sidebar; ambos comparten la misma fuente)
# ----------------------------------------------------------------------
tipos = ["csv", "xlsx", "xls"]
sub_sidebar = st.sidebar.file_uploader("📂 Cargar CSV/Excel (uno o varios)", type=tipos,
                                       accept_multiple_files=True, key="up_side")
sub_main = st.file_uploader("📂 Cargar o actualizar archivos de rutas (podés subir varios CSV/Excel)",
                            type=tipos, accept_multiple_files=True, key="up_main")

# Ambos uploaders alimentan el dashboard; si un archivo está en los dos, se usa una sola vez
archivos, vistos = [], set()
for a in list(sub_main or []) + list(sub_sidebar or []):
    if (a.name, a.size) not in vistos:
        vistos.add((a.name, a.size))
        archivos.append(a)

resumen_archivos = pd.DataFrame()
if archivos:
    bruto, resumen_archivos = unir_archivos(archivos)
    if bruto.empty:
        st.error("Ningún archivo pudo leerse.")
        st.stop()
    dup = st.session_state.get("duplicadas", 0)
    origen = f"{len(resumen_archivos)} archivo(s) cargado(s) · {len(bruto)} filas"
    if dup:
        origen += f" · {dup} filas repetidas descartadas"
elif Path(ARCHIVO_DEFECTO).exists():
    bruto = leer_archivo(ARCHIVO_DEFECTO)
    origen = f"Archivo local: {ARCHIVO_DEFECTO}"
else:
    bruto = datos_demo()
    origen = "Datos de demostración (cargá uno o varios archivos para ver datos reales)"
st.caption(origen)
if not resumen_archivos.empty:
    with st.expander("Archivos incluidos"):
        st.dataframe(resumen_archivos, use_container_width=True, hide_index=True)

datos = preparar(bruto)
if datos.empty:
    st.warning("No hay visitas con registro de llegada para evaluar.")
    st.stop()

# ----------------------------------------------------------------------
# Filtros (sidebar)
# ----------------------------------------------------------------------
st.sidebar.header("Filtros")
frecuencia = st.sidebar.radio("Frecuencia", ["Diario", "Semanal", "Mensual"], horizontal=True)

f_min, f_max = datos["fecha"].min().date(), datos["fecha"].max().date()
rango = st.sidebar.date_input("Fecha / rango", value=(f_min, f_max), min_value=f_min, max_value=f_max)
if isinstance(rango, tuple) and len(rango) == 2:
    desde, hasta = rango
else:
    desde = hasta = rango[0] if isinstance(rango, tuple) else rango

choferes = sorted(datos["driver_name"].dropna().unique())
sel_ch = st.sidebar.multiselect("Chofer", choferes, default=choferes)
planillas = sorted(datos["tour_display_id"].dropna().astype(str).unique())
sel_pl = st.sidebar.multiselect("Planilla / Ruta", planillas, default=planillas)

mask = (
    (datos["fecha"].dt.date >= desde) & (datos["fecha"].dt.date <= hasta)
    & datos["driver_name"].isin(sel_ch)
    & datos["tour_display_id"].astype(str).isin(sel_pl)
)
d = asignar_periodo(datos[mask], frecuencia)
if d.empty:
    st.info("Sin datos para los filtros seleccionados.")
    st.stop()

# ----------------------------------------------------------------------
# Tarjeta consolidada
# ----------------------------------------------------------------------
g2, g5 = pct(d["cumple_2"]), pct(d["cumple_5"])
etiqueta = f"{desde:%d/%m/%Y}" if desde == hasta else f"{desde:%d/%m/%Y} - {hasta:%d/%m/%Y}"
st.markdown(f"**FECHA:** {etiqueta}")

# ----------------------------------------------------------------------
# Tabla por chofer
# ----------------------------------------------------------------------
tabla = (
    d.groupby("driver_name")
    .agg(Clientes=("arrived_at", "size"), c2=("cumple_2", "mean"), c5=("cumple_5", "mean"))
    .reset_index()
    .rename(columns={"driver_name": "CHOFER",
                     "c2": "Notificaciones mayores a 2 minutos",
                     "c5": "Notificaciones mayores a 5 minutos"})
)
tabla["Notificaciones mayores a 2 minutos"] *= 100
tabla["Notificaciones mayores a 5 minutos"] *= 100
tabla = tabla.sort_values("Notificaciones mayores a 2 minutos", ascending=False).reset_index(drop=True)

# Fila GENERAL al inicio de la misma tabla (hace de encabezado con el puntaje global)
general = pd.DataFrame([{
    "CHOFER": "GENERAL",
    "Clientes": len(d),
    "Notificaciones mayores a 2 minutos": g2,
    "Notificaciones mayores a 5 minutos": g5,
}])
tabla = pd.concat([general, tabla], ignore_index=True)

tab1, tab2, tab3 = st.tabs(["📋 Por chofer", "📈 Evolución", "⚠️ Visitas < 2 min"])

with tab1:
    html_tabla, alto_tabla = tabla_componente(tabla)
    components.html(html_tabla, height=alto_tabla, scrolling=True)
    st.caption("Hacé clic en un encabezado para ordenar (▲▼). La fila GENERAL queda fija arriba.")
    st.download_button("⬇️ Descargar Excel", a_excel(tabla.round(1)), "notificacion_por_chofer.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab2:
    evo = (d.groupby("periodo")
             .agg(**{"% > 2 min": ("cumple_2", "mean"), "% > 5 min": ("cumple_5", "mean")})
             .mul(100).reset_index())
    fig = px.line(evo.melt("periodo", var_name="Métrica", value_name="%"),
                  x="periodo", y="%", color="Métrica", markers=True,
                  color_discrete_sequence=["#D32F2F", "#F9A825"])
    fig.add_hline(y=OBJETIVO, line_dash="dash", line_color="green", annotation_text=f"Objetivo {OBJETIVO:.0f}%")
    fig.update_layout(yaxis_range=[0, 105], xaxis_title=frecuencia, yaxis_title="% cumplimiento")
    st.plotly_chart(fig, use_container_width=True)

    # --- Evolución a lo largo del horario del día ---
    st.subheader("Evolución según la hora del día")
    hor = (d.groupby("hora")
             .agg(Visitas=("arrived_at", "size"),
                  **{"% > 2 min": ("cumple_2", "mean"), "% > 5 min": ("cumple_5", "mean")})
             .reset_index())
    hor[["% > 2 min", "% > 5 min"]] *= 100
    hor["Franja"] = hor["hora"].astype(str).str.zfill(2) + ":00"

    fig_h = px.line(hor.melt(["hora", "Franja", "Visitas"], var_name="Métrica", value_name="%"),
                    x="Franja", y="%", color="Métrica", markers=True, hover_data=["Visitas"],
                    color_discrete_sequence=["#D32F2F", "#F9A825"])
    fig_h.add_hline(y=OBJETIVO, line_dash="dash", line_color="green", annotation_text=f"Objetivo {OBJETIVO:.0f}%")
    fig_h.update_layout(yaxis_range=[0, 105], xaxis_title="Hora de llegada al cliente (hora local)",
                        yaxis_title="% cumplimiento")
    st.plotly_chart(fig_h, use_container_width=True)

    fig_v = px.bar(hor, x="Franja", y="Visitas", color_discrete_sequence=["#FFC933"])
    fig_v.update_layout(height=220, xaxis_title=None, yaxis_title="Visitas evaluadas")
    st.plotly_chart(fig_v, use_container_width=True)

with tab3:
    inc = d[~d["cumple_2"]].copy()
    inc["Anticipación (seg)"] = inc["anticipacion_seg"].round(0)
    inc["arrived_at"] = inc["arrived_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
    vista = (inc[["fecha", "driver_name", "tour_display_id", "arrived_at", "Anticipación (seg)"]]
             .rename(columns={"fecha": "Fecha", "driver_name": "Chofer",
                              "tour_display_id": "Planilla", "arrived_at": "Llegada"})
             .sort_values("Anticipación (seg)", na_position="first"))
    vista["Fecha"] = vista["Fecha"].dt.strftime("%Y-%m-%d")
    st.caption(f"{len(vista)} visitas sin cumplir (vacío = sin notificación registrada).")
    st.dataframe(vista, use_container_width=True, hide_index=True)
