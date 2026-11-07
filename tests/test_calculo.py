from datetime import date
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.calculo import planificar, promedios_anuales, resumir, tramo_jornada
from app.modelos import Parametros


def parametros(**extra):
    return Parametros.model_validate({"cargos": [{
        "cargo": "K", "porcentaje_directa": 60, "porcentaje_indirecta": 30,
        "porcentaje_nas": 10, "minutos_directa": 30,
    }], **extra})


def vigencia(horas=40, desde="2026-01-01", hasta="2026-12-31", **extra):
    return {"desde": date.fromisoformat(desde), "hasta": date.fromisoformat(hasta), "datos": {
        "profesional_id": "P", "instituto": "I", "area": "A", "cargo": "K",
        "horas_semanales": horas, **extra,
    }}


def mes(vs, p, numero):
    return resumir([v for k, v in planificar(vs, p).items() if k[2] == numero], p.meta_pct, p.redondeo)


@pytest.mark.parametrize("horas,tramo", [(21.99, "menos_22"), (22, "22_a_menos_40"),
                                         (39.99, "22_a_menos_40"), (40, "40_o_mas")])
def test_limites(horas, tramo):
    p = parametros()
    promedio = promedios_anuales([vigencia(horas)], p)[("P", "I")]
    assert tramo_jornada(promedio) == tramo


def test_cambio_mitad_mes_y_ponderacion():
    vs = [vigencia(20, hasta="2026-01-15"), vigencia(40, desde="2026-01-16")]
    assert mes(vs, parametros(), 1)["horas_contratadas"] == 132  # 11*4 + 11*8
    assert promedios_anuales(vs, parametros())[("P", "I")] == Fraction(20*11 + 40*250, 261)


def test_regla_por_tramo_anual():
    reglas = []
    for tramo, pct in [("menos_22", 20), ("22_a_menos_40", 50), ("40_o_mas", 80)]:
        reglas.append({"cargo": "K", "tramo": tramo, "porcentaje_directa": pct,
                       "porcentaje_indirecta": 100-pct, "porcentaje_nas": 0, "minutos_directa": 30})
    p = parametros(cargos=reglas)
    assert mes([vigencia(22)], p, 1)["foja"] == 0.5
    assert mes([vigencia(40)], p, 1)["foja"] == 0.8


def test_mayo_sin_doble_descuento():
    p = parametros()
    pendiente = [vigencia(44, reduccion_mayo_pendiente=True)]
    assert mes(pendiente, p, 4)["horas_contratadas"] == 193.6
    assert mes(pendiente, p, 5)["horas_contratadas"] == 176.4
    assert mes([vigencia(42)], p, 5)["horas_contratadas"] == 176.4
    assert mes([vigencia(44)], p, 5)["horas_contratadas"] == 184.8
    buk_actualizado = [vigencia(44, hasta="2026-04-30", reduccion_mayo_pendiente=True),
                       vigencia(42, desde="2026-05-01")]
    assert mes(buk_actualizado, p, 5)["horas_contratadas"] == 176.4


def test_feriados_regionales_y_vacaciones():
    p = parametros(dias_no_laborales=[{"fecha": "2026-01-01", "region": "R"}])
    assert mes([vigencia(region="R")], p, 1)["horas_contratadas"] == 168
    assert mes([vigencia(region="OTRA")], p, 1)["horas_contratadas"] == 176
    v = vigencia(region="R", vacaciones=[{"desde": "2026-01-02", "hasta": "2026-01-05"}])
    resultado = mes([v], p, 1)
    assert resultado["horas_contratadas"] == 168
    assert resultado["horas_vacaciones"] == 16
    assert resultado["horas_directas"] == 91.2
    assert resultado["foja"] == round(91.2/168, 6)


def test_asignaciones_redistribucion():
    p = parametros(asignaciones_especiales=[{"codigo": "E", "horas_semanales": 5}])
    r = mes([vigencia(asignaciones_especiales=["E"])], p, 1)
    assert r["horas_especiales"] == 22
    assert r["horas_directas"] == 90.93
    assert r["horas_indirectas"] == 45.47
    assert r["horas_nas"] == 17.6
    assert r["horas_disponibles"] + r["horas_no_asistenciales"] == 176


@pytest.mark.parametrize("horas,cantidad,acepta", [(30, 2, False), (30.01, 2, True), (40, 3, False)])
def test_limite_asignaciones(horas, cantidad, acepta):
    codigos = [f"E{i}" for i in range(cantidad)]
    p = parametros(asignaciones_especiales=[{"codigo": c, "horas_semanales": 1} for c in codigos])
    vs = [vigencia(horas, asignaciones_especiales=codigos)]
    if acepta:
        assert planificar(vs, p)
    else:
        with pytest.raises(ValueError, match="Máximo dos"):
            planificar(vs, p)


