import json
import time

# Coordenadas de los 8 sectores alrededor de la nave (Fila, Columna)
SECTORES = {
    "NO": (0, 0), "N": (0, 1), "NE": (0, 2),
    "O":  (1, 0),             "E":  (1, 2),
    "SO": (2, 0), "S": (2, 1), "SE": (2, 2)
}

def crear_mensaje(tipo, origen, datos_extra=None):
    """Arma el paquete de datos en formato JSON para enviarlo por la red"""
    paquete = {
        "tipo": tipo,          # Puede ser: REGISTRO, HEARTBEAT, DETECCION
        "origen": origen,      # Nombre del sector (ej: N, NE, S...)
        "tiempo": time.time(),
        "datos": datos_extra or {}
    }
    # Lo convierte a texto y le agrega un salto de linea al final
    return (json.dumps(paquete) + "\n").encode('utf-8')