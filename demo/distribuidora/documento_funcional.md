---
title: "Sistema comercial de Distribuidora Sur S.A."
subtitle: "Documento funcional de la base de datos DistribuidoraDemo"
author: "Gerencia de Sistemas · Distribuidora Sur S.A."
date: "Versión 1.0 · Noviembre de 2026"
lang: es-AR
---

# 1. Propósito

Este documento describe qué guarda la base de datos **DistribuidoraDemo**, el sistema comercial de
Distribuidora Sur S.A., y cómo se interpreta cada dato. Está pensado para analistas, auditores y
herramientas de consulta que necesitan responder preguntas del negocio (ventas, cobranzas, stock,
clientes) sin conocer el sistema por dentro.

La base combina dos generaciones de tablas: un **modelo comercial nuevo**, con nombres claros
(`clientes`, `pedidos`, `productos`), y **tablas heredadas** del sistema anterior, con nombres
abreviados y códigos numéricos (`tbl_fac_cab`, `tbl_cob`, `tbl_mov_stk`, `stk_act`). Este documento
explica el significado de todas.

Distribuidora Sur S.A. es una empresa ficticia y todos los datos son de prueba. La información
de la base llega hasta el **23/11/2026 (fecha de corte)**.

# 2. La empresa

Distribuidora Sur S.A. es una distribuidora mayorista de productos de consumo masivo (almacén,
bebidas, limpieza, perfumería y congelados). Vende a comercios minoristas, gastronómicos,
otros mayoristas e instituciones.

- **Sucursales.** Opera desde seis sucursales: Casa Central (Ciudad de Buenos Aires), Norte (San
  Isidro), Córdoba, Rosario, Mendoza y Neuquén. La sucursal Neuquén abrió el **02/06/2025**; no
  tiene operaciones anteriores a esa fecha.
- **Fuerza de ventas.** Cada sucursal tiene vendedores que atienden una cartera de clientes. Cada
  cliente tiene un vendedor asignado.
- **Segmentos de clientes.** Minorista (almacenes, kioscos, autoservicios), Gastronomía
  (restaurantes, hoteles, cafeterías), Mayorista (otras distribuidoras) e Institucional
  (colegios, clínicas, clubes).
- **Canales de venta.** Visita del vendedor, pedido telefónico, mostrador de la sucursal y tienda
  web. Los pedidos web no tienen vendedor.
- **Moneda.** Todos los importes están en pesos argentinos (ARS). Los precios aumentan con la
  inflación: un mismo producto se vendió más barato en 2024 que en 2026.

# 3. Circuito comercial

1. **Pedido.** El cliente hace un pedido (por cualquiera de los canales). Queda *pendiente* y,
   una vez revisado el crédito, *aprobado*.
2. **Facturación.** El pedido aprobado se factura. La factura es tipo A o B según la condición
   frente al IVA del cliente. Desde la facturación, la mercadería sale del stock de la sucursal.
3. **Entrega.** El pedido se entrega en la fecha estimada o cerca de ella. Un pedido puede
   anularse antes de facturarse.
4. **Cobranza.** Cada factura vence a los 15 días (clientes minoristas) o a los 30 días (el
   resto). Se cobra en uno o más pagos, por distintos medios.
5. **Devoluciones.** El cliente puede devolver parte de lo facturado; se emite una nota de
   crédito por el importe devuelto.
6. **Stock.** Cada sucursal tiene su propio stock. Se repone con compras trimestrales y se
   controla con ajustes de inventario y transferencias entre sucursales.

# 4. Glosario del negocio

