# Casos de uso propuestos (pendientes de aprobación)

Flujos propuestos para los casos de uso que figuran en el índice del TFI sin detalle.
Siguen el formato del documento original.

**Actores** (esquema de 3 roles vigente): **ADM** Administrador · **DBA** · **CON** Consulta.
Correspondencia con los actores del TFI: A → ADM; DS, DJ y D → DBA; UN → CON.
Todos los flujos operan dentro del *tenant* del usuario autenticado.

Los códigos de error marcados con *(nuevo)* no existen en el catálogo del TFI.

---

## CU-000-001-C · Exportar resultados de consulta

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA \| CON |
| Propósito | Descargar el resultado de una consulta ejecutada |
| Precondición | CU-001-001-A, CU-000-001-B |
| Postcondición | Se genera el archivo y se registra la exportación en la bitácora de transacciones |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Desde la vista de resultados presiona **Exportar**. | 2- Ofrece el formato CSV y la opción de exportar todo o solo lo filtrado. |
| 3- Elige y confirma. | 4- Genera el archivo con las columnas sensibles enmascaradas según su rol. |
| | 5- Descarga el archivo y registra la exportación (usuario, consulta, filas). |

**Flujo alternativo**
- 4.0 Resultado vacío: no se habilita la exportación.
- 4.1 Volumen excede el límite [Error-EXP_LIM_000].

---

## CU-000-002-C · Visualizar logs de Sandbox

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA |
| Propósito | Revisar las validaciones ejecutadas en el Sandbox |
| Precondición | CU-001-001-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Sandbox → Historial**. | 2- Lista las ejecuciones paginadas con filtros por fecha, usuario, conexión y resultado. |
| 3- Selecciona una ejecución. | 4- Muestra el T-SQL, la duración, las filas afectadas, el plan de ejecución, los errores y la fecha de la snapshot utilizada. |
| 5- (Opcional) Presiona **Promover a PROD**. | 6- Deriva a CU-000-002-B si la ejecución fue exitosa y el rol lo permite. |

**Flujo alternativo**
- 2.0 Sin ejecuciones registradas: lo informa.
- 6.0 Ejecución con error: no permite promover [Error-SBX_PRM_000 *(nuevo)*].

---

## CU-000-003-C · Consultar histórico de métricas

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA \| CON (lectura) |
| Propósito | Analizar la evolución de una métrica en el tiempo |
| Precondición | CU-001-001-A, CU-000-003-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Desde el dashboard expande un indicador o accede a **Monitoreo → Histórico**. | 2- Solicita la conexión, la métrica y el rango (1 h, 24 h, 7 d, 30 d o personalizado). |
| 3- Selecciona los parámetros. | 4- Grafica la serie con las líneas de umbral *warning* y *critical* y marca las alertas disparadas en el período. |

**Flujo alternativo**
- 4.0 Sin datos en el rango: lo informa.
- 4.1 Error obteniendo métricas [Error-MNT_DAT_000].

---

## CU-000-004-D · Eliminar alerta

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA |
| Propósito | Dar de baja una regla de alerta configurada |
| Precondición | CU-001-001-A, CU-000-004-A |
| Postcondición | La regla queda inactiva; el histórico de alertas se conserva |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Alertas → Reglas**. | 2- Lista las reglas configuradas. |
| 3- Selecciona una y presiona **Eliminar**. | 4- Muestra el detalle y las alertas activas que generó, y pide confirmación. |
| 5- Confirma. | 6- Da de baja la regla (borrado lógico), cierra sus alertas activas como "regla eliminada" y registra el evento en la bitácora de auditoría. |

**Flujo alternativo**
- 4.0 Regla no encontrada [Error-ALR_VAL_000 *(nuevo)*].
- 4.1 Regla integrada del sistema: solo se puede desactivar, no eliminar.

---

## CU-000-005-C · Eliminar workaround

| Campo | Valor |
|---|---|
| Actores | DBA |
| Propósito | Retirar un workaround del repositorio |
| Precondición | CU-001-001-A, CU-000-005-A |
| Postcondición | El workaround queda inactivo; versiones y ejecuciones se conservan |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Workarounds** y selecciona uno. | 2- Muestra el detalle y las reglas de alerta que lo usan. |
| 3- Presiona **Eliminar**. | 4- Pide confirmación. |
| 5- Confirma. | 6- Da de baja el workaround (borrado lógico) y registra el evento en la bitácora de auditoría. |

**Flujo alternativo**
- 2.0 Workaround no encontrado [Error-WKR_VAL_000].
- 4.0 Está vinculado a reglas de alerta: exige desvincularlo antes [Error-WKR_VAL_002 *(nuevo)*].
- 4.1 Workaround integrado del sistema: solo se puede desactivar.

---

