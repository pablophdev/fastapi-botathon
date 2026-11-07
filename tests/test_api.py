from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import main
from app.modelos import CargaBUK
from test_calculo import parametros, vigencia


@pytest.fixture
def cliente(monkeypatch):
    if os.getenv("CHC_PRUEBAS_DB") != "1":
        pytest.skip("Active CHC_PRUEBAS_DB=1 para probar PostgreSQL con rollback total.")
    if not os.getenv("DATABASE_URL"):
        pytest.skip("Falta DATABASE_URL: no se pudo verificar PostgreSQL real.")
    # Nunca se usan ni alteran tablas del esquema cada_hora existente.
    esquema = "chc_test_" + uuid4().hex
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
        try:
            for archivo in ("01_esquema.sql", "02_planificacion.sql"):
                contenido = Path("sql", archivo).read_text(encoding="utf-8")
                contenido = contenido.replace("BEGIN;", "").replace("COMMIT;", "").replace("cada_hora", esquema)
                db.execute(contenido)
            db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(esquema)))

            @contextmanager
            def conexion_prueba(*, escritura=False):
                with db.transaction():  # savepoint: 409/422 deshacen el lote completo
                    yield db

            monkeypatch.setattr(main, "conexion", conexion_prueba)
            with TestClient(main.app) as client:
                yield client, db
        finally:
            db.rollback()  # elimina también el esquema de prueba sin dejar datos


def lote(identificador="inicial", fecha="2026-01-01", horas=22):
    return {"carga_id": identificador, "anio": 2026, "cambios": [{
        "asignacion_id": "ASG", "vigente_desde": fecha, "operacion": "alta_o_cambio",
        "contrato": {"profesional_id": "P", "instituto": "I", "area": "A", "cargo": "K", "horas_semanales": horas},
    }]}


def test_ciclo_json_transacciones_y_sic(cliente):
    c, db = cliente
    assert c.put("/parametros/2026", json=parametros().model_dump(mode="json")).status_code == 200
    assert c.post("/cargas/buk", json=lote()).json()["estado"] == "procesada"
    assert c.post("/cargas/buk", json=lote()).json()["estado"] == "ya_procesada"
    assert db.execute("SELECT count(*) AS n FROM vigencias").fetchone()["n"] == 1
    assert c.post("/cargas/buk", json=lote(horas=40)).status_code == 409
    assert c.post("/cargas/buk", json=lote("cambio", "2026-01-16", 40)).status_code == 200
    r = c.get("/planificacion", params={"anio": 2026, "mes": 1}).json()
    assert r["resumen"]["horas_contratadas"] == 136.4
    assert r["detalle"][0]["tramo_jornada"] == "22_a_menos_40"
    sic = {"carga_id": "sic", "anio": 2026, "registros": [{"profesional_id": "P", "instituto": "I", "mes": 1, "atenciones": 80}]}
    assert c.post("/cargas/sic", json=sic).status_code == 200
    assert c.post("/cargas/sic", json=sic).json()["estado"] == "ya_procesada"
    assert c.get("/planificacion?anio=2026&mes=1").json()["sic"][0]["atenciones"] == 80
    assert c.get("/planificacion?anio=2026&area=A").json()["sic"] is None
    assert c.get("/planificacion?anio=2026&profesional_id=NO").json()["detalle"] == []
    # Un segundo cambio inválido deshace también el primero y el registro de carga.
    malo = lote("fallido", "2026-02-01", 30)
    otro = deepcopy(malo["cambios"][0])
    otro["asignacion_id"] = "OTRA"
    otro["contrato"]["cargo"] = "DESCONOCIDO"
    malo["cambios"].append(otro)
    assert c.post("/cargas/buk", json=malo).status_code == 422
    assert db.execute("SELECT count(*) AS n FROM cargas WHERE id='fallido'").fetchone()["n"] == 0
    assert len(c.get("/buk/2026/vigencias").json()) == 2
    # Reutilizar el id tras un rollback sí es válido.
    assert c.post("/cargas/buk", json=lote("fallido", "2026-02-01", 30)).status_code == 200
    baja = {"carga_id": "baja", "anio": 2026, "cambios": [{"asignacion_id": "ASG", "vigente_desde": "2026-03-01", "operacion": "baja"}]}
    assert c.post("/cargas/buk", json=baja).status_code == 200
    assert c.get("/planificacion?anio=2026&mes=3").json()["resumen"]["horas_contratadas"] == 0
    assert c.post("/cargas/buk", json=lote("retroactivo", "2026-02-15")).status_code == 409


