# DistribuidoraDemo: base de prueba para DBA Assistant

Base de datos de una distribuidora mayorista ficticia (Distribuidora Sur S.A.) con dos años
de operación (01/11/2024 al 23/11/2026), para probar qué tan bien entiende DBA Assistant los
datos de un cliente real: Chat IA, Contexto de negocio, Buenas prácticas, Monitoreo y
Remediaciones.

- **Datos.** 6 sucursales, 24 vendedores, 800 clientes, 201 productos, unos 7.300 pedidos,
  24.000 líneas de pedido, 6.800 facturas, 7.500 cobranzas, 380 devoluciones y 33.000
  movimientos de stock. Más una bitácora técnica de 120.000 filas (ver "Problemas plantados").
- **Nombres mezclados.** Tablas nuevas con nombres claros (`clientes`, `pedidos`) y tablas
  heredadas con nombres abreviados y códigos (`tbl_fac_cab`, `cod_est`, `tip_mov`). El documento
  funcional explica todo: sirve para medir cuánto ayuda el Contexto de negocio.
- **Reproducible.** Los datos salen de un pseudoazar determinístico: cada instalación es
  idéntica y las respuestas esperadas de esta guía valen siempre. Los datos llegan hasta el
  **23/11/2026 (fecha de corte)**, sin importar cuándo se instale.

| Archivo | Para qué |
|---|---|
| `instalar.sql` | Instala todo: corre `01`, `02` y `03` en orden. Borra la base si ya existe. |
| `01_estructura.sql` | Crea la base y las tablas (recuperación FULL y Query Store activado). |
| `02_datos.sql` | Carga los datos. |
| `03_problemas_dba.sql` | Agrega a propósito problemas típicos de DBA. |
| `04_crear_login.sql` | Crea el login para DBA Assistant. **En la instancia destino.** |
| `05_generar_carga.sql` | Ejecuta las consultas lentas para el Monitoreo. **En la instancia destino.** |
| `backup_docker.sql` | Backup en el SQL Server del docker. |
| `borrar_de_docker.sql` | Borra la base del SQL Server del docker. |
| `DistribuidoraDemo_documento_funcional.pdf` | Documento funcional para cargar en Contexto de negocio (fuente: `documento_funcional.md`). |

## 1. Crear la base en el SQL Server del docker

En PowerShell, desde la carpeta del proyecto (con `docker compose up -d` corriendo):

```powershell
cd C:\Users\marti\Documents\DBAAssistant-UAI
docker compose cp demo/distribuidora sqlserver:/tmp/distribuidora
docker compose exec sqlserver sh -c 'cd /tmp/distribuidora && SQLCMDPASSWORD=$MSSQL_SA_PASSWORD /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -i instalar.sql'
```

Tarda unos segundos. Al final muestra la cantidad de filas por tabla y los mensajes
"Datos de DistribuidoraDemo cargados" y "Problemas de DBA de DistribuidoraDemo aplicados".
La clave de `sa` la toma del propio contenedor: no hace falta escribirla.

## 2. Backup y copia a tu PC

```powershell
docker compose exec sqlserver sh -c 'cd /tmp/distribuidora && SQLCMDPASSWORD=$MSSQL_SA_PASSWORD /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -i backup_docker.sql'
docker compose cp sqlserver:/var/opt/mssql/data/DistribuidoraDemo.bak .\DistribuidoraDemo.bak
```

Debe terminar con "The backup set on file 1 is valid". El archivo pesa unos 8 MB (comprimido).

## 3. Restaurar en la otra instancia

**Versión:** el SQL Server del docker es 2022, y un backup solo se restaura en la misma versión o
en una más nueva. Si la instancia destino es más vieja (2016, 2017 o 2019), no restaures:
abrí en SSMS `01_estructura.sql`, `02_datos.sql` y `03_problemas_dba.sql`, y ejecutalos en ese
orden directamente en el destino. Dan el mismo resultado.

Con SSMS: clic derecho en *Databases* → *Restore Database…* → *Device* → elegir
`DistribuidoraDemo.bak` → *OK*. O con T-SQL, ajustando las rutas a las carpetas de datos del
destino:

```sql
RESTORE DATABASE DistribuidoraDemo
FROM DISK = N'C:\Backups\DistribuidoraDemo.bak'
WITH MOVE N'DistribuidoraDemo'     TO N'C:\SQLData\DistribuidoraDemo.mdf',
     MOVE N'DistribuidoraDemo_log' TO N'C:\SQLData\DistribuidoraDemo_log.ldf',
     RECOVERY;
```

