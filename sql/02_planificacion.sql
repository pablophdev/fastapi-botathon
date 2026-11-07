-- Incremental, no elimina ni modifica datos. Los nuevos campos se almacenan
-- en los JSONB existentes y tienen valores predeterminados compatibles.
BEGIN;
CREATE INDEX IF NOT EXISTS vigencias_anio_desde_hasta_idx
    ON cada_hora.vigencias (anio, desde, hasta);
CREATE INDEX IF NOT EXISTS vigencias_profesional_instituto_idx
    ON cada_hora.vigencias (anio, (datos->>'profesional_id'), (datos->>'instituto'));
COMMENT ON COLUMN cada_hora.vigencias.datos IS
    'Contrato BUK: jornada, región/zona, vacaciones, catálogo de asignaciones y reducción de mayo pendiente. Vigencias inclusivas.';
COMMENT ON COLUMN cada_hora.parametros.datos IS
    'Parámetros editables por cargo/tramo, calendario, grupos, asignaciones, meta y redondeo. Compatible con reglas generales anteriores.';
COMMIT;