| Término | Definición | Dónde está |
|---|---|---|
| Venta neta | Suma del importe neto (sin IVA) de las facturas **no anuladas** del período, menos el importe de las devoluciones del período. Es la medida oficial de ventas. | `tbl_fac_cab.imp_net` (con `cod_est_fac <> 8`) menos `devoluciones.importe` |
| Facturación | Suma del importe neto de las facturas no anuladas, sin restar devoluciones. | `tbl_fac_cab.imp_net` |
| Ticket promedio | Importe neto promedio por factura no anulada. | `AVG(tbl_fac_cab.imp_net)` |
| Pedido anulado | Pedido cancelado antes de facturarse. No genera factura ni venta. | `pedidos.cod_est = 9` |
| Factura anulada | Factura dada de baja. No cuenta como venta ni como deuda. | `tbl_fac_cab.cod_est_fac = 8` |
| Saldo de una factura | Total de la factura (con IVA) menos lo cobrado. | `tbl_fac_cab.imp_tot` menos `SUM(tbl_cob.imp_cob)` |
| Deuda de un cliente | Suma de los saldos de sus facturas no anuladas. | `tbl_fac_cab` + `tbl_cob` |
| Factura vencida | Factura con saldo mayor que cero cuya fecha de vencimiento ya pasó. | `tbl_fac_cab.fec_vto` |
| Cliente moroso | Cliente con al menos una factura con saldo vencida hace **más de 30 días**. | `tbl_fac_cab.fec_vto`, `tbl_cob` |
| Cliente activo | Cliente habilitado para comprar. Se inactiva si deja de operar. | `clientes.activo = 1` |
| Cartera de un vendedor | Clientes asignados hoy a un vendedor. | `clientes.id_vendedor` |
| Vendedor del pedido | Vendedor que tomó cada pedido (histórico). Puede no coincidir con el vendedor actual del cliente. | `pedidos.id_vendedor` |
| Comisión | Venta neta de los pedidos del vendedor multiplicada por su porcentaje de comisión. La venta web no paga comisión. | `vendedores.comision_pct` |
| Punto de reposición | Cantidad mínima de stock de un producto en una sucursal; por debajo hay que reponer. | `stk_act.cant_min` |
| Quiebre de stock | Producto activo con stock disponible menor que su punto de reposición. | `stk_act.cant_disp < stk_act.cant_min` |
| Comprobante A / B | Tipo de factura: A para responsables inscriptos, B para el resto. | `tbl_fac_cab.tip_cbte` |
| Nota de crédito | Comprobante de una devolución; su importe resta de la venta neta. | `devoluciones` |
| Categoría / rubro | Clasificación de productos en dos niveles: rubro (padre) y categoría (hija). | `categorias.id_categoria_padre` |

# 5. Tablas

## 5.1 sucursales

Sucursales de la empresa.

| Columna | Significado |
|---|---|
| `id_sucursal` | Identificador de la sucursal (1 a 6). |
| `nombre`, `ciudad`, `provincia` | Datos de la sucursal. |
| `fecha_apertura` | Fecha en que empezó a operar. |
| `activa` | 1 si opera actualmente. |

## 5.2 vendedores

Fuerza de ventas.

| Columna | Significado |
|---|---|
| `id_vendedor` | Identificador del vendedor. |
| `nombre`, `apellido`, `email` | Datos del vendedor. El email es dato personal. |
| `id_sucursal` | Sucursal a la que pertenece. |
| `fecha_ingreso`, `fecha_egreso` | Fechas de ingreso y de egreso (vacía si sigue en la empresa). |
| `comision_pct` | Porcentaje de comisión sobre la venta neta. Dato confidencial. |

## 5.3 clientes

Clientes de la empresa.

| Columna | Significado |
|---|---|
| `id_cliente` | Identificador del cliente. En las tablas heredadas se llama `cod_cli`. |
| `razon_social` | Nombre legal del cliente. |
| `cuit` | CUIT (identificación fiscal). Dato sensible. |
| `email`, `telefono`, `direccion` | Datos de contacto. Datos personales. |
| `ciudad`, `provincia` | Localidad del cliente. |
| `segmento` | Minorista, Gastronomía, Mayorista o Institucional. |
| `cond_iva` | Condición frente al IVA (ver códigos). Define el tipo de factura. |
| `id_vendedor` | Vendedor asignado **hoy** (cartera actual). |
| `fecha_alta` | Fecha de alta como cliente. |
| `limite_credito` | Deuda máxima permitida, en pesos. Dato confidencial. |
| `activo` | 1 si el cliente está habilitado. |