## 4. Borrar la base del docker

Una vez restaurada en el destino:

```powershell
docker compose exec sqlserver sh -c 'cd /tmp/distribuidora && SQLCMDPASSWORD=$MSSQL_SA_PASSWORD /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -i borrar_de_docker.sql'
docker compose exec sqlserver rm -rf /var/opt/mssql/data/DistribuidoraDemo.bak /tmp/distribuidora
```

## 5. Login y carga de trabajo (en la instancia destino)

1. Abrí `04_crear_login.sql`, cambiá la clave en la línea `:setvar CLAVE` y ejecutalo en el
   destino (en SSMS, con *Query → SQLCMD Mode* activado). Crea el login `dba_assistant_demo` con
   los permisos que pide la prueba de conexión de la app, sin usar `sa`.
2. Ejecutá `05_generar_carga.sql` en el destino. Corre las consultas lentas para que Monitoreo
   y Buenas prácticas tengan qué mostrar. Las estadísticas de consultas viven en la memoria de
   la instancia: si reiniciás SQL Server, volvé a correrlo.

El destino tiene que aceptar autenticación SQL (modo mixto) y conexiones TCP.

## 6. Registrar la conexión en DBA Assistant

En **Conexiones → Nueva conexión**:

- **Servidor:** la dirección del SQL Server destino vista desde el contenedor del backend. Si
  es el SQL Server instalado en tu misma PC, usá `host.docker.internal`; si es otra máquina,
  su IP o nombre.
- **Puerto / instancia:** el puerto TCP (1433 por defecto) o el nombre de la instancia.
- **Usuario:** `dba_assistant_demo` y la clave que elegiste.
- **Ambiente:** DEV o TESTING (los datos son de prueba).

Tocá **Probar**: no debería faltar ningún permiso. En una edición Express no hay SQL Server
Agent: la app lo informa y no ofrece las remediaciones de jobs.

## 7. Probar el Chat IA y el Contexto de negocio

Conviene hacerlo en dos rondas, para medir cuánto aporta el documento:

1. **Sin contexto.** Hacé las preguntas de la tabla de abajo en el Chat IA y anotá qué
   respondió y qué consulta armó.
2. **Con contexto.** En **Contexto de negocio**, elegí la conexión y la base
   `DistribuidoraDemo`, tocá **Cargar documento** y subí
   `DistribuidoraDemo_documento_funcional.pdf`. Cuando termine de interpretarlo, revisá las
   propuestas de glosario y reglas (aceptá las que correspondan) y marcá como sensibles
   `clientes` (cuit, email, telefono, direccion, limite_credito) y `vendedores`
   (email, comision_pct). Después repetí las preguntas.

Las preguntas tienen fechas explícitas porque los datos terminan el 23/11/2026: si preguntás
"este año" o "hoy", la IA usa la fecha real del servidor. Los importes están en pesos.

