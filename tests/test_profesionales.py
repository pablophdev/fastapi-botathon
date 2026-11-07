from contextlib import contextmanager

from fastapi.testclient import TestClient
import pytest

from app import main
from app.modelos import Contrato
from app.profesionales import catalogo_profesionales
from test_calculo import parametros, vigencia


@pytest.fixture
def cliente_nombres(monkeypatch):
    vs = [
        vigencia(20, hasta="2026-01-15", nombre_profesional="María Antiguo"),
        vigencia(40, desde="2026-01-16", hasta="2026-06-30", nombre_profesional="María Pérez"),
        vigencia(40, desde="2026-07-01"),  # un cambio sin nombre no lo borra
        vigencia(22, profesional_id="P2", nombre_profesional="María Pérez", instituto="Otro"),
        vigencia(30, profesional_id="P3", nombre_profesional="Juan Soto"),
        vigencia(22, profesional_id="P4"),
    ]
    class Resultado:
        def __init__(self, filas):
            self.filas = filas
        def fetchall(self):
            return self.filas
        def fetchone(self):
            return self.filas[0]
    class DB:
        def execute(self, consulta, argumentos):
            if "FROM vigencias" in consulta:
                return Resultado(vs if argumentos[0] == 2026 else [])
            if "FROM parametros" in consulta:
                return Resultado([{"datos": parametros().model_dump(mode="json")}])
            if "FROM sic" in consulta:
                return Resultado([{"profesional_id": p, "instituto": i, "mes": 1, "atenciones": 10}
                                  for p, i in [("P", "I"), ("P2", "Otro"), ("P3", "I"), ("P4", "I")]])
            raise AssertionError(consulta)
    @contextmanager
    def conexion(*, escritura=False):
        yield DB()
    monkeypatch.setattr(main, "conexion", conexion)
    with TestClient(main.app) as c:
        yield c


def test_busqueda_parcial_sin_tildes_y_homonimos(cliente_nombres):
    c = cliente_nombres
    r = c.get("/profesionales", params={"anio": 2026, "nombre": "  MARIA   pÉ  "})
    assert r.status_code == 200
    assert r.json() == [
        {"profesional_id": "P", "nombre_profesional": "María Pérez", "institutos": ["I"]},
        {"profesional_id": "P2", "nombre_profesional": "María Pérez", "institutos": ["Otro"]},
    ]
    assert c.get("/profesionales?anio=2026&nombre=antiguo").json() == []
    assert c.get("/profesionales?anio=2026&nombre=inexistente").json() == []
    assert c.get("/profesionales?anio=2025&nombre=maria").json() == []


def test_busqueda_instituto_y_paginacion(cliente_nombres):
    c = cliente_nombres
    r = c.get("/profesionales?anio=2026&nombre=maria&instituto=Otro").json()
    assert [p["profesional_id"] for p in r] == ["P2"]
    r = c.get("/profesionales?anio=2026&nombre=maria&limite=1&desplazamiento=1").json()
    assert [p["profesional_id"] for p in r] == ["P2"]
    assert c.get("/profesionales?anio=2026&limite=101").status_code == 422
    assert c.get("/profesionales?anio=2026&nombre=%20%20").status_code == 422


def test_planificacion_nombre_filtra_resumen_detalle_y_sic(cliente_nombres):
    c = cliente_nombres
    r = c.get("/planificacion?anio=2026&mes=1&nombre_profesional=maria").json()
    assert {f["profesional_id"] for f in r["detalle"]} == {"P", "P2"}
    assert all(f["nombre_profesional"] == "María Pérez" for f in r["detalle"])
    assert {f["profesional_id"] for f in r["sic"]} == {"P", "P2"}
    assert r["resumen"]["horas_contratadas"] == 228.8
    seleccionado = c.get("/planificacion?anio=2026&mes=1&profesional_id=P2").json()
    assert seleccionado["detalle"][0]["nombre_profesional"] == "María Pérez"
    vacio = c.get("/planificacion?anio=2026&nombre_profesional=maria&profesional_id=P3").json()
    assert vacio["detalle"] == vacio["sic"] == []
    assert vacio["resumen"]["horas_contratadas"] == 0
    assert c.get("/planificacion?anio=2026&nombre_profesional=maria&area=A").json()["sic"] is None


def test_contratos_anteriores_sin_nombre(cliente_nombres):
    c = cliente_nombres
    r = c.get("/planificacion?anio=2026&profesional_id=P4").json()
    assert r["detalle"][0]["nombre_profesional"] is None
    assert "P4" in {p["profesional_id"] for p in c.get("/profesionales?anio=2026").json()}


def test_catalogo_no_duplica_personas_por_instituto():
    vs = [vigencia(nombre_profesional="Ana"), vigencia(nombre_profesional="Ana", instituto="Otro")]
    assert catalogo_profesionales(vs)["P"]["institutos"] == ["I", "Otro"]


def test_nombre_opcional_validado():
    assert Contrato.model_validate(vigencia(nombre_profesional="  José Pérez  ")["datos"]).nombre_profesional == "José Pérez"
    with pytest.raises(ValueError):
        Contrato.model_validate(vigencia(nombre_profesional=" ")["datos"])