def test_grupal_meta_foja_y_precedencia():
    p = parametros(atencion_grupal=[
        {"area": "A", "porcentaje_grupal": 100, "pacientes_por_grupo": 9},
        {"area": "A", "zona": "Z", "porcentaje_grupal": 50, "pacientes_por_grupo": 4},
        {"area": "A", "instituto": "I", "porcentaje_grupal": 25, "pacientes_por_grupo": 3},
    ])
    r = mes([vigencia(zona="Z")], p, 1)
    assert r["atenciones_directas"] == 316  # 105.6 h * 2 * (0.75 + 0.25*3)
    assert r["atenciones_indirectas"] == 105
    assert r["atenciones_planificadas"] == 421
    assert r["meta_gestion"] == 378.9
    assert r["foja"] == 0.6 and r["foja_pct"] == 60


def test_redondeo_mensual_y_resumen():
    p = parametros(redondeo="mitad_arriba")
    vs = [vigencia(22)]
    detalle = planificar(vs, p)
    assert mes(vs, p, 1)["atenciones_directas"] == 116
    anual = resumir(detalle.values(), p.meta_pct, p.redondeo)
    assert anual["atenciones_planificadas"] == sum(mes(vs, p, n)["atenciones_planificadas"] for n in range(1, 13))


def test_fecha_fin_y_cero():
    r = mes([vigencia(fecha_fin="2026-01-02")], parametros(), 1)
    assert r["horas_contratadas"] == 16
    assert resumir([], 90)["foja"] is None


def test_validaciones():
    with pytest.raises(ValidationError):
        parametros(cargos=[{"cargo": "K", "porcentaje_directa": 60, "porcentaje_indirecta": 30,
                             "porcentaje_nas": 20, "minutos_directa": 30}])
    with pytest.raises(ValueError, match="desconocida"):
        planificar([vigencia(asignaciones_especiales=["NO"])], parametros())
    with pytest.raises(ValidationError):
        planificar([vigencia(42, reduccion_mayo_pendiente=True)], parametros())


def test_altas_bajas_ponderan_con_cero_y_vacaciones_no_alteran_tramo():
    vs = [vigencia(40, desde="2026-07-01", vacaciones=[{"desde": "2026-08-01", "hasta": "2026-08-31"}])]
    promedio = promedios_anuales(vs, parametros())[("P", "I")]
    assert promedio == Fraction(40 * 132, 261)
    assert tramo_jornada(promedio) == "menos_22"
    assert mes(vs, parametros(), 8)["horas_disponibles"] == 0


def test_asignaciones_se_controlan_entre_contratos():
    p = parametros(asignaciones_especiales=[{"codigo": "E", "horas_semanales": 1}])
    with pytest.raises(ValueError, match="Máximo dos"):
        planificar([vigencia(20, asignaciones_especiales=["E"]),
                    vigencia(20, asignaciones_especiales=["E"])], p)


def test_grupos_zona_y_sin_regla():
    p = parametros(atencion_grupal=[{"area": "A", "zona": "Z", "porcentaje_grupal": 50, "pacientes_por_grupo": 4}])
    assert mes([vigencia(zona="Z")], p, 1)["atenciones_directas"] == 528
    assert mes([vigencia(zona="OTRA")], p, 1)["atenciones_directas"] == 211


def test_calendario_configurable():
    p = parametros(dias_semana=[0, 1, 2, 3])
    assert mes([vigencia(40)], p, 1)["horas_contratadas"] == 170


def test_feriados_nacionales_instituto_y_duplicados():
    p = parametros(dias_no_laborales=[{"fecha": "2026-01-01"},
                                    {"fecha": "2026-01-01", "instituto": "I"},
                                    {"fecha": "2026-01-02", "instituto": "OTRO"}])
    assert mes([vigencia()], p, 1)["horas_contratadas"] == 168


def test_nas_total_y_descuento_con_tope():
    p = parametros(cargos=[{"cargo": "K", "porcentaje_directa": 0, "porcentaje_indirecta": 0,
                            "porcentaje_nas": 100, "minutos_directa": 30}])
    r = mes([vigencia(horas_especiales_semanales=40)], p, 1)
    assert r["horas_nas"] == 176
    assert r["horas_especiales"] == 0
    assert r["atenciones_planificadas"] == 0
    assert r["foja"] == 0