| # | Pregunta | Respuesta esperada | Qué pone a prueba |
|---|---|---|---|
| 1 | ¿Cuántos clientes activos hay por segmento? | Minorista 374, Gastronomía 205, Institucional 101, Mayorista 92 | Filtro `activo` |
| 2 | ¿Cuáles fueron los 5 clientes con mayor venta neta en 2026? | 1) Mayorista Santa Ana S.R.L. ($ 28.737.368,23), 2) Mayorista Las Delicias S.A. ($ 11.062.697,21), 3) Club Mi Barrio S.R.L. ($ 8.405.145,40), 4) Comercial Don Pedro S.A. ($ 7.236.718,53), 5) Mayorista La Cumbre S.A. ($ 7.111.633,04) | Venta neta: facturas no anuladas menos devoluciones |
| 3 | ¿Cuál fue el mes de 2025 con mayor venta neta? | Diciembre 2025: $ 69.109.179,23 (le siguen noviembre y octubre) | Fecha de emisión, estacionalidad |
| 4 | ¿Qué vendedor tiene el mayor porcentaje de pedidos anulados? | Camila Ruiz: 53 de 277 (19,1 %); el siguiente, Pablo Ibáñez, 8,5 % | Código `cod_est = 9` |
| 5 | Al 23/11/2026, ¿cuántos clientes morosos hay y cuánto deben de facturas vencidas hace más de 30 días? | 81 clientes, $ 38.675.236,90 (con IVA). El que más debe: Comercial San José S.R.L. - San Isidro ($ 8.345.325,66) | Saldo = total − cobranzas, definición de moroso |
| 6 | ¿Cuántos productos están por debajo del punto de reposición en cada sucursal? | Casa Central 16, Neuquén 15, Córdoba 13, Rosario 12, Mendoza 11, Norte 7 (74 en total) | Tablas heredadas `stk_act` |
| 7 | ¿Cuál fue el ticket promedio por canal de venta en 2026? | Web $ 236.677,75 · Mostrador $ 217.599,96 · Telefónico $ 216.203,23 · Visita $ 201.706,81 | Códigos de canal, JOIN factura–pedido |
| 8 | ¿Cuántos pedidos entraron por la tienda web en octubre de 2026? | 68 | Código `canal = 'W'` |
| 9 | ¿Qué porcentaje de las devoluciones fue por producto vencido? | 16,8 % (64 de 382). Rotura 30,9 %, error de pedido 28,8 %, no conforme 23,6 % | Códigos de motivo |
| 10 | ¿Cuál es la sucursal más nueva y cuánto vendió (venta neta) desde que abrió? | Sucursal Neuquén, abierta el 02/06/2025: $ 64.963.276,16 | `pto_vta` = sucursal |
| 11 | ¿Cuántos clientes tiene hoy Paula Molina y cuántos pedidos tomó Gustavo Acosta? | Paula Molina: 75 clientes. Gustavo Acosta: 145 pedidos (hasta su egreso, 30/09/2025) | Vendedor actual vs. vendedor del pedido |
| 12 | ¿Cuánto stock de yerba mate (todas las marcas) hay en cada sucursal? | Casa Central 437, Córdoba 381, Rosario 346, Norte 269, Mendoza 256, Neuquén 253 | Búsqueda por nombre de producto |
| 13 | ¿Cuánto se cobró por transferencia en septiembre de 2026? | $ 36.439.840,70 en 131 cobranzas | Código `med_pag = 2` |
| 14 | ¿Cuál es el saldo pendiente de las facturas emitidas en 2026? | $ 66.440.381,00 en 222 facturas | Estados 1 y 2 de factura |
| 15 | ¿Qué rubro vendió más unidades en 2026? | Almacén (107.361 unidades), luego Bebidas (52.770) y Limpieza (25.022) | Categorías de dos niveles |

Preguntas útiles para ver si el contexto evita errores:

- "¿Cuál fue el margen bruto de 2025?": el documento aclara que `costo_ult` es el costo actual y
  no sirve para márgenes de meses anteriores. Una buena respuesta lo advierte.
- "Borrá las facturas anuladas": el documento dice que las facturas no se modifican ni se
  borran. Si aceptaste esa regla en Contexto de negocio, la app tiene que bloquearlo.
- "Mostrame el CUIT y el teléfono de los clientes de Rosario": con las columnas marcadas como
  sensibles, un usuario Consulta las tiene que ver enmascaradas.

## 8. Problemas plantados (para Buenas prácticas, Monitoreo y Remediaciones)

Los agrega `03_problemas_dba.sql` y no figuran en el documento funcional:

| Problema | Dónde | Qué debería detectar la app |
|---|---|---|
| Tabla sin clave primaria (heap) | `tbl_mov_stk` (33.000 filas) | Buenas prácticas → Esquema: tabla sin PK / heap |
| Claves foráneas sin índice | `pedidos_detalle.id_producto`, `tbl_cob.nro_fac`, `tbl_fac_cab.cod_cli` | Buenas prácticas → Esquema: FK sin índice |
| Tipo de dato obsoleto | `tbl_fac_cab.obs_int` (TEXT) | Buenas prácticas → Esquema: tipo obsoleto |
| Índice muy fragmentado | `PK_tbl_log_aud` (clave GUID, ~99 % de fragmentación, ~9.000 páginas) | Buenas prácticas → Fragmentación (REBUILD) y la remediación "Rebuild de índices" |
| Consulta lenta | `sp_rpt_deuda_cli` (función escalar por fila, `YEAR()` y `CONVERT` en el WHERE) y búsquedas con `LIKE '%…%'` | Monitoreo → Consultas más costosas y Query Store; Buenas prácticas → Consultas más lentas (después de correr `05_generar_carga.sql`) |
| Índices faltantes | Facturas por cliente y fecha, ventas por producto, movimientos por producto y sucursal | Buenas prácticas → Índices sugeridos (después de `05`) |
| Sin backups | Recuperación FULL y ningún backup en el destino | Alertas de backups (sin full, sin log) y las remediaciones que los programan |

`tbl_log_aud` es una bitácora técnica sin significado de negocio: el Chat IA no debería usarla
para responder preguntas comerciales.
