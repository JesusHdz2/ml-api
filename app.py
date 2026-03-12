from flask import Flask, request, jsonify
import requests
import re
from urllib.parse import quote

app = Flask(__name__)

MI_VENDEDOR = "COMERCIALIZADORADEPROMOCIONES"
ML_API_SEARCH = "https://api.mercadolibre.com/sites/MLM/search"


def normalizar(texto: str) -> str:
    texto = str(texto or "").upper().strip()
    reemplazos = {
        "Á": "A",
        "É": "E",
        "Í": "I",
        "Ó": "O",
        "Ú": "U",
        "Ñ": "N"
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)

    texto = re.sub(r"\s+", " ", texto)
    return texto


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
        "CONTINENTAL", "GOODYEAR", "HANKOOK", "GITI", "ATLAS",
        "MICHELIN", "PIRELLI", "BRIDGESTONE", "FIRESTONE",
        "YOKOHAMA", "DUNLOP", "TOYO", "KUMHO", "MAXXIS"
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
        r"\b195R15C\b",
        r"\b\d{3}R\d{2}C\b"
    ]

    medida = ""
    for pat in medida_patterns:
        m = re.search(pat, t)
        if m:
            medida = m.group(0)
            break

    indice_pat = re.search(r"\b\d{2,3}\/?\d{0,3}[A-Z]{1,2}\b|\b\d{2,3}[A-Z]\b", t)
    indice = indice_pat.group(0) if indice_pat else ""

    limpio = t

    basura = [
        "LLANTA", "LLANTAS", "NEUMATICO", "NEUMATICOS",
        "AUTO", "AUTOMOVIL", "CARRO", "SUV", "CAMIONETA"
    ]

    for token in basura:
        limpio = re.sub(rf"\b{token}\b", " ", limpio)

    tokens = [x for x in limpio.split() if len(x) > 1]

    modelo_tokens = tokens[:]

    if marca:
        modelo_tokens = [x for x in modelo_tokens if x != marca]

    if medida:
        modelo_tokens = [x for x in modelo_tokens if x != medida]

    if indice:
        modelo_tokens = [x for x in modelo_tokens if x != indice]

    modelo_tokens = [
        x for x in modelo_tokens
        if not re.fullmatch(r"\d{1,3}", x)
        and not re.fullmatch(r"[A-Z]{1,2}", x)
    ]

    return {
        "marca": marca,
        "medida": medida,
        "indice": indice,
        "modelo": " ".join(modelo_tokens).strip(),
        "tokens": modelo_tokens
    }


def contar_coincidencias(lista_a: list, lista_b: list) -> int:
    if not lista_a or not lista_b:
        return 0

    set_b = set(lista_b)
    return sum(1 for x in lista_a if x in set_b)


def similitud_modelo(a: str, b: str) -> float:
    ta = [x for x in normalizar(a).split() if x]
    tb = [x for x in normalizar(b).split() if x]

    if not ta or not tb:
        return 0.0

    comunes = contar_coincidencias(ta, tb)
    base = max(len(ta), len(tb), 1)

    return comunes / base


def medida_compatible(medida_obj: str, medida_enc: str) -> bool:
    if not medida_obj or not medida_enc:
        return False
    return normalizar(medida_obj) == normalizar(medida_enc)


def es_publicacion_propia(vendedor: str) -> bool:
    return MI_VENDEDOR in normalizar(vendedor)


def penalizacion_modelo_conflictivo(objetivo_modelo: str, encontrado_modelo: str) -> int:
    objetivo = normalizar(objetivo_modelo)
    encontrado = normalizar(encontrado_modelo)

    familias = [
        "PREMIUMCONTACT",
        "ULTRACONTACT",
        "POWERCONTACT",
        "PROCONTACT",
        "CONTIPROCONTACT",
        "ECOCONTACT",
        "CONTIECOCONTACT",
        "EAGLE SPORT",
        "ASSURANCE",
        "KINERGY",
        "OPTIMO",
        "VENTUS",
        "COMFORT F50"
    ]

    objetivo_hit = [f for f in familias if f in objetivo]
    encontrado_hit = [f for f in familias if f in encontrado]

    if objetivo_hit and encontrado_hit and objetivo_hit[0] != encontrado_hit[0]:
        return -250

    return 0


def calcular_score(descripcion_objetivo: dict, titulo_encontrado: str, precio_num: int) -> tuple:
    encontrado = analizar_llanta(titulo_encontrado)

    score = 0
    razones = []

    # Marca
    if descripcion_objetivo["marca"] and encontrado["marca"]:
        if descripcion_objetivo["marca"] == encontrado["marca"]:
            score += 80
            razones.append("marca")
        else:
            score -= 300

    # Medida
    if descripcion_objetivo["medida"]:
        if encontrado["medida"]:
            if medida_compatible(descripcion_objetivo["medida"], encontrado["medida"]):
                score += 240
                razones.append("medida")
            else:
                score -= 600
        else:
            score -= 180

    # Índice
    if descripcion_objetivo["indice"] and encontrado["indice"]:
        if descripcion_objetivo["indice"] == encontrado["indice"]:
            score += 40
            razones.append("indice")
        else:
            score -= 70

    # Similitud de modelo
    sim_modelo = similitud_modelo(descripcion_objetivo["modelo"], encontrado["modelo"])
    score += int(sim_modelo * 160)

    coincidencias = contar_coincidencias(descripcion_objetivo["tokens"], encontrado["tokens"])
    score += coincidencias * 12

    score += penalizacion_modelo_conflictivo(
        descripcion_objetivo["modelo"],
        encontrado["modelo"]
    )

    # Penaliza palabras conflictivas
    conflictivas = [
        "MOTO", "MOTOCICLETA", "SCOOTER", "ATV", "CUATRIMOTO",
        "RIN", "ARO", "VALVULA", "CAMARA", "REFACCION"
    ]
    titulo_norm = normalizar(titulo_encontrado)
    for palabra in conflictivas:
        if palabra in titulo_norm:
            score -= 220

    # Penaliza kits
    paquete = detectar_paquete(titulo_encontrado)
    if paquete == 4:
        score -= 500
    elif paquete == 2:
        score -= 250
    else:
        score += 60

    # Pequeño premio al menor precio
    if precio_num > 0:
        score += int(25000 / max(precio_num, 1))

    return score, paquete, razones, encontrado


