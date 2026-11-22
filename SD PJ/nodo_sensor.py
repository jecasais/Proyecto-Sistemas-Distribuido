import argparse
import os
import socket
import threading
import time
import json
import sys
import random
from protocolo import (
    SECTORES,
    ANILLO_PERIMETRAL,
    SEMILLA_COMPARTIDA,
    DURACION_CICLO,
    TIPO_REGISTRO,
    TIPO_HEARTBEAT,
    TIPO_DETECCION,
    TIPO_RECONFIGURACION,
    TIPO_CUARENTENA,
    crear_mensaje,
)

class NodoSensor:
    def __init__(self, sector, host="127.0.0.1", puerto=5000, semilla=SEMILLA_COMPARTIDA, modo="normal"):
        if sector not in SECTORES:
            raise ValueError(f"Sector invalido. Elige entre: {list(SECTORES.keys())}")
        self.sector = sector
        self.host = host
        self.puerto = puerto
        self.semilla = semilla
        self.modo = modo
        
        self.socket_cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lock_envio = threading.Lock()  # Para evitar que dos hilos envíen datos al mismo tiempo
        self.lock_sectores = threading.Lock()  # Para proteger la lista de sectores vecinos
        
        self.sectores_vigilados = {self.sector}
        self.activo = True
        self.en_cuarentena = False
        self.tiempo_inicio = time.time()
        
    def iniciar(self):
        '''Conecta con la Nave, arranca hilos auxiliares y ejecuta el escaneo de meteoritos'''
        try:
            self.socket_cliente.connect((self.host, self.puerto))
        except ConnectionRefusedError:
            print(f"[SENSOR {self.sector}] No se pudo conectar a la nave en {self.host}:{self.puerto}.")
            return
        
        self.enviar_paquete(TIPO_REGISTRO, {'modo': self.modo, 'semilla': self.semilla})
        print(f"[SENSOR {self.sector}] Conectado a la nave. Modo: {self.modo}, Semilla: {self.semilla}")
        
        threading.Thread(target=self.bucle_heartbeats, daemon=True).start()
        threading.Thread(target=self.escuchar_servidor, daemon=True).start()
        
        try:
            self.bucle_escaneo_prng()
        except KeyboardInterrupt:
            print(f'\n[SENSOR {self.sector}] Apagado manual.')
        finally:
            self.detener()
            
    def bucle_heartbeats(self):
        '''Envia señales de vida a la nave cada 1.5 segundos'''
        while self.activo:
            try:
                if not self.en_cuarentena:
                    with self.lock_sectores:
                        cobertura = sorted(self.sectores_vigilados)
                    self.enviar_paquete(TIPO_HEARTBEAT, {'cobertura': cobertura})
                time.sleep(1.5)
            except OSError:
                break
            
    def escuchar_servidor(self):
        '''Escucha mensajes de la nave y actua en consecuencia'''
        buffer_texto = ""
        while self.activo:
            try:
                datos = self.socket_cliente.recv(1024).decode('utf-8')
                if not datos:
                    print(f"[SENSOR {self.sector}] Conexion cerrada por la nave.")
                    self.activo = False
                    break
                
                buffer_texto += datos
                while "\n" in buffer_texto:
                    linea, buffer_texto = buffer_texto.split("\n", 1)
                    if not linea.strip():
                        continue
                    mensaje = json.loads(linea)
                    self.procesar_orden_mensaje(mensaje)
            except (OSError, json.JSONDecodeError):
                break
            
    def procesar_orden_servidor(self, mensaje):
        '''Ejecuta reconfiguracion dinamica de sectores o acata aislamiento.'''
        tipo = mensaje.get("tipo")
        datos = mensaje.get("datos", {})
        
        if tipo == TIPO_RECONFIGURACION:
            accion = datos.get("accion", "CUBRIR")
            sector_objetivo = datos.get("sector_objetivo")
            if sector_objetivo in SECTORES:
                with self.lock_sectores:
                    if accion == "CUBRIR":
                        self.sectores_vigilados.add(sector_objetivo)
                        print(f"[SENSOR {self.sector}] Ahora vigilando sector {sector_objetivo}.")
                    elif accion == "LIBERAR" and sector_objetivo != self.sector:
                        self.sectores_vigilados.discard(sector_objetivo)
                        print(f"[SENSOR {self.sector}] Dejando de vigilar sector {sector_objetivo}.")

        elif tipo == TIPO_CUARENTENA:
            self.en_cuarentena = True
            print(f"[SENSOR {self.sector}] Aislado por la nave. No se enviaran datos hasta nuevo aviso.")
            
    def generar_evento_ciclo(self,ciclo):
        '''
        Usa el PRNG con semilla compartida para que todos los nodos calculen
        de forma defeinitiva y coordinada la trauectoria del meteorito en este ciclo.
        Retorna un dict {sector: retraso_segundos} con los sensores que deben disparar
        '''
        rng = random.Random(self.semilla + ciclo)
        tirada = rng.random()
        
        if tirada < 0.25:
            return {}
        
        if tirada < 0.65:
            objetivo = rng.choice(ANILLO_PERIMETRAL)
            return {objetivo: 0.0}
        
        if tirada < 0.90:
            indice_inicio = rng.randrange(len(ANILLO_PERIMETRAL))
            direccion = rng.choice([-1, 1])
            longitud = rng.choice([2, 3])
            activaciones = {}
            for paso in range(longitud):
                sec = ANILLO_PERIMETRAL[(indice_inicio + paso * direccion) % len(ANILLO_PERIMETRAL)]
                activaciones[sec] = paso * 0.35
            return activaciones
        
        idx1 = rng.randrange(len(ANILLO_PERIMETRAL))
        desplazamiento = rng.choice([3, 4])
        idx2 = (idx1 + desplazamiento) % len(ANILLO_PERIMETRAL)
        return {
            ANILLO_PERIMETRAL[idx1]: 0.0,
            ANILLO_PERIMETRAL[idx2]: 0.15
        }
    
    def ejecutar_comportamiento_alien(self):
        '''Simula un nodo comprometido por aliens enviando mensajes corruptos o spam.'''
        rng_alien = random.Random()
        ataque = rng_alien.choice(['VALOR_CORRUPTO', 'RAFAGA_SPAM', 'SECTOR_FALSO'])
        
        if ataque == 'VALOR_CORRUPTO':
            print(f"[SENSOR {self.sector} - ALIEN] Enviando valor corrupto a la nave.")
            self.enviar_paquete(TIPO_DETECCION, {'valor': 999, 'sector_objetivo': self.sector})
            
        elif ataque == 'SECTOR_FALSO':
            print(f"[SENSOR {self.sector} - ALIEN] Suplantando sector inexistente 'X9'... ")
            self.enviar_paquete(TIPO_DETECCION, {"valor": 999, "sector_objetivo": self.sector})

        elif ataque == "SECTOR_FALSO":
            print(f"[SENSOR {self.sector} - ALIEN] Suplantando sector inexistente 'X9'...")
            paquete_falso = {"tipo": TIPO_DETECCION, "origen": "X9", "tiempo": time.time(), "datos": {"valor": 1}}
            with self.lock_envio:
                self.socket_cliente.sendall((json.dumps(paquete_falso) + "\n").encode("utf-8"))

        elif ataque == "RAFAGA_SPAM":
            print(f"[SENSOR {self.sector} - ALIEN] Enviando ráfaga anómala de detecciones...")
            for _ in range(6):
                self.enviar_paquete(TIPO_DETECCION, {"valor": 1, "sector_objetivo": self.sector})
                time.sleep(0.05)

    def bucle_escaneo_prng(self):
        """Ciclo principal sincronizado por ventanas de tiempo."""
        ultimo_ciclo_procesado = -1

        while self.activo:
            ahora = time.time()

            # Simulación de caída repentina (Objective 4: Heartbeat timeout)
            if self.modo == "falla" and (ahora - self.tiempo_inicio) > 10.0:
                print(f"[SENSOR {self.sector} - FALLA] ¡Fallo crítico de hardware! Desconectando abruptamente.")
                os._exit(1)

            # Simulación de control alienígena tras 8 segundos de operación normal
            if self.modo == "alien" and (ahora - self.tiempo_inicio) > 8.0 and not self.en_cuarentena:
                self.ejecutar_comportamiento_alien()
                time.sleep(3.0)
                continue

            ciclo_actual = int(ahora // DURACION_CICLO)
            if ciclo_actual != ultimo_ciclo_procesado and not self.en_cuarentena:
                ultimo_ciclo_procesado = ciclo_actual
                activaciones = self.generar_evento_ciclo(ciclo_actual)

                with self.lock_sectores:
                    mis_sectores = list(self.sectores_vigilados)

                # Verifica si alguno de los sectores vigilados por este nodo fue impactado
                detecciones_locales = [
                    (sec, activaciones[sec]) for sec in mis_sectores if sec in activaciones
                ]
                detecciones_locales.sort(key=lambda item: item[1])

                retraso_acumulado = 0.0
                for sector_detectado, retraso in detecciones_locales:
                    espera = max(0.0, retraso - retraso_acumulado)
                    if espera > 0:
                        time.sleep(espera)
                        retraso_acumulado += espera

                    es_respaldo = sector_detectado != self.sector
                    etiqueta = f"{self.sector} (respaldando {sector_detectado})" if es_respaldo else self.sector
                    print(f"[SENSOR {etiqueta}] ¡Meteorito detectado en ciclo {ciclo_actual}!")

                    self.enviar_paquete(
                        TIPO_DETECCION,
                        {
                            "valor": 1,
                            "sector_objetivo": sector_detectado,
                            "reconfigurado": es_respaldo,
                            "ciclo": ciclo_actual
                        }
                    )

            time.sleep(0.1)

    def detener(self):
        self.activo = False
        try:
            self.socket_cliente.close()
        except OSError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nodo Sensor Radar - Aegis-Star 8-Sector")
    parser.add_argument("sector", nargs="?", default="NE", help="Sector del sensor (NO, N, NE, E, SE, S, SO, O)")
    parser.add_argument("--host", default="127.0.0.1", help="IP del Servidor Central")
    parser.add_argument("--puerto", type=int, default=5000, help="Puerto del Servidor Central")
    parser.add_argument("--semilla", type=int, default=SEMILLA_COMPARTIDA, help="Semilla compartida para el PRNG")
    parser.add_argument(
        "--modo",
        choices=["normal", "alien", "falla"],
        default="normal",
        help="Modo de operación: normal, alien (comprometido) o falla (caída de heartbeat)"
    )

    args = parser.parse_args()
    sensor = NodoSensor(
        sector=args.sector.upper(),
        host=args.host,
        puerto=args.puerto,
        semilla=args.semilla,
        modo=args.modo
    )
    sensor.iniciar()
