from flask import Flask, request, jsonify
import requests
import re
import json
from bs4 import BeautifulSoup
from urllib.parse import quote

app = Flask(__name__)

BASE_LISTADO = "https://listado.mercadolibre.com.mx/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip()


def extraer_texto(node):
    return normalizar(node.get_text(" ", strip=True)) if node else ""


def extraer_precio_desde_html(item):
    """
    Intenta varios selectores comunes en Mercado Libre.
    """
    selectores = [
        ".andes-money-amount__fraction",
        ".price-tag-fraction",
        "span.andes-money-amount__fraction",
        "span.price-tag-fraction",
    ]

    for sel in selectores:
        n = item.select_one(sel)
        if n:
            txt = extraer_texto(n)
            txt = re.sub(r"[^\d]", "", txt)
            if txt:
                return f"${int(txt):,}".replace(",", ",")

    return ""


def extraer_link(item):
    selectores = [
        "a.poly-component__title",
        "a.ui-search-link",
        "a[href*='/MLM-']",
        "a[href]"
    ]

    for sel in selectores:
        a = item.select_one(sel)
        if a and a.get("href"):
            return a["href"]

    return ""


def extraer_titulo(item):
    selectores = [
        "a.poly-component__title",
        "h2",
        ".ui-search-item__title",
    ]
    for sel in selectores:
        n = item.select_one(sel)
        txt = extraer_texto(n)
        if txt:
            return txt
    return ""


def extraer_vendedor_desde_listado(item):
    patrones = [
        ".poly-component__seller",
        ".ui-search-item__group__element--seller",
        "[class*='seller']",
    ]
    for sel in patrones:
        n = item.select_one(sel)
        txt = extraer_texto(n)
        if txt:
            return txt
    return ""


def extraer_vendedor_desde_detalle(url):
    if not url:
        return ""

    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")
        texto = soup.get_text(" ", strip=True)

        # Intentos por HTML visible
        candidatos = [
            soup.select_one("[data-testid='seller-link']"),
            soup.select_one("[data-testid='seller-name']"),
            soup.select_one("a[href*='perfil']"),
            soup.select_one("a[href*='seller']"),
        ]

        for c in candidatos:
            txt = extraer_texto(c)
            if txt:
                return txt

        # Intento por JSON embebido
        m = re.search(r'"nickname"\s*:\s*"([^"]+)"', r.text)
        if m:
            return m.group(1)

        # Intento por texto visible
        m2 = re.search(r"Vendido por\s+([A-Za-z0-9 _\-\.]+)", texto, re.IGNORECASE)
        if m2:
            return normalizar(m2.group(1))

        return ""

    except Exception:
        return ""


def extraer_precio_desde_scripts(html):
    """
    Fallback: busca precio dentro de scripts embebidos.
    """
    patrones = [
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for patron in patrones:
        m = re.search(patron, html)
        if m:
            try:
                valor = float(m.group(1))
                return f"${valor:,.0f}".replace(",", ",")
            except Exception:
                pass

    return ""


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "ml-html-parser"})


@app.get("/buscar")
def buscar():
    q = normalizar(request.args.get("q", ""))

    if not q:
        return jsonify({
            "estado": "QUERY VACIA",
            "precio": "",
            "proveedor": "",
            "url": "",
            "titulo_encontrado": ""
        }), 400

    url_busqueda = f"{BASE_LISTADO}{quote(q)}"

    try:
        r = requests.get(url_busqueda, headers=HEADERS, timeout=25)

        if r.status_code != 200:
            return jsonify({
                "estado": f"HTTP {r.status_code}",
                "precio": "",
                "proveedor": "",
                "url": url_busqueda,
                "titulo_encontrado": ""
            })

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        # Intenta varios contenedores posibles
        contenedores = (
            soup.select("li.ui-search-layout__item") or
            soup.select(".ui-search-result__wrapper") or
            soup.select("ol li")
        )

        if not contenedores:
            return jsonify({
                "estado": "NO RESULTADOS HTML",
                "precio": "",
                "proveedor": "",
                "url": url_busqueda,
                "titulo_encontrado": ""
            })

        item = contenedores[0]

        titulo = extraer_titulo(item)
        link = extraer_link(item)
        precio = extraer_precio_desde_html(item)
        proveedor = extraer_vendedor_desde_listado(item)

        if not precio:
            precio = extraer_precio_desde_scripts(str(item)) or extraer_precio_desde_scripts(html)

        if not proveedor:
            proveedor = extraer_vendedor_desde_detalle(link)

        # Si el link es relativo o viene vacío
        if not link:
            link = url_busqueda

        estado = "OK"
        if not precio:
            estado = "SIN PRECIO"
        elif not proveedor:
            estado = "OK SIN PROVEEDOR"

        return jsonify({
            "estado": estado,
            "precio": precio,
            "proveedor": proveedor,
            "url": link,
            "titulo_encontrado": titulo
        })

    except Exception as e:
        return jsonify({
            "estado": f"ERROR: {str(e)[:120]}",
            "precio": "",
            "proveedor": "",
            "url": url_busqueda,
            "titulo_encontrado": ""
        })