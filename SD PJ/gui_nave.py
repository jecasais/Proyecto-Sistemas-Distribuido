import tkinter as tk
import threading
from queue import Empty

from protocolo import ANILLO_PERIMETRAL
from servidor_nave import ServidorNave

BG_PRINCIPAL = "#F8FAFC"
BG_TARJETA = "#FFFFFF"
TEXTO_PRINCIPAL = "#0F172A"
TEXTO_SECUNDARIO = "#64748B"

COLOR_VERDE = "#10B981"
COLOR_AZUL = "#3B82F6"
COLOR_GRIS = "#94A3B8"
COLOR_MORADO = "#8B5CF6"
COLOR_ROJO = "#EF4444"
COLOR_NARANJA = "#F59E0B"
COLOR_NAVE = "#0EA5E9"

MAPA_ESTADOS_SENSOR = {
    "activo": (COLOR_VERDE, "ACTIVO"),
    "cubriendo": (COLOR_AZUL, "CUBRIENDO"),
    "caido": (COLOR_GRIS, "CAÍDO"),
    "cuarentena": (COLOR_MORADO, "CUARENTENA"),
}


class InterfazNave:
    def __init__(self, ventana, servidor):
        self.ventana = ventana
        self.servidor = servidor

        self.ventana.title("Centro de Mando Aegis-Star 8-Sector")
        self.ventana.geometry("580x700")
        self.ventana.configure(bg=BG_PRINCIPAL)
        self.ventana.resizable(False, False)

        marco_superior = tk.Frame(ventana, bg=BG_PRINCIPAL, padx=20, pady=15)
        marco_superior.pack(fill=tk.X)

        lbl_subtitulo = tk.Label(
            marco_superior,
            text="SISTEMA DISTRIBUIDO DE DETECCIÓN Y EVASIÓN EN ESTRELLA",
            font=("Helvetica", 9, "bold"),
            bg=BG_PRINCIPAL,
            fg=TEXTO_SECUNDARIO,
        )
        lbl_subtitulo.pack(anchor="w")

        self.lbl_alerta = tk.Label(
            marco_superior,
            text="ESTADO: SIN AMENAZAS",
            font=("Helvetica", 13, "bold"),
            bg=COLOR_VERDE,
            fg="white",
            pady=10,
        )
        self.lbl_alerta.pack(fill=tk.X, pady=(6, 0))

        tarjeta_matriz = tk.Frame(ventana, bg=BG_TARJETA, bd=1, relief="solid")
        tarjeta_matriz.pack(pady=10, padx=20)

        lbl_titulo_matriz = tk.Label(
            tarjeta_matriz,
            text="REJILLA ESPACIAL PERIMÉTRICA (3x3)",
            font=("Helvetica", 10, "bold"),
            bg=BG_TARJETA,
            fg=TEXTO_PRINCIPAL,
            pady=8,
        )
        lbl_titulo_matriz.pack()

        rejilla_grid = tk.Frame(tarjeta_matriz, bg=BG_TARJETA, padx=15, pady=15)
        rejilla_grid.pack()

        self.celdas_gui: list[list[tk.Label]] = []

        for f in range(3):
            fila_celdas: list[tk.Label] = []
            for c in range(3):
                borde_celda = tk.Frame(rejilla_grid, bg="#E2E8F0", padx=1, pady=1)
                borde_celda.grid(row=f, column=c, padx=4, pady=4)

                lbl = tk.Label(
                    borde_celda,
                    text="0",
                    font=("Consolas", 18, "bold"),
                    width=5,
                    height=2,
                    bg="#F1F5F9",
                    fg=TEXTO_PRINCIPAL,
                )
                lbl.pack()

                if f == 1 and c == 1:
                    lbl.config(
                        text="NAVE",
                        font=("Helvetica", 10, "bold"),
                        bg=COLOR_NAVE,
                        fg="white",
                    )

                fila_celdas.append(lbl)
            self.celdas_gui.append(fila_celdas)

        marco_sensores = tk.Frame(
            ventana, bg=BG_TARJETA, bd=1, relief="solid", padx=12, pady=10
        )
        marco_sensores.pack(fill=tk.X, padx=20, pady=(0, 10))

        lbl_titulo_sensores = tk.Label(
            marco_sensores,
            text="ESTADO DE SENSORES (8 SECTORES)",
            font=("Helvetica", 9, "bold"),
            bg=BG_TARJETA,
            fg=TEXTO_SECUNDARIO,
        )
        lbl_titulo_sensores.pack(anchor="w", pady=(0, 6))

        panel_sensores = tk.Frame(marco_sensores, bg=BG_TARJETA)
        panel_sensores.pack()

        self.etiquetas_sensores: dict[str, tk.Label] = {}
        for indice, sector in enumerate(ANILLO_PERIMETRAL):
            color, texto = MAPA_ESTADOS_SENSOR["caido"]
            etiqueta = tk.Label(
                panel_sensores,
                text=f"{sector}: {texto}",
                font=("Helvetica", 8, "bold"),
                width=13,
                pady=4,
                bg=color,
                fg="white",
            )
            etiqueta.grid(
                row=indice // 4, column=indice % 4, padx=3, pady=3
            )
            self.etiquetas_sensores[sector] = etiqueta

        marco_log = tk.Frame(ventana, bg=BG_PRINCIPAL, padx=20, pady=10)
        marco_log.pack(fill=tk.BOTH, expand=True)

        lbl_log_titulo = tk.Label(
            marco_log,
            text="LOG DE REGISTRO Y MENSAJES DISTRIBUIDOS",
            font=("Helvetica", 9, "bold"),
            bg=BG_PRINCIPAL,
            fg=TEXTO_SECUNDARIO,
        )
        lbl_log_titulo.pack(anchor="w", pady=(0, 4))

        self.txt_log = tk.Text(
            marco_log,
            font=("Consolas", 9),
            bg="#0F172A",
            fg="#38BDF8",
            bd=0,
            padx=10,
            pady=10,
            state="disabled",
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        self.ventana.after(100, self.actualizar_pantalla)

    def _escribir_log(self, texto):
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, f"> {texto}\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")

    def actualizar_pantalla(self):
        while True:
            try:
                evento = self.servidor.cola_eventos.get_nowait()
            except Empty:
                break

            if evento.get("tipo") != "LOG":
                continue

            self._escribir_log(evento.get("texto", ""))

            matriz = evento.get("matriz")
            if matriz:
                for f in range(3):
                    for c in range(3):
                        if f == 1 and c == 1:
                            continue
                        val = matriz[f][c]
                        if val == 1:
                            self.celdas_gui[f][c].config(
                                text="1", bg=COLOR_ROJO, fg="white"
                            )
                        else:
                            self.celdas_gui[f][c].config(
                                text="0", bg="#F1F5F9", fg=TEXTO_PRINCIPAL
                            )

            alerta = evento.get("alerta", "SIN AMENAZAS")
            if "ROJA" in alerta:
                color = COLOR_ROJO
            elif "TRANSITO" in alerta:
                color = COLOR_NARANJA
            else:
                color = COLOR_VERDE
            self.lbl_alerta.config(text=f"ESTADO: {alerta}", bg=color)

            for sector, estado in evento.get("sensores", {}).items():
                if sector not in self.etiquetas_sensores:
                    continue
                color_estado, texto_estado = MAPA_ESTADOS_SENSOR.get(
                    str(estado).lower(), MAPA_ESTADOS_SENSOR["caido"]
                )
                self.etiquetas_sensores[sector].config(
                    text=f"{sector}: {texto_estado}", bg=color_estado
                )

        self.ventana.after(100, self.actualizar_pantalla)


def al_cerrar():
    servidor.en_ejecucion = False
    ventana_principal.destroy()


if __name__ == "__main__":
    servidor = ServidorNave()
    hilo_servidor = threading.Thread(target=servidor.iniciar, daemon=True)
    hilo_servidor.start()

    ventana_principal = tk.Tk()
    app = InterfazNave(ventana_principal, servidor)
    ventana_principal.protocol("WM_DELETE_WINDOW", al_cerrar)
    ventana_principal.mainloop()
