"""Protocolo de comunicación del sistema Aegis-Star 8-Sector.

Define el mapeo de los 8 sectores perimetrales sobre la matriz 3x3, la
topología circular (cada sector tiene dos vecinos), la semilla compartida
del PRNG determinista, la serialización JSON delimitada por ``\\n`` y la
validación de integridad de los paquetes enviados por TCP.
"""

import json
import time

SECTORES = {
    "NO": (0, 0), "N": (0, 1), "NE": (0, 2),
    "O":  (1, 0),             "E":  (1, 2),
    "SO": (2, 0), "S": (2, 1), "SE": (2, 2)
}

ANILLO_PERIMETRAL = ["NO", "N", "NE", "E", "SE", "S", "SO", "O"]

VECINOS_ADYACENTES = {
    sector: (
        ANILLO_PERIMETRAL[(i - 1) % len(ANILLO_PERIMETRAL)],
        ANILLO_PERIMETRAL[(i + 1) % len(ANILLO_PERIMETRAL)]
    )
    for i, sector in enumerate(ANILLO_PERIMETRAL)
}

SEMILLA_COMPARTIDA = 202608
DURACION_CICLO = 5.0

TIPO_REGISTRO = "REGISTRO"
TIPO_HEARTBEAT = "HEARTBEAT"
TIPO_DETECCION = "DETECCION"
TIPO_RESPUESTA = "RESPUESTA"
TIPO_RECONFIGURACION = "RECONFIGURACION"
TIPO_CUARENTENA = "CUARENTENA"

def crear_mensaje(tipo, origen, datos_extra=None):
    """Serializa un paquete JSON con salto de línea como delimitador."""
    paquete = {
        "tipo": tipo,
        "origen": origen,
        "tiempo": time.time(),
        "datos": datos_extra or {}
    }
    return (json.dumps(paquete) + "\n").encode('utf-8')

def validar_mensaje(mensaje):
    """Verifica tipos, origenes y rango binario de una detección."""
    if not isinstance(mensaje, dict):
        return False, "El mensaje no es un diccionario"
    
    tipo = mensaje.get("tipo")
    origen = mensaje.get("origen")
    datos = mensaje.get("datos", {})
    
    tipos_validos = {
        TIPO_REGISTRO, TIPO_HEARTBEAT, TIPO_DETECCION,
        TIPO_RESPUESTA, TIPO_RECONFIGURACION, TIPO_CUARENTENA
    }
    if tipo not in tipos_validos:
        return False, f"Tipo de mensaje invalido: {tipo}"
    
    if origen != "Nave" and origen not in SECTORES:
        return False, f"Origen invalido: {origen}"
    
    if not isinstance(datos, dict):
        return False, "Campo 'datos' corrupto"
    
    if tipo == TIPO_DETECCION:
        valor = datos.get("valor")
        if valor not in (0, 1):
            return False, f"Lectura anomala fuera de rango binario: valor={valor}"
        
        sector_obj = datos.get("sector_objetivo", origen)
        if sector_obj not in SECTORES:
            return False, f"Sector de deteccion invalido: {sector_obj}"
        
    return True, "OK"
