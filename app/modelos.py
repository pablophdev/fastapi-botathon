from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Clave = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Horas = Annotated[Decimal, Field(ge=0, le=168)]
Porcentaje = Annotated[Decimal, Field(ge=0, le=100)]
Anio = Annotated[int, Field(ge=2000, le=2099)]
Mes = Annotated[int, Field(ge=1, le=12)]
Tramo = Literal["menos_22", "22_a_menos_40", "40_o_mas"]


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profesional(Entrada):
    profesional_id: Clave
    nombre_profesional: Clave | None = None
    institutos: list[Clave]


class Periodo(Entrada):
    desde: date
    hasta: date

    @model_validator(mode="after")
    def validar_periodo(self):
        if self.hasta < self.desde:
            raise ValueError("El término del período precede al inicio.")
        return self


class Contrato(Entrada):
    profesional_id: Clave
    instituto: Clave
    area: Clave
    cargo: Clave
    horas_semanales: Horas
    horas_especiales_semanales: Horas = Decimal(0)
    fecha_fin: date | None = None
    region: Clave | None = None
    zona: Clave | None = None
    vacaciones: list[Periodo] = Field(default_factory=list)
    asignaciones_especiales: list[Clave] = Field(default_factory=list)
    reduccion_mayo_pendiente: bool = False
    nombre_profesional: Clave | None = None

    @model_validator(mode="after")
    def validar_horas(self):
        if self.horas_especiales_semanales > self.horas_semanales:
            raise ValueError("Las horas especiales exceden las horas semanales.")
        if len(set(self.asignaciones_especiales)) != len(self.asignaciones_especiales):
            raise ValueError("No repita asignaciones especiales.")
        if self.asignaciones_especiales and self.horas_especiales_semanales:
            raise ValueError("Use catálogo o descuento legado, no ambos.")
        if self.reduccion_mayo_pendiente and self.horas_semanales != 44:
            raise ValueError("La reducción pendiente solo corresponde a una jornada de 44 horas.")
        return self


class CambioBUK(Entrada):
    asignacion_id: Clave
    vigente_desde: date
    operacion: Literal["alta_o_cambio", "baja"]
    contrato: Contrato | None = None

    @model_validator(mode="after")
    def validar_cambio(self):
        if (self.operacion == "alta_o_cambio") != (self.contrato is not None):
            raise ValueError("Incluya contrato en alta_o_cambio y omítalo en baja.")
        if self.contrato and self.contrato.fecha_fin and self.contrato.fecha_fin < self.vigente_desde:
            raise ValueError("La fecha final debe ser igual o posterior al inicio.")
        return self


class CargaBUK(Entrada):
    carga_id: Clave
    anio: Anio
    cambios: list[CambioBUK]


class AtencionSIC(Entrada):
    profesional_id: Clave
    instituto: Clave
    mes: Mes
    atenciones: Annotated[int, Field(ge=0)]


class CargaSIC(Entrada):
    carga_id: Clave
    anio: Anio
    registros: list[AtencionSIC]


class ReglaCargo(Entrada):
    cargo: Clave
    tramo: Tramo | None = None
    porcentaje_directa: Porcentaje
    porcentaje_indirecta: Porcentaje
    porcentaje_nas: Porcentaje
    minutos_directa: Annotated[int, Field(gt=0)]
    minutos_indirecta: Annotated[int, Field(gt=0)] = 30

    @model_validator(mode="after")
    def validar_distribucion(self):
        if self.porcentaje_directa + self.porcentaje_indirecta + self.porcentaje_nas != 100:
            raise ValueError("Los porcentajes del cargo deben sumar 100.")
        return self


class DiaNoLaboral(Entrada):
    fecha: date
    instituto: Clave | None = None
    region: Clave | None = None


class AsignacionEspecial(Entrada):
    codigo: Clave
    horas_semanales: Horas


class ReglaGrupal(Entrada):
    area: Clave
    instituto: Clave | None = None
    zona: Clave | None = None
    porcentaje_grupal: Porcentaje = Decimal(0)
    pacientes_por_grupo: Annotated[int, Field(ge=1)] = 1

    @model_validator(mode="after")
    def validar_ambito(self):
        if self.instituto and self.zona:
            raise ValueError("Configure instituto o zona, no ambos.")
        return self


class Parametros(Entrada):
    meta_pct: Porcentaje = Decimal(90)
    cargos: Annotated[list[ReglaCargo], Field(min_length=1)]
    dias_no_laborales: list[DiaNoLaboral] = Field(default_factory=list)
    dias_semana: Annotated[list[Annotated[int, Field(ge=0, le=6)]], Field(min_length=1)] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    asignaciones_especiales: list[AsignacionEspecial] = Field(default_factory=list)
    atencion_grupal: list[ReglaGrupal] = Field(default_factory=list)
    redondeo: Literal["truncar", "mitad_arriba"] = "truncar"
    fecha_reduccion_44_42: date = date(2026, 5, 1)

    @model_validator(mode="after")
    def validar_cargos(self):
        if len({(r.cargo, r.tramo) for r in self.cargos}) != len(self.cargos):
            raise ValueError("Defina una regla por cargo y tramo.")
        for cargo in {r.cargo for r in self.cargos}:
            tramos = {r.tramo for r in self.cargos if r.cargo == cargo}
            if None not in tramos and tramos != {"menos_22", "22_a_menos_40", "40_o_mas"}:
                raise ValueError("Incluya los tres tramos o una regla general para cada cargo.")
        if len(set(self.dias_semana)) != len(self.dias_semana):
            raise ValueError("No repita días de la semana.")
        if len({a.codigo for a in self.asignaciones_especiales}) != len(self.asignaciones_especiales):
            raise ValueError("No repita códigos del catálogo especial.")
        if len({(g.area, g.instituto, g.zona) for g in self.atencion_grupal}) != len(self.atencion_grupal):
            raise ValueError("No repita reglas grupales del mismo ámbito.")
        return self
