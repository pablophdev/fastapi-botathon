from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction

from .modelos import Contrato, Parametros

UN_DIA = timedelta(days=1)


def tramo_jornada(horas):
    return "menos_22" if horas < 22 else "22_a_menos_40" if horas < 40 else "40_o_mas"


def jornada(contrato, dia, parametros):
    if contrato.reduccion_mayo_pendiente and dia >= parametros.fecha_reduccion_44_42:
        return Fraction(42)
    return Fraction(contrato.horas_semanales)


def habil(dia, contrato, parametros):
    return dia.weekday() in parametros.dias_semana and not any(
        f.fecha == dia and (f.instituto is None or f.instituto == contrato.instituto)
        and (f.region is None or f.region == contrato.region)
        for f in parametros.dias_no_laborales
    )


def dias(desde, hasta):
    while desde <= hasta:
        yield desde
        desde += UN_DIA


def regla_grupal(contrato, parametros):
    candidatas = [g for g in parametros.atencion_grupal if g.area == contrato.area
                  and (g.instituto is None or g.instituto == contrato.instituto)
                  and (g.zona is None or g.zona == contrato.zona)]
    return max(candidatas, key=lambda g: 2 if g.instituto else 1 if g.zona else 0, default=None)


def preparar(vigencias):
    resultado = []
    for v in vigencias:
        c = Contrato.model_validate(v["datos"])
        fin = min(v["hasta"], c.fecha_fin or v["hasta"], date(v["desde"].year, 12, 31))
        resultado.append((c, v["desde"], fin))
    return resultado


def promedios_anuales(vigencias, parametros):
    """Ponderación por días hábiles del año; períodos sin contrato aportan cero."""
    acumulado = defaultdict(Fraction)
    calendarios = defaultdict(set)
    for c, inicio, fin in preparar(vigencias):
        clave = (c.profesional_id, c.instituto)
        calendarios[clave].update(d for d in dias(date(inicio.year, 1, 1), date(inicio.year, 12, 31))
                                if habil(d, c, parametros))
        for d in dias(inicio, fin):
            if habil(d, c, parametros):
                acumulado[clave] += jornada(c, d, parametros)
    return {k: acumulado[k] / len(ds) if ds else Fraction(0) for k, ds in calendarios.items()}


def validar_vigencias(vigencias, parametros):
    catalogo = {a.codigo: Fraction(a.horas_semanales) for a in parametros.asignaciones_especiales}
    por_dia = defaultdict(list)
    for c, inicio, fin in preparar(vigencias):
        if c.cargo not in {r.cargo for r in parametros.cargos}:
            raise ValueError(f"Faltan reglas para el cargo {c.cargo}.")
        if set(c.asignaciones_especiales) - catalogo.keys():
            raise ValueError(f"Asignación especial desconocida en {c.profesional_id}.")
        for d in dias(inicio, fin):
            horas = jornada(c, d, parametros)
            especiales = sum((catalogo[a] for a in c.asignaciones_especiales), Fraction(c.horas_especiales_semanales))
            if especiales > horas:
                raise ValueError("Las horas especiales exceden la jornada efectiva.")
            por_dia[(c.profesional_id, c.instituto, d)].append((horas, c.asignaciones_especiales))
    for registros in por_dia.values():
        horas = sum(r[0] for r in registros)
        codigos = [a for r in registros for a in r[1]]
        if len(codigos) != len(set(codigos)) or len(codigos) > (2 if horas > 30 else 1):
            raise ValueError("Máximo dos asignaciones especiales si la jornada supera 30 h; en otro caso, una. No duplique códigos entre contratos.")


def planificar(vigencias, parametros: Parametros):
    validar_vigencias(vigencias, parametros)
    promedios = promedios_anuales(vigencias, parametros)
    reglas = {(r.cargo, r.tramo): r for r in parametros.cargos}
    catalogo = {a.codigo: Fraction(a.horas_semanales) for a in parametros.asignaciones_especiales}
    # Índices 0..5 compatibles; 6 vacaciones, 7 especiales, 8 NAS, 9 sesiones grupales.
    detalle = defaultdict(lambda: [Fraction(0)] * 10)
    for c, inicio, fin in preparar(vigencias):
        tramo = tramo_jornada(promedios[(c.profesional_id, c.instituto)])
        regla = reglas.get((c.cargo, tramo), reglas.get((c.cargo, None)))
        grupo = regla_grupal(c, parametros)
        grupal = Fraction(grupo.porcentaje_grupal) / 100 if grupo else Fraction(0)
        pacientes = grupo.pacientes_por_grupo if grupo else 1
        especiales = sum((catalogo[a] for a in c.asignaciones_especiales), Fraction(c.horas_especiales_semanales))
        for d in dias(inicio, fin):
            if not habil(d, c, parametros):
                continue
            clave = (c.profesional_id, c.instituto, d.month, c.area, c.cargo)
            v = detalle[clave]
            diaria = jornada(c, d, parametros) / len(parametros.dias_semana)
            v[0] += diaria
            if any(p.desde <= d <= p.hasta for p in c.vacaciones):
                v[6] += diaria
                v[3] += diaria
                continue
            nas = diaria * Fraction(regla.porcentaje_nas) / 100
            base = diaria - nas
            descuento = min(base, especiales / len(parametros.dias_semana))
            disponible = base - descuento
            suma_pct = regla.porcentaje_directa + regla.porcentaje_indirecta
            directa = disponible * Fraction(regla.porcentaje_directa) / Fraction(suma_pct) if suma_pct else Fraction(0)
            indirecta = disponible - directa
            v[1] += directa
            v[2] += indirecta
            v[3] += nas + descuento
            v[7] += descuento
            v[8] += nas
            sesiones = directa * 60 / regla.minutos_directa
            v[4] += sesiones * (1 - grupal + grupal * pacientes)
            v[5] += indirecta * 60 / regla.minutos_indirecta
            v[9] += sesiones * grupal
    return detalle


def porcentaje(numerador, denominador):
    return round(float(numerador * 100 / denominador), 2) if denominador else None


def resumir(detalle, meta_pct, redondeo="truncar"):
    detalle = list(detalle)
    totales = [sum((v[i] for v in detalle), Fraction(0)) for i in range(10)]
    def entero(valor):
        return int(valor if redondeo == "truncar" else valor + Fraction(1, 2))
    directas = sum(entero(v[4]) for v in detalle)
    indirectas = sum(entero(v[5]) for v in detalle)
    planificadas = directas + indirectas
    return {
        "horas_contratadas": round(float(totales[0]), 2),
        "horas_directas": round(float(totales[1]), 2),
        "horas_indirectas": round(float(totales[2]), 2),
        "horas_no_asistenciales": round(float(totales[3]), 2),
        "horas_disponibles": round(float(totales[1] + totales[2]), 2),
        "horas_vacaciones": round(float(totales[6]), 2),
        "horas_especiales": round(float(totales[7]), 2),
        "horas_nas": round(float(totales[8]), 2),
        "sesiones_grupales": sum(entero(v[9]) for v in detalle),
        "atenciones_directas": directas,
        "atenciones_indirectas": indirectas,
        "atenciones_planificadas": planificadas,
        "meta_gestion": round(float(Fraction(planificadas) * Fraction(meta_pct) / 100), 2),
        "foja": round(float(totales[1] / totales[0]), 6) if totales[0] else None,
        "foja_pct": porcentaje(totales[1], totales[0]),
    }
