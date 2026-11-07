# Cada hora cuenta — FastAPI

Backend de planificación de Teletón. Recibe JSON BUK/SIC y guarda parámetros y vigencias en PostgreSQL (Supabase). No implementa Blue Prism ni dashboard. Los ejemplos son **ficticios**, no reglas oficiales.

## Instalación y base de prueba

Python 3.11 o superior, desde la raíz:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Configura `DATABASE_URL` en `.env` con la conexión PostgreSQL de un **proyecto Supabase de prueba** (Session pooler si necesitas IPv4, `sslmode=require`). No publiques credenciales. No sobrescribas un `.env` existente con el ejemplo sin revisarlo.

En SQL Editor de esa base:

1. Base nueva: ejecuta `sql/01_esquema.sql`.
2. Base nueva o existente: ejecuta `sql/02_planificacion.sql`. Es incremental, transaccional y repetible: agrega índices y comentarios sin borrar ni transformar datos. Los campos nuevos usan los JSONB existentes; los contratos y reglas antiguos siguen siendo válidos.
3. Verifica `/salud`. El usuario de conexión necesita permisos sobre el esquema `cada_hora` y sus tablas. No es necesario exponerlas mediante la API pública de Supabase.

## Probar desde `/docs`

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abre <http://127.0.0.1:8000/docs>, pulsa **Try it out** y **Execute**:

1. `GET /salud`.
2. `PUT /parametros/2026`: pega `ejemplos/parametros_completos.json`.
3. `POST /cargas/buk`: pega `ejemplos/buk_completo.json`.
4. Reenvía exactamente el lote: responde `ya_procesada`, sin duplicados. Mismo `carga_id` con otro contenido: 409.
5. `GET /buk/2026/vigencias`: muestra los períodos inclusivos.
6. `GET /planificacion?anio=2026&profesional_id=P-DEMO-002`: resumen y detalle mensual, incluyendo promedio anual, tramo, horas, atenciones, Meta y FOJA. Prueba filtros `mes`, `instituto`, `area`, `cargo`, `profesional_id`.
7. Para probar SIC con los ejemplos originales, carga además `ejemplos/buk_inicial.json` y luego `ejemplos/sic.json`. SIC conserva el total por profesional/instituto/mes y reemplaza el total al recibir un nuevo lote para esa clave. No suma duplicados.
8. `GET /parametros/2026` permite revisar la configuración. Un PUT reemplaza la configuración anual completa y recalcula al consultar; no modifica las vigencias.

Los cuatro ejemplos originales siguen siendo compatibles. No mezcles lotes retroactivos de una misma asignación: envía primero la dotación inicial y luego cambios en orden cronológico.

## Contrato de integración JSON

### Búsqueda por nombre para el dashboard

Las cargas BUK aceptan `contrato.nombre_profesional`, opcional para conservar compatibilidad. Ejemplo: `ejemplos/buk_con_nombre.json`, que puede cargarse después de los parámetros de ejemplo. El nombre se guarda en el JSONB existente: **no requiere otra migración SQL**.

- `GET /profesionales?anio=2026&nombre=maria`: búsqueda parcial, sin distinguir mayúsculas, tildes ni espacios repetidos. Devuelve una lista de `{profesional_id, nombre_profesional, institutos}`. Admite `instituto`, `limite` (1–100, inicialmente 50) y `desplazamiento` (inicialmente 0). Sin coincidencias devuelve `[]`.
- `GET /planificacion?anio=2026&nombre_profesional=maria`: filtra resumen, detalle y SIC por las personas coincidentes. Puede combinarse con los filtros anteriores. Cada fila del detalle incluye `nombre_profesional`.
- Para el frontend: buscar al escribir mediante `/profesionales`, mostrar nombre e instituto para distinguir homónimos y, al seleccionar, consultar `/planificacion?anio=2026&profesional_id=P-NOMBRE-001`. No seleccionar automáticamente a la primera persona si hay varias coincidencias.

