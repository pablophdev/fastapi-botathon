from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from .modelos import Contrato, Parametros

UN_DIA = timedelta(days=1)


def planificar(vigencias, parametros: Parametros):
    reglas = {regla.cargo: regla for regla in parametros.cargos}
    feriados = {(dia.fecha, dia.instituto) for dia in parametros.dias_no_laborales}
    detalle = defaultdict(lambda: [Fraction(0)] * 6)

    for vigencia in vigencias:
        contrato = Contrato.model_validate(vigencia["datos"])
        regla = reglas[contrato.cargo]
        dia = vigencia["desde"]
        fin = vigencia["hasta"]
        diaria = Fraction(contrato.horas_semanales) / 5
        directa_base = diaria * Fraction(regla.porcentaje_directa) / 100
        indirecta_base = diaria * Fraction(regla.porcentaje_indirecta) / 100
        disponible_base = directa_base + indirecta_base
        disponible = max(Fraction(0), disponible_base - Fraction(contrato.horas_especiales_semanales) / 5)
        directa = disponible * directa_base / disponible_base if disponible_base else Fraction(0)
        indirecta = disponible - directa

        while dia <= fin:
            if dia.weekday() < 5 and (dia, None) not in feriados and (dia, contrato.instituto) not in feriados:
                clave = (contrato.profesional_id, contrato.instituto, dia.month, contrato.area, contrato.cargo)
                valores = detalle[clave]
                valores[0] += diaria
                valores[1] += directa
                valores[2] += indirecta
                valores[3] += diaria - disponible
                valores[4] += directa * 60 / regla.minutos_directa
                valores[5] += indirecta * 60 / regla.minutos_indirecta
            dia += UN_DIA
    return detalle


def porcentaje(numerador, denominador):
    return round(float(numerador * 100 / denominador), 2) if denominador else None


def resumir(detalle, meta_pct):
    contratadas = sum(v[0] for v in detalle)
    directas = sum(v[1] for v in detalle)
    indirectas = sum(v[2] for v in detalle)
    no_asistenciales = sum(v[3] for v in detalle)
    atenciones_directas = sum(int(v[4]) for v in detalle)
    atenciones_indirectas = sum(int(v[5]) for v in detalle)
    planificadas = atenciones_directas + atenciones_indirectas
    return {
        "horas_contratadas": round(float(contratadas), 2),
        "horas_directas": round(float(directas), 2),
        "horas_indirectas": round(float(indirectas), 2),
        "horas_no_asistenciales": round(float(no_asistenciales), 2),
        "horas_disponibles": round(float(directas + indirectas), 2),
        "atenciones_directas": atenciones_directas,
        "atenciones_indirectas": atenciones_indirectas,
        "atenciones_planificadas": planificadas,
        "meta_gestion": round(float(planificadas * meta_pct / 100), 2),
        "foja_pct": porcentaje(directas, contratadas),
    }