@app.get("/")
def health():
    return jsonify({
        "ok": True,
        "service": "ml-api"
    })


@app.get("/buscar")
def buscar():
    q = normalizar(request.args.get("q", ""))

    if not q:
        return jsonify({
            "estado": "QUERY_VACIA",
            "precio_ml": "",
            "proveedor": "",
            "url": "",
            "precio_propio": "",
            "proveedor_propio": "",
            "url_propia": "",
            "titulo_encontrado": ""
        }), 400

    url_api = f"{ML_API_SEARCH}?q={quote(q, safe='')}"

    try:
        r = requests.get(
            url_api,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=25
        )

        if r.status_code != 200:
            return jsonify({
                "estado": f"HTTP_{r.status_code}",
                "precio_ml": "",
                "proveedor": "",
                "url": url_api,
                "precio_propio": "",
                "proveedor_propio": "",
                "url_propia": "",
                "titulo_encontrado": ""
            })

        data = r.json()
        resultados = data.get("results", [])

        if not resultados:
            return jsonify({
                "estado": "SIN_RESULTADOS_API",
                "precio_ml": "",
                "proveedor": "",
                "url": url_api,
                "precio_propio": "",
                "proveedor_propio": "",
                "url_propia": "",
                "titulo_encontrado": ""
            })

        objetivo = analizar_llanta(q)

        mejor_comp = None
        mejor_comp_score = -999999

        mejor_propio = None
        mejor_propio_score = -999999

        for item in resultados[:30]:
            titulo = normalizar(item.get("title", ""))
            if not titulo:
                continue

            precio_num = int(item.get("price", 0) or 0)
            precio_txt = f"${precio_num:,.0f}" if precio_num else ""
            link = item.get("permalink", "")
            proveedor = normalizar(item.get("seller", {}).get("nickname", ""))
            estado_item = normalizar(item.get("status", ""))
            stock = int(item.get("available_quantity", 0) or 0)

            if estado_item and estado_item != "ACTIVE":
                continue

            if stock <= 0:
                continue

            score, paquete, razones, encontrado = calcular_score(objetivo, titulo, precio_num)

            if objetivo["marca"] and encontrado["marca"] and objetivo["marca"] != encontrado["marca"]:
                continue

            if objetivo["medida"] and encontrado["medida"]:
                if not medida_compatible(objetivo["medida"], encontrado["medida"]):
                    continue

            if paquete != 1:
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

            if es_publicacion_propia(proveedor):
                if candidato["score"] > mejor_propio_score:
                    mejor_propio_score = candidato["score"]
                    mejor_propio = candidato
            else:
                if candidato["score"] > mejor_comp_score:
                    mejor_comp_score = candidato["score"]
                    mejor_comp = candidato

        estado = "SIN_RESULTADOS"

        if mejor_comp and mejor_propio:
            if mejor_propio["precio_num"] and mejor_comp["precio_num"]:
                if mejor_propio["precio_num"] < mejor_comp["precio_num"]:
                    estado = "MAS_BARATO"
                elif mejor_propio["precio_num"] > mejor_comp["precio_num"]:
                    estado = "MAS_CARO"
                else:
                    estado = "IGUALADO"
            else:
                estado = "OK"

        elif mejor_comp and not mejor_propio:
            estado = "SIN_PUBLICACION_PROPIA"

        elif mejor_propio and not mejor_comp:
            estado = "SIN_COMPETENCIA"

        return jsonify({
            "estado": estado,
            "precio_ml": mejor_comp["precio_txt"] if mejor_comp else "",
            "proveedor": mejor_comp["proveedor"] if mejor_comp else "",
            "url": mejor_comp["link"] if mejor_comp else url_api,
            "titulo_encontrado": mejor_comp["titulo"] if mejor_comp else "",
            "precio_propio": mejor_propio["precio_txt"] if mejor_propio else "",
            "proveedor_propio": mejor_propio["proveedor"] if mejor_propio else "",
            "url_propia": mejor_propio["link"] if mejor_propio else ""
        })

    except Exception as e:
        return jsonify({
            "estado": f"ERROR: {str(e)[:120]}",
            "precio_ml": "",
            "proveedor": "",
            "url": url_api,
            "titulo_encontrado": "",
            "precio_propio": "",
            "proveedor_propio": "",
            "url_propia": ""
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)