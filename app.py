from flask import Flask, request, jsonify
import requests
import re
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
    texto = str(texto or "").upper().strip()
    texto = (
        texto.replace("Á", "A")
        .replace("É", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ú", "U")
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto


def extraer_texto(node):
    return normalizar(node.get_text(" ", strip=True)) if node else ""


def detectar_paquete(texto: str) -> int:
    t = normalizar(texto)

    if re.search(r"\bKIT DE 4\b|\bPAQUETE DE 4\b|\b4 LLANTAS\b|\b4 NEUMATICOS\b", t):
        return 4
    if re.search(r"\bKIT DE 2\b|\bPAQUETE DE 2\b|\b2 LLANTAS\b|\b2 NEUMATICOS\b", t):
        return 2
    return 1


def analizar_llanta(texto: str) -> dict:
    t = normalizar(texto)

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

    medida_patterns = [
        r"\b\d{3}\/\d{2}R\d{2}\b",
        r"\b\d{3}\/\d{2}ZR\d{2}\b",
        r"\b\d{3}\/\d{2}SR\d{2}\b",
        r"\b\d{3}\/\d{2}-\d{2}\b",
        r"\b\d{2,3}X\d{2}\.?\d{1,2}-\d{2}\b"
    ]

    medida = ""
    for pat in medida_patterns:
        m = re.search(pat, t)
        if m:
            medida = m.group(0)
            break

    limpio = t
    for token in [
        "LLANTA", "LLANTAS", "NEUMATICO", "NEUMATICOS", "KIT", "PAQUETE", "P",
        "AUTO", "AUTOMOVIL", "CARRO", "CARRo", "SUV", "CAMIONETA"
    ]:
        limpio = re.sub(rf"\b{token}\b", " ", limpio)

    tokens = [x for x in limpio.split() if len(x) > 1]

    modelo_tokens = tokens[:]
    if marca:
        modelo_tokens = [x for x in modelo_tokens if x != marca]
    if medida:
        modelo_tokens = [x for x in modelo_tokens if x != medida]

    modelo_tokens = [
        x for x in modelo_tokens
        if not re.fullmatch(r"\d{2,3}", x)
        and not re.fullmatch(r"[A-Z]{1,2}", x)
        and not re.fullmatch(r"\d{2,3}[A-Z]", x)
    ]

    return {
        "marca": marca,
        "medida": medida,
        "tokens": modelo_tokens,
        "modelo": " ".join(modelo_tokens).strip()
    }


def contar_coincidencias(a: list, b: list) -> int:
    if not a or not b:
        return 0
    set_b = set(b)
    return sum(1 for x in a if x in set_b)


def similitud_modelo(a: str, b: str) -> float:
    ta = [x for x in normalizar(a).split() if x]
    tb = [x for x in normalizar(b).split() if x]
    if not ta or not tb:
        return 0.0

    comunes = contar_coincidencias(ta, tb)
    base = max(len(ta), len(tb), 1)
    return comunes / base


def penalizacion_modelo_conflictivo(objetivo_modelo: str, encontrado_modelo: str) -> int:
    objetivo = normalizar(objetivo_modelo)
    encontrado = normalizar(encontrado_modelo)

    familias_conflictivas = [
        "PREMIUMCONTACT", "POWERCONTACT", "ULTRACONTACT",
        "ECOCONTACT", "PROCONTACT", "CONTIPROCONTACT",
        "CONTIECOCONTACT", "CROSSCONTACT", "SPORTCONTACT",
        "EAGLE SPORT", "KINERGY", "VENTUS", "OPTIMO",
        "CONTISCOOT", "SCORPION", "WRANGLER"
    ]

    objetivo_hits = [f for f in familias_conflictivas if f in objetivo]
    encontrado_hits = [f for f in familias_conflictivas if f in encontrado]

    if objetivo_hits and encontrado_hits and objetivo_hits[0] != encontrado_hits[0]:
        return -250

    return 0


def penalizacion_palabras_conflictivas(titulo: str) -> int:
    t = normalizar(titulo)

    conflictivas = [
        "SCOOTER", "MOTO", "MOTOCICLETA", "CUATRIMOTO", "ATV",
        "BICICLETA", "TRAILER", "REMOLQUE", "CAMARA", "RIN",
        "ARO", "REFACCION", "VALVULA"
    ]

    penalty = 0
    for palabra in conflictivas:
        if palabra in t:
            penalty -= 220

    return penalty


def extraer_precio(item):
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
                valor = int(txt)
                return valor, f"${valor:,.0f}"

    html = str(item)
    patrones = [
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for patron in patrones:
        m = re.search(patron, html)
        if m:
            valor = int(float(m.group(1)))
            return valor, f"${valor:,.0f}"

    return 0, ""


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


def extraer_vendedor_listado(item):
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


def extraer_vendedor_detalle(url):
    if not url:
        return ""

    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")
        texto = soup.get_text(" ", strip=True)

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

        m = re.search(r'"nickname"\s*:\s*"([^"]+)"', r.text)
        if m:
            return normalizar(m.group(1))

        m2 = re.search(r"VENDIDO POR\s+([A-Z0-9 _\-.]+)", normalizar(texto))
        if m2:
            return normalizar(m2.group(1))

        return ""

    except Exception:
        return ""


def medida_compatible(medida_obj: str, medida_enc: str) -> bool:
    if not medida_obj or not medida_enc:
        return False
    return normalizar(medida_obj) == normalizar(medida_enc)


def calcular_score(descripcion_objetivo: dict, titulo_encontrado: str, precio_num: int) -> tuple:
    encontrado = analizar_llanta(titulo_encontrado)

    score = 0
    razones = []

    if descripcion_objetivo["marca"] and encontrado["marca"]:
        if descripcion_objetivo["marca"] == encontrado["marca"]:
            score += 80
            razones.append("marca")
        else:
            score -= 250

    # medida casi obligatoria
    if descripcion_objetivo["medida"]:
        if encontrado["medida"]:
            if medida_compatible(descripcion_objetivo["medida"], encontrado["medida"]):
                score += 220
                razones.append("medida")
            else:
                score -= 500
        else:
            score -= 200

    sim_modelo = similitud_modelo(descripcion_objetivo["modelo"], encontrado["modelo"])
    score += int(sim_modelo * 140)

    coincidencias = contar_coincidencias(descripcion_objetivo["tokens"], encontrado["tokens"])
    score += coincidencias * 12

    score += penalizacion_modelo_conflictivo(
        descripcion_objetivo["modelo"],
        encontrado["modelo"]
    )

    score += penalizacion_palabras_conflictivas(titulo_encontrado)

    nums_obj = re.findall(r"\b\d+\b", descripcion_objetivo["modelo"])
    nums_enc = re.findall(r"\b\d+\b", encontrado["modelo"])
    if nums_obj:
        if any(n in nums_enc for n in nums_obj):
            score += 40
        else:
            score -= 80

    paquete = detectar_paquete(titulo_encontrado)
    if paquete == 4:
        score += 15
    elif paquete == 2:
        score += 10

    if precio_num > 0:
        score += int(40000 / max(precio_num / paquete, 1))

    return score, paquete, razones, encontrado


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

        soup = BeautifulSoup(r.text, "html.parser")

        items = (
            soup.select("li.ui-search-layout__item") or
            soup.select(".ui-search-result__wrapper") or
            soup.select("ol li")
        )

        if not items:
            return jsonify({
                "estado": "NO RESULTADOS HTML",
                "precio": "",
                "proveedor": "",
                "url": url_busqueda,
                "titulo_encontrado": ""
            })

        objetivo = analizar_llanta(q)

        mejor = None
        mejor_score = -999999

        for item in items[:12]:
            titulo = extraer_titulo(item)
            if not titulo:
                continue

            precio_num, precio_txt = extraer_precio(item)
            link = extraer_link(item)
            proveedor = extraer_vendedor_listado(item)

            score, paquete, razones, encontrado = calcular_score(objetivo, titulo, precio_num)

            # filtro mínimo: si la marca o medida chocan muy feo, no considerar
            if objetivo["marca"] and encontrado["marca"] and objetivo["marca"] != encontrado["marca"]:
                continue

            if objetivo["medida"] and encontrado["medida"]:
                if not medida_compatible(objetivo["medida"], encontrado["medida"]):
                    continue

            candidato = {
                "score": score,
                "titulo": titulo,
                "precio_num": precio_num,
                "precio_txt": precio_txt,
                "link": link,
                "proveedor": proveedor,
                "paquete": paquete,
                "razones": razones
            }

            if candidato["score"] > mejor_score:
                mejor_score = candidato["score"]
                mejor = candidato

        if not mejor:
            return jsonify({
                "estado": "SIN MATCH",
                "precio": "",
                "proveedor": "",
                "url": url_busqueda,
                "titulo_encontrado": ""
            })

        proveedor_final = mejor["proveedor"]
        if not proveedor_final:
            proveedor_final = extraer_vendedor_detalle(mejor["link"])

        estado = "OK"
        if not mejor["precio_txt"]:
            estado = "SIN PRECIO"
        elif not proveedor_final:
            estado = "OK SIN PROVEEDOR"

        return jsonify({
            "estado": estado,
            "precio": mejor["precio_txt"],
            "proveedor": proveedor_final,
            "url": mejor["link"] or url_busqueda,
            "titulo_encontrado": mejor["titulo"],
            "score": mejor["score"],
            "paquete": mejor["paquete"]
        })

    except Exception as e:
        return jsonify({
            "estado": f"ERROR: {str(e)[:120]}",
            "precio": "",
            "proveedor": "",
            "url": url_busqueda,
            "titulo_encontrado": ""
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)