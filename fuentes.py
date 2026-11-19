"""Fuentes de precios.

- Google Flights (librería faster-flights): aerolíneas tradicionales, la mayoría de
  low cost y agencias. Una consulta por día y tramo. NO muestra Ryanair ni Wizz Air.
- Ryanair (API pública de su buscador): precio más barato de cada día, una consulta
  por mes y tramo.
- Wizz Air (API pública de su calendario): precio más barato de cada día, una consulta
  por bloque de 30 días y tramo.
- Aviasales / Travelpayouts (opcional, token gratuito): precios que han visto otros
  usuarios en las últimas 48 h, solo ida e ida y vuelta.

Todas devuelven "tarifas": diccionarios con fuente, tramo (ida | vuelta | iv), fecha,
fecha_vuelta, precio (€ por persona), aerolinea, escalas, hora, enlace, duracion_min
(duración total del trayecto con escalas) y duracion_vuelta_min (solo en iv).

Con varias personas (medido el 19/11/2026):
- Google Flights da el precio TOTAL del grupo y ya solo enseña tarifas con plazas para
  todos (se divide entre las personas para guardarlo por persona).
- Wizz Air da el precio por persona de la tarifa que tiene plazas para todos.
- Ryanair (cheapestPerDay) no admite pasajeros: es el precio de la plaza más barata, y
  para un grupo puede salir algo más caro. Su enlace ya abre la reserva con todos.
- Aviasales son precios vistos por otros usuarios, por persona.
"""
from __future__ import annotations

import logging
import math
import os
import random
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import airportsdata
import requests
from fast_flights import (FlightQuery, Passengers, create_query, get_flights, get_return_flights,
                          select_flight)

log = logging.getLogger("buscador.fuentes")

NOMBRES = {"google": "Google Flights", "ryanair": "Ryanair", "wizz": "Wizz Air", "aviasales": "Aviasales"}
DURACION_ESTIMADA = {"wizz"}   # fuentes cuya duración se estima por distancia

NAVEGADOR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
}

_AEROPUERTOS = airportsdata.load("IATA")


def pausa(minimo: float = 0.8, maximo: float = 1.8) -> None:
    """Espera aleatoria entre consultas para no saturar a nadie."""
    time.sleep(random.uniform(minimo, maximo))


def meses(desde: date, hasta: date) -> list[date]:
    m, salida = desde.replace(day=1), []
    while m <= hasta:
        salida.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return salida


def tarifa(fuente, tramo, fecha, precio, aerolinea=None, escalas=None, hora=None, enlace=None,
           fecha_vuelta=None, duracion_min=None, duracion_vuelta_min=None) -> dict:
    return {
        "fuente": fuente, "tramo": tramo, "fecha": fecha, "fecha_vuelta": fecha_vuelta,
        "precio": round(float(precio), 2), "aerolinea": aerolinea, "escalas": escalas,
        "hora": hora, "enlace": enlace, "duracion_min": duracion_min,
        "duracion_vuelta_min": duracion_vuelta_min,
    }


# ---------------------------------------------------------------- monedas

_cambios: dict = {"dia": None, "tipos": {}}


def a_euros(importe: float, moneda: str | None) -> float | None:
    """Convierte a euros con el cambio oficial del BCE del día (se descarga una vez al día).

    Wizz Air cobra en la moneda del aeropuerto de salida (florines desde Budapest, zlotys
    desde Varsovia…), así que sus precios de vuelta no siempre vienen en euros.
    """
    if not moneda or moneda.upper() == "EUR":
        return importe
    if _cambios["dia"] != date.today():
        r = requests.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", timeout=20)
        r.raise_for_status()
        _cambios["tipos"] = {m: float(t) for m, t in re.findall(r"currency='([A-Z]{3})' rate='([\d.]+)'", r.text)}
        _cambios["dia"] = date.today()
    tipo = _cambios["tipos"].get(moneda.upper())
    return round(importe / tipo, 2) if tipo else None


# ---------------------------------------------------------------- duraciones

def _zona(iata: str) -> ZoneInfo | None:
    a = _AEROPUERTOS.get(iata)
    try:
        return ZoneInfo(a["tz"]) if a and a.get("tz") else None
    except Exception:
        return None


def minutos_entre(origen: str, salida_local: str, destino: str, llegada_local: str) -> int | None:
    """Duración real de un vuelo a partir de sus horas locales y las zonas horarias."""
    try:
        z1, z2 = _zona(origen), _zona(destino)
        if not z1 or not z2:
            return None
        s = datetime.fromisoformat(salida_local[:19]).replace(tzinfo=z1)
        l = datetime.fromisoformat(llegada_local[:19]).replace(tzinfo=z2)
        m = int((l - s).total_seconds() // 60)
        return m if 0 < m < 24 * 60 else None
    except Exception:
        return None


def minutos_estimados(origen: str, destino: str) -> int | None:
    """Duración aproximada de un vuelo directo: 35 min de rodaje y subida + crucero a 780 km/h."""
    a, b = _AEROPUERTOS.get(origen), _AEROPUERTOS.get(destino)
    if not a or not b:
        return None
    f1, f2 = math.radians(a["lat"]), math.radians(b["lat"])
    df, dl = f2 - f1, math.radians(b["lon"] - a["lon"])
    km = 2 * 6371 * math.asin(math.sqrt(math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2))
    return int(round(35 + km / 780 * 60))


# ---------------------------------------------------------------- Google Flights

EN_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"


def _reintentar(funcion, *args, intentos: int = 3):
    """Google responde de forma irregular a las conexiones desde centros de datos (GitHub):
    la misma consulta que falla suele salir bien unos segundos después."""
    for i in range(intentos):
        try:
            return funcion(*args)
        except Exception:
            if i == intentos - 1:
                raise
            pausa(2, 4)


def _consulta_google(vuelos: list[FlightQuery], viaje: str, max_escalas: int | None = None,
                     personas: int = 1):
    return create_query(flights=vuelos, trip=viaje, passengers=Passengers(adults=personas),
                        language="es", currency="EUR", max_stops=max_escalas)


def _hora(momento) -> str | None:
    try:
        t = list(momento.time or [])
        return f"{(t[0] if t else 0):02d}:{(t[1] if len(t) > 1 else 0):02d}"
    except Exception:
        return None


def _momento(sd) -> datetime:
    t = list(sd.time or []) + [0, 0]
    return datetime(*sd.date, t[0], t[1])


def minutos_google(f) -> int | None:
    """Duración total del trayecto: vuelos + esperas en las escalas.

    Cada espera se calcula con horas locales del mismo aeropuerto, así que es exacta.
    """
    try:
        total = sum(s.duration or 0 for s in f.flights)
        for a, b in zip(f.flights, f.flights[1:]):
            total += int((_momento(b.departure) - _momento(a.arrival)).total_seconds() // 60)
        return total or None
    except Exception:
        return None


def _cabe(f, max_min: int | None) -> bool:
    if not max_min:
        return True
    m = minutos_google(f)
    return m is not None and m <= max_min


def google_url_ida_vuelta(origen: str, destino: str, ida: str, vuelta: str, personas: int = 1) -> str:
    """Enlace a la búsqueda de ida y vuelta en Google Flights (no hace petición)."""
    q = _consulta_google([FlightQuery(date=ida, from_airport=origen, to_airport=destino),
                          FlightQuery(date=vuelta, from_airport=destino, to_airport=origen)],
                         "round-trip", personas=personas)
    return q.url()


def google_tramo(origen: str, destino: str, fecha: str, tramo: str,
                 max_min: int | None = None, personas: int = 1) -> list[dict]:
    """Solo ida más barato del día, el directo más barato y el más barato dentro del
    límite de horas (si son vuelos distintos)."""
    q = _consulta_google([FlightQuery(date=fecha, from_airport=origen, to_airport=destino)], "one-way",
                         personas=personas)
    vuelos = [f for f in _reintentar(get_flights, q) if f.price and f.price > 0]
    if not vuelos:
        return []
    enlace = q.url()
    barato = lambda lista: min(lista, key=lambda f: f.price)
    elegidos = [barato(vuelos)]
    directos = [f for f in vuelos if len(f.flights) == 1]
    if directos:
        elegidos.append(barato(directos))
    if max_min:
        dentro = [f for f in vuelos if _cabe(f, max_min)]
        if dentro:
            elegidos.append(barato(dentro))
    salida, vistos = [], set()
    for f in elegidos:
        if id(f) in vistos:
            continue
        vistos.add(id(f))
        primero = f.flights[0] if f.flights else None
        salida.append(tarifa("google", tramo, fecha, f.price / personas,   # Google da el total del grupo
                             aerolinea=", ".join(f.airlines) if f.airlines else None,
                             escalas=max(len(f.flights) - 1, 0),
                             hora=_hora(primero.departure) if primero else None,
                             enlace=enlace, duracion_min=minutos_google(f)))
    return salida


def google_ida_vuelta(origen: str, destino: str, ida: str, vuelta: str, solo_directos: bool,
                      max_min: int | None = None, personas: int = 1) -> list[dict]:
    """Precio total de ida y vuelta comprado como un único billete.

    En vuelos largos o con aerolíneas tradicionales suele ser mucho más barato que sumar
    dos billetes de solo ida. Con límite de horas se elige la ida que cabe y se consulta
    también la vuelta, para comprobar que tampoco lo supera.
    """
    q = _consulta_google([FlightQuery(date=ida, from_airport=origen, to_airport=destino),
                          FlightQuery(date=vuelta, from_airport=destino, to_airport=origen)],
                         "round-trip", max_escalas=0 if solo_directos else None, personas=personas)
    idas = sorted((f for f in _reintentar(get_flights, q) if f.price and f.price > 0), key=lambda f: f.price)
    if not idas:
        return []

    def construir(f_ida, f_vuelta, precio):
        primero = f_ida.flights[0] if f_ida.flights else None
        aerolineas = list(dict.fromkeys((f_ida.airlines or []) + ((f_vuelta.airlines or []) if f_vuelta else [])))
        escalas = max(len(f_ida.flights) - 1, (len(f_vuelta.flights) - 1) if f_vuelta else 0, 0)
        return tarifa("google", "iv", ida, precio / personas, aerolinea=", ".join(aerolineas) or None,
                      escalas=0 if solo_directos else escalas,
                      hora=_hora(primero.departure) if primero else None, enlace=q.url(),
                      fecha_vuelta=vuelta, duracion_min=minutos_google(f_ida),
                      duracion_vuelta_min=minutos_google(f_vuelta) if f_vuelta else None)

    if not max_min:
        return [construir(idas[0], None, idas[0].price)]

    for f_ida in [f for f in idas if _cabe(f, max_min)][:2]:
        if not f_ida.select_token:
            continue
        pausa(0.5, 1.2)
        vueltas = [v for v in _reintentar(get_return_flights, select_flight(q, f_ida)) if v.price and v.price > 0]
        # En este paso Google da el total de ida y vuelta: nunca por debajo del precio de la ida
        # elegida. Si sale menos, la librería ha devuelto precios de solo ida y no sirven.
        if not vueltas or min(v.price for v in vueltas) < f_ida.price - 1:
            continue
        dentro = [v for v in vueltas if _cabe(v, max_min)]
        if dentro:
            v = min(dentro, key=lambda x: x.price)
            return [construir(f_ida, v, v.price)]
    return []


# ---------------------------------------------------------------- Ryanair

RYANAIR_URL = "https://www.ryanair.com/api/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay"


def ryanair_enlace(origen: str, destino: str, fecha: str, personas: int = 1) -> str:
    params = {"adults": personas, "teens": 0, "children": 0, "infants": 0, "dateOut": fecha,
              "isConnectedFlight": "false", "isReturn": "false", "discount": 0,
              "originIata": origen, "destinationIata": destino}
    return "https://www.ryanair.com/es/es/trip/flights/select?" + urlencode(params)


def ryanair_tramo(origen: str, destino: str, desde: date, hasta: date, tramo: str,
                  personas: int = 1) -> list[dict]:
    """Tarifa más barata de cada día. Si Ryanair no opera la ruta, devuelve [].
    Esta consulta no admite pasajeros: con varias personas solo cambia el enlace."""
    salida = []
    for mes in meses(desde, hasta):
        r = requests.get(RYANAIR_URL.format(o=origen, d=destino),
                         params={"outboundMonthOfDate": mes.isoformat(), "currency": "EUR"},
                         headers=NAVEGADOR, timeout=20)
        if r.status_code in (400, 404):
            return []
        r.raise_for_status()
        for f in (r.json().get("outbound") or {}).get("fares") or []:
            importe = (f.get("price") or {}).get("value")
            dia = f.get("day") or ""
            if f.get("unavailable") or f.get("soldOut") or not importe:
                continue
            precio = a_euros(importe, (f.get("price") or {}).get("currencyCode"))
            if not precio:
                continue
            if not (desde.isoformat() <= dia <= hasta.isoformat()):
                continue
            sale, llega = f.get("departureDate") or "", f.get("arrivalDate") or ""
            salida.append(tarifa("ryanair", tramo, dia, precio, aerolinea="Ryanair", escalas=0,
                                 hora=sale[11:16] or None, enlace=ryanair_enlace(origen, destino, dia, personas),
                                 duracion_min=minutos_entre(origen, sale, destino, llega) if sale and llega else None))
        pausa(0.3, 0.8)
    return salida


# ---------------------------------------------------------------- Wizz Air

_wizz_api: str | None = None


def _wizz_base() -> str:
    """La API de Wizz Air lleva el número de versión en la URL y cambia a menudo."""
    global _wizz_api
    if _wizz_api:
        return _wizz_api
    for url in ("https://wizzair.com/buildnumber", "https://wizzair.com/es-es"):
        try:
            r = requests.get(url, headers={**NAVEGADOR, "Accept": "text/html,*/*"}, timeout=20)
        except requests.RequestException:
            continue
        m = re.search(r"https://be\.wizzair\.com/[\d.]+", r.text)
        if m:
            _wizz_api = m.group(0)
            return _wizz_api
    raise RuntimeError("no se encuentra la versión actual de la API de Wizz Air")


def wizz_enlace(origen: str, destino: str, fecha: str, personas: int = 1) -> str:
    return f"https://wizzair.com/en-gb/booking/select-flight/{origen}/{destino}/{fecha}/null/{personas}/0/0/null"


def wizz_tramo(origen: str, destino: str, desde: date, hasta: date, tramo: str,
               personas: int = 1) -> list[dict]:
    """Tarifa más barata de cada día (por persona, con plazas para todos).
    Si Wizz Air no opera la ruta, devuelve []."""
    global _wizz_api
    base = _wizz_base()
    estimada = minutos_estimados(origen, destino)
    salida, inicio = [], desde
    while inicio <= hasta:
        fin = min(hasta, inicio + timedelta(days=30))
        r = requests.post(f"{base}/Api/search/timetable", timeout=20, json={
            "flightList": [{"departureStation": origen, "arrivalStation": destino,
                            "from": inicio.isoformat(), "to": fin.isoformat()}],
            "priceType": "regular", "adultCount": personas, "childCount": 0, "infantCount": 0},
            headers={**NAVEGADOR, "Content-Type": "application/json",
                     "Origin": "https://wizzair.com", "Referer": "https://wizzair.com/"})
        if r.status_code in (400, 404):
            return salida
        if r.status_code in (401, 403, 429):
            _wizz_api = None   # puede haber cambiado la versión; se vuelve a buscar la próxima vez
            raise RuntimeError(f"Wizz Air rechazó la consulta (código {r.status_code})")
        r.raise_for_status()
        for f in r.json().get("outboundFlights") or []:
            importe = (f.get("price") or {}).get("amount")
            dia = (f.get("departureDate") or "")[:10]
            if not importe or f.get("priceType") != "price":
                continue
            precio = a_euros(importe, (f.get("price") or {}).get("currencyCode"))
            if not precio:
                continue
            if not (desde.isoformat() <= dia <= hasta.isoformat()):
                continue
            horas = f.get("departureDates") or []
            salida.append(tarifa("wizz", tramo, dia, precio, aerolinea="Wizz Air", escalas=0,
                                 hora=horas[0][11:16] if horas else None,
                                 enlace=wizz_enlace(origen, destino, dia, personas), duracion_min=estimada))
        inicio = fin + timedelta(days=1)
        pausa(0.3, 0.8)
    return salida


# ---------------------------------------------------------------- Aviasales (opcional)

AVIASALES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def _aviasales(token: str, params: dict) -> list[dict]:
    r = requests.get(AVIASALES_URL, timeout=25, headers={"Accept-Encoding": "gzip"},
                     params={**params, "currency": "eur", "sorting": "price", "limit": 1000,
                             "unique": "false", "token": token})
    r.raise_for_status()
    datos = r.json()
    if datos.get("success") is False:
        raise RuntimeError(datos.get("error") or "respuesta sin éxito")
    return datos.get("data") or []


def _aviasales_enlace(t: dict) -> str | None:
    enlace = t.get("link")
    return ("https://www.aviasales.com" + enlace) if enlace else None


def _entero(x) -> int | None:
    try:
        return int(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def aviasales_tramo(token: str, origen: str, destino: str, desde: date, hasta: date,
                    tramo: str, max_min: int | None = None) -> list[dict]:
    mejores: dict[tuple, dict] = {}
    for mes in meses(desde, hasta):
        for t in _aviasales(token, {"origin": origen, "destination": destino,
                                    "departure_at": mes.strftime("%Y-%m"), "one_way": "true"}):
            dia = (t.get("departure_at") or "")[:10]
            if not (desde.isoformat() <= dia <= hasta.isoformat()) or not t.get("price"):
                continue
            escalas = int(t.get("transfers") or 0)
            duracion = _entero(t.get("duration_to") or t.get("duration"))
            cabe = not max_min or (duracion is not None and duracion <= max_min)
            nueva = tarifa("aviasales", tramo, dia, t["price"], aerolinea=t.get("airline"),
                           escalas=escalas, hora=(t.get("departure_at") or "")[11:16] or None,
                           enlace=_aviasales_enlace(t), duracion_min=duracion)
            for clave in {(dia, "todas"), (dia, "directo" if escalas == 0 else "todas"),
                          (dia, "cabe" if cabe else "todas")}:
                if clave not in mejores or nueva["precio"] < mejores[clave]["precio"]:
                    mejores[clave] = nueva
        pausa(0.3, 0.8)
    return list({id(t): t for t in mejores.values()}.values())


def aviasales_ida_vuelta(token: str, origen: str, destino: str, ida_desde: date, ida_hasta: date,
                         n_min: int, n_max: int) -> list[dict]:
    mejores: dict[tuple, dict] = {}
    for mes_ida in meses(ida_desde, ida_hasta):
        siguiente = (mes_ida.replace(day=28) + timedelta(days=4)).replace(day=1)
        for mes_vuelta in (mes_ida, siguiente):
            for t in _aviasales(token, {"origin": origen, "destination": destino,
                                        "departure_at": mes_ida.strftime("%Y-%m"),
                                        "return_at": mes_vuelta.strftime("%Y-%m"),
                                        "one_way": "false"}):
                ida = (t.get("departure_at") or "")[:10]
                vuelta = (t.get("return_at") or "")[:10]
                if not ida or not vuelta or not t.get("price"):
                    continue
                if not (ida_desde.isoformat() <= ida <= ida_hasta.isoformat()):
                    continue
                n = (date.fromisoformat(vuelta) - date.fromisoformat(ida)).days
                if not (n_min <= n <= n_max):
                    continue
                escalas = max(int(t.get("transfers") or 0), int(t.get("return_transfers") or 0))
                clave = (ida, vuelta, escalas == 0)
                if clave not in mejores or t["price"] < mejores[clave]["precio"]:
                    mejores[clave] = tarifa("aviasales", "iv", ida, t["price"],
                                            aerolinea=t.get("airline"), escalas=escalas,
                                            hora=(t.get("departure_at") or "")[11:16] or None,
                                            enlace=_aviasales_enlace(t), fecha_vuelta=vuelta,
                                            duracion_min=_entero(t.get("duration_to")),
                                            duracion_vuelta_min=_entero(t.get("duration_back")))
            pausa(0.3, 0.8)
    return list(mejores.values())
