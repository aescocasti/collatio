# -*- coding: utf-8 -*-
"""
app.py — Collatio: colación automática de testimonios.
Ejecutar con:  streamlit run app.py
"""

import hashlib
import io
import csv
import json
import os
import zipfile
import streamlit as st

# Asegurar que graphviz 'dot' esté en el PATH (necesario en algunos entornos cloud)
for _gv_path in ("/usr/bin", "/usr/local/bin"):
    if _gv_path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _gv_path + ":" + os.environ.get("PATH", "")
from collatex.exceptions import SegmentationError

from collation_engine import run_collation, OUTPUT_FORMATS, extract_witnesses_from_zip, _build_preview_graph

# ---------------------------------------------------------------------------
# Configuración de la página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Collatio · Interfaz para CollateX",
    page_icon=":material/compare:",
    layout="centered",
)

# CSS personalizado
st.markdown("""
<style>
    /* Tipografía más académica */
    html, body, [class*="css"] {
        font-family: 'Georgia', serif;
    }
    /* Encabezado principal */
    .collatio-header {
        text-align: center;
        padding: 2rem 0 1rem 0;
        border-bottom: 2px solid #2c3e50;
        margin-bottom: 2rem;
    }
    .collatio-header h1 {
        font-size: 2.4rem;
        letter-spacing: 0.15em;
        color: #2c3e50;
        margin: 0;
    }
    .collatio-header p {
        color: #666;
        font-style: italic;
        margin: 0.3rem 0 0 0;
        font-size: 0.95rem;
    }
    /* Pasos numerados */
    .step-label {
        font-size: 0.75rem;
        font-weight: bold;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #888;
        margin-bottom: 0.25rem;
    }
    /* Zona de descarga */
    .download-section {
        background: #f0f4f8;
        border-radius: 10px;
        padding: 1.5rem;
        margin-top: 1rem;
    }
    /* Quitar borde de los expanders */
    details summary {
        font-size: 0.85rem;
        color: #555;
    }
    /* Footer */
    .footer {
        text-align: center;
        color: #aaa;
        font-size: 0.78rem;
        padding: 2rem 0 1rem 0;
        font-style: italic;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------

st.markdown("""
<div class="collatio-header">
    <h1>COLLATIO</h1>
    <p>Interfaz para CollateX 2.3 · Colación automática de testimonios</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# PASO 1 — Subir testimonios
# ---------------------------------------------------------------------------

st.markdown('<p class="step-label">Paso 1 · Testimonios</p>', unsafe_allow_html=True)

input_mode = st.radio(
    "Formato de entrada",
    options=["Archivos individuales", "ZIP (carpetas por testimonio)"],
    horizontal=True,
    label_visibility="collapsed",
    help="Usa ZIP si tus testimonios son carpetas con varias páginas PAGE XML "
         "(estructura: ZIP → carpeta_testimonio → hojas.xml).",
)

witnesses_from_zip = {}

if input_mode == "Archivos individuales":
    uploaded_files = st.file_uploader(
        "Sube los archivos de texto de cada testimonio",
        type=["txt", "xml"],
        accept_multiple_files=True,
        help="Archivos .txt (texto plano) o .xml (PAGE XML de eScriptorium / Transkribus). "
             "El nombre del archivo se usará como sigla.",
    )
    if uploaded_files:
        names_str = "  ·  ".join(f"`{f.name.rsplit('.',1)[0]}`" for f in uploaded_files)
        if len(uploaded_files) >= 2:
            st.success(f"{len(uploaded_files)} testimonios listos: {names_str}")
        else:
            st.warning("Necesitas al menos **dos** testimonios.")
else:
    uploaded_files = []
    zip_file = st.file_uploader(
        "Sube el ZIP con subcarpetas por testimonio",
        type=["zip"],
        help="Estructura esperada: ZIP → una carpeta por testimonio → archivos PAGE XML (.xml) por hoja.",
    )
    if zip_file:
        try:
            witnesses_from_zip = extract_witnesses_from_zip(zip_file.read())
            names_str = "  ·  ".join(f"`{n}`" for n in witnesses_from_zip)
            if len(witnesses_from_zip) >= 2:
                st.success(
                    f"{len(witnesses_from_zip)} testimonios detectados: {names_str}"
                )
            else:
                st.warning("Se necesitan al menos dos testimonios en el ZIP.")
            # Mostrar desglose de hojas por testimonio
            with zipfile.ZipFile(io.BytesIO(zip_file.getvalue())) as zf:
                for wname in witnesses_from_zip:
                    pages = [n for n in zf.namelist()
                             if f"/{wname}/" in n.replace('\\','/') and not n.endswith('/')]
                    st.caption(f"  · **{wname}**: {len(pages)} hoja(s)")
        except Exception as e:
            st.error(f"Error al leer el ZIP: {e}")

st.write("")

# ---------------------------------------------------------------------------
# PASO 2 — Etiqueta
# ---------------------------------------------------------------------------

st.markdown('<p class="step-label">Paso 2 · Título de la collatio</p>', unsafe_allow_html=True)

label = st.text_input(
    "Título de la collatio",
    value="",
    placeholder="p. ej.  2.1.27   o   libro_I",
    label_visibility="collapsed",
)

st.write("")

# ---------------------------------------------------------------------------
# Opciones avanzadas (ocultas por defecto)
# ---------------------------------------------------------------------------

with st.expander("⚙️  Opciones avanzadas", expanded=False):
    col1, col2 = st.columns(2)

    with col1:
        algorithm = "edit_graph"

        near_match = st.toggle(
            "Near matching (Levenshtein)",
            value=False,
            help="Alinea variantes ortográficas similares entre testimonios "
                 "(p. ej. 'muriere' ~ 'muriere'). Cuando está activo, la segmentación "
                 "automática se desactiva porque son incompatibles.",
        )

        # Segmentación deshabilitada si near_match está activo (son incompatibles)
        segmentation = st.toggle(
            "Segmentación automática",
            value=not near_match,
            disabled=near_match,
            help="Agrupa tokens consecutivos idénticos en segmentos, produciendo "
                 "una tabla más compacta. Incompatible con near matching.",
        )
        if near_match:
            segmentation = False

    with col2:
        layout = st.selectbox(
            "Disposición de la tabla",
            options=["vertical", "horizontal"],
            format_func=lambda k: {
                "vertical":   "Vertical — posiciones como filas",
                "horizontal": "Horizontal — posiciones como columnas",
            }[k],
            index=0,
            help="Vertical: cada fila es una posición de alineación. "
                 "Horizontal: cada columna es una posición de alineación.",
        )

        strip_punct = st.toggle(
            "Eliminar puntuación",
            value=True,
            help="Elimina signos de puntuación antes de ejecutar la colación. "
                 "Se conserva el tironiano (⁊). Desactiva si quieres incluir "
                 "la puntuación en la colación.",
        )

        detect_transpositions = False  # Roto en CollateX 2.3

    st.write("")
    st.markdown("**Formatos de salida**")
    st.caption("Por defecto se generan HTML y TEI. Puedes añadir o quitar.")

    selected_formats = st.multiselect(
        "Formatos",
        options=list(OUTPUT_FORMATS.keys()),
        default=["csv"],
        format_func=lambda k: OUTPUT_FORMATS[k],
        label_visibility="collapsed",
    )

    tei_indent = False
    if "tei" in selected_formats:
        tei_indent = st.toggle("Indentar TEI", value=True,
            help="Formatea el XML/TEI con indentación legible.")

# Defaults si el usuario no tocó opciones avanzadas
if "algorithm" not in dir():
    algorithm = "edit_graph"
if "selected_formats" not in dir():
    selected_formats = ["csv"]

st.write("")

# ---------------------------------------------------------------------------
# PASO 3 — Ejecutar
# ---------------------------------------------------------------------------

st.markdown('<p class="step-label">Paso 3 · Ejecutar collatio</p>', unsafe_allow_html=True)

n_witnesses = len(witnesses_from_zip) if witnesses_from_zip else (len(uploaded_files) if uploaded_files else 0)
run_disabled = n_witnesses < 2 or not selected_formats

run_button = st.button(
    "▶  Ejecutar collatio",
    type="primary",
    disabled=run_disabled,
    use_container_width=True,
)

# ---------------------------------------------------------------------------
# Lógica de colación
# ---------------------------------------------------------------------------

def _hash(witnesses, opts):
    h = hashlib.md5()
    for name, data in sorted(witnesses.items()):
        h.update(name.encode() + data)
    h.update(json.dumps(opts, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _max_tokens_per_witness(witnesses_dict):
    """Tokens del testimonio más largo (words tras eliminar etiquetas XML)."""
    import re as _re
    max_tok = 0
    for content in witnesses_dict.values():
        text = content.decode("utf-8", errors="replace")
        text = _re.sub(r'<[^>]+>', ' ', text)
        max_tok = max(max_tok, len(text.split()))
    return max_tok

_TOKENS_PER_WITNESS_WARN = 1500  # aviso; CollateX puede manejarlo pero será lento

if run_button and n_witnesses >= 2:
    witnesses = witnesses_from_zip if witnesses_from_zip else {
        f.name.rsplit(".", 1)[0]: f.read() for f in uploaded_files
    }

    _max_tok = _max_tokens_per_witness(witnesses)
    if _max_tok > _TOKENS_PER_WITNESS_WARN:
        st.warning(
            f"El testimonio más largo tiene ~{_max_tok:,} tokens. "
            "La colación puede ser lenta. Considera dividir el texto en secciones."
        )

    opts = dict(
        formats=sorted(selected_formats),
        layout=layout,
        segmentation=segmentation,
        near_match=near_match,
        algorithm=algorithm,
        detect_transpositions=detect_transpositions,
        strip_punct=strip_punct,
        tei_indent=tei_indent,
        label=label,
    )
    current_hash = _hash(witnesses, opts)

    if st.session_state.get("result_hash") != current_hash:
        with st.spinner("Procesando…"):
            try:
                results, rows = run_collation(witnesses, **opts)
                st.session_state.update({
                    "results": results,
                    "rows": rows,
                    "result_hash": current_hash,
                    "result_label": label or "colacion",
                    "witness_names": list(witnesses.keys()),
                    "witnesses": witnesses,
                    "strip_punct": strip_punct,
                })
            except SegmentationError as e:
                st.error(str(e))
                st.session_state.pop("results", None)
            except Exception as e:
                st.error(f"Error: {e}")
                st.session_state.pop("results", None)

# ---------------------------------------------------------------------------
# RESULTADOS
# ---------------------------------------------------------------------------

if "results" in st.session_state:
    results     = st.session_state["results"]
    rows        = st.session_state.get("rows")
    lbl         = st.session_state["result_label"]
    names       = st.session_state["witness_names"]
    _witnesses  = st.session_state.get("witnesses", {})
    _strip_punct = st.session_state.get("strip_punct", True)

    st.divider()

    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.markdown(
            f"**Colación completada** · {len(names)} testimonios · "
            + "  ".join(f"`{n}`" for n in names)
        )
    with col_btn:
        if st.button("↺ Nueva", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    st.write("")

    # --- Descargas ---
    MIME = {
        "csv":        ("text/csv",                 f"colacion_{lbl}.csv"),
        "tsv":        ("text/tab-separated-values", f"colacion_{lbl}.tsv"),
        "html":       ("text/html",                 f"colacion_{lbl}.html"),
        "json":       ("application/json",          f"colacion_{lbl}.json"),
        "xml":        ("application/xml",           f"colacion_{lbl}.xml"),
        "tei":        ("application/xml",           f"colacion_{lbl}.tei.xml"),
        "svg":        ("image/svg+xml",             f"colacion_{lbl}_grafo.svg"),
        "svg_simple": ("image/svg+xml",             f"colacion_{lbl}_grafo_simple.svg"),
        "dot":        ("text/vnd.graphviz",         f"colacion_{lbl}_grafo.gv"),
        "dot_simple": ("text/vnd.graphviz",         f"colacion_{lbl}_grafo_simple.gv"),
    }
    LABELS = {
        "csv":        "📄 CSV",
        "tsv":        "📄 TSV",
        "html":       "🌐 HTML",
        "json":       "🔣 JSON",
        "xml":        "📝 XML",
        "tei":        "📝 TEI",
        "svg":        "🔀 SVG (grafo)",
        "svg_simple": "🔀 SVG (simple)",
        "dot":        "📐 DOT (grafo)",
        "dot_simple": "📐 DOT (simple)",
    }

    fmt_keys = [k for k in ("html", "tei", "csv", "tsv", "json", "xml", "svg", "svg_simple", "dot", "dot_simple") if k in results]
    cols = st.columns(len(fmt_keys))
    for i, fmt in enumerate(fmt_keys):
        mime, filename = MIME[fmt]
        cols[i].download_button(
            label=LABELS[fmt],
            data=results[fmt],
            file_name=filename,
            mime=mime,
            use_container_width=True,
        )

    # Aviso si el SVG generado es de un grafo muy grande
    _svg_dot = results.get("dot_source") or results.get("dot_source_simple")
    if _svg_dot and (any(f in results for f in ("svg", "svg_simple"))):
        import re as _re2
        _svg_nodes = len(_re2.findall(r'^\s+\d+\s+\[label=', _svg_dot, _re2.MULTILINE))
        if _svg_nodes > 600:
            st.caption(
                f"⚠️ El SVG descargado contiene {_svg_nodes} nodos. "
                "Usa un visor externo (Inkscape, navegador) para abrirlo."
            )

    # --- Vista previa ---
    import streamlit.components.v1 as components

    st.write("")
    has_dot = "dot_source" in results or "dot_source_simple" in results
    has_svg = "svg" in results or "svg_simple" in results
    preview_tabs = st.tabs(
        [":material/table_chart: Tabla"] +
        ([":material/account_tree: Grafo variante"] if has_dot else [])
    )

    with preview_tabs[0]:
        if "html" in results:
            # Pasar el HTML completo (con estilos) a components.html para que la tabla tenga formato
            components.html(results["html"].decode("utf-8"), height=420, scrolling=True)
        elif rows:
            # st.dataframe() no admite columnas duplicadas (frecuentes en colación);
            # construimos la tabla como HTML simple.
            def _rows_to_html(rows):
                esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                hdr = "".join(f"<th>{esc(c)}</th>" for c in rows[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                    for row in rows[1:]
                )
                return (
                    "<style>table{border-collapse:collapse;font-family:Georgia,serif;font-size:0.82rem}"
                    "th,td{border:1px solid #ccc;padding:4px 8px;white-space:nowrap}"
                    "th{background:#2c3e50;color:#fff}</style>"
                    f"<table><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>"
                )
            components.html(_rows_to_html(rows), height=420, scrolling=True)
        else:
            st.info("No hay datos de tabla disponibles.")

    if has_dot:
        with preview_tabs[1]:
            # Selector si hay los dos tipos
            both_dot = "dot_source" in results and "dot_source_simple" in results
            if both_dot:
                dot_choice = st.radio(
                    "Tipo de grafo",
                    options=["dot_source", "dot_source_simple"],
                    format_func=lambda k: {"dot_source": "Detallado", "dot_source_simple": "Simplificado"}[k],
                    horizontal=True,
                    label_visibility="collapsed",
                )
            else:
                dot_choice = "dot_source" if "dot_source" in results else "dot_source_simple"

            zoom = st.slider("Zoom", min_value=0.25, max_value=2.0, value=0.5, step=0.25)

            dot_src = results[dot_choice]

            import re as _re
            _node_count = len(_re.findall(r'^\s+\d+\s+\[label=', dot_src, _re.MULTILINE))
            _PREVIEW_MAX = 600

            if _node_count > _PREVIEW_MAX:
                _preview_dot = _build_preview_graph(
                    _witnesses,
                    max_tokens=50,
                    strip_punct=_strip_punct,
                )
                if _preview_dot:
                    _render_dot = _preview_dot
                    st.caption(
                        f"⚠️ Previsualización parcial — primeros 50 tokens "
                        f"({_node_count} nodos en el grafo completo). "
                        "Descarga el SVG o DOT para ver el grafo completo."
                    )
                else:
                    _render_dot = None
                    st.info(
                        f"El grafo tiene {_node_count} nodos. "
                        "Descarga el SVG o DOT para visualizarlo localmente."
                    )
            else:
                _render_dot = dot_src
                st.caption("Grafo renderizado en el navegador. Descarga el SVG para el grafo completo.")

            if _render_dot:
                _dot_escaped = _render_dot.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                components.html(f"""<!DOCTYPE html>
<html><head>
  <meta charset="utf-8">
  <script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3/lib/viz-standalone.js"></script>
  <style>
    body {{ margin: 0; padding: 4px; background: #fff; overflow: auto; }}
    #error {{ color: #c00; font-family: monospace; padding: 1em; }}
  </style>
</head><body>
  <div id="error"></div>
  <div id="graph"></div>
  <script>
    var zoom = {zoom:.3f};
    Viz.instance().then(function(viz) {{
      var svgEl = viz.renderSVGElement(`{_dot_escaped}`);
      if (svgEl) {{
        var wPt = parseFloat(svgEl.getAttribute("width")) || 800;
        var hPt = parseFloat(svgEl.getAttribute("height")) || 400;
        svgEl.setAttribute("width",  (wPt * zoom).toFixed(1) + "pt");
        svgEl.setAttribute("height", (hPt * zoom).toFixed(1) + "pt");
        document.getElementById("graph").appendChild(svgEl);
      }}
    }}).catch(function(err) {{
      document.getElementById("error").textContent = "Error: " + err;
    }});
  </script>
</body></html>""",
                    height=520,
                    scrolling=True,
                )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.write("")
st.markdown(
    '<p class="footer">Collatio · '
    '<a href="https://collatex.net/" target="_blank">CollateX</a> · '
    'Los archivos se procesan en memoria y no se almacenan</p>',
    unsafe_allow_html=True,
)
