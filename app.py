from flask import Flask, request, jsonify
import requests
import re
from urllib.parse import quote

app = Flask(__name__)

ML_SEARCH_URL = "https://api.mercadolibre.com/sites/MLM/search"


def normalizar_texto(texto: str) -> str:
    texto = (texto or "").upper().strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def limpiar_busqueda(texto: str) -> str:
    texto = str(texto or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"[^\w\s\/\-\.\+]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def detectar_paquete(texto: str) -> int:
    t = normalizar_texto(texto)

    if re.search(r"\bKIT DE 4\b|\bPAQUETE DE 4\b|\b4 LLANTAS\b|\b4 NEUMATICOS\b", t):
        return 4
    if re.search(r"\bKIT DE 2\b|\bPAQUETE DE 2\b|\b2 LLANTAS\b|\b2 NEUMATICOS\b", t):
        return 2
    return 1


def analizar_descripcion_llanta(texto: str) -> dict:
    t = normalizar_texto(texto)

    marcas = [
        "CONTINENTAL", "GOODYEAR", "HANKOOK", "GITI", "ATLAS", "MICHELIN",
        "PIRELLI", "BRIDGESTONE", "FIRESTONE", "YOKOHAMA", "DUNLOP",
        "GENERAL TIRE", "BFGOODRICH", "TOYO", "KUMHO", "MAXXIS"
    ]

    marca = ""
    for m in marcas:
        if m in t:
            marca = m
            break

    medida_match = re.search(r"\b\d{3}\/\d{2}R\d{2}\b|\b\d{3}\/\d{2}ZR\d{2}\b|\b\d{3}\/\d{2}SR\d{2}\b|\b\d{3}\/\d{2}\/R\d{2}\b", t)
    medida = medida_match.group(0).replace("/R", "R") if medida_match else ""

    limpio = t
    for token in ["LLANTA", "NEUMATICO", "NEUMATICOS", "KIT", "PAQUETE", "AUTO", "CAMIONETA"]:
        limpio = re.sub(rf"\b{token}\b", " ", limpio)

    tokens = [x for x in limpio.split() if len(x) > 2]

    modelo_tokens = list(tokens)
    if marca:
        modelo_tokens = [x for x in modelo_tokens if x != marca]
    if medida:
        modelo_tokens = [x for x in modelo_tokens if x != medida]

    return {
        "marca": marca,
        "medida": medida,
        "modelo": " ".join(modelo_tokens).strip(),
        "tokens": modelo_tokens
    }


def contar_tokens_coincidentes(a: list, b: list) -> int:
    if not a or not b:
        return 0
    set_b = set(b)
    return sum(1 for x in a if x in set_b)


def similitud_texto(a: str, b: str) -> float:
    ta = (a or "").strip()
    tb = (b or "").strip()
    if not ta or not tb:
        return 0.0

    tokens_a = ta.split()
    tokens_b = tb.split()
    comunes = contar_tokens_coincidentes(tokens_a, tokens_b)
    base = max(len(tokens_a), len(tokens_b), 1)
    return comunes / base


def formatear_moneda(num) -> str:
    try:
        n = float(num)
    except Exception:
        return ""
    return "${:,.2f}".format(n)


def elegir_mejor_resultado(descripcion: str, results: list) -> dict | None:
    objetivo = analizar_descripcion_llanta(descripcion)

    mejor = None
    mejor_score = -999999

    for item in results:
        titulo = item.get("title", "")
        analisis_titulo = analizar_descripcion_llanta(titulo)

        score = 0

        if objetivo["marca"] and analisis_titulo["marca"] and objetivo["marca"] == analisis_titulo["marca"]:
            score += 40

        if objetivo["medida"] and analisis_titulo["medida"] and objetivo["medida"] == analisis_titulo["medida"]:
            score += 80

        if objetivo["modelo"] and analisis_titulo["modelo"]:
            score += round(similitud_texto(objetivo["modelo"], analisis_titulo["modelo"]) * 50)

        score += contar_tokens_coincidentes(objetivo["tokens"], analisis_titulo["tokens"]) * 5

        paquete = detectar_paquete(titulo)
        if paquete == 4:
            score += 20
        elif paquete == 2:
            score += 15

        if objetivo["medida"] and analisis_titulo["medida"] and objetivo["medida"] != analisis_titulo["medida"]:
            score -= 120

        if objetivo["marca"] and analisis_titulo["marca"] and objetivo["marca"] != analisis_titulo["marca"]:
            score -= 60

        price = float(item.get("price") or 0)
        if price > 0:
            score += 100000 / (price / paquete)

        if score > mejor_score:
            mejor_score = score
            mejor = item

    return mejor


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "ml-search-api"})


@app.get("/buscar")
def buscar():
    q = request.args.get("q", "").strip()

    if not q:
        return jsonify({
            "precio": "",
            "proveedor": "",
            "url": "",
            "estado": "QUERY VACIA"
        }), 400

    query = limpiar_busqueda(q)

    params = {
        "q": query,
        "limit": 10
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(ML_SEARCH_URL, params=params, headers=headers, timeout=20)

        if response.status_code != 200:
            return jsonify({
                "precio": "",
                "proveedor": "",
                "url": f"https://listado.mercadolibre.com.mx/{quote(query)}",
                "estado": f"HTTP {response.status_code}"
            }), 200

        data = response.json()
        results = data.get("results", [])

        if not results:
            return jsonify({
                "precio": "",
                "proveedor": "",
                "url": f"https://listado.mercadolibre.com.mx/{quote(query)}",
                "estado": "NO RESULTS"
            })

        mejor = elegir_mejor_resultado(q, results)

        if not mejor:
            return jsonify({
                "precio": "",
                "proveedor": "",
                "url": f"https://listado.mercadolibre.com.mx/{quote(query)}",
                "estado": "SIN MATCH"
            })

        return jsonify({
            "precio": formatear_moneda(mejor.get("price")),
            "proveedor": (mejor.get("seller") or {}).get("nickname", ""),
            "url": mejor.get("permalink", ""),
            "estado": "OK",
            "titulo": mejor.get("title", ""),
            "paquete": detectar_paquete(mejor.get("title", "")),
            "precio_numerico": mejor.get("price", 0)
        })

    except Exception as e:
        return jsonify({
            "precio": "",
            "proveedor": "",
            "url": f"https://listado.mercadolibre.com.mx/{quote(query)}",
            "estado": f"ERROR: {str(e)[:120]}"
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)