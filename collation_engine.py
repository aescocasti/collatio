# -*- coding: utf-8 -*-
"""
collation_engine.py
Lógica completa de colación con CollateX.
Expone todas las opciones de la librería: algoritmos, formatos, near matching, etc.
"""

import csv
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from collatex import Collation, collate
from collatex.exceptions import SegmentationError


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PUNCT_KEEP = {"\u204A"}  # tironiano ⁊

OUTPUT_FORMATS = {
    "csv":        "CSV (punto y coma)",
    "tsv":        "TSV (tabulador)",
    "xlsx":       "Excel (.xlsx)",
    "html":       "HTML (tabla)",
    "json":       "JSON",
    "xml":        "XML (pseudo-TEI)",
    "tei":        "TEI (segmentación paralela)",
    "svg":        "SVG — grafo variante (detallado)",
    "svg_simple": "SVG — grafo variante (simplificado)",
}

LAYOUT_OPTIONS = {
    "vertical":   "Vertical (posiciones como filas)",
    "horizontal": "Horizontal (posiciones como columnas)",
}

ALGORITHM_OPTIONS = {
    "edit_graph": "Edit Graph — Needleman-Wunsch (estándar)",
    # "astar" desactivado: bug en CollateX 2.3 (ExperimentalAstarAligner.collate requiere
    # dos argumentos pero core_functions.py lo llama con uno).
}


# ---------------------------------------------------------------------------
# Normalización y limpieza de texto
# ---------------------------------------------------------------------------

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def strip_punctuation(text: str) -> str:
    """Elimina puntuación excepto el tironiano ⁊; colapsa espacios."""
    out = []
    for ch in text:
        if ch in PUNCT_KEEP:
            out.append(ch)
        elif unicodedata.category(ch).startswith("P"):
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def clean_text(raw: str) -> str:
    return strip_punctuation(nfc(raw))


def decode_bytes(raw_bytes: bytes) -> str:
    return raw_bytes.decode("utf-8", errors="replace")


def dehyphenate(text: str) -> str:
    """Une palabras partidas con guión al final de línea: 'pa- dre' → 'padre'."""
    return re.sub(r'(\w)- +(\w)', r'\1\2', text)


# ---------------------------------------------------------------------------
# Parser de PAGE XML (eScriptorium / Transkribus)
# ---------------------------------------------------------------------------

# El namespace estándar de PAGE XML
_PAGE_NS = {
    "page": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15",
    # versiones alternativas del namespace
    "page2019": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15",
}


def extract_text_from_page_xml(raw_bytes: bytes) -> str:
    """
    Extrae el texto transcrito de un archivo PAGE XML (eScriptorium, Transkribus).
    Recorre TextRegion → TextLine → TextEquiv → Unicode en orden de documento.
    Devuelve el texto como string plano, líneas separadas por espacio.
    """
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as e:
        raise ValueError(f"No se pudo parsear el XML: {e}")

    # Detectar namespace automáticamente desde el elemento raíz
    tag = root.tag  # p. ej. '{http://schema.primaresearch.org/PAGE/...}PcGts'
    if "{" in tag:
        ns_uri = tag[1:tag.index("}")]
        ns = {"page": ns_uri}
    else:
        ns = {}

    lines = []

    # Intentar con namespace y sin él
    def find_all(parent, path_with_ns, path_no_ns):
        result = parent.findall(path_with_ns, ns) if ns else []
        if not result:
            result = parent.findall(path_no_ns)
        return result

    # Buscar todas las TextLine en orden
    text_lines = find_all(
        root,
        ".//page:TextLine",
        ".//TextLine",
    )

    for line in text_lines:
        # Preferir TextEquiv con index="0" o el primero disponible
        equivs = find_all(line, "page:TextEquiv", "TextEquiv")
        if not equivs:
            continue
        # Ordenar por atributo 'index' si existe; tomar el de menor índice
        equivs_sorted = sorted(
            equivs,
            key=lambda e: int(e.get("index", 0))
        )
        for equiv in equivs_sorted:
            unicode_el = (
                equiv.find("page:Unicode", ns) if ns
                else equiv.find("Unicode")
            )
            if unicode_el is not None and unicode_el.text:
                lines.append(unicode_el.text.strip())
                break  # solo la primera lectura por línea

    if not lines:
        raise ValueError(
            "No se encontró texto en el archivo PAGE XML. "
            "Verifica que el archivo tenga transcripciones en los elementos <Unicode>."
        )

    return dehyphenate(" ".join(lines))


