from datetime import date, timedelta
from hashlib import sha256
import json

from fastapi import FastAPI, HTTPException, Query
from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege, UndefinedTable
from psycopg.types.json import Jsonb

from .calculo import planificar, promedios_anuales, resumir, tramo_jornada, validar_vigencias
from .db import conexion, verificar_base
from .modelos import Anio, CargaBUK, CargaSIC, Clave, Mes, Parametros, Profesional
from .profesionales import catalogo_profesionales, coincide_nombre, normalizar_nombre


app = FastAPI(title="Cada hora cuenta", version="0.4.0")


def cargar_parametros(db, anio):
    fila = db.execute("SELECT datos FROM parametros WHERE anio = %s", (anio,)).fetchone()
    if fila is None:
        raise HTTPException(404, f"Configure primero los parámetros para {anio}.")
    return Parametros.model_validate(fila["datos"])


def registrar_carga(db, fuente, carga):
    datos = carga.model_dump(mode="json")
    huella = sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    previa = db.execute(
        "SELECT huella FROM cargas WHERE fuente = %s AND id = %s", (fuente, carga.carga_id)
    ).fetchone()
    if previa is not None:
        huellas = {huella}
        # Compatibilidad con la versión anterior, que no guardaba nombres.
        legado = carga.model_dump(mode="json")
        compatible = True
        for cambio in legado.get("cambios", []):
            contrato = cambio.get("contrato")
            if contrato:
                valor = contrato.pop("nombre_profesional")
                compatible = compatible and valor is None
        if compatible:
            huellas.add(sha256(json.dumps(legado, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        # Reconoce también lotes anteriores a la ampliación del motor.
        for cambio in legado.get("cambios", []):
            contrato = cambio.get("contrato")
            if contrato:
                for campo in ("region", "zona", "vacaciones", "asignaciones_especiales", "reduccion_mayo_pendiente"):
                    valor = contrato.pop(campo)
                    compatible = compatible and not valor
        if compatible:
            huellas.add(sha256(json.dumps(legado, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
        if previa["huella"] not in huellas:
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
    vigencias = db.execute("SELECT desde, hasta, datos FROM vigencias WHERE anio = %s", (anio,)).fetchall()
    try:
        validar_vigencias(vigencias, parametros)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


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
    if ultima_fecha and inicio <= ultima_fecha["desde"]:
        raise HTTPException(409, f"El cambio de {cambio.asignacion_id} no es posterior a su último cambio aplicado; reenvíe el lote original para reintentar.")
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


@app.get("/profesionales", response_model=list[Profesional])
def buscar_profesionales(
    anio: Anio, nombre: Clave | None = None, instituto: Clave | None = None,
    limite: int = Query(50, ge=1, le=100), desplazamiento: int = Query(0, ge=0),
):
    with conexion() as db:
        vigencias = db.execute(
            "SELECT desde, datos FROM vigencias WHERE anio = %s", (anio,),
        ).fetchall()
    coincidencias = [p for p in catalogo_profesionales(vigencias).values()
                    if coincide_nombre(p, nombre) and (instituto is None or instituto in p["institutos"])]
    coincidencias.sort(key=lambda p: (normalizar_nombre(p["nombre_profesional"]), p["profesional_id"]))
    return coincidencias[desplazamiento:desplazamiento + limite]


@app.get("/planificacion")
def consultar_planificacion(
    anio: Anio, mes: Mes | None = None, instituto: Clave | None = None,
    area: Clave | None = None, cargo: Clave | None = None, profesional_id: Clave | None = None,
    nombre_profesional: Clave | None = None,
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
    profesionales = catalogo_profesionales(vigencias)
    ids_por_nombre = {clave for clave, p in profesionales.items() if coincide_nombre(p, nombre_profesional)}
    promedios = promedios_anuales(vigencias, parametros)
    valores_filtrados = {
        clave: valores for clave, valores in planificar(vigencias, parametros).items()
        if all(filtro is None or filtro == dato for filtro, dato in zip(filtros, clave))
        and (nombre_profesional is None or clave[0] in ids_por_nombre)
    }
    detalle = [
        {"profesional_id": persona, "nombre_profesional": profesionales[persona]["nombre_profesional"],
         "instituto": centro, "mes": periodo, "area": unidad,
         "cargo": rol,
         "jornada_promedio_anual": round(float(promedios[(persona, centro)]), 4),
         "tramo_jornada": tramo_jornada(promedios[(persona, centro)]),
         **resumir([valores], parametros.meta_pct, parametros.redondeo)}
        for (persona, centro, periodo, unidad, rol), valores in sorted(valores_filtrados.items())
    ]
    sic = None
    if area is None and cargo is None:
        sic = [
            dict(f) for f in realizadas
            if all(filtro is None or filtro == f[campo]
                    for filtro, campo in zip(filtros[:3], ("profesional_id", "instituto", "mes")))
            and (nombre_profesional is None or f["profesional_id"] in ids_por_nombre)
        ]

    return {
        "anio": anio,
        "resumen": resumir(valores_filtrados.values(), parametros.meta_pct, parametros.redondeo),
        "detalle": detalle,
        "sic": sic,
    }