Se muestra y busca el último nombre no vacío informado en las vigencias del año, también para meses históricos. Un contrato posterior sin nombre no borra el conocido. Los homónimos mantienen IDs distintos. Los registros antiguos sin nombre siguen accesibles por ID y se muestran con `nombre_profesional: null`; no se inventan nombres ni se buscan en SIC. Para añadir nombres a contratos existentes, envía un nuevo cambio BUK con contrato completo y vigencia posterior, no modifiques ni reenvíes con cambios un lote ya procesado.

Este repositorio solo contiene el backend; la incorporación visual al frontend queda pendiente de disponer de ese proyecto.

Se conservan todas las rutas anteriores. `asignacion_id` es estable por contrato de origen; un profesional puede tener varios. Una carga BUK contiene altas/cambios con **contrato completo**, no parches, o bajas sin contrato. Para contratos anteriores al año, envía vigencia desde el 1 de enero. El inicio debe estar en el año de carga; el término se limita al 31 de diciembre. Una baja rige desde su fecha, por lo que cierra el contrato el día anterior.

Cada lote es atómico. La conexión mantiene transacciones y bloqueo PostgreSQL entre escritores; cualquier error revierte también el registro del lote. Los cambios con fecha anterior o igual al último aplicado se rechazan con 409: para reintentar, reutiliza el lote original. Se reconocen también huellas de lotes de la versión anterior. Los reintentos por errores de red se hacen reenviando **el mismo cuerpo y `carga_id`** hasta obtener `procesada` o `ya_procesada`; no se reejecutan escrituras parcialmente dentro de una transacción fallida. Los totales SIC no se desglosan por área/cargo: si se usan esos filtros, `sic` es `null` para evitar comparativos incorrectos.

**No hay autenticación en esta etapa**: usa localhost y datos ficticios. Autenticación y despliegue seguro son requisitos antes de recibir información real de profesionales o exponer el servicio.

## Reglas y supuestos pendientes de confirmación

No se encontró el enunciado ni la planilla de planificación en el repositorio. Se inspeccionó `../Plantilla_Prueba_Teleton_RPA.xlsx`: sus hojas son instrucciones, donaciones, transporte y dashboard de donaciones; **no permite comparar horas o atenciones de profesionales**. Se necesita la planilla y el enunciado correctos antes de validar resultados oficiales.