def extract_witnesses_from_zip(zip_bytes: bytes) -> dict:
    """
    Extrae testimonios desde un ZIP con estructura de carpetas:
        zip/
          NombreTestimonio1/
            hoja_001.xml
            hoja_002.xml
          NombreTestimonio2/
            hoja_001.xml
            ...

    Cada subcarpeta = un testimonio. Las hojas se concatenan en orden alfabético.
    Devuelve {nombre_testimonio: texto_completo_bytes}.
    Ignora archivos que no sean PAGE XML.
    """
    witnesses = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in sorted(zf.namelist()):
                # Ignorar directorios y metadatos macOS
                if name.endswith('/') or '__MACOSX' in name or name.startswith('.'):
                    continue
                parts = name.replace('\\', '/').split('/')
                # Necesitamos al menos: raiz/testimonio/archivo
                if len(parts) < 2:
                    continue
                witness_name = parts[-2]
                raw = zf.read(name)
                if is_page_xml(raw):
                    witnesses.setdefault(witness_name, []).append((parts[-1], raw))
    except zipfile.BadZipFile:
        raise ValueError("El archivo no es un ZIP válido.")

    if not witnesses:
        raise ValueError(
            "No se encontraron archivos PAGE XML en el ZIP. "
            "Asegúrate de que el ZIP contiene subcarpetas con archivos .xml de eScriptorium."
        )

    # Concatenar hojas de cada testimonio en orden y devolver como bytes
    result = {}
    for wname, pages in witnesses.items():
        pages_sorted = sorted(pages, key=lambda x: x[0])
        full_text = " ".join(
            extract_text_from_page_xml(raw) for _, raw in pages_sorted
        )
        result[wname] = full_text.encode("utf-8")

    return result


