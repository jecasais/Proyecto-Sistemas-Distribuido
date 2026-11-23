import socket
import threading
import json
import time
from queue import Queue

from protocolo import (
    ANILLO_PERIMETRAL,
    SECTORES,
    VECINOS_ADYACENTES,
    TIPO_REGISTRO,
    TIPO_HEARTBEAT,
    TIPO_DETECCION,
    TIPO_RESPUESTA,
    TIPO_RECONFIGURACION,
    TIPO_CUARENTENA,
    crear_mensaje,
    validar_mensaje,
)


class ServidorNave:
    TIEMPO_INACTIVIDAD = 4.0
    INTERVALO_REVISION = 2.0
    DURACION_METEORO = 3.0
    VENTANA_SPAM = 1.0
    LIMITE_DETECCIONES = 5

    def __init__(self, host="127.0.0.1", puerto=5000):
        self.host = host
        self.puerto = puerto

        self.matriz_amenazas = [[0] * 3 for _ in range(3)]

        self.candado_matriz = threading.RLock()
        self.candado_sensores = threading.Lock()

        self.sensores_conectados = {}
        self.sockets_sensores = {}
        self.identidades_sockets = {}
        self.estados_sensores = {sector: "caido" for sector in ANILLO_PERIMETRAL}
        self.sensores_bloqueados = set()
        self.historial_detecciones = {}
        self.ultima_alerta = "SIN AMENAZAS"

        self.cola_eventos = Queue()
        self.en_ejecucion = True

    def iniciar(self):
        conexion_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conexion_servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conexion_servidor.bind((self.host, self.puerto))
        conexion_servidor.listen(8)
        conexion_servidor.settimeout(0.5)
        print(f"[NAVE] Servidor listo en el puerto {self.puerto}")

        threading.Thread(target=self.revisar_desconexiones, daemon=True).start()

        while self.en_ejecucion:
            try:
                socket_cliente, _ = conexion_servidor.accept()
                threading.Thread(
                    target=self.atender_sensor,
                    args=(socket_cliente,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError as error:
                if self.en_ejecucion:
                    print(f"[ERROR NAVE] {error}")
                break

        conexion_servidor.close()

    def atender_sensor(self, socket_cliente):
        buffer_texto = ""
        nombre_sensor = None

        while self.en_ejecucion:
            try:
                datos = socket_cliente.recv(1024).decode("utf-8")
                if not datos:
                    break

                buffer_texto += datos
                while "\n" in buffer_texto:
                    linea, buffer_texto = buffer_texto.split("\n", 1)
                    if not linea.strip():
                        continue
                    mensaje = json.loads(linea)
                    if isinstance(mensaje, dict):
                        nombre_sensor = mensaje.get("origen") or nombre_sensor
                    self.procesar_mensaje(mensaje, socket_cliente)
                    nombre_sensor = (
                        self._sensor_del_socket(socket_cliente) or nombre_sensor
                    )
            except json.JSONDecodeError as error:
                sensor = (
                    self._sensor_del_socket(socket_cliente)
                    or nombre_sensor
                    or "desconocido"
                )
                self._poner_en_cuarentena(
                    sensor, f"paquete JSON mal formado: {error}", socket_cliente
                )
                break
            except (OSError, UnicodeDecodeError) as error:
                print(f"[NAVE] Error al leer un paquete: {error}")
                break

        if nombre_sensor:
            self.registrar_desconexion(nombre_sensor)
        with self.candado_sensores:
            self.identidades_sockets.pop(socket_cliente, None)
        try:
            socket_cliente.close()
        except OSError:
            pass

    def procesar_mensaje(self, mensaje, socket_cliente):
        if not isinstance(mensaje, dict):
            sensor = self._sensor_del_socket(socket_cliente) or "desconocido"
            self._poner_en_cuarentena(
                sensor, "paquete mal formado", socket_cliente
            )
            return

        origen = mensaje.get("origen")
        identidad = self._sensor_del_socket(socket_cliente)
        sensor = identidad or (
            origen if origen in SECTORES else origen or "desconocido"
        )

        with self.candado_sensores:
            if sensor in self.sensores_bloqueados:
                return

        es_valido, motivo = validar_mensaje(mensaje)
        if es_valido:
            datos = mensaje.get("datos") or {}
            sector_obj = datos.get(
                "sector_objetivo", datos.get("sector_obejtivo", origen)
            )
            if sector_obj not in SECTORES:
                es_valido = False
                motivo = f"Sector de detección inexistente: {sector_obj}"

        if not es_valido:
            self._poner_en_cuarentena(sensor, motivo, socket_cliente)
            return

        tipo = mensaje.get("tipo")
        if identidad and origen != identidad:
            self._poner_en_cuarentena(
                identidad,
                f"el origen {origen} no coincide con el sensor conectado",
                socket_cliente,
            )
            return
        if identidad is None and tipo != TIPO_REGISTRO:
            self._poner_en_cuarentena(
                sensor, "el primer mensaje debe ser de registro", socket_cliente
            )
            return

        with self.candado_sensores:
            self.identidades_sockets.setdefault(socket_cliente, sensor)
            self.sensores_conectados[sensor] = time.time()
            self.sockets_sensores[sensor] = socket_cliente
            if self.estados_sensores.get(sensor) != "cubriendo":
                self.estados_sensores[sensor] = "activo"

        if tipo == TIPO_REGISTRO:
            print(f"[NAVE] Sensor {sensor} conectado con éxito.")
            socket_cliente.sendall(
                crear_mensaje(TIPO_RESPUESTA, "NAVE", {"estado": "OK"})
            )
            self._publicar_evento(f"Sensor {sensor} conectado con éxito.")

        elif tipo == TIPO_HEARTBEAT:
            pass

        elif tipo == TIPO_DETECCION:
            if self._registrar_deteccion(sensor):
                self._poner_en_cuarentena(
                    sensor, "ráfaga anómala de detecciones (spam)", socket_cliente
                )
                return

            datos = mensaje["datos"]
            sector_obj = datos.get(
                "sector_objetivo", datos.get("sector_obejtivo", origen)
            )
            valor = datos.get("valor", 1)
            fila, columna = SECTORES[sector_obj]

            with self.candado_matriz:
                self.matriz_amenazas[fila][columna] = valor

            estado_alerta = self.evaluar_amenaza()
            self._actualizar_alerta(estado_alerta)

            texto_log = (
                f"Sensor {sensor} detectó objeto en {sector_obj} -> Estado: {estado_alerta}"
            )
            print(texto_log)
            self._publicar_evento(texto_log, estado_alerta)

            def apagar_deteccion():
                with self.candado_matriz:
                    self.matriz_amenazas[fila][columna] = 0
                nuevo_estado = self.evaluar_amenaza()
                self._actualizar_alerta(nuevo_estado)
                self._publicar_evento(
                    f"Sector {sector_obj} libre de amenazas.", nuevo_estado
                )

            threading.Timer(self.DURACION_METEORO, apagar_deteccion).start()

    def evaluar_amenaza(self):
        with self.candado_matriz:
            return self._calcular_amenaza()

    def _actualizar_alerta(self, estado):
        with self.candado_sensores:
            activar_evasion = "ROJA" in estado and self.ultima_alerta != estado
            self.ultima_alerta = estado
        if activar_evasion:
            self._activar_evasion()

    def _activar_evasion(self):
        texto = "[ALERTA ROJA] Maniobra de evasión activada: escudos desplegados."
        print(texto)

        orden = crear_mensaje(
            TIPO_RECONFIGURACION, "NAVE", {"accion": "EVASION", "escudos": "ACTIVAR"}
        )
        with self.candado_sensores:
            destinos = [
                socket_sensor for sensor, socket_sensor in self.sockets_sensores.items()
                if sensor not in self.sensores_bloqueados
            ]
        for socket_sensor in destinos:
            try:
                socket_sensor.sendall(orden)
            except OSError as error:
                print(f"[NAVE] No se pudo enviar la orden de evasión: {error}")

        self._publicar_evento(texto)

    def _registrar_deteccion(self, sensor):
        ahora = time.time()
        with self.candado_sensores:
            recientes = [
                instante for instante in self.historial_detecciones.get(sensor, [])
                if ahora - instante <= self.VENTANA_SPAM
            ]
            recientes.append(ahora)
            self.historial_detecciones[sensor] = recientes
            return len(recientes) >= self.LIMITE_DETECCIONES

    def _sensor_del_socket(self, socket_cliente):
        with self.candado_sensores:
            for sensor, socket_sensor in self.sockets_sensores.items():
                if socket_sensor is socket_cliente:
                    return sensor
        return None

    def _poner_en_cuarentena(self, sensor, motivo, socket_cliente):
        with self.candado_sensores:
            self.sensores_bloqueados.add(sensor)
            self.sensores_conectados.pop(sensor, None)
            self.sockets_sensores.pop(sensor, None)
            if sensor in self.estados_sensores:
                self.estados_sensores[sensor] = "cuarentena"

        try:
            socket_cliente.sendall(crear_mensaje(TIPO_CUARENTENA, "NAVE"))
        except OSError as error:
            print(f"[NAVE] No se pudo notificar la cuarentena de {sensor}: {error}")

        texto = f"[CUARENTENA] Sensor {sensor} aislado por anomalía: {motivo}."
        print(texto)
        self._publicar_evento(texto)

    def revisar_desconexiones(self):
        while self.en_ejecucion:
            ahora = time.time()
            with self.candado_sensores:
                vencidos = [
                    sensor for sensor, ultimo in self.sensores_conectados.items()
                    if ahora - ultimo > self.TIEMPO_INACTIVIDAD
                ]
            for sensor in vencidos:
                self.registrar_desconexion(sensor)
            time.sleep(self.INTERVALO_REVISION)

    def registrar_desconexion(self, sensor):
        with self.candado_sensores:
            if sensor not in self.sensores_conectados:
                self.sockets_sensores.pop(sensor, None)
                return

            self.sensores_conectados.pop(sensor, None)
            socket_caido = self.sockets_sensores.pop(sensor, None)
            bloqueado = sensor in self.sensores_bloqueados
            if sensor in self.estados_sensores:
                self.estados_sensores[sensor] = (
                    "cuarentena" if bloqueado else "caido"
                )

            ahora = time.time()
            candidatos = [
                vecino for vecino in VECINOS_ADYACENTES.get(sensor, ())
                if vecino in self.sensores_conectados
                and ahora - self.sensores_conectados[vecino] <= self.TIEMPO_INACTIVIDAD
                and vecino not in self.sensores_bloqueados
            ]
            candidatos.sort(
                key=lambda vecino: self.estados_sensores.get(vecino) == "cubriendo"
            )
            vecino = candidatos[0] if candidatos else None
            socket_vecino = self.sockets_sensores.get(vecino) if vecino else None
            if vecino:
                self.estados_sensores[vecino] = "cubriendo"

        if socket_caido:
            try:
                socket_caido.close()
            except OSError:
                pass

        if vecino and socket_vecino:
            try:
                socket_vecino.sendall(crear_mensaje(
                    TIPO_RECONFIGURACION,
                    "NAVE",
                    {"accion": "CUBRIR", "sector_objetivo": sensor},
                ))
            except OSError as error:
                print(f"[NAVE] No se pudo reconfigurar {vecino}: {error}")

        if bloqueado:
            return

        if vecino:
            texto = (
                f"[DESCONEXIÓN] Sensor {sensor} sin heartbeat por más de "
                f"{self.TIEMPO_INACTIVIDAD:.0f}s. {vecino} cubrirá su sector."
            )
        else:
            texto = (
                f"[DESCONEXIÓN] Sensor {sensor} caído. "
                "No hay vecinos activos disponibles para cubrirlo."
            )
        print(texto)
        self._publicar_evento(texto)

    def _publicar_evento(self, texto, alerta=None):
        with self.candado_matriz:
            estado = alerta if alerta is not None else self._calcular_amenaza()
            evento = {
                "tipo": "LOG",
                "texto": texto,
                "matriz": [fila[:] for fila in self.matriz_amenazas],
                "alerta": estado,
            }
        with self.candado_sensores:
            evento["sensores"] = dict(self.estados_sensores)

        self.cola_eventos.put(evento)

    def _calcular_amenaza(self):
        activos = {
            sector for sector in ANILLO_PERIMETRAL
            if self.matriz_amenazas[SECTORES[sector][0]][SECTORES[sector][1]] == 1
        }
        if not activos:
            return "SIN AMENAZAS"

        total = len(ANILLO_PERIMETRAL)
        for sector in activos:
            indice = ANILLO_PERIMETRAL.index(sector)
            vecinos = (
                ANILLO_PERIMETRAL[(indice - 1) % total],
                ANILLO_PERIMETRAL[(indice + 1) % total],
            )
            if not activos.intersection(vecinos):
                return "ALERTA ROJA: COLISION INMINENTE"
        return "TRANSITO SEGURO (TANGENCIAL)"


if __name__ == "__main__":
    nave = ServidorNave()
    nave.iniciar()