## CU-000-005-D · Consultar workaround

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA |
| Propósito | Ver el detalle, versiones y ejecuciones de un workaround |
| Precondición | CU-001-001-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Workarounds**. | 2- Lista la biblioteca con filtros por categoría, criticidad y ambiente. |
| 3- Selecciona uno. | 4- Muestra descripción, pasos T-SQL, ambiente target, criticidad, rol mínimo, versiones y últimas ejecuciones. |
| 5- (Opcional) Elige dos versiones. | 6- Muestra la diferencia entre ambas. |

**Flujo alternativo**
- 2.0 Biblioteca vacía: lo informa.

---

## CU-000-006-D · Modificar contexto de negocio

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA |
| Propósito | Actualizar una tabla sensible, un término del glosario o una regla de negocio |
| Precondición | CU-001-001-A, CU-000-006-A / B / C |
| Postcondición | Se actualiza el contexto y queda disponible para el motor IA |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Contexto de Negocio**, elige la sección y el elemento. | 2- Muestra el formulario con los datos actuales. |
| 3- Modifica y guarda. | 4- Aplica las mismas validaciones que el alta. |
| | 5- Guarda, invalida la caché de contexto y registra en la bitácora de auditoría el valor anterior y el nuevo. |

**Flujo alternativo**
- 4.0 Datos requeridos no completados [Error-USR_VAL_002].
- 4.1 Entidad técnica no encontrada [Error-CTX_VAL_003].
- 4.2 Conflicto con regla existente [Error-CTX_VAL_002].

---

## CU-000-006-E · Consultar contexto de negocio

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA \| CON (lectura) |
| Propósito | Ver el contexto de negocio cargado para una base |
| Precondición | CU-001-001-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Contexto de Negocio** y selecciona conexión y base. | 2- Muestra en pestañas las tablas sensibles, el glosario y las reglas, con buscador. |

**Flujo alternativo**
- 2.0 Sin contexto cargado: lo informa e invita a cargarlo (solo ADM y DBA).

---

## CU-000-008-B · Comparar versiones de schema

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA \| CON (lectura) |
| Propósito | Ver las diferencias estructurales entre dos versiones |
| Precondición | CU-001-001-A, CU-000-008-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- En la línea de tiempo selecciona dos versiones (o una versión y el estado actual). | 2- Muestra los objetos agregados, eliminados y modificados, con el DDL lado a lado. |
| 3- (Opcional) Presiona **Exportar**. | 4- Descarga el reporte de diferencias. |

**Flujo alternativo**
- 2.0 Se eligió la misma versión: lo informa.
- 2.1 Snapshot no disponible [Error-SCH_SNP_000 *(nuevo)*].

---

## CU-000-009-C · Consultar bitácora de excepciones

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA |
| Propósito | Revisar errores y eventos técnicos del sistema (visor de logs) |
| Precondición | CU-001-001-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Auditoría → Excepciones**. | 2- Lista los registros paginados con filtros por nivel (DEBUG a CRITICAL), módulo, código de error, usuario, fecha y texto. |
| 3- Selecciona un registro. | 4- Muestra el mensaje, el código, el detalle técnico (traza) y el contexto (usuario, conexión, identificador de la operación). |
| 5- (Solo ADM) Accede a **Configurar nivel**. | 6- Permite elegir el nivel mínimo que se registra y guarda el cambio en la bitácora de auditoría. |

**Flujo alternativo**
- 2.0 Sin registros para los filtros: lo informa.

---

## CU-001-000-D · Consultar usuario

| Campo | Valor |
|---|---|
| Actores | ADM |
| Propósito | Ver los datos, permisos y actividad de un usuario |
| Precondición | CU-001-001-A |
| Postcondición | N/A |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Panel Admin → Usuarios**. | 2- Lista los usuarios con filtros por rol y estado. |
| 3- Selecciona uno. | 4- Muestra datos, rol, permisos por ambiente, estado del MFA, método de ingreso (Google o email), último ingreso y últimas acciones, con acceso a la bitácora filtrada por ese usuario. |

**Flujo alternativo**
- 4.0 Usuario no encontrado [Error-USR_VAL_000].

---

## CU-001-001-C · Cambiar contraseña

| Campo | Valor |
|---|---|
| Actores | ADM \| DBA \| CON |
| Propósito | Cambiar la contraseña de ingreso |
| Precondición | CU-001-001-A con usuario de email y contraseña |
| Postcondición | La contraseña queda actualizada en Auth0 |

| Acción del actor | Respuesta del sistema |
|---|---|
| 1- Accede a **Mi perfil → Cambiar contraseña**. | 2- Solicita a Auth0 el envío del correo de cambio de contraseña (la app nunca maneja contraseñas). |
| 3- Sigue el enlace del correo y define la nueva contraseña. | 4- Auth0 valida la política de contraseñas y la actualiza. |
| | 5- Registra la solicitud en la bitácora de auditoría. |

**Flujo alternativo**
- 1.0 Usuario que ingresa con Google: la opción no se muestra; la contraseña la gestiona Google.
- 2.0 Falla en Auth0 [Error-AUT_API_000].