## 5.4 categorias y productos

`categorias` tiene dos niveles: los rubros (Almacén, Bebidas, Limpieza, Perfumería, Congelados)
tienen `id_categoria_padre` vacío; las categorías hijas (por ejemplo, Fideos y arroz, Gaseosas,
Vinos) apuntan a su rubro.

| Columna de `productos` | Significado |
|---|---|
| `id_producto` | Identificador del producto. En las tablas heredadas se llama `cod_prod`. |
| `sku` | Código comercial del producto. |
| `nombre` | Descripción: artículo, marca y presentación (por ejemplo, "Yerba mate Campo Dorado 1 kg"). |
| `id_categoria` | Categoría hija del producto. |
| `marca` | Marca. |
| `unidad_medida` | UN (unidad), CJ (caja), PAQ (paquete). |
| `precio_lista` | Precio de lista **vigente**, sin IVA. Los precios de cada venta están en `pedidos_detalle`. |
| `costo_ult` | Último costo de compra, sin IVA. Es el costo **actual**: no sirve para calcular el margen de ventas de meses anteriores. |
| `activo` | 0 si el producto se discontinuó (a fines de 2025). Los discontinuados no se venden en 2026 y no tienen stock. |
| `fecha_alta` | Fecha de alta del producto. |

## 5.5 pedidos y pedidos_detalle

| Columna de `pedidos` | Significado |
|---|---|
| `id_pedido` | Identificador interno del pedido. |
| `nro_pedido` | Número visible del pedido (P-0000001). |
| `id_cliente` | Cliente que pidió. |
| `id_vendedor` | Vendedor que tomó el pedido. Vacío en los pedidos web. |
| `id_sucursal` | Sucursal que atiende el pedido. |
| `fecha_pedido` | Fecha y hora del pedido. |
| `fec_ent_est` | Fecha de entrega estimada. |
| `cod_est` | Estado del pedido (ver códigos). |
| `canal` | Canal de venta (ver códigos). |
| `obs` | Observaciones del pedido. |

| Columna de `pedidos_detalle` | Significado |
|---|---|
| `id_pedido`, `nro_linea` | Pedido y número de línea. |
| `id_producto` | Producto pedido. |
| `cant` | Cantidad, en la unidad de medida del producto. |
| `precio_unit` | Precio unitario sin IVA **al momento del pedido**. |
| `dto_pct` | Descuento de la línea, en porcentaje (0 a 15). |

El importe neto de una línea es `cant × precio_unit × (1 − dto_pct / 100)`.

## 5.6 tbl_fac_cab (facturas)

Tabla heredada con la cabecera de cada factura. Cada pedido facturado tiene una sola factura.

| Columna | Significado |
|---|---|
| `nro_fac` | Número de factura. |
| `tip_cbte` | Tipo de comprobante: A o B. |
| `pto_vta` | Punto de venta: es el `id_sucursal` que facturó. |
| `id_ped` | Pedido facturado (`pedidos.id_pedido`). |
| `cod_cli` | Cliente facturado (`clientes.id_cliente`). |
| `fec_emi` | Fecha de emisión. Es la fecha que se usa para medir ventas. |
| `fec_vto` | Fecha de vencimiento. |
| `imp_net` | Importe neto, sin IVA. |
| `imp_iva` | IVA (21 %). |
| `imp_tot` | Total con IVA. Es lo que el cliente debe pagar. |
| `cod_est_fac` | Estado de la factura (ver códigos). |

## 5.7 tbl_cob (cobranzas)

Pagos recibidos de los clientes. Una factura puede cobrarse en varios pagos.

