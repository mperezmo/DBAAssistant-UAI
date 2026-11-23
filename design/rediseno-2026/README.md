# Rediseño 2026 — "años 50 corporativo"

Referencia visual vigente de DBA Assistant. Reemplaza a `design/DBA Assistant.html`,
que queda solo como antecedente del primer diseño.

Las láminas de esta carpeta muestran el resultado esperado:

| Lámina | Contenido |
|---|---|
| `01-inicio.png` | Inicio: estado general derivado de las alertas, indicadores y acciones rápidas. |
| `02-alertas.png` | Alertas: lista con detalle a la derecha y remediación sugerida con su riesgo. |
| `03-monitoreo.png` | Monitoreo: medidores con zonas de umbral, contadores y serie temporal. |
| `04-componentes.png` | Confirmación de riesgo alto, aviso de error, botones, badges y paleta. |

La implementación vive en `backend/tailwind/diseno.css` (tokens y componentes) y
`backend/tailwind/entrada.css` (tema de Tailwind y reglas globales).

## Principios

1. **Estado antes que dato.** Cada pantalla responde primero "¿está todo bien?"; el
   estado general se deriva de las alertas abiertas, nunca se fija a mano.
2. **El color comunica severidad, no decora.** Rojo ladrillo solo para crítico y acciones
   destructivas; mostaza para advertencia; oliva para normal; azul para información.
3. **Riesgo y severidad son cosas distintas.** La severidad es de la alerta (qué tan grave
   es lo que pasa); el riesgo es de la remediación (qué tan invasivo es corregirlo).
4. **Nada de jerga sin traducir.** Títulos en castellano, valores con unidad y formato
   local (`55,4 %`, `10.000`, `23/11/2026 16:24`, 24 h), plurales correctos.
5. **Monoespaciada solo para lo técnico.** T-SQL, códigos de error, identificadores y
   nombres de objetos. Fechas y cantidades usan cifras tabulares de la geométrica.

## Paleta

| Token | Valor | Uso |
|---|---|---|
| `--color-paper` | `#F5F6F7` | Fondo de la página. |
| `--color-surface` | `#FFFFFF` | Tarjetas y paneles. |
| `--color-surface-sunk` | `#ECEFF1` | Encabezados de tabla, áreas hundidas. |
| `--color-sidebar` | `#DCE3E8` | Menú lateral. |
| `--color-fg` | `#2B2620` | Tinta principal. |
| `--color-primary` | `#1F6B66` | Verde agua: acción principal, elemento activo. |
| `--color-danger` | `#B0432B` | Ladrillo: crítico, riesgo alto, PROD. |
| `--color-warn-fill` | `#D9A23A` | Mostaza: advertencia (relleno); el texto usa `#9A6A12`. |
| `--color-ok` | `#46703C` | Oliva: normal, en línea, DEV. |
| `--color-info` | `#2E5B86` | Azul: información. |

Cada color de estado tiene su variante `-tint` para fondos de badge.

## Tipografía

- **Jost** (geométrica): títulos de página (32 px), títulos de tarjeta (18 px), métricas
  grandes (40 px) y cifras en tablas.
- **Libre Franklin**: texto de interfaz, formularios y tablas (14 px base).
- **IBM Plex Mono**: T-SQL, códigos y nombres de objetos.

## Componentes

- **Botones**: primario con borde inferior marcado; secundario con contorno; "riesgo alto"
  con contorno ladrillo y versión rellena solo en la confirmación final.
- **Badges** (`.tag`): severidad, ambiente y riesgo, siempre con texto (no solo color).
- **Pestañas** (`.pestanas`) para cambiar de vista; **segmentado** (`.segmentado`) para
  filtros dentro de una vista.
- **Tarjeta de estado** (`.card-estado`): fondo del color del nivel general.
- **Medidor**: arco de 180° con zonas normal / advertencia / crítico según los umbrales y
  escala que siempre incluye el umbral crítico.
- **Aviso de error** (`parciales/aviso_error.html`): título humano, qué hacer, botón
  *Reintentar* y el código técnico plegado en *Detalle técnico*.
- **Estados vacíos**: una frase que explica por qué no hay datos y qué hacer.
- **Carga**: bloques hundidos (`.esqueleto`) en lugar de indicadores giratorios.

## Confirmación de acciones de riesgo

Las remediaciones de riesgo alto en PROD piden escribir el nombre de la instancia antes de
habilitar el botón, muestran el T-SQL exacto que se va a ejecutar y, al confirmar, solicitan
el segundo factor de la cuenta (llave de acceso o Auth0). La lámina `04-componentes.png`
dibuja casillas de código; la aplicación usa el segundo factor ya existente (ver desvíos en
`docs/arquitectura.md`).
