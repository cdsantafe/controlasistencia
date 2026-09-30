import re
import unicodedata
from datetime import date
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st

# ───────────────────────── CONFIGURACIÓN ─────────────────────────
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/105lSTWCMAiXiG-zTaCZO6ZFtp-K4R1U1NEL1N4Vpess/edit"
WS_RESPUESTAS = "Respuestas de formulario 1"
WS_VACACIONES = "Vacaciones 2026"   # nombre exacto de la pestaña de vacaciones/ausencias
WS_PADRON = None                    # opcional: pestaña con el padrón completo (ej. "Datos")
UMBRAL = 3                          # asistencias mínimas por semana para el 100%
CACHE_TTL = 300                     # segundos entre lecturas de Google Sheets
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

st.set_page_config(page_title="Control Asistencia Matinal", page_icon="🕖", layout="wide")


# ───────────────────────── UTILIDADES ─────────────────────────
def clean_name(s):
    return re.sub(r"\s+", " ", str(s)).strip() if pd.notna(s) else ""


def norm_key(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def norm_col(c):
    return norm_key(c).lower()


def buscar_col(df, *claves, exacto=False):
    for c in df.columns:
        n = norm_col(c)
        if any((n == k) if exacto else (k in n) for k in claves):
            return c
    return None


def mostrar(obj):
    try:
        st.dataframe(obj, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(obj, use_container_width=True, hide_index=True)


def estilo_pct(v):
    if pd.isna(v):
        return "background-color:#e5e7eb;color:#374151"
    if v >= 99.999:
        return "background-color:#bbf7d0;color:#14532d"
    if v >= 70:
        return "background-color:#fef08a;color:#713f12"
    return "background-color:#fecaca;color:#7f1d1d"


def estilo_celda(v):
    v = str(v)
    if v.startswith("🏖"):
        return "background-color:#e5e7eb;color:#374151"
    if "✅" in v:
        return "background-color:#bbf7d0;color:#14532d"
    if v.startswith("0"):
        return "background-color:#fecaca;color:#7f1d1d"
    return "background-color:#fef08a;color:#713f12"


def mostrar_pct(df, cols):
    sty = df.style.map(estilo_pct, subset=cols).format({c: "{:.0f}%" for c in cols}, na_rep="—")
    mostrar(sty)


def estado(p):
    if pd.isna(p):
        return "⚪ Sin semanas activas"
    if p >= 99.999:
        return "🟢 100%"
    return "🟡 Parcial" if p >= 70 else "🔴 Bajo"


# ───────────────────────── CARGA Y LIMPIEZA ─────────────────────────
def leer_hoja(nombre):
    """Lee una pestaña como CSV (la planilla debe estar compartida: cualquiera con el enlace, Lector)."""
    doc_id = re.search(r"/d/([\w-]+)", SPREADSHEET_URL).group(1)
    url = (f"https://docs.google.com/spreadsheets/d/{doc_id}/gviz/tq"
           f"?tqx=out:csv&headers=1&sheet={quote(nombre, safe='')}")
    return pd.read_csv(url)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Leyendo Google Sheets…")
def cargar(ws_resp, ws_vac, ws_padron):
    resp = leer_hoja(ws_resp)

    def leer_opcional(ws):
        if not ws:
            return None
        try:
            df = leer_hoja(ws)
        except Exception:
            return None
        # si el nombre no existe, Google puede devolver otra pestaña: se descarta
        return None if list(df.columns) == list(resp.columns) else df

    return resp, leer_opcional(ws_vac), leer_opcional(ws_padron)


def preparar_respuestas(raw):
    df = raw.dropna(how="all").copy()
    df.columns = [str(c).strip() for c in df.columns]
    c_nom = buscar_col(df, "apellido y nombre", "apellido")
    c_fecha = buscar_col(df, "fecha", exacto=True)
    c_marca = buscar_col(df, "marca temporal")
    c_sem = buscar_col(df, "semana", exacto=True)
    c_mes = buscar_col(df, "mes", exacto=True)
    c_anio = buscar_col(df, "ano", exacto=True)
    c_op = buscar_col(df, "operador")
    if not c_nom or not (c_fecha or c_marca):
        st.error("No encuentro las columnas 'Apellido y nombre' y 'Fecha'/'Marca temporal' en la pestaña de respuestas.")
        st.stop()

    vacio = pd.Series(pd.NaT, index=df.index)
    marca = pd.to_datetime(df[c_marca], dayfirst=True, errors="coerce") if c_marca else vacio
    fecha = pd.to_datetime(df[c_fecha], dayfirst=True, errors="coerce") if c_fecha else vacio
    fecha = fecha.fillna(marca).dt.normalize()

    def num(c):
        return pd.to_numeric(df[c], errors="coerce") if c else pd.Series(np.nan, index=df.index)

    out = pd.DataFrame({
        "persona": df[c_nom].map(clean_name),
        "fecha": fecha,
        "hora": marca.dt.strftime("%H:%M:%S"),
        "operador": (df[c_op] if c_op else pd.Series("Sin dato", index=df.index)).fillna("Sin dato").map(clean_name),
    })
    iso = out["fecha"].dt.isocalendar()
    out["semana"] = num(c_sem).fillna(iso.week.astype("float"))
    out["anio"] = num(c_anio).fillna(iso.year.astype("float"))
    out["mes"] = num(c_mes).fillna(out["fecha"].dt.month.astype("float"))
    out["key"] = out["persona"].map(norm_key)

    valido = out["key"].ne("") & out["fecha"].notna()
    invalidas = int((~valido).sum())
    out = out[valido].copy()
    for c in ("semana", "anio", "mes"):
        out[c] = out[c].astype(int)
    desfase = int((out["fecha"].dt.month != out["mes"]).sum())

    out = out.sort_values(["fecha", "hora"])
    n_antes = len(out)
    out = out.drop_duplicates(["key", "fecha"], keep="first").reset_index(drop=True)  # 1 asistencia por persona y día
    info = {"filas": len(raw), "invalidas": invalidas, "duplicadas": n_antes - len(out), "desfase_mes": desfase}
    return out, info


def _rango_semanas(txt):
    nums = [int(x) for x in re.findall(r"\d+", str(txt))]
    if len(nums) == 2 and nums[0] < nums[1] and re.search(r"\d+\s*(-|–|al|a)\s*\d+", str(txt)):
        return list(range(nums[0], nums[1] + 1))
    return nums


def preparar_vacaciones(raw):
    cols = ["key", "persona", "semana", "motivo"]
    if raw is None or raw.dropna(how="all").empty:
        return pd.DataFrame(columns=cols)
    df = raw.dropna(how="all").copy()
    df.columns = [str(c).strip() for c in df.columns]
    c_nom = buscar_col(df, "apellido", "nombre", "persona", "empleado") or df.columns[0]
    c_mot = buscar_col(df, "motivo", "tipo", "estado", "novedad")
    cols_sem = [c for c in df.columns if "semana" in norm_col(c)]
    filas = []
    for _, r in df.iterrows():
        nom = clean_name(r[c_nom])
        if not nom:
            continue
        if len(cols_sem) == 1:  # formato largo: una fila por persona/semana (o rango)
            motivo = clean_name(r[c_mot]) if c_mot else "Vacaciones"
            for s in _rango_semanas(r[cols_sem[0]]):
                filas.append((norm_key(nom), nom, s, motivo))
        else:  # formato ancho: una columna por semana, celda con contenido = ausente
            for c in df.columns:
                m = re.search(r"\d+", str(c))
                if c == c_nom or c == c_mot or not m:
                    continue
                v = r[c]
                if pd.notna(v) and str(v).strip() not in ("", "0", "False", "nan"):
                    filas.append((norm_key(nom), nom, int(m.group()), clean_name(v)))
    return pd.DataFrame(filas, columns=cols).drop_duplicates(["key", "semana"])


def preparar_padron(raw):
    if raw is None or raw.dropna(how="all").empty:
        return pd.DataFrame(columns=["key", "persona", "operador"])
    df = raw.dropna(how="all").copy()
    df.columns = [str(c).strip() for c in df.columns]
    c_nom = buscar_col(df, "apellido", "nombre") or df.columns[0]
    c_op = buscar_col(df, "operador")
    out = pd.DataFrame({"persona": df[c_nom].map(clean_name)})
    out["operador"] = df[c_op].fillna("Sin dato").map(clean_name) if c_op else "Sin dato"
    out["key"] = out["persona"].map(norm_key)
    return out[out["key"].ne("")].drop_duplicates("key")


# ───────────────────────── DATOS ─────────────────────────
st.title("🕖 Control de Asistencia Matinal")

try:
    raw_resp, raw_vac, raw_pad = cargar(WS_RESPUESTAS, WS_VACACIONES, WS_PADRON)
except Exception as e:
    st.error("No pude leer la planilla. Verificá que esté compartida como 'Cualquier persona con el enlace → Lector' "
             "y que el nombre de la pestaña sea exacto.")
    st.code(f"{type(e).__name__}: {e}")
    st.stop()
asist_all, info = preparar_respuestas(raw_resp)
vac_df = preparar_vacaciones(raw_vac)
padron = preparar_padron(raw_pad)
if raw_vac is None:
    st.warning(f"No pude leer la pestaña '{WS_VACACIONES}'. Se calcula sin excepciones de vacaciones.")

# ───────────────────────── FILTROS ─────────────────────────
sb = st.sidebar
sb.header("Filtros")
if sb.button("🔄 Actualizar datos"):
    st.cache_data.clear()
    st.rerun()

anios = sorted(asist_all["anio"].unique())
anio = sb.selectbox("Año", anios, index=len(anios) - 1)
base = asist_all[asist_all["anio"] == anio]
fmin, fmax = base["fecha"].min().date(), base["fecha"].max().date()
rango = sb.date_input("Rango de fechas", (fmin, fmax), min_value=fmin, max_value=fmax, format="DD/MM/YYYY")
if not isinstance(rango, (tuple, list)) or len(rango) != 2:
    st.info("Elegí fecha de inicio y de fin en el filtro de la izquierda.")
    st.stop()
d1, d2 = rango
meses_sel = sb.multiselect("Mes", sorted(base["mes"].unique()), format_func=lambda m: MESES[m - 1])
sem_sel = sb.multiselect("Semana", sorted(base["semana"].unique()))

# Padrón: todos los que aparecen en respuestas del año, vacaciones y (opcional) padrón
roster = (base.sort_values("fecha").groupby("key")
          .agg(persona=("persona", "last"), operador=("operador", "last")).reset_index())
extras = pd.concat([
    vac_df[["key", "persona"]].drop_duplicates("key").assign(operador="Sin dato"),
    padron[["key", "persona", "operador"]],
]).drop_duplicates("key")
extras = extras[~extras["key"].isin(roster["key"])]
if len(extras):
    roster = pd.concat([roster, extras], ignore_index=True)
roster = roster.sort_values("persona").reset_index(drop=True)

ops = sb.multiselect("Operador logístico", sorted(roster["operador"].unique()))
q = sb.text_input("Buscar nombre / apellido")
excluir_en_curso = sb.checkbox("Excluir semana en curso del cálculo", value=True,
                               help="La semana que todavía no terminó penalizaría el cumplimiento.")

if ops:
    roster = roster[roster["operador"].isin(ops)]
if q.strip():
    roster = roster[roster["key"].str.contains(norm_key(q), regex=False)]
if roster.empty:
    st.warning("Ningún registro coincide con los filtros.")
    st.stop()

recs = base[(base["fecha"].dt.date >= d1) & (base["fecha"].dt.date <= d2)]
if meses_sel:
    recs = recs[recs["mes"].isin(meses_sel)]
if sem_sel:
    recs = recs[recs["semana"].isin(sem_sel)]
if recs.empty:
    st.warning("No hay marcas en el período elegido.")
    st.stop()

semanas_tbl = (recs.groupby("semana")
               .agg(desde=("fecha", "min"), hasta=("fecha", "max"), dias=("fecha", "nunique"),
                    mes=("mes", lambda s: int(s.mode().iat[0])))
               .reset_index().sort_values("desde"))
hoy = date.today().isocalendar()
if excluir_en_curso and anio == hoy.year:
    semanas_tbl = semanas_tbl[semanas_tbl["semana"] != hoy.week]
if semanas_tbl.empty:
    st.warning("No quedan semanas completas para evaluar con estos filtros.")
    st.stop()

recs_eval = recs[recs["semana"].isin(semanas_tbl["semana"])]
vac_set = set(zip(vac_df["key"], vac_df["semana"]))

# ───────────────────────── CÁLCULO ─────────────────────────
grid = roster[["key", "persona", "operador"]].merge(semanas_tbl[["semana", "mes"]], how="cross")
cnt = recs_eval.groupby(["key", "semana"]).size().rename("asistencias").reset_index()
grid = grid.merge(cnt, on=["key", "semana"], how="left")
grid["asistencias"] = grid["asistencias"].fillna(0).astype(int)
grid["vac"] = [(k, s) in vac_set for k, s in zip(grid["key"], grid["semana"])]
grid["pct"] = np.where(grid["vac"], np.nan, grid["asistencias"].clip(upper=UMBRAL) / UMBRAL * 100)
grid["cumple"] = (~grid["vac"]) & (grid["asistencias"] >= UMBRAL)

res = grid.groupby("key").agg(activas=("vac", lambda s: int((~s).sum())), vacs=("vac", "sum"),
                              cumplidas=("cumple", "sum"), pct=("pct", "mean")).reset_index()
tot = recs_eval.groupby("key").size().rename("total").reset_index()
res = roster.merge(res, on="key", how="left").merge(tot, on="key", how="left")
res["total"] = res["total"].fillna(0).astype(int)

# ───────────────────────── KPIs ─────────────────────────
k1, k2, k3, k4 = st.columns(4)
k1.metric("Personas", len(roster))
k2.metric("Asistencias válidas", int(res["total"].sum()))
k3.metric("Semanas evaluadas", len(semanas_tbl))
k4.metric("Cumplimiento global", f"{grid['pct'].mean():.0f}%" if grid["pct"].notna().any() else "—")
st.caption(f"Regla: mínimo {UMBRAL} asistencias por semana = 100%; menos se calcula proporcional. "
           "Las semanas en vacaciones/ausente/suspendido no entran en el denominador. "
           "Máximo 1 asistencia por persona y día.")

t_gen, t_dia, t_sem, t_mes, t_diag = st.tabs(
    ["👥 Matriz general", "📅 Día", "🗓️ Semana", "📆 Mes", "🔍 Diagnóstico"])

# ───────────────────────── MATRIZ GENERAL ─────────────────────────
with t_gen:
    st.subheader("Consolidado por operador logístico")
    tot_op = (recs_eval.drop(columns="operador").merge(roster[["key", "operador"]], on="key")
              .groupby("operador").size().rename("Asistencias"))
    op = grid.groupby("operador").agg(Personas=("key", "nunique"),
                                      **{"Semanas activas": ("vac", lambda s: int((~s).sum()))},
                                      **{"Semanas cumplidas": ("cumple", "sum")},
                                      **{"% Cumplimiento": ("pct", "mean")}).join(tot_op).reset_index()
    op = op.rename(columns={"operador": "Operador logístico"})
    op["Asistencias"] = op["Asistencias"].fillna(0).astype(int)
    mostrar_pct(op[["Operador logístico", "Personas", "Asistencias", "Semanas activas",
                    "Semanas cumplidas", "% Cumplimiento"]], ["% Cumplimiento"])

    st.subheader("Listado completo de personas")
    m = res.rename(columns={"persona": "Persona", "operador": "Operador logístico", "total": "Total asistencias",
                            "activas": "Semanas activas", "vacs": "Semanas vacaciones/licencia",
                            "cumplidas": "Semanas cumplidas", "pct": "% Cumplimiento"})
    m["Estado"] = m["% Cumplimiento"].map(estado)
    mostrar_pct(m[["Persona", "Operador logístico", "Total asistencias", "Semanas activas",
                   "Semanas vacaciones/licencia", "Semanas cumplidas", "% Cumplimiento", "Estado"]],
                ["% Cumplimiento"])
    st.download_button("⬇️ Descargar CSV", m.to_csv(index=False).encode("utf-8-sig"),
                       "cumplimiento_general.csv", "text/csv")

# ───────────────────────── DÍA ─────────────────────────
with t_dia:
    fechas = sorted(recs["fecha"].dt.date.unique(), reverse=True)
    dia = st.selectbox("Fecha", fechas, format_func=lambda d: d.strftime("%d/%m/%Y"))
    del_dia = recs[recs["fecha"].dt.date == dia]
    sem_dia = int(del_dia["semana"].mode().iat[0])
    horas = del_dia.set_index("key")["hora"]
    d = roster[["key", "persona", "operador"]].copy()
    d["hora"] = d["key"].map(horas)
    d["vac"] = [(k, sem_dia) in vac_set for k in d["key"]]
    d["Estado"] = np.where(d["hora"].notna(), "✅ Asistió",
                           np.where(d["vac"], "🏖 Vacaciones/Ausente", "❌ No asistió"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Asistieron", int((d["Estado"] == "✅ Asistió").sum()))
    c2.metric("No asistieron", int((d["Estado"] == "❌ No asistió").sum()))
    c3.metric("Vacaciones / Ausente", int((d["Estado"] == "🏖 Vacaciones/Ausente").sum()))
    filtro = st.radio("Mostrar", ["Todos", "Solo asistieron", "Solo no asistieron"], horizontal=True)
    if filtro == "Solo asistieron":
        d = d[d["Estado"] == "✅ Asistió"]
    elif filtro == "Solo no asistieron":
        d = d[d["Estado"] == "❌ No asistió"]
    mostrar(d.rename(columns={"persona": "Persona", "operador": "Operador logístico", "hora": "Hora de marca"})
            [["Persona", "Operador logístico", "Estado", "Hora de marca"]])
    st.markdown("**Asistentes por día (período filtrado)**")
    st.bar_chart(recs[recs["key"].isin(roster["key"])].groupby("fecha").size())

# ───────────────────────── SEMANA ─────────────────────────
with t_sem:
    grid["celda"] = [("🏖 Vac." if v else (f"{n} ✅" if n >= UMBRAL else f"{n} ⚠️"))
                     for n, v in zip(grid["asistencias"], grid["vac"])]
    piv = grid.pivot(index=["persona", "operador"], columns="semana", values="celda")
    piv.columns = [f"S{c}" for c in piv.columns]
    piv = piv.reset_index().rename(columns={"persona": "Persona", "operador": "Operador logístico"})
    st.caption(f"✅ = {UMBRAL} o más asistencias · ⚠️ = menos de {UMBRAL} · 🏖 = vacaciones/ausente/suspendido (no se evalúa)")
    mostrar(piv.style.map(estilo_celda, subset=[c for c in piv.columns if c.startswith("S")]))
    st.markdown("**Cumplimiento promedio por semana**")
    st.line_chart(grid.groupby("semana")["pct"].mean().rename(index=lambda s: f"S{s}"))
    st.markdown("**Semanas del período**")
    sw = semanas_tbl.assign(desde=lambda x: x["desde"].dt.strftime("%d/%m"), hasta=lambda x: x["hasta"].dt.strftime("%d/%m"),
                            mes=lambda x: x["mes"].map(lambda m: MESES[m - 1]))
    mostrar(sw.rename(columns={"semana": "Semana", "desde": "Primer día", "hasta": "Último día",
                               "dias": "Días con marcas", "mes": "Mes"}))

# ───────────────────────── MES ─────────────────────────
with t_mes:
    gm = grid.groupby(["key", "mes"])["pct"].mean().unstack("mes")
    gm.columns = [MESES[int(c) - 1] for c in gm.columns]
    gm = roster[["key", "persona", "operador"]].merge(gm.reset_index(), on="key", how="left")
    gm = gm.merge(res[["key", "pct"]], on="key").drop(columns="key")
    gm = gm.rename(columns={"persona": "Persona", "operador": "Operador logístico", "pct": "Total período"})
    cols_pct = [c for c in gm.columns if c not in ("Persona", "Operador logístico")]
    st.caption("Cada mes agrega los resultados semanales (semanas asignadas al mes con más días de marcas).")
    st.subheader("Por persona")
    mostrar_pct(gm, cols_pct)
    st.subheader("Por operador logístico")
    go = grid.groupby(["operador", "mes"])["pct"].mean().unstack("mes")
    go.columns = [MESES[int(c) - 1] for c in go.columns]
    go = go.reset_index().rename(columns={"operador": "Operador logístico"})
    mostrar_pct(go, [c for c in go.columns if c != "Operador logístico"])

# ───────────────────────── DIAGNÓSTICO ─────────────────────────
with t_diag:
    st.write(f"Filas leídas: **{info['filas']}** · filas inválidas descartadas: **{info['invalidas']}** · "
             f"marcas duplicadas (misma persona y día) unificadas: **{info['duplicadas']}**")
    if info["desfase_mes"]:
        st.warning(f"{info['desfase_mes']} filas tienen un mes en 'Fecha' distinto a la columna 'Mes'. "
                   "Revisá el formato de fecha (debe ser día/mes/año).")
    sin_match = vac_df[~vac_df["key"].isin(asist_all["key"])]["persona"].drop_duplicates()
    if len(sin_match):
        st.info("Personas de la pestaña de vacaciones sin ninguna marca en 'Respuestas' "
                "(pueden ser nombres escritos distinto): " + ", ".join(sin_match))
    st.markdown("**Vacaciones / ausencias interpretadas**")
    mostrar(vac_df.drop(columns="key").rename(columns={"persona": "Persona", "semana": "Semana", "motivo": "Motivo"}))
