"""Genera static/aeropuertos.json para el buscador de destinos de la página.

Fuente: OurAirports (dominio público), https://davidmegginson.github.io/ourairports-data/airports.csv
Uso:    python herramientas/generar_aeropuertos.py ruta/airports.csv

Se quedan los aeropuertos con código IATA y vuelos regulares (más todos los grandes).
Cada fila: [iata, ciudad, nombre del aeropuerto, país ISO, tamaño (0 grande, 1 mediano,
2 pequeño), alias para buscar, 1 si es un destino habitual].
El nombre del país en español lo pone el navegador (Intl.DisplayNames).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

SALIDA = Path(__file__).resolve().parent.parent / "static" / "aeropuertos.json"
TAMANO = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}

# Ciudad en español cuando el aeropuerto da servicio a una ciudad con otro nombre o está fuera
# de ella (Narita → Tokio, Beauvais → París…). Varios aeropuertos de una ciudad comparten nombre.
CIUDAD_IATA = {
    **dict.fromkeys(["LHR", "LGW", "STN", "LTN", "LCY", "SEN"], "Londres"),
    **dict.fromkeys(["CDG", "ORY", "BVA"], "París"),
    **dict.fromkeys(["MXP", "LIN", "BGY"], "Milán"),
    **dict.fromkeys(["FCO", "CIA"], "Roma"),
    **dict.fromkeys(["VCE", "TSF"], "Venecia"),
    **dict.fromkeys(["JFK", "EWR", "LGA"], "Nueva York"),
    **dict.fromkeys(["NRT", "HND"], "Tokio"),
    **dict.fromkeys(["KIX", "ITM", "UKB"], "Osaka"),
    **dict.fromkeys(["ICN", "GMP"], "Seúl"),
    **dict.fromkeys(["PVG", "SHA"], "Shanghái"),
    **dict.fromkeys(["PEK", "PKX"], "Pekín"),
    **dict.fromkeys(["SVO", "DME", "VKO"], "Moscú"),
    **dict.fromkeys(["IST", "SAW"], "Estambul"),
    **dict.fromkeys(["ARN", "BMA", "NYO"], "Estocolmo"),
    **dict.fromkeys(["OSL", "TRF"], "Oslo"),
    **dict.fromkeys(["BRU", "CRL"], "Bruselas"),
    **dict.fromkeys(["FRA", "HHN"], "Fráncfort"),
    **dict.fromkeys(["WAW", "WMI"], "Varsovia"),
    **dict.fromkeys(["BKK", "DMK"], "Bangkok"),
    **dict.fromkeys(["DXB", "DWC"], "Dubái"),
    **dict.fromkeys(["EZE", "AEP"], "Buenos Aires"),
    **dict.fromkeys(["GRU", "CGH", "VCP"], "São Paulo"),
    **dict.fromkeys(["GIG", "SDU"], "Río de Janeiro"),
    **dict.fromkeys(["YYZ", "YTZ"], "Toronto"),
    **dict.fromkeys(["ORD", "MDW"], "Chicago"),
    **dict.fromkeys(["IAD", "DCA", "BWI"], "Washington"),
    **dict.fromkeys(["MIA", "FLL"], "Miami"),
    **dict.fromkeys(["MEX", "NLU"], "Ciudad de México"),
    **dict.fromkeys(["TPE", "TSA"], "Taipéi"),
    **dict.fromkeys(["GLA", "PIK"], "Glasgow"),
    **dict.fromkeys(["MLA"], "Malta"),
    **dict.fromkeys(["MRU"], "Mauricio"),
    **dict.fromkeys(["MLE"], "Malé (Maldivas)"),
    **dict.fromkeys(["DPS"], "Bali (Denpasar)"),
    **dict.fromkeys(["SCL"], "Santiago de Chile"),
    **dict.fromkeys(["SCQ"], "Santiago de Compostela"),
    **dict.fromkeys(["BCN"], "Barcelona"),
    **dict.fromkeys(["MAD"], "Madrid"),
    **dict.fromkeys(["MAH"], "Menorca"),
    **dict.fromkeys(["LPA"], "Gran Canaria"),
    **dict.fromkeys(["TFN"], "Tenerife Norte"),
    **dict.fromkeys(["TFS"], "Tenerife Sur"),
    **dict.fromkeys(["ACE"], "Lanzarote"),
    **dict.fromkeys(["FUE"], "Fuerteventura"),
    **dict.fromkeys(["SPC"], "La Palma"),
    **dict.fromkeys(["JTR"], "Santorini"),
    **dict.fromkeys(["JMK"], "Mykonos"),
    **dict.fromkeys(["HER"], "Heraclión (Creta)"),
    **dict.fromkeys(["CHQ"], "La Canea (Creta)"),
    **dict.fromkeys(["LCA"], "Lárnaca (Chipre)"),
    **dict.fromkeys(["PFO"], "Pafos (Chipre)"),
    **dict.fromkeys(["FNC"], "Madeira (Funchal)"),
    **dict.fromkeys(["PDL"], "Azores (Ponta Delgada)"),
    **dict.fromkeys(["PUJ"], "Punta Cana"),
    **dict.fromkeys(["CUN"], "Cancún"),
    **dict.fromkeys(["ZNZ"], "Zanzíbar"),
    **dict.fromkeys(["SEZ"], "Seychelles (Mahé)"),
    **dict.fromkeys(["HKT"], "Phuket"),
    **dict.fromkeys(["RAK"], "Marrakech"),
    **dict.fromkeys(["EAS"], "San Sebastián"),
    **dict.fromkeys(["OVD"], "Asturias"),
    **dict.fromkeys(["RMU"], "Murcia"),
    **dict.fromkeys(["CDT"], "Castellón"),
    **dict.fromkeys(["LCG"], "A Coruña"),
}

# Traducción de los nombres de ciudad más habituales que da OurAirports.
CIUDAD_ES = {
    "London": "Londres", "Paris": "París", "Milan": "Milán", "Rome": "Roma", "Venice": "Venecia",
    "Lisbon": "Lisboa", "Porto": "Oporto", "Munich": "Múnich", "Cologne": "Colonia", "Hamburg": "Hamburgo",
    "Berlin": "Berlín", "Geneva": "Ginebra", "Zurich": "Zúrich", "Vienna": "Viena", "Prague": "Praga",
    "Athens": "Atenas", "Copenhagen": "Copenhague", "Edinburgh": "Edimburgo", "Dublin": "Dublín",
    "Naples": "Nápoles", "Florence": "Florencia", "Turin": "Turín", "Genoa": "Génova", "Bologna": "Bolonia",
    "Krakow": "Cracovia", "Kraków": "Cracovia", "Bucharest": "Bucarest", "Sofia": "Sofía",
    "Belgrade": "Belgrado", "Thessaloniki": "Salónica", "Reykjavik": "Reikiavik", "Reykjavík": "Reikiavik",
    "Cairo": "El Cairo", "Marrakesh": "Marrakech", "Tangier": "Tánger", "Fes": "Fez", "Tunis": "Túnez",
    "Algiers": "Argel", "Abu Dhabi": "Abu Dabi", "Havana": "La Habana", "Philadelphia": "Filadelfia",
    "New Orleans": "Nueva Orleans", "Los Angeles": "Los Ángeles", "Singapore": "Singapur",
    "Hanoi": "Hanói", "Ho Chi Minh City": "Ciudad Ho Chi Minh", "New Delhi": "Nueva Delhi",
    "Delhi": "Nueva Delhi", "Mumbai": "Bombay", "Kyiv": "Kiev", "Vilnius": "Vilna", "Tallinn": "Tallin",
    "Ljubljana": "Liubliana", "Skopje": "Skopie", "Luxembourg": "Luxemburgo", "Basel": "Basilea",
    "Strasbourg": "Estrasburgo", "Marseille": "Marsella", "Nice": "Niza", "Bordeaux": "Burdeos",
    "Seville": "Sevilla", "Malaga": "Málaga", "Saragossa": "Zaragoza", "Cape Town": "Ciudad del Cabo",
    "Johannesburg": "Johannesburgo", "Sydney": "Sídney", "St Petersburg": "San Petersburgo",
    "Saint Petersburg": "San Petersburgo", "Jerusalem": "Jerusalén", "Amman": "Ammán",
    "Tehran": "Teherán", "Riyadh": "Riad", "Muscat": "Mascate", "Addis Ababa": "Adís Abeba",
    "Antwerp": "Amberes", "The Hague": "La Haya", "Rotterdam": "Róterdam", "Gothenburg": "Gotemburgo",
    "Corfu": "Corfú", "Rhodes": "Rodas", "Alghero": "Alguer", "Hanover": "Hannover",
    "Nuremberg": "Núremberg", "Dusseldorf": "Düsseldorf", "Wroclaw": "Breslavia", "Gdansk": "Gdansk",
    "Montreal": "Montreal", "Quebec City": "Quebec", "Beijing": "Pekín", "Shanghai": "Shanghái",
    "Seoul": "Seúl", "Tokyo": "Tokio", "Moscow": "Moscú", "Istanbul": "Estambul", "Stockholm": "Estocolmo",
    "Brussels": "Bruselas", "Frankfurt": "Fráncfort", "Frankfurt am Main": "Fráncfort", "Warsaw": "Varsovia",
    "Mexico City": "Ciudad de México", "Sao Paulo": "São Paulo", "Rio De Janeiro": "Río de Janeiro",
    "Taipei": "Taipéi", "Dubai": "Dubái", "Kuwait City": "Kuwait", "Bogota": "Bogotá",
    "Panama City": "Ciudad de Panamá", "Guatemala City": "Ciudad de Guatemala", "San Jose": "San José",
    "Palma De Mallorca": "Palma de Mallorca", "Palma de Mallorca": "Palma de Mallorca",
    "Amsterdam": "Ámsterdam", "Manchester": "Mánchester", "Bergamo": "Bérgamo",
}

# Palabras extra para encontrar un aeropuerto: islas, regiones y la ciudad a la que sirve.
ALIAS = {
    **dict.fromkeys(["GRO", "REU"], "Barcelona"),
    **dict.fromkeys(["BVA"], "Paris Beauvais"),
    **dict.fromkeys(["CRL"], "Charleroi"),
    **dict.fromkeys(["BGY"], "Bergamo Bérgamo"),
    **dict.fromkeys(["TSF"], "Treviso"),
    **dict.fromkeys(["NYO"], "Skavsta"),
    **dict.fromkeys(["TRF"], "Torp Sandefjord"),
    **dict.fromkeys(["HHN"], "Hahn"),
    **dict.fromkeys(["WMI"], "Modlin"),
    **dict.fromkeys(["SEN"], "Southend"),
    **dict.fromkeys(["OAK", "SJC"], "San Francisco"),
    **dict.fromkeys(["PMI", "IBZ", "MAH"], "Baleares Islas Baleares"),
    **dict.fromkeys(["PMI"], "Baleares Mallorca"),
    **dict.fromkeys(["LPA", "TFN", "TFS", "ACE", "FUE", "SPC", "VDE", "GMZ"], "Canarias Islas Canarias"),
    **dict.fromkeys(["HER", "CHQ", "JSH"], "Creta Crete"),
    **dict.fromkeys(["PMO", "CTA", "TPS", "CIY"], "Sicilia Sicily"),
    **dict.fromkeys(["CAG", "OLB", "AHO"], "Cerdeña Sardinia"),
    **dict.fromkeys(["AJA", "BIA", "FSC", "CLY"], "Córcega Corsica"),
    **dict.fromkeys(["PDL", "TER", "HOR"], "Azores Açores"),
    **dict.fromkeys(["FNC", "PXO"], "Madeira"),
    **dict.fromkeys(["LCA", "PFO"], "Chipre Cyprus"),
    **dict.fromkeys(["DPS"], "Bali"),
    **dict.fromkeys(["MLE"], "Maldivas"),
    **dict.fromkeys(["JTR"], "Thira Cícladas"),
    **dict.fromkeys(["JMK"], "Míkonos Cícladas"),
    **dict.fromkeys(["NRT", "HND"], "Japón Tokyo"),
    **dict.fromkeys(["KIX", "ITM"], "Kioto Kyoto"),
    **dict.fromkeys(["EWR"], "Newark"),
    **dict.fromkeys(["DXB", "DWC"], "Emiratos"),
    **dict.fromkeys(["PUJ"], "República Dominicana"),
    **dict.fromkeys(["CUN"], "Riviera Maya Tulum Playa del Carmen"),
    **dict.fromkeys(["ZNZ"], "Tanzania"),
    **dict.fromkeys(["HKT"], "Tailandia"),
}

# Destinos habituales (suben en la lista cuando empatan).
POPULARES = set(CIUDAD_IATA) | set(ALIAS) | set(
    "GRO REU MAD PMI IBZ AGP SVQ VLC ALC BIO SCQ LIS OPO FAO DUB EDI MAN AMS BRU FRA MUC BER HAM ZRH GVA "
    "VIE PRG BUD WAW KRK CPH ARN OSL HEL KEF NAP BLQ FLR PSA CTA PMO ATH IST MLA RAK TNG CAI DXB DOH TLV "
    "MIA LAX SFO YUL YVR CUN PUJ HAV BOG LIM EZE GRU NRT HND KIX ICN BKK SIN DPS NCE MRS LYS TLS BOD NTE "
    "BSL GNB PRG RIX TLL VNO OTP SOF BEG ZAG DBV SPU LJU TIA SKG CFU RHO HER CHQ LCA PFO AYT DLM BJV ADB "
    "SSH HRG AGA FEZ RBA CMN TUN DJE ALG DSS NBO ZNZ JNB CPT AUH MCT AMM BEY BOS ORD IAD SEA LAS MCO YYZ "
    "MEX SJO PTY CTG MDE UIO SCL GIG HKG TPE PVG PEK DEL BOM CMB MLE KUL HAN SGN MNL SYD MEL AKL".split())


EN_DE = {}   # «Londres» → «London»
for _en, _es in CIUDAD_ES.items():
    EN_DE.setdefault(_es, _en)
EN_DE.update({"Milán": "Milan Milano", "Nueva York": "New York NYC", "Fráncfort": "Frankfurt"})


def main(ruta_csv: str) -> None:
    filas = {}
    with open(ruta_csv, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            iata = (r["iata_code"] or "").strip().upper()
            tam = TAMANO.get(r["type"])
            if len(iata) != 3 or not iata.isalpha() or tam is None:
                continue
            if r["scheduled_service"] != "yes" and tam != 0:
                continue
            municipio = (r["municipality"] or "").strip()
            base = municipio.split(",")[0].strip()   # «Manchester, Greater Manchester» → «Manchester»
            ciudad = CIUDAD_IATA.get(iata) or CIUDAD_ES.get(base) or base or r["name"]
            # Luton o Narita: además del municipio, el nombre en inglés de la ciudad («London», «Tokyo»).
            ingles = EN_DE.get(ciudad, "") if iata in CIUDAD_IATA else ""
            alias = " ".join(x for x in (ALIAS.get(iata, ""), ingles, municipio if municipio != ciudad else "",
                                          (r["keywords"] or "").replace(",", " ")[:80]) if x).strip()
            fila = [iata, ciudad, r["name"].strip(), r["iso_country"], tam, alias, int(iata in POPULARES)]
            # Si un código sale repetido, gana el aeropuerto más grande.
            if iata not in filas or tam < filas[iata][4]:
                filas[iata] = fila
    datos = sorted(filas.values(), key=lambda x: (x[4], -x[6], x[0]))
    SALIDA.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(datos)} aeropuertos -> {SALIDA} ({SALIDA.stat().st_size // 1024} KB)")
    print("por tamaño:", {t: sum(1 for x in datos if x[4] == n) for t, n in TAMANO.items()})
    faltan = sorted(x for x in POPULARES if x not in filas)
    if faltan:
        print("habituales que no están:", faltan)


if __name__ == "__main__":
    main(sys.argv[1])