- **Calendario:** `dias_semana` usa lunes=0 a domingo=6 (por defecto lunes a viernes). Jornada diaria = jornada semanal / cantidad de días configurados. No se inventan feriados: carga el calendario completo nacional y regional. Un feriado puede aplicar a todos, a una región, a un instituto o a la intersección de ambos; días repetidos no descuentan dos veces. Los contratos deben informar `region` y/o `zona` para aplicar sus reglas.
- **Ponderación anual:** por profesional e instituto, suma jornada semanal efectiva × días hábiles vigentes / días hábiles del año. Las jornadas simultáneas se suman; períodos sin contrato aportan cero. Se usa el calendario anual del instituto (unión si excepcionalmente hay regiones distintas). Vacaciones no reducen la jornada contractual ponderada. Esta elección del denominador **requiere confirmación oficial**, especialmente para altas y bajas. Se calcula antes de filtros mensuales o de área/cargo.
- **Tramos:** menos de 22; desde 22 hasta menos de 40; desde 40. Se comparan valores exactos, no el promedio redondeado mostrado. `cargos[].tramo` permite reglas específicas; deben existir los tres tramos o una regla general (`tramo: null`, compatible con los ejemplos originales). La específica prevalece sobre la general. Directa + indirecta + NAS debe sumar exactamente 100.
- **Mayo de 2026:** desde `fecha_reduccion_44_42` (inicialmente 2026-05-01), únicamente un contrato de 44 h con `reduccion_mayo_pendiente: true` pasa a 42. Por defecto es `false`: BUK se considera fuente de jornada efectiva. Si BUK ya envía 42, no marques pendiente. No se reducen proporcionalmente jornadas parciales. Confirmar fecha y tratamiento con el enunciado.
- **Horas y vacaciones:** las horas contratadas son horas de días hábiles con contrato (feriados excluidos); vacaciones inclusivas conservan horas contratadas, pero aportan cero disponibilidad y se informan aparte. Ausencias superpuestas no se duplican. Por tanto FOJA disminuye con vacaciones; confirmar este denominador con el Excel oficial.
- **Especiales:** catálogo con código y horas semanales. Se descuentan después de NAS, con tope en las horas asistenciales (no hay disponibilidad negativa); el saldo se reparte en la proporción directa:indirecta. Se informan NAS, especiales y vacaciones por separado; `horas_no_asistenciales` incluye los tres para conservar el balance. Máximo dos códigos para jornada efectiva >30 h por profesional/instituto/día, y, como supuesto, máximo uno para <=30. No se permiten códigos repetidos entre contratos simultáneos. El campo legado `horas_especiales_semanales` sigue aceptándose sin catálogo, pero no junto a códigos: por no identificar asignaciones, su cantidad no puede auditarse y queda pendiente normalizarlo desde BUK.
- **Grupos:** porcentaje de las **horas directas**, no de pacientes. Precedencia por área: instituto > zona > general. Sin regla, 0 % grupal y un paciente. Atenciones directas = horas directas × 60/minutos × (1 − porcentaje grupal + porcentaje grupal × pacientes por grupo). Las indirectas no se multiplican. `sesiones_grupales` informa sesiones, no pacientes.
- **Redondeo provisional:** se conserva el truncamiento previo por profesional/instituto/mes/área/cargo y tipo de atención, después de sumar las fracciones diarias y ponderar grupos. Opción `mitad_arriba` configurable. El resumen suma las filas ya redondeadas; nunca redondea nuevamente el total anual. Confirmar si el enunciado exige redondear sesiones individuales/grupales por separado.
- **Indicadores:** `meta_gestion` = atenciones directas + indirectas planificadas, multiplicadas por `meta_pct` (90 inicialmente); no se vuelve a truncar, se muestra con dos decimales. `foja` = horas directas disponibles / horas contratadas, y `foja_pct` es su porcentaje. Sin horas contratadas ambos son `null`. FOJA global es razón de sumas, no promedio de porcentajes.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# Integración PostgreSQL: usa DATABASE_URL de una base de prueba.
$env:CHC_PRUEBAS_DB = "1"
.\.venv\Scripts\python.exe -m pytest -q
```

Las pruebas de integración crean un esquema de nombre único **dentro de una transacción**, aplican ambos SQL y usan savepoints por petición; al terminar hacen rollback de todo, incluido el esquema. No alteran tablas de `cada_hora`. Requieren permiso CREATE SCHEMA. No establezcas `CHC_PRUEBAS_DB` contra producción. Sin bandera o URL se omiten explícitamente; un fallo de conexión con URL configurada falla la prueba.

Cobertura: límites 21,99/22/39,99/40; cambio a mitad de mes; promedio con altas/bajas y vacaciones; reducción de mayo sin doble descuento; feriados regionales; catálogo, redistribución y límite de asignaciones; grupos y precedencia; calendario; Meta, FOJA y redondeo. El reenvío BUK se prueba por HTTP con almacén simulado, incluyendo huellas antiguas y conflicto de contenido. PostgreSQL añade carga real/reenvío, alta/cambio/baja, rollback de lote, filtros y SIC.

Resultado de esta ejecución: **25 pruebas aprobadas y 2 omitidas**. Se verificaron también los JSON completos de ejemplo y la generación de OpenAPI. No estaba configurada `DATABASE_URL` en el `.env` disponible y Docker no estaba iniciado. Las dos pruebas PostgreSQL y la comparación con la planilla oficial quedan pendientes.

Verificación posterior de búsqueda por nombre: **31 pruebas aprobadas y 2 de PostgreSQL omitidas** (integración no activada). Incluye nombres parciales, tildes, homónimos, nombres ausentes/actualizados, paginación, filtros consistentes de planificación/SIC y compatibilidad de reenvíos anteriores. Las búsquedas HTTP ejecutadas usan almacenamiento simulado.
