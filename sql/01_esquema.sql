BEGIN;

CREATE SCHEMA IF NOT EXISTS cada_hora;
REVOKE ALL ON SCHEMA cada_hora FROM PUBLIC;

CREATE TABLE IF NOT EXISTS cada_hora.cargas (
    fuente text NOT NULL,
    id text NOT NULL,
    huella text NOT NULL,
    fecha timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, id)
);

CREATE TABLE IF NOT EXISTS cada_hora.parametros (
    anio integer PRIMARY KEY,
    datos jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS cada_hora.vigencias (
    anio integer NOT NULL,
    asignacion text NOT NULL,
    desde date NOT NULL,
    hasta date NOT NULL,
    datos jsonb NOT NULL,
    PRIMARY KEY (anio, asignacion, desde),
    CHECK (hasta >= desde)
);

CREATE TABLE IF NOT EXISTS cada_hora.ultimo_cambio (
    anio integer NOT NULL,
    asignacion text NOT NULL,
    desde date NOT NULL,
    PRIMARY KEY (anio, asignacion)
);

CREATE TABLE IF NOT EXISTS cada_hora.sic (
    anio integer NOT NULL,
    profesional text NOT NULL,
    instituto text NOT NULL,
    mes integer NOT NULL CHECK (mes BETWEEN 1 AND 12),
    atenciones integer NOT NULL CHECK (atenciones >= 0),
    PRIMARY KEY (anio, profesional, instituto, mes)
);

COMMIT;