def is_page_xml(raw_bytes: bytes) -> bool:
    """Detecta si un archivo es PAGE XML comprobando su contenido."""
    try:
        # Rápido: buscar indicadores en los primeros 2KB
        header = raw_bytes[:2048].decode("utf-8", errors="ignore")
        return (
            "PcGts" in header
            or "primaresearch.org/PAGE" in header
            or "<Page " in header
            or "<TextRegion" in header
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Corrección de caracteres combinatorios mal alineados
# ---------------------------------------------------------------------------

def _is_all_combining(s: str) -> bool:
    s = (s or "").strip()
    return bool(s) and all(unicodedata.category(ch) == "Mn" for ch in s)


def fix_combining_chars(rows: list) -> list:
    """
    Si una celda contiene solo caracteres combinatorios (Mn),
    la pega a la celda previa no vacía.
    """
    for row in rows:
        for j in range(1, len(row)):
            if _is_all_combining(row[j]):
                k = j - 1
                while k >= 0 and not (row[k] or "").strip():
                    k -= 1
                if k >= 0:
                    row[k] = (row[k] or "") + row[j]
                    row[j] = ""
    return rows


# ---------------------------------------------------------------------------
# Serialización de filas a formatos
# ---------------------------------------------------------------------------

def build_svg_bytes(graph, mode: str = "svg") -> bytes:
    """
    Genera el grafo variante como SVG (bytes) usando graphviz.
    mode: "svg" (nodos detallados con lecturas por testimonio)
          "svg_simple" (solo etiquetas de token)
    """
    import re
    import graphviz
    from collections import defaultdict
    from collatex.core_classes import VariantGraphRanking

    a = graphviz.Digraph(format="svg", graph_attr={"rankdir": "LR"})
    counter = 0
    mapping = {}
    ranking = VariantGraphRanking.of(graph)

    for n in graph.graph.nodes():
        counter += 1
        mapping[n] = str(counter)
        if mode == "svg_simple":
            label = n.label or "#"
            a.node(mapping[n], label=label)
        else:
            rank = ranking.byVertex[n]
            rows_html = [
                "<TR><TD ALIGN='LEFT'><B>{}</B></TD>"
                "<TD ALIGN='LEFT'>rank: {}</TD></TR>".format(n.label or "#", rank)
            ]
            reverse_dict = defaultdict(list)
            for sigil, tokens in n.tokens.items():
                reading = "".join(
                    re.sub(r">", "&gt;", re.sub(r"<", "&lt;", t.token_data["t"]))
                    for t in tokens
                )
                reverse_dict[reading].append(sigil)
            for reading, sigils in sorted(reverse_dict.items()):
                rows_html.append(
                    "<TR><TD ALIGN='LEFT'>{}</TD>"
                    "<TD ALIGN='LEFT'>{}</TD></TR>".format(reading, ", ".join(sigils))
                )
            a.node(mapping[n], label="<<TABLE CELLSPACING='0'>" + "".join(rows_html) + "</TABLE>>")

    for u, v, data in graph.graph.edges(data=True):
        a.edge(mapping[u], mapping[v], label=data.get("label", ""))

    for u, v, data in graph.near_graph.edges(data=True):
        a.edge(mapping[u], mapping[v], style="dashed", label="{:.2f}".format(data.get("weight", 0)))

    for rank_val, vertices in ranking.byRank.items():
        sub = graphviz.Digraph(graph_attr={"rank": "same"})
        for n in [mapping[v] for v in vertices]:
            sub.node(n)
        a.subgraph(sub)

    return a.pipe(format="svg")


def rows_to_csv_bytes(rows: list, delimiter: str = ";") -> bytes:
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL).writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def rows_to_xlsx_bytes(rows: list) -> bytes:
    import pandas as pd

    if len(rows) > 1:
        # Desduplicar nombres de columna para que pandas no falle
        headers = rows[0]
        seen = {}
        unique_headers = []
        for h in headers:
            if h in seen:
                seen[h] += 1
                unique_headers.append(f"{h}_{seen[h]}")
            else:
                seen[h] = 0
                unique_headers.append(h)
        df = pd.DataFrame(rows[1:], columns=unique_headers)
    else:
        df = pd.DataFrame(rows)

    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def rows_to_html_bytes(rows: list, title: str = "") -> bytes:
    """Genera tabla HTML estilizada desde las filas de la colación."""
    import html as hl

    def cell(content, tag="td", is_variant=False):
        style = ' style="background:#fff3cd"' if (tag == "td" and is_variant) else ""
        return f"<{tag}{style}>{hl.escape(str(content or ''))}</{tag}>"

    # Detectar columnas variantes (más de un valor distinto)
    variant_cols = set()
    if len(rows) > 1:
        n_cols = len(rows[0])
        for col_i in range(n_cols):
            vals = {rows[r][col_i] for r in range(1, len(rows)) if col_i < len(rows[r])}
            if len(vals) > 1:
                variant_cols.add(col_i)

    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>Colación{' — ' + hl.escape(title) if title else ''}</title>",
        "<style>",
        "  body { font-family: 'Palatino Linotype', Palatino, serif; padding: 1.5em; }",
        "  h1 { font-size: 1.2em; color: #333; }",
        "  table { border-collapse: collapse; font-size: 0.88em; width: 100%; }",
        "  th, td { border: 1px solid #ccc; padding: 5px 10px; text-align: left; vertical-align: top; }",
        "  th { background: #343a40; color: #fff; font-weight: bold; }",
        "  tr:nth-child(even) td:not([style]) { background: #f8f9fa; }",
        "  td[style] { color: #856404; }",
        "  caption { caption-side: bottom; font-size: 0.8em; color: #666; padding-top: 0.5em; }",
        "</style>",
        "</head><body>",
        f"<h1>Collatio{' — ' + hl.escape(title) if title else ''}</h1>",
        "<table>",
        f"<caption>Columnas en amarillo indican variantes. Generado con CollateX.</caption>",
    ]

    for i, row in enumerate(rows):
        if i == 0:
            lines.append("<thead><tr>" + "".join(cell(c, "th") for c in row) + "</tr></thead><tbody>")
        else:
            cells = "".join(
                cell(row[j] if j < len(row) else "", "td", j in variant_cols)
                for j in range(len(rows[0]))
            )
            lines.append(f"<tr>{cells}</tr>")

    lines += ["</tbody></table>", "</body></html>"]
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Construcción de la colación
# ---------------------------------------------------------------------------