def test_reenvio_lote_anterior_a_migracion(cliente):
    c, db = cliente
    carga = lote()
    datos = CargaBUK.model_validate(carga).model_dump(mode="json")
    for cambio in datos["cambios"]:
        for campo in ("region", "zona", "vacaciones", "asignaciones_especiales", "reduccion_mayo_pendiente"):
            cambio["contrato"].pop(campo)
    huella = sha256(json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    db.execute("INSERT INTO cargas(fuente,id,huella) VALUES ('BUK',%s,%s)", (carga["carga_id"], huella))
    assert c.post("/cargas/buk", json=carga).json()["estado"] == "ya_procesada"


def test_validacion_http_sin_base():
    with TestClient(main.app) as c:
        carga = lote()
        carga["cambios"].append(deepcopy(carga["cambios"][0]))
        assert c.post("/cargas/buk", json=carga).status_code == 422
        assert c.get("/planificacion?anio=2026&mes=13").status_code == 422
        assert c.post("/cargas/buk", json={"inventado": True}).status_code == 422


def test_reenvio_buk_json_con_almacen_simulado(monkeypatch):
    # Verifica el flujo HTTP y la huella, NO reemplaza la prueba PostgreSQL.
    almacen = {}
    class Resultado:
        def __init__(self, fila):
            self.fila = fila
        def fetchone(self):
            return self.fila
    class DB:
        def execute(self, consulta, argumentos):
            if consulta.startswith("SELECT huella"):
                return Resultado(almacen.get(tuple(argumentos)))
            if consulta.startswith("INSERT INTO cargas"):
                fuente, identificador, huella = argumentos
                almacen[(fuente, identificador)] = {"huella": huella}
                return Resultado(None)
            raise AssertionError(consulta)
    @contextmanager
    def conexion(*, escritura=False):
        yield DB()
    aplicados = []
    monkeypatch.setattr(main, "conexion", conexion)
    monkeypatch.setattr(main, "cargar_parametros", lambda *args: parametros())
    monkeypatch.setattr(main, "verificar_cargos", lambda *args: None)
    monkeypatch.setattr(main, "aplicar_cambio", lambda db, anio, cambio: aplicados.append(cambio))
    with TestClient(main.app) as c:
        assert c.post("/cargas/buk", json=lote()).json()["estado"] == "procesada"
        assert c.post("/cargas/buk", json=lote()).json()["estado"] == "ya_procesada"
        assert len(aplicados) == 1
        assert c.post("/cargas/buk", json=lote(horas=40)).status_code == 409
        antiguo = CargaBUK.model_validate(lote("legado")).model_dump(mode="json")
        for campo in ("region", "zona", "vacaciones", "asignaciones_especiales", "reduccion_mayo_pendiente"):
            antiguo["cambios"][0]["contrato"].pop(campo)
        huella = sha256(json.dumps(antiguo, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        almacen[("BUK", "legado")] = {"huella": huella}
        assert c.post("/cargas/buk", json=lote("legado")).json()["estado"] == "ya_procesada"
        assert c.post("/cargas/buk", json=lote("legado", horas=40)).status_code == 409


def test_planificacion_json_y_filtros_con_lectura_simulada(monkeypatch):
    p = parametros()
    vs = [vigencia(20, hasta="2026-01-15"), vigencia(40, desde="2026-01-16")]
    class Resultado:
        def __init__(self, filas):
            self.filas = filas
        def fetchone(self):
            return self.filas[0]
        def fetchall(self):
            return self.filas
    class DB:
        def execute(self, consulta, argumentos):
            if "FROM parametros" in consulta:
                return Resultado([{"datos": p.model_dump(mode="json")}])
            if "FROM vigencias" in consulta:
                return Resultado(vs)
            if "FROM sic" in consulta:
                return Resultado([{"profesional_id": "P", "instituto": "I", "mes": 1, "atenciones": 50}])
            raise AssertionError(consulta)
    @contextmanager
    def conexion(*, escritura=False):
        yield DB()
    monkeypatch.setattr(main, "conexion", conexion)
    with TestClient(main.app) as c:
        assert c.get("/parametros/2026").status_code == 200
        r = c.get("/planificacion?anio=2026&mes=1&instituto=I&profesional_id=P").json()
        assert r["resumen"]["horas_contratadas"] == 132
        assert r["detalle"][0]["tramo_jornada"] == "22_a_menos_40"
        assert r["resumen"]["atenciones_planificadas"] == 237
        assert r["resumen"]["meta_gestion"] == 213.3
        assert r["resumen"]["foja"] == 0.6
        assert r["sic"][0]["atenciones"] == 50
        assert c.get("/planificacion?anio=2026&cargo=K").json()["sic"] is None
        assert c.get("/planificacion?anio=2026&instituto=OTRO").json()["detalle"] == []
