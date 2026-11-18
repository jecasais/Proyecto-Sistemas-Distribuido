from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, ValidationError
from datetime import datetime
import csv
import os

app = FastAPI(title="Aegis-Star 8-Sector Landing Page")

templates = Jinja2Templates(directory=".")

CSV_FILE = "usuarios.csv"

def inicializar_csv():
    """Crea el CSV con encabezados si no existe o si está vacío."""
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, mode="w", newline="", encoding="utf-8") as f:
            escritor = csv.writer(f)
            escritor.writerow(["email", "timestamp"])

def correo_ya_registrado(email: str) -> bool:
    """Verifica si el correo ya está registrado en el CSV."""
    if not os.path.exists(CSV_FILE):
        return False
    with open(CSV_FILE, mode="r", newline="", encoding="utf-8") as f:
        lector = csv.reader(f)
        next(lector)  # Saltar encabezados
        for fila in lector:
            if fila[0] == email:
                return True
    return False

inicializar_csv()

class RegistroCorreo(BaseModel):
    email: EmailStr
    
@app.get("/", response_class=HTMLResponse)
async def leer_formulario(request: Request):
    """Renderiza el formulario de registro."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "mensaje": None}
    )
    
@app.post("/registrar", response_class=HTMLResponse)
async def registrar_usuario(request: Request, email: str = Form(...)):
    """Procesa el formulario HTTP POST, verifica el correo y lo guarda en CSV"""
    try:
        datos_validos = RegistroCorreo(email=email.strip())
        correo_confirmado = datos_validos.email
        
        inicializar_csv()
        
        if correo_ya_registrado(correo_confirmado):
            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={
                    "mensaje": None,
                    "error": "El correo ya está registrado."
                }
            )
            
        marca_tiempo = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
            escritor = csv.writer(f)
            escritor.writerow([correo_confirmado, marca_tiempo])
            
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "mensaje": "¡Registro exitoso! Gracias por unirte.",
                "error": None
            }
        )
        
    except ValidationError:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "mensaje": None,
                "error": "Correo inválido. Por favor, ingresa un correo válido."
            }
        )