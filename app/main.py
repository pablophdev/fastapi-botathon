from datetime import date, timedelta
from hashlib import sha256

from fastapi import FastAPI, HTTPException
from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege, UndefinedTable
from psycopg.types.json import Jsonb

from .calculo import planificar, resumir
from .db import conexion, verificar_base
from .modelos import Anio, CargaBUK, CargaSIC, Clave, Mes, Parametros


app = FastAPI(title="Cada hora cuenta", version="0.2.0")


def cargar_parametros(db, anio):
    fila = db.execute("SELECT datos FROM parametros WHERE anio = %s", (anio,)).fetchone()
    if fila is None:
        raise HTTPException(404, f"Configure primero los parámetros para {anio}.")
    return Parametros.model_validate(fila["datos"])


def registrar_carga(db, fuente, carga):
    huella = sha256(carga.model_dump_json().encode()).hexdigest()
    previa = db.execute(
        "SELECT huella FROM cargas WHERE fuente = %s AND id = %s", (fuente, carga.carga_id)
    ).fetchone()
    if previa is not None:
        if previa["huella"] != huella:
            raise HTTPException(409, "La carga_id ya existe con otros datos.")
        return False
    db.execute("INSERT INTO cargas (fuente, id, huella) VALUES (%s, %s, %s)", (fuente, carga.carga_id, huella))
    return True


def resultado_carga(carga_id, nueva, cantidad):
    return {"carga_id": carga_id, "estado": "procesada" if nueva else "ya_procesada", "registros": cantidad}


def verificar_cargos(db, anio, parametros):
    cargos = {regla.cargo for regla in parametros.cargos}
    usados = {
        fila["cargo"] for fila in db.execute(
            "SELECT DISTINCT datos->>'cargo' AS cargo FROM vigencias WHERE anio = %s", (anio,)
        )
    }
    if usados - cargos:
        faltan = ", ".join(sorted(usados - cargos))
        raise HTTPException(422, f"Faltan reglas de cálculo para: {faltan}.")


@app.get("/salud")
def salud():
    try:
        verificar_base()
    except (OperationalError, RuntimeError) as error:
        raise HTTPException(503, "Comprueba la conexión DATABASE_URL con Supabase.") from error
    except (UndefinedTable, InsufficientPrivilege) as error:
        raise HTTPException(503, "Comprueba el esquema sql/01_esquema.sql y sus permisos.") from error
    return {"estado": "ok", "base_datos": "postgresql"}


@app.get("/parametros/{anio}", response_model=Parametros)
def leer_parametros(anio: Anio):
    with conexion() as db:
        return cargar_parametros(db, anio)


@app.put("/parametros/{anio}", response_model=Parametros)
def editar_parametros(anio: Anio, parametros: Parametros):
    if any(dia.fecha.year != anio for dia in parametros.dias_no_laborales):
        raise HTTPException(422, "Los días no laborales deben corresponder al año indicado.")
    with conexion(escritura=True) as db:
        verificar_cargos(db, anio, parametros)
        db.execute(
            "INSERT INTO parametros VALUES (%s, %s) ON CONFLICT(anio) DO UPDATE SET datos = excluded.datos",
            (anio, Jsonb(parametros.model_dump(mode="json"))),
        )
    return parametros


@app.get("/buk/{anio}/vigencias")
def leer_vigencias(anio: Anio):
    with conexion() as db:
        filas = db.execute(
            """SELECT asignacion, desde, hasta, datos FROM vigencias
               WHERE anio = %s ORDER BY asignacion, desde""",
            (anio,),
        )

        return [
            {"asignacion_id": f["asignacion"], "vigente_desde": f["desde"],
             "vigente_hasta": f["hasta"], "contrato": f["datos"]}
            for f in filas
        ]


def aplicar_cambio(db, anio, cambio):
    inicio = cambio.vigente_desde
    clave = (anio, cambio.asignacion_id)
    ultima_fecha = db.execute(
        "SELECT desde FROM ultimo_cambio WHERE anio = %s AND asignacion = %s", clave
    ).fetchone()
    if ultima_fecha and inicio < ultima_fecha["desde"]:
        raise HTTPException(409, f"El cambio de {cambio.asignacion_id} es anterior a su último cambio aplicado.")
    db.execute(
        """INSERT INTO ultimo_cambio VALUES (%s, %s, %s)
           ON CONFLICT(anio, asignacion) DO UPDATE SET desde = excluded.desde""",
        (*clave, inicio),
    )
    anterior = inicio - timedelta(days=1)
    db.execute(
        """UPDATE vigencias SET hasta = %s
           WHERE anio = %s AND asignacion = %s AND desde < %s AND hasta >= %s""",
        (anterior, *clave, inicio, inicio),
    )
    db.execute(
        "DELETE FROM vigencias WHERE anio = %s AND asignacion = %s AND desde = %s",
        (*clave, inicio),
    )
    if cambio.contrato is not None:
        fin = min(cambio.contrato.fecha_fin or date(anio, 12, 31), date(anio, 12, 31))
        db.execute(
            "INSERT INTO vigencias VALUES (%s, %s, %s, %s, %s)",
            (*clave, inicio, fin, Jsonb(cambio.contrato.model_dump(mode="json"))),
        )


@app.post("/cargas/buk")
def cargar_buk(carga: CargaBUK):
    claves = {(c.asignacion_id, c.vigente_desde) for c in carga.cambios}
    if len(claves) != len(carga.cambios):
        raise HTTPException(422, "Cada asignación y fecha de vigencia debe ser única en el lote.")
    if any(c.vigente_desde.year != carga.anio for c in carga.cambios):
        raise HTTPException(422, "Las fechas de vigencia deben corresponder al año de carga.")
    with conexion(escritura=True) as db:
        nueva = registrar_carga(db, "BUK", carga)
        if nueva:
            parametros = cargar_parametros(db, carga.anio)
            for cambio in sorted(carga.cambios, key=lambda c: c.vigente_desde):
                aplicar_cambio(db, carga.anio, cambio)
            verificar_cargos(db, carga.anio, parametros)
    return resultado_carga(carga.carga_id, nueva, len(carga.cambios))


@app.post("/cargas/sic")
def cargar_sic(carga: CargaSIC):
    claves = {(r.profesional_id, r.instituto, r.mes) for r in carga.registros}
    if len(claves) != len(carga.registros):
        raise HTTPException(422, "Envíe un total SIC por profesional, instituto y mes.")
    with conexion(escritura=True) as db:
        nueva = registrar_carga(db, "SIC", carga)
        if nueva:
            with db.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO sic VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT(anio, profesional, instituto, mes)
                       DO UPDATE SET atenciones = excluded.atenciones""",
                    [(carga.anio, r.profesional_id, r.instituto, r.mes, r.atenciones) for r in carga.registros],
                )
    return resultado_carga(carga.carga_id, nueva, len(carga.registros))


@app.get("/planificacion")
def consultar_planificacion(
    anio: Anio, mes: Mes | None = None, instituto: Clave | None = None,
    area: Clave | None = None, cargo: Clave | None = None, profesional_id: Clave | None = None,
):
    with conexion() as db:
        parametros = cargar_parametros(db, anio)
        vigencias = db.execute(
            "SELECT desde, hasta, datos FROM vigencias WHERE anio = %s", (anio,)
        ).fetchall()
        realizadas = db.execute(
            """SELECT profesional AS profesional_id, instituto, mes, atenciones
               FROM sic WHERE anio = %s ORDER BY instituto, profesional, mes""",
            (anio,),
        ).fetchall()

    filtros = (profesional_id, instituto, mes, area, cargo)
    valores_filtrados = {
        clave: valores for clave, valores in planificar(vigencias, parametros).items()
        if all(filtro is None or filtro == dato for filtro, dato in zip(filtros, clave))
    }
    detalle = [
        {"profesional_id": persona, "instituto": centro, "mes": periodo, "area": unidad,
         "cargo": rol, **resumir([valores], parametros.meta_pct)}
        for (persona, centro, periodo, unidad, rol), valores in sorted(valores_filtrados.items())
    ]
    sic = None
    if area is None and cargo is None:
        sic = [
            dict(f) for f in realizadas
            if all(filtro is None or filtro == f[campo]
                   for filtro, campo in zip(filtros[:3], ("profesional_id", "instituto", "mes")))
        ]

    return {
        "anio": anio,
        "resumen": resumir(valores_filtrados.values(), parametros.meta_pct),
        "detalle": detalle,
        "sic": sic,
    }