| Columna | Significado |
|---|---|
| `id_cob` | Identificador del pago. |
| `nro_fac` | Factura que cancela (`tbl_fac_cab.nro_fac`). |
| `fec_cob` | Fecha del pago. |
| `imp_cob` | Importe pagado, con IVA. |
| `med_pag` | Medio de pago (ver códigos). |
| `nro_ref` | Referencia del pago (transferencia, cheque o cupón de tarjeta). |

## 5.8 devoluciones

Devoluciones de mercadería facturada (notas de crédito).

| Columna | Significado |
|---|---|
| `id_devolucion` | Identificador de la devolución. |
| `nro_fac` | Factura original. |
| `id_producto`, `cantidad` | Producto y cantidad devueltos. |
| `motivo_cod` | Motivo (ver códigos). |
| `fecha` | Fecha de la devolución. Resta de la venta neta de ese período. |
| `importe` | Importe de la nota de crédito, sin IVA. |

## 5.9 tbl_mov_stk (movimientos de stock) y stk_act (stock actual)

`tbl_mov_stk` registra cada entrada y salida de mercadería por producto y sucursal. `stk_act`
tiene el saldo resultante y el punto de reposición.

| Columna de `tbl_mov_stk` | Significado |
|---|---|
| `id_mov` | Identificador del movimiento. |
| `cod_prod` | Producto (`productos.id_producto`). |
| `cod_suc` | Sucursal (`sucursales.id_sucursal`). |
| `fec_mov` | Fecha y hora del movimiento. |
| `tip_mov` | Tipo de movimiento (ver códigos). |
| `cant` | Cantidad: positiva si entra, negativa si sale. |
| `ref_doc` | Documento de origen: F + número de factura, D + número de devolución, OC (orden de compra), TR (transferencia), INV (inventario inicial o control) o BAJA-PRODUCTO. Vacío en los ajustes manuales. |
| `usr_alt` | Usuario del sistema anterior que registró el movimiento. SIST son movimientos automáticos. |

| Columna de `stk_act` | Significado |
|---|---|
| `cod_prod`, `cod_suc` | Producto y sucursal. |
| `cant_disp` | Stock disponible. |
| `cant_min` | Punto de reposición. |
| `fec_ult_act` | Fecha del último movimiento. |

## 5.10 prm_gral (parámetros)

Parámetros generales del sistema anterior: `IVA_GRAL` (alícuota de IVA, 21), `DIAS_VTO` (días de
vencimiento, 30), `DIAS_VTO_MIN` (días de vencimiento para minoristas, 15), `DTO_MAX` (descuento
máximo por línea, 15), `MON_DEF` (moneda, ARS), `STK_ALERTA` (alerta de stock activada, S) y
`ULT_CIERRE` (fecha del último cierre contable).

# 6. Códigos

| Campo | Código | Significado |
|---|---|---|
| `pedidos.cod_est` | 1 | Pendiente |
| | 2 | Aprobado |
| | 3 | Facturado (todavía sin entregar) |
| | 4 | Entregado |
| | 9 | Anulado |
| `pedidos.canal` | V | Visita del vendedor |
| | T | Telefónico |
| | M | Mostrador de la sucursal |
| | W | Tienda web |
| `tbl_fac_cab.tip_cbte` | A | Factura A (responsable inscripto) |
| | B | Factura B (resto) |
| `tbl_fac_cab.cod_est_fac` | 1 | Emitida, sin cobrar |
| | 2 | Cobrada parcialmente |
| | 3 | Cobrada |
| | 8 | Anulada |
| `tbl_cob.med_pag` | 1 | Efectivo |
| | 2 | Transferencia |
| | 3 | Cheque |
| | 4 | Tarjeta |
| `tbl_mov_stk.tip_mov` | IN | Ingreso por compra |
| | VT | Salida por venta (factura) |
| | DV | Reingreso por devolución |
| | AJ | Ajuste de inventario |
| | TR | Transferencia entre sucursales |
| `devoluciones.motivo_cod` | RT | Producto roto |
| | VC | Producto vencido |
| | ER | Error en el pedido |
| | NC | Producto no conforme |
| `clientes.cond_iva` | RI | Responsable inscripto |
| | MT | Monotributista |
| | CF | Consumidor final |
| | EX | Exento |

