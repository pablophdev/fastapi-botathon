from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Clave = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Horas = Annotated[Decimal, Field(ge=0, le=168)]
Porcentaje = Annotated[Decimal, Field(ge=0, le=100)]
Anio = Annotated[int, Field(ge=2000, le=2099)]
Mes = Annotated[int, Field(ge=1, le=12)]


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contrato(Entrada):
    profesional_id: Clave
    instituto: Clave
    area: Clave
    cargo: Clave
    horas_semanales: Horas
    horas_especiales_semanales: Horas = Decimal(0)
    fecha_fin: date | None = None

    @model_validator(mode="after")
    def validar_horas(self):
        if self.horas_especiales_semanales > self.horas_semanales:
            raise ValueError("Las horas especiales exceden las horas semanales.")
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


class Parametros(Entrada):
    meta_pct: Porcentaje = Decimal(90)
    cargos: Annotated[list[ReglaCargo], Field(min_length=1)]
    dias_no_laborales: list[DiaNoLaboral] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar_cargos(self):
        if len({regla.cargo for regla in self.cargos}) != len(self.cargos):
            raise ValueError("Defina una regla por cargo.")
        return self