def build_collation(witnesses: dict, strip_punct: bool = True) -> Collation:
    """
    witnesses: {nombre: bytes | str}
    Acepta .txt (bytes/str) y PAGE XML (bytes) de eScriptorium/Transkribus.
    """
    collation = Collation()
    for name, content in witnesses.items():
        safe_name = name.strip().replace(" ", "_") or "witness"
        if isinstance(content, bytes) and is_page_xml(content):
            text = extract_text_from_page_xml(content)
        elif isinstance(content, bytes):
            text = decode_bytes(content)
        else:
            text = str(content)
        if strip_punct:
            text = clean_text(text)
        else:
            text = nfc(text)
        collation.add_plain_witness(safe_name, text)
    return collation


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def run_collation(
    witnesses: dict,
    formats: list = None,
    layout: str = "vertical",
    segmentation: bool = True,
    near_match: bool = False,
    algorithm: str = "edit_graph",
    detect_transpositions: bool = False,
    strip_punct: bool = True,
    tei_indent: bool = False,
    label: str = "",
) -> dict:
    """
    Parámetros
    ----------
    witnesses        : {nombre: bytes_o_str} — testimonios
    formats          : lista de formatos a generar (ver OUTPUT_FORMATS)
    layout           : "vertical" | "horizontal"
    segmentation     : True por defecto. Debe ser False si near_match=True.
    near_match       : Activa emparejamiento aproximado (Levenshtein). Requiere segmentation=False.
    algorithm        : "edit_graph" (estándar) | "astar" (experimental)
    detect_transpositions : Detecta transposiciones de bloques.
    strip_punct      : Eliminar puntuación antes de la colación (excepto ⁊).
    tei_indent       : Indentar el output TEI (solo cosmético).
    label            : Etiqueta del pasaje para nombrar archivos.

    Devuelve
    --------
    dict {formato: bytes}
    """
    if not witnesses:
        raise ValueError("No se proporcionaron testimonios.")
    if len(witnesses) < 2:
        raise ValueError("Se necesitan al menos dos testimonios para ejecutar la colación.")
    if near_match and segmentation:
        raise SegmentationError(
            "Near matching requiere segmentación desactivada. "
            "Desactiva 'Segmentación automática' para usar near matching."
        )

    if formats is None:
        formats = ["csv", "tsv", "xlsx", "html"]

    collation = build_collation(witnesses, strip_punct=strip_punct)

    use_astar = algorithm == "astar"

    # Parámetros base para collate()
    collate_kwargs = dict(
        layout=layout,
        segmentation=segmentation,
        near_match=near_match,
        astar=use_astar,
        detect_transpositions=detect_transpositions,
    )

    results = {}

    # Formatos SVG (requieren el grafo variante)
    svg_formats = [f for f in formats if f in ("svg", "svg_simple")]
    if svg_formats:
        graph = collate(collation, output="graph", **collate_kwargs)
        for fmt in svg_formats:
            try:
                results[fmt] = build_svg_bytes(graph, mode=fmt)
            except Exception as e:
                results[fmt + "_error"] = str(e)
        # Reconstruir la colación para los demás formatos (collate() es no-destructivo)
        collation = build_collation(witnesses, strip_punct=strip_punct)

    # Formatos que se obtienen directamente de CollateX como string
    raw_text_formats = []
    if "json" in formats:
        raw_text_formats.append("json")
    if "xml" in formats:
        raw_text_formats.append("xml")
    if "tei" in formats:
        raw_text_formats.append("tei")

    for fmt in raw_text_formats:
        kwargs = dict(collate_kwargs)
        if fmt == "tei":
            kwargs["indent"] = tei_indent
        text = collate(collation, output=fmt, **kwargs)
        if text is not None:
            results[fmt] = text.encode("utf-8")

    # Formatos basados en filas (CSV, TSV, XLSX, HTML)
    row_formats = [f for f in formats if f in ("csv", "tsv", "xlsx", "html")]
    if row_formats:
        csv_text = collate(collation, output="csv", **collate_kwargs)
        rows = list(csv.reader(io.StringIO(csv_text)))
        rows = fix_combining_chars(rows)

        if "csv" in row_formats:
            results["csv"] = rows_to_csv_bytes(rows, delimiter=";")
        if "tsv" in row_formats:
            results["tsv"] = rows_to_csv_bytes(rows, delimiter="\t")
        if "xlsx" in row_formats:
            results["xlsx"] = rows_to_xlsx_bytes(rows)
        if "html" in row_formats:
            results["html"] = rows_to_html_bytes(rows, title=label)

    return results, rows if row_formats else None