# 7. Cómo se relacionan las tablas

| Desde | Hacia | Condición |
|---|---|---|
| `pedidos` | `clientes` | `pedidos.id_cliente = clientes.id_cliente` |
| `pedidos` | `vendedores` | `pedidos.id_vendedor = vendedores.id_vendedor` (vacío en la web) |
| `pedidos_detalle` | `pedidos` / `productos` | `id_pedido` / `id_producto` |
| `tbl_fac_cab` | `pedidos` | `tbl_fac_cab.id_ped = pedidos.id_pedido` |
| `tbl_fac_cab` | `clientes` | `tbl_fac_cab.cod_cli = clientes.id_cliente` |
| `tbl_fac_cab` | `sucursales` | `tbl_fac_cab.pto_vta = sucursales.id_sucursal` |
| `tbl_cob` | `tbl_fac_cab` | `tbl_cob.nro_fac = tbl_fac_cab.nro_fac` |
| `devoluciones` | `tbl_fac_cab` | `devoluciones.nro_fac = tbl_fac_cab.nro_fac` |
| `tbl_mov_stk`, `stk_act` | `productos` / `sucursales` | `cod_prod = id_producto` / `cod_suc = id_sucursal` |
| `productos` | `categorias` | `productos.id_categoria = categorias.id_categoria` |
| `categorias` | `categorias` (rubro) | `categorias.id_categoria_padre = rubro.id_categoria` |
| `clientes` | `vendedores` | `clientes.id_vendedor = vendedores.id_vendedor` (vendedor actual) |

Las líneas facturadas de una factura son las de su pedido: `pedidos_detalle.id_pedido =
tbl_fac_cab.id_ped`.

# 8. Reglas de negocio

1. Las ventas se miden por **fecha de emisión de la factura** (`fec_emi`), no por fecha de pedido.
2. Las facturas anuladas (`cod_est_fac = 8`) y los pedidos anulados (`cod_est = 9`) no cuentan
   como venta, ni como deuda.
3. La **venta neta** resta las devoluciones del mismo período, según la fecha de la devolución.
4. Los importes de venta se informan **sin IVA** (`imp_net`); las deudas y cobranzas, **con IVA**
   (`imp_tot`, `imp_cob`).
5. El descuento por línea no puede superar el 15 % (`prm_gral.DTO_MAX`).
6. Las facturas a minoristas vencen a los 15 días y el resto, a los 30 días de la emisión.
7. Los productos devueltos por rotura (RT) o vencimiento (VC) no vuelven al stock; los devueltos
   por error en el pedido (ER) o no conformes (NC) sí.
8. La venta web no tiene vendedor y no paga comisión.
9. `clientes.id_vendedor` es el vendedor **actual**; para saber quién vendió, usar
   `pedidos.id_vendedor`. Gustavo Acosta dejó la empresa el 30/09/2025 y su cartera pasó a Paula
   Molina, que desde entonces toma los pedidos de esos clientes.
10. El margen solo puede estimarse para el período reciente: `costo_ult` es el costo actual y los
    precios de venta de meses anteriores eran más bajos por la inflación.
11. Las facturas (`tbl_fac_cab`) y las cobranzas (`tbl_cob`) son documentos fiscales: **no se
    modifican ni se borran** desde consultas; las correcciones se hacen con notas de crédito o
    anulaciones desde el sistema comercial.
12. El stock (`stk_act`) no se modifica directamente: cambia solo a través de movimientos en
    `tbl_mov_stk`.

# 9. Datos sensibles

Los siguientes datos son personales o confidenciales y solo deben mostrarse a quien los necesite:

- `clientes`: `cuit`, `email`, `telefono`, `direccion` y `limite_credito`.
- `vendedores`: `email` y `comision_pct`.
