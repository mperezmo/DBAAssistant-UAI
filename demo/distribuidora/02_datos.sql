/*
    DistribuidoraDemo · 2. Datos

    Dos años de operación de Distribuidora Sur S.A. (01/11/2024 al 23/11/2026): sucursales,
    vendedores, clientes, productos, pedidos, facturas, cobranzas, devoluciones y stock.

    Los valores salen de un pseudoazar determinístico (un hash de cada fila): la base es
    idéntica en cada instalación y no depende de la fecha en que se ejecute el script.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

USE DistribuidoraDemo;
GO

-- Número en [0, 1) que depende solo de la clave y del número.
CREATE OR ALTER FUNCTION dbo.aux_azar(@clave VARCHAR(20), @n BIGINT)
RETURNS TABLE
AS RETURN
    SELECT CAST(CAST(SUBSTRING(HASHBYTES('MD5', CONCAT(@clave, ':', @n)), 1, 4) AS BIGINT) AS FLOAT)
           / 4294967296.0 AS r;
GO

DROP TABLE IF EXISTS #n;
SELECT TOP (250000) n = CAST(ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS INT)
INTO #n
FROM sys.all_columns AS a CROSS JOIN sys.all_columns AS b;
CREATE UNIQUE CLUSTERED INDEX IX_n ON #n (n);
GO

/* --- Sucursales, vendedores, parámetros ---------------------------------------- */

INSERT INTO dbo.sucursales (id_sucursal, nombre, ciudad, provincia, fecha_apertura) VALUES
    (1, N'Casa Central',     N'Ciudad de Buenos Aires', N'CABA',         '2012-03-01'),
    (2, N'Sucursal Norte',   N'San Isidro',             N'Buenos Aires', '2015-08-10'),
    (3, N'Sucursal Córdoba', N'Córdoba',                N'Córdoba',      '2017-05-02'),
    (4, N'Sucursal Rosario', N'Rosario',                N'Santa Fe',     '2018-09-17'),
    (5, N'Sucursal Mendoza', N'Mendoza',                N'Mendoza',      '2020-02-03'),
    (6, N'Sucursal Neuquén', N'Neuquén',                N'Neuquén',      '2025-06-02');

INSERT INTO dbo.vendedores
    (id_vendedor, nombre, apellido, email, id_sucursal, fecha_ingreso, fecha_egreso, comision_pct)
VALUES
    (1,  N'Lucía',     N'Fernández', N'lucia.fernandez@distribuidorasur.example',    1, '2016-03-01', NULL, 4.00),
    (2,  N'Martín',    N'Gómez',     N'martin.gomez@distribuidorasur.example',       1, '2018-07-16', NULL, 3.50),
    (3,  N'Sofía',     N'Díaz',      N'sofia.diaz@distribuidorasur.example',         1, '2019-02-04', NULL, 3.50),
    (4,  N'Diego',     N'Romero',    N'diego.romero@distribuidorasur.example',       1, '2021-05-10', NULL, 3.00),
    (5,  N'Valentina', N'Sosa',      N'valentina.sosa@distribuidorasur.example',     1, '2022-08-01', NULL, 3.00),
    (6,  N'Javier',    N'Torres',    N'javier.torres@distribuidorasur.example',      1, '2023-11-13', NULL, 2.50),
    (7,  N'Camila',    N'Ruiz',      N'camila.ruiz@distribuidorasur.example',        2, '2015-09-01', NULL, 4.00),
    (8,  N'Nicolás',   N'Álvarez',   N'nicolas.alvarez@distribuidorasur.example',    2, '2017-04-03', NULL, 3.50),
    (9,  N'Agustina',  N'Medina',    N'agustina.medina@distribuidorasur.example',    2, '2020-10-05', NULL, 3.00),
    (10, N'Tomás',     N'Herrera',   N'tomas.herrera@distribuidorasur.example',      2, '2023-03-20', NULL, 2.50),
    (11, N'Florencia', N'Benítez',   N'florencia.benitez@distribuidorasur.example',  3, '2017-06-12', NULL, 4.00),
    (12, N'Gustavo',   N'Acosta',    N'gustavo.acosta@distribuidorasur.example',     3, '2018-02-19', '2025-09-30', 3.50),
    (13, N'Paula',     N'Molina',    N'paula.molina@distribuidorasur.example',       3, '2021-08-02', NULL, 3.00),
    (14, N'Federico',  N'Castro',    N'federico.castro@distribuidorasur.example',    3, '2024-01-15', NULL, 2.50),
    (15, N'Carolina',  N'Ortiz',     N'carolina.ortiz@distribuidorasur.example',     4, '2018-10-01', NULL, 4.00),
    (16, N'Matías',    N'Silva',     N'matias.silva@distribuidorasur.example',       4, '2019-07-08', NULL, 3.50),
    (17, N'Julieta',   N'Rojas',     N'julieta.rojas@distribuidorasur.example',      4, '2022-04-18', NULL, 3.00),
    (18, N'Ezequiel',  N'Núñez',     N'ezequiel.nunez@distribuidorasur.example',     4, '2024-06-03', NULL, 2.50),
    (19, N'Romina',    N'Luna',      N'romina.luna@distribuidorasur.example',        5, '2020-02-10', NULL, 4.00),
    (20, N'Pablo',     N'Ibáñez',    N'pablo.ibanez@distribuidorasur.example',       5, '2020-09-14', NULL, 3.50),
    (21, N'Daniela',   N'Vega',      N'daniela.vega@distribuidorasur.example',       5, '2022-11-07', NULL, 3.00),
    (22, N'Leandro',   N'Cabrera',   N'leandro.cabrera@distribuidorasur.example',    5, '2024-03-04', NULL, 2.50),
    (23, N'Micaela',   N'Ríos',      N'micaela.rios@distribuidorasur.example',       6, '2025-05-19', NULL, 3.00),
    (24, N'Hernán',    N'Figueroa',  N'hernan.figueroa@distribuidorasur.example',    6, '2025-05-19', NULL, 3.00);

INSERT INTO dbo.prm_gral (cod_prm, val_prm) VALUES
    ('IVA_GRAL', N'21'),
    ('DIAS_VTO', N'30'),
    ('DIAS_VTO_MIN', N'15'),
    ('DTO_MAX', N'15'),
    ('MON_DEF', N'ARS'),
    ('STK_ALERTA', N'S'),
    ('ULT_CIERRE', N'2026-10-31');
GO

/* --- Clientes -------------------------------------------------------------------- */

DROP TABLE IF EXISTS #prefijos, #nombres, #calles, #ciudades;
CREATE TABLE #prefijos (i INT PRIMARY KEY, prefijo NVARCHAR(30), segmento NVARCHAR(20));
INSERT INTO #prefijos VALUES
    (0, N'Almacén', N'Minorista'), (1, N'Autoservicio', N'Minorista'), (2, N'Kiosco', N'Minorista'),
    (3, N'Despensa', N'Minorista'), (4, N'Minimercado', N'Minorista'), (5, N'Supermercado', N'Minorista'),
    (6, N'Restaurante', N'Gastronomía'), (7, N'Parrilla', N'Gastronomía'), (8, N'Cafetería', N'Gastronomía'),
    (9, N'Rotisería', N'Gastronomía'), (10, N'Pizzería', N'Gastronomía'), (11, N'Hotel', N'Gastronomía'),
    (12, N'Distribuidora', N'Mayorista'), (13, N'Mayorista', N'Mayorista'), (14, N'Comercial', N'Mayorista'),
    (15, N'Colegio', N'Institucional'), (16, N'Clínica', N'Institucional'), (17, N'Club', N'Institucional'),
    (18, N'Residencia', N'Institucional'), (19, N'Hogar de día', N'Institucional');

CREATE TABLE #nombres (i INT PRIMARY KEY, nombre NVARCHAR(40));
INSERT INTO #nombres VALUES
    (0, N'Don Pedro'), (1, N'La Esquina'), (2, N'San Martín'), (3, N'Los Andes'), (4, N'El Progreso'),
    (5, N'Santa Rita'), (6, N'La Estrella'), (7, N'El Sol'), (8, N'Doña Rosa'), (9, N'Las Delicias'),
    (10, N'El Trébol'), (11, N'La Familia'), (12, N'Del Centro'), (13, N'San José'), (14, N'La Unión'),
    (15, N'El Molino'), (16, N'Los Pinos'), (17, N'La Amistad'), (18, N'Belgrano'), (19, N'El Faro'),
    (20, N'La Colonia'), (21, N'Los Álamos'), (22, N'Mi Barrio'), (23, N'El Puente'), (24, N'La Plaza'),
    (25, N'San Cayetano'), (26, N'El Roble'), (27, N'La Perla'), (28, N'Los Hermanos'), (29, N'El Rincón'),
    (30, N'Santa Ana'), (31, N'La Nueva'), (32, N'El Buen Gusto'), (33, N'Las Lomas'), (34, N'El Mirador'),
    (35, N'Don Luis'), (36, N'La Cumbre'), (37, N'Rivadavia'), (38, N'Los Tilos'), (39, N'El Mercadito');

CREATE TABLE #calles (i INT PRIMARY KEY, calle NVARCHAR(40));
INSERT INTO #calles VALUES
    (0, N'Av. San Martín'), (1, N'Belgrano'), (2, N'Mitre'), (3, N'Sarmiento'), (4, N'Av. Colón'),
    (5, N'9 de Julio'), (6, N'Av. Libertador'), (7, N'Urquiza'), (8, N'Moreno'), (9, N'Alem'),
    (10, N'Italia'), (11, N'España');

-- Ciudades por sucursal y su característica telefónica.
CREATE TABLE #ciudades (id_sucursal SMALLINT, i INT, ciudad NVARCHAR(60), provincia NVARCHAR(40), area VARCHAR(4),
    PRIMARY KEY (id_sucursal, i));
INSERT INTO #ciudades VALUES
    (1, 0, N'Ciudad de Buenos Aires', N'CABA', '11'),
    (2, 0, N'San Isidro', N'Buenos Aires', '11'), (2, 1, N'Vicente López', N'Buenos Aires', '11'),
    (2, 2, N'Tigre', N'Buenos Aires', '11'), (2, 3, N'Pilar', N'Buenos Aires', '230'),
    (3, 0, N'Córdoba', N'Córdoba', '351'), (3, 1, N'Villa Carlos Paz', N'Córdoba', '3541'),
    (3, 2, N'Río Cuarto', N'Córdoba', '358'),
    (4, 0, N'Rosario', N'Santa Fe', '341'), (4, 1, N'Funes', N'Santa Fe', '341'),
    (4, 2, N'Villa Gobernador Gálvez', N'Santa Fe', '341'),
    (5, 0, N'Mendoza', N'Mendoza', '261'), (5, 1, N'Godoy Cruz', N'Mendoza', '261'),
    (5, 2, N'Luján de Cuyo', N'Mendoza', '261'),
    (6, 0, N'Neuquén', N'Neuquén', '299'), (6, 1, N'Plottier', N'Neuquén', '299'),
    (6, 2, N'Centenario', N'Neuquén', '299');

-- Vendedores de cada sucursal, numerados para elegir uno al azar.
DROP TABLE IF EXISTS #vend;
SELECT id_vendedor, id_sucursal,
       rn = ROW_NUMBER() OVER (PARTITION BY id_sucursal ORDER BY id_vendedor) - 1,
       cantidad = COUNT(*) OVER (PARTITION BY id_sucursal)
INTO #vend
FROM dbo.vendedores;

-- Cliente: sucursal (por peso), segmento (por el prefijo) y vendedor de esa sucursal.
DROP TABLE IF EXISTS #cli;
SELECT n.n AS id_cliente,
       suc.id_sucursal,
       pre.i AS i_prefijo,
       r_nom.r AS r_nombre, r_iva.r AS r_iva, r_vend.r AS r_vend, r_ciu.r AS r_ciudad,
       r_cuit.r AS r_cuit, r_mail.r AS r_mail, r_tel.r AS r_tel, r_dir.r AS r_dir, r_lim.r AS r_lim
INTO #cli
FROM #n AS n
CROSS APPLY dbo.aux_azar('cli_suc', n.n) AS r_suc
CROSS APPLY dbo.aux_azar('cli_seg', n.n) AS r_seg
CROSS APPLY dbo.aux_azar('cli_pre', n.n) AS r_pre
CROSS APPLY dbo.aux_azar('cli_nom', n.n) AS r_nom
CROSS APPLY dbo.aux_azar('cli_iva', n.n) AS r_iva
CROSS APPLY dbo.aux_azar('cli_vend', n.n) AS r_vend
CROSS APPLY dbo.aux_azar('cli_ciu', n.n) AS r_ciu
CROSS APPLY dbo.aux_azar('cli_cuit', n.n) AS r_cuit
CROSS APPLY dbo.aux_azar('cli_mail', n.n) AS r_mail
CROSS APPLY dbo.aux_azar('cli_tel', n.n) AS r_tel
CROSS APPLY dbo.aux_azar('cli_dir', n.n) AS r_dir
CROSS APPLY dbo.aux_azar('cli_lim', n.n) AS r_lim
CROSS APPLY (SELECT id_sucursal = CAST(CASE
        WHEN r_suc.r < 0.30 THEN 1 WHEN r_suc.r < 0.48 THEN 2 WHEN r_suc.r < 0.65 THEN 3
        WHEN r_suc.r < 0.80 THEN 4 WHEN r_suc.r < 0.94 THEN 5 ELSE 6 END AS SMALLINT)) AS suc
CROSS APPLY (SELECT i = CASE
        WHEN r_seg.r < 0.50 THEN FLOOR(r_pre.r * 6)          -- Minorista
        WHEN r_seg.r < 0.75 THEN 6 + FLOOR(r_pre.r * 6)      -- Gastronomía
        WHEN r_seg.r < 0.87 THEN 12 + FLOOR(r_pre.r * 3)     -- Mayorista
        ELSE 15 + FLOOR(r_pre.r * 5) END) AS pre              -- Institucional
WHERE n.n <= 800;

INSERT INTO dbo.clientes
    (id_cliente, razon_social, cuit, email, telefono, direccion, ciudad, provincia, segmento,
     cond_iva, id_vendedor, fecha_alta, limite_credito, activo)
SELECT c.id_cliente,
       CONCAT(p.prefijo, N' ', nom.nombre,
              CASE WHEN p.segmento IN (N'Mayorista', N'Institucional') OR p.i = 11 THEN
                       CASE WHEN c.r_iva < 0.5 THEN N' S.A.' ELSE N' S.R.L.' END
                   WHEN p.segmento = N'Gastronomía' AND c.r_iva < 0.4 THEN N' S.R.L.'
                   ELSE N'' END),
       CONCAT(CASE WHEN iva.cond_iva IN ('RI', 'EX') THEN '30' WHEN c.r_cuit < 0.5 THEN '20' ELSE '27' END,
              '-', RIGHT(CONCAT('0000000', CAST(10000000 + FLOOR(c.r_cuit * 89999999) AS BIGINT)), 8),
              '-', CAST(FLOOR(c.r_mail * 10) AS INT)),
       CASE WHEN c.r_mail < 0.10 THEN NULL
            ELSE CONCAT(CASE WHEN c.r_mail < 0.55 THEN 'compras' ELSE 'administracion' END,
                        '.', c.id_cliente, '@clientes-distribuidorasur.example') END,
       CASE WHEN c.r_tel < 0.05 THEN NULL
            ELSE CONCAT('+54 ', ciu.area, ' ', CAST(4000 + FLOOR(c.r_tel * 5999) AS INT), '-',
                        RIGHT(CONCAT('000', CAST(FLOOR(c.r_dir * 9999) AS INT)), 4)) END,
       CONCAT(ca.calle, N' ', CAST(100 + FLOOR(c.r_dir * 4800) AS INT)),
       ciu.ciudad, ciu.provincia, p.segmento, iva.cond_iva,
       v.id_vendedor,
       '2024-11-01',               -- se ajusta con el primer pedido
       CASE p.segmento
            WHEN N'Minorista' THEN 300000 + ROUND(c.r_lim * 24, 0) * 50000
            WHEN N'Gastronomía' THEN 500000 + ROUND(c.r_lim * 50, 0) * 50000
            WHEN N'Mayorista' THEN 3000000 + ROUND(c.r_lim * 240, 0) * 50000
            ELSE 1000000 + ROUND(c.r_lim * 100, 0) * 50000 END,
       1
FROM #cli AS c
JOIN #prefijos AS p ON p.i = c.i_prefijo
JOIN #nombres AS nom ON nom.i = FLOOR(c.r_nombre * 40)
JOIN #calles AS ca ON ca.i = FLOOR(c.r_dir * 12)
CROSS APPLY (SELECT TOP (1) x.ciudad, x.provincia, x.area
             FROM #ciudades AS x
             WHERE x.id_sucursal = c.id_sucursal
               AND x.i = FLOOR(c.r_ciudad * (SELECT COUNT(*) FROM #ciudades AS y WHERE y.id_sucursal = c.id_sucursal))) AS ciu
CROSS APPLY (SELECT cond_iva = CAST(CASE
        WHEN p.segmento = N'Institucional' AND c.r_iva < 0.5 THEN 'EX'
        WHEN p.segmento <> N'Minorista' THEN 'RI'
        WHEN c.r_iva < 0.60 THEN 'MT'
        WHEN c.r_iva < 0.85 THEN 'RI'
        ELSE 'CF' END AS CHAR(2))) AS iva
JOIN #vend AS v ON v.id_sucursal = c.id_sucursal AND v.rn = FLOOR(c.r_vend * v.cantidad);

-- Razones sociales repetidas: se distinguen por la ciudad y, si todavía chocan, por un número.
WITH repetidas AS (
    SELECT razon_social, ciudad, rn = ROW_NUMBER() OVER (PARTITION BY razon_social ORDER BY id_cliente)
    FROM dbo.clientes
)
UPDATE repetidas SET razon_social = CONCAT(razon_social, N' - ', ciudad) WHERE rn > 1;
WITH repetidas AS (
    SELECT razon_social, rn = ROW_NUMBER() OVER (PARTITION BY razon_social ORDER BY id_cliente)
    FROM dbo.clientes
)
UPDATE repetidas SET razon_social = CONCAT(razon_social, N' (', rn, N')') WHERE rn > 1;

-- Gustavo Acosta (12) dejó la empresa el 30/09/2025: su cartera pasó a Paula Molina (13).
DROP TABLE IF EXISTS #cartera_original;
SELECT id_cliente, id_vendedor INTO #cartera_original FROM dbo.clientes;
UPDATE dbo.clientes SET id_vendedor = 13 WHERE id_vendedor = 12;
GO

/* --- Categorías y productos -------------------------------------------------------- */

INSERT INTO dbo.categorias (id_categoria, nombre, id_categoria_padre) VALUES
    (1, N'Almacén', NULL), (2, N'Bebidas', NULL), (3, N'Limpieza', NULL),
    (4, N'Perfumería', NULL), (5, N'Congelados', NULL),
    (11, N'Fideos y arroz', 1), (12, N'Aceites y aderezos', 1), (13, N'Conservas', 1),
    (14, N'Galletitas y snacks', 1), (15, N'Infusiones', 1),
    (21, N'Gaseosas', 2), (22, N'Aguas', 2), (23, N'Jugos', 2), (24, N'Cervezas', 2), (25, N'Vinos', 2),
    (31, N'Detergentes y lavandinas', 3), (32, N'Papelería', 3),
    (41, N'Cuidado personal', 4), (42, N'Pañales', 4),
    (51, N'Hamburguesas y rebozados', 5), (52, N'Helados y postres', 5);

DROP TABLE IF EXISTS #plantillas, #marcas;
CREATE TABLE #plantillas (id_categoria SMALLINT, item NVARCHAR(60), presentacion NVARCHAR(20),
    unidad VARCHAR(5), precio_base DECIMAL(12, 2));
INSERT INTO #plantillas VALUES
    (11, N'Fideos spaghetti', N'500 g', 'UN', 1450), (11, N'Fideos tirabuzón', N'500 g', 'UN', 1450),
    (11, N'Arroz largo fino', N'1 kg', 'UN', 2100), (11, N'Arroz parboil', N'1 kg', 'UN', 2400),
    (11, N'Fideos al huevo', N'500 g', 'UN', 1900), (11, N'Polenta', N'500 g', 'UN', 1300),
    (12, N'Aceite de girasol', N'1,5 L', 'UN', 4200), (12, N'Aceite de oliva', N'500 ml', 'UN', 8900),
    (12, N'Mayonesa', N'475 g', 'UN', 2600), (12, N'Vinagre de alcohol', N'1 L', 'UN', 1100),
    (12, N'Kétchup', N'250 g', 'UN', 1700),
    (13, N'Tomate triturado', N'520 g', 'UN', 1350), (13, N'Arvejas', N'300 g', 'UN', 990),
    (13, N'Atún al natural', N'170 g', 'UN', 3200), (13, N'Durazno en almíbar', N'820 g', 'UN', 3900),
    (13, N'Choclo amarillo', N'300 g', 'UN', 1150),
    (14, N'Galletitas de agua', N'300 g', 'UN', 1600), (14, N'Galletitas dulces', N'400 g', 'UN', 2300),
    (14, N'Papas fritas', N'150 g', 'UN', 2800), (14, N'Maní salado', N'200 g', 'UN', 1900),
    (14, N'Alfajor triple', N'80 g', 'UN', 1100),
    (15, N'Yerba mate', N'1 kg', 'UN', 5200), (15, N'Café molido', N'500 g', 'UN', 7800),
    (15, N'Té en saquitos', N'x50', 'CJ', 1500), (15, N'Mate cocido', N'x50', 'CJ', 1700),
    (21, N'Gaseosa cola', N'2,25 L', 'UN', 2900), (21, N'Gaseosa lima limón', N'2,25 L', 'UN', 2700),
    (21, N'Gaseosa naranja', N'2,25 L', 'UN', 2700), (21, N'Agua tónica', N'1,5 L', 'UN', 2400),
    (22, N'Agua mineral sin gas', N'2 L', 'UN', 1300), (22, N'Agua con gas', N'2 L', 'UN', 1350),
    (22, N'Agua saborizada', N'1,5 L', 'UN', 1800), (22, N'Bidón de agua', N'6 L', 'UN', 2900),
    (23, N'Jugo de naranja', N'1 L', 'UN', 2200), (23, N'Jugo en polvo', N'18 g', 'UN', 450),
    (23, N'Jugo multifruta', N'1 L', 'UN', 2300),
    (24, N'Cerveza rubia', N'1 L', 'UN', 2600), (24, N'Cerveza negra', N'1 L', 'UN', 2900),
    (24, N'Cerveza rubia en lata', N'473 ml', 'UN', 1700), (24, N'Cerveza roja', N'1 L', 'UN', 2900),
    (25, N'Vino tinto Malbec', N'750 ml', 'UN', 5800), (25, N'Vino blanco Torrontés', N'750 ml', 'UN', 5200),
    (25, N'Vino tinto de mesa', N'1 L', 'UN', 2900), (25, N'Espumante extra brut', N'750 ml', 'UN', 8900),
    (31, N'Detergente', N'750 ml', 'UN', 2100), (31, N'Lavandina', N'2 L', 'UN', 1600),
    (31, N'Jabón en polvo', N'800 g', 'UN', 4300), (31, N'Suavizante', N'900 ml', 'UN', 2700),
    (31, N'Limpiador de pisos', N'900 ml', 'UN', 1900),
    (32, N'Papel higiénico', N'x4', 'PAQ', 3100), (32, N'Rollo de cocina', N'x3', 'PAQ', 2600),
    (32, N'Servilletas', N'x100', 'PAQ', 1200),
    (41, N'Shampoo', N'400 ml', 'UN', 4200), (41, N'Jabón de tocador', N'x3', 'PAQ', 2400),
    (41, N'Desodorante', N'150 ml', 'UN', 3300), (41, N'Crema dental', N'90 g', 'UN', 1900),
    (42, N'Pañales talle M', N'x30', 'PAQ', 14500), (42, N'Pañales talle G', N'x30', 'PAQ', 15500),
    (42, N'Toallitas húmedas', N'x50', 'PAQ', 2600),
    (51, N'Hamburguesas de carne', N'x4', 'CJ', 4600), (51, N'Medallones de pollo', N'x4', 'CJ', 3900),
    (51, N'Bastones de muzzarella', N'x12', 'CJ', 5200),
    (52, N'Helado', N'1 kg', 'UN', 7800), (52, N'Palitos helados', N'x6', 'CJ', 4200);

-- Marcas (ficticias) por rubro.
CREATE TABLE #marcas (id_padre SMALLINT, marca NVARCHAR(40), premium DECIMAL(4, 2));
INSERT INTO #marcas VALUES
    (1, N'Campo Dorado', 1.10), (1, N'Don Aurelio', 1.00), (1, N'Pampa Verde', 0.90), (1, N'Sierra Azul', 1.20),
    (2, N'Río Claro', 1.00), (2, N'Cumbre Fresca', 1.15), (2, N'Brisa Sur', 0.88), (2, N'Viñas del Sur', 1.25),
    (3, N'Limpix', 1.00), (3, N'Nubecita', 0.90), (3, N'Brillo Total', 1.15),
    (4, N'Aurora Andina', 1.10), (4, N'Nubecita', 0.92), (4, N'Suave Sur', 1.00),
    (5, N'Frío Sur', 1.00), (5, N'Campo Dorado', 1.10), (5, N'Glaciar', 1.20);

-- Combinaciones artículo × marca de cada categoría; se toman hasta 19 por categoría.
DROP TABLE IF EXISTS #combos;
SELECT pl.id_categoria, pl.item, pl.presentacion, pl.unidad, pl.precio_base, m.marca, m.premium,
       rn = ROW_NUMBER() OVER (PARTITION BY pl.id_categoria
                               ORDER BY HASHBYTES('MD5', CONCAT(pl.item, '|', m.marca)))
INTO #combos
FROM #plantillas AS pl
JOIN dbo.categorias AS c ON c.id_categoria = pl.id_categoria
JOIN #marcas AS m ON m.id_padre = c.id_categoria_padre
   -- Los vinos son de Viñas del Sur y el resto de las bebidas, de las otras marcas.
   AND NOT (pl.id_categoria = 25 AND m.marca <> N'Viñas del Sur')
   AND NOT (pl.id_categoria <> 25 AND m.marca = N'Viñas del Sur');

INSERT INTO dbo.productos
    (id_producto, sku, nombre, id_categoria, marca, unidad_medida, precio_lista, costo_ult, activo, fecha_alta)
SELECT id_producto = ROW_NUMBER() OVER (ORDER BY c.id_categoria, c.rn),
       CONCAT('SKU-', c.id_categoria, '-', RIGHT(CONCAT('00', c.rn), 3)),
       CONCAT(c.item, N' ', c.marca, N' ', c.presentacion),
       c.id_categoria, c.marca, c.unidad,
       precio = ROUND(c.precio_base * c.premium * (0.90 + 0.25 * r1.r), -1),
       0, 1,
       DATEADD(DAY, FLOOR(r2.r * 3500), '2015-01-05')
FROM #combos AS c
CROSS APPLY dbo.aux_azar('prd_pre', c.id_categoria * 100 + c.rn) AS r1
CROSS APPLY dbo.aux_azar('prd_alt', c.id_categoria * 100 + c.rn) AS r2
WHERE c.rn <= 19;

UPDATE p
SET costo_ult = ROUND(p.precio_lista * (0.62 + 0.16 * r.r), 2),
    -- Algunos productos se dieron de baja a fines de 2025.
    activo = CASE WHEN r2.r < 0.05 THEN 0 ELSE 1 END
FROM dbo.productos AS p
CROSS APPLY dbo.aux_azar('prd_cos', p.id_producto) AS r
CROSS APPLY dbo.aux_azar('prd_act', p.id_producto) AS r2;
GO

/* --- Pedidos --------------------------------------------------------------------- */

-- Días del período con su cantidad de pedidos (estacionalidad, día de semana y crecimiento).
DROP TABLE IF EXISTS #dias;
SELECT d.dia,
       cantidad = CAST(FLOOR(
           11.0
           * CASE MONTH(d.dia) WHEN 1 THEN 0.75 WHEN 2 THEN 0.80 WHEN 3 THEN 1.00 WHEN 4 THEN 0.95
                 WHEN 5 THEN 1.00 WHEN 6 THEN 0.95 WHEN 7 THEN 1.05 WHEN 8 THEN 1.00 WHEN 9 THEN 1.00
                 WHEN 10 THEN 1.05 WHEN 11 THEN 1.10 ELSE 1.45 END
           * CASE DATEDIFF(DAY, '19000101', d.dia) % 7 WHEN 5 THEN 0.50 WHEN 6 THEN 0.15 ELSE 1.00 END
           * (1.0 + 0.15 * DATEDIFF(DAY, '2024-11-01', d.dia) / 753.0)
           + r.r) AS INT)
INTO #dias
FROM #n AS n
CROSS APPLY (SELECT dia = CAST(DATEADD(DAY, n.n - 1, '2024-11-01') AS DATE)) AS d
CROSS APPLY dbo.aux_azar('dia', n.n) AS r
WHERE n.n <= 753;

-- Clientes por "popularidad" (unos pocos compran mucho). Antes de abrir Neuquén
-- (02/06/2025) no hay clientes de esa sucursal.
DROP TABLE IF EXISTS #pop_cli;
SELECT c.id_cliente, v.id_sucursal,
       rn_todos = ROW_NUMBER() OVER (ORDER BY HASHBYTES('MD5', CONCAT('pop', c.id_cliente))) - 1,
       rn_sin_nqn = CASE WHEN v.id_sucursal <> 6
           THEN ROW_NUMBER() OVER (PARTITION BY CASE WHEN v.id_sucursal <> 6 THEN 0 ELSE 1 END
                                   ORDER BY HASHBYTES('MD5', CONCAT('pop', c.id_cliente))) - 1 END
INTO #pop_cli
FROM dbo.clientes AS c
JOIN dbo.vendedores AS v ON v.id_vendedor = c.id_vendedor;

DECLARE @total_cli INT = (SELECT COUNT(*) FROM #pop_cli);
DECLARE @cli_sin_nqn INT = (SELECT COUNT(*) FROM #pop_cli WHERE id_sucursal <> 6);

DROP TABLE IF EXISTS #ped;
SELECT id_pedido = ROW_NUMBER() OVER (ORDER BY d.dia, n.n),
       d.dia, n.n AS orden_del_dia
INTO #ped
FROM #dias AS d
JOIN #n AS n ON n.n <= d.cantidad;

DROP TABLE IF EXISTS #ped2;
SELECT p.id_pedido, p.dia,
       fecha_pedido = CASE WHEN canal.canal = 'W'
           THEN DATEADD(MINUTE, FLOOR(r_hora.r * 1440), CAST(p.dia AS DATETIME2(0)))
           ELSE DATEADD(MINUTE, 480 + FLOOR(r_hora.r * 600), CAST(p.dia AS DATETIME2(0))) END,
       pc.id_cliente, canal.canal, r_est.r AS r_est, r_ent.r AS r_ent, r_obs.r AS r_obs
INTO #ped2
FROM #ped AS p
CROSS APPLY dbo.aux_azar('ped_cli', p.id_pedido) AS r_cli
CROSS APPLY dbo.aux_azar('ped_can', p.id_pedido) AS r_can
CROSS APPLY dbo.aux_azar('ped_hora', p.id_pedido) AS r_hora
CROSS APPLY dbo.aux_azar('ped_est', p.id_pedido) AS r_est
CROSS APPLY dbo.aux_azar('ped_ent', p.id_pedido) AS r_ent
CROSS APPLY dbo.aux_azar('ped_obs', p.id_pedido) AS r_obs
CROSS APPLY (SELECT t = DATEDIFF(DAY, '2024-11-01', p.dia) / 753.0) AS tiempo
JOIN #pop_cli AS pc
  ON (p.dia < '2025-06-02' AND pc.rn_sin_nqn = FLOOR(@cli_sin_nqn * POWER(r_cli.r, 1.6)))
  OR (p.dia >= '2025-06-02' AND pc.rn_todos = FLOOR(@total_cli * POWER(r_cli.r, 1.6)))
-- La venta web crece con el tiempo; los domingos solo se vende por la web.
CROSS APPLY (SELECT canal = CAST(CASE
        WHEN DATEDIFF(DAY, '19000101', p.dia) % 7 = 6 THEN 'W'
        WHEN r_can.r < 0.08 + 0.14 * tiempo.t THEN 'W'
        WHEN r_can.r < 0.23 + 0.14 * tiempo.t THEN 'M'
        WHEN r_can.r < 0.48 + 0.14 * tiempo.t THEN 'T'
        ELSE 'V' END AS CHAR(1))) AS canal;

INSERT INTO dbo.pedidos
    (id_pedido, nro_pedido, id_cliente, id_vendedor, id_sucursal, fecha_pedido, fec_ent_est, cod_est, canal, obs)
SELECT p.id_pedido,
       CONCAT('P-', RIGHT(CONCAT('0000000', p.id_pedido), 7)),
       p.id_cliente,
       vend.id_vendedor,
       v.id_sucursal,
       p.fecha_pedido,
       DATEADD(DAY, 2 + FLOOR(p.r_ent * 4) + CASE WHEN c.segmento = N'Mayorista' THEN 1 ELSE 0 END, p.dia),
       est.cod_est,
       p.canal,
       CASE WHEN p.r_obs < 0.03 THEN N'Entregar por la mañana'
            WHEN p.r_obs < 0.06 THEN N'Llamar antes de entregar'
            WHEN p.r_obs < 0.08 THEN N'Retira el cliente por sucursal'
            WHEN p.r_obs < 0.10 THEN N'Urgente: sin stock en el local'
            WHEN p.r_obs < 0.11 THEN N'Facturar a nombre de la casa central'
            ELSE NULL END
FROM #ped2 AS p
JOIN dbo.clientes AS c ON c.id_cliente = p.id_cliente
JOIN #cartera_original AS orig ON orig.id_cliente = p.id_cliente
CROSS APPLY (SELECT id_vendedor = CASE
        WHEN p.canal = 'W' THEN NULL
        WHEN orig.id_vendedor = 12 AND p.dia <= '2025-09-30' THEN 12
        ELSE c.id_vendedor END) AS vend
JOIN dbo.vendedores AS v ON v.id_vendedor = c.id_vendedor
CROSS APPLY (SELECT edad = DATEDIFF(DAY, p.dia, '2026-11-23')) AS e
-- Estados: los viejos están entregados o anulados; los recientes, en curso.
-- Camila Ruiz (7) tiene muchas más anulaciones que el resto.
CROSS APPLY (SELECT cod_est = CAST(CASE
        WHEN e.edad > 10 THEN CASE WHEN p.r_est < CASE WHEN vend.id_vendedor = 7 THEN 0.18 ELSE 0.05 END
                                   THEN 9 ELSE 4 END
        WHEN e.edad > 3 THEN CASE WHEN p.r_est < 0.05 THEN 9 WHEN p.r_est < 0.65 THEN 4 ELSE 3 END
        ELSE CASE WHEN p.r_est < 0.03 THEN 9 WHEN p.r_est < 0.35 THEN 1
                  WHEN p.r_est < 0.65 THEN 2 ELSE 3 END END AS TINYINT)) AS est;
GO

/* --- Detalle de pedidos ------------------------------------------------------------ */

-- Productos por "popularidad"; desde 2026 solo se venden los activos.
DROP TABLE IF EXISTS #pop_prod;
SELECT id_producto, activo,
       rn_todos = ROW_NUMBER() OVER (ORDER BY HASHBYTES('MD5', CONCAT('popp', id_producto))) - 1,
       rn_activos = CASE WHEN activo = 1 THEN ROW_NUMBER() OVER (PARTITION BY activo
                         ORDER BY HASHBYTES('MD5', CONCAT('popp', id_producto))) - 1 END
INTO #pop_prod
FROM dbo.productos;

-- Números de línea posibles: una tabla chica evita que el motor convierta a SMALLINT
-- valores de la tabla de números antes de filtrarlos.
DROP TABLE IF EXISTS #lineas;
SELECT n = CAST(n AS SMALLINT) INTO #lineas FROM #n WHERE n <= 9;

DECLARE @total_prod INT = (SELECT COUNT(*) FROM #pop_prod);
DECLARE @prod_activos INT = (SELECT COUNT(*) FROM #pop_prod WHERE activo = 1);

INSERT INTO dbo.pedidos_detalle (id_pedido, nro_linea, id_producto, cant, precio_unit, dto_pct)
SELECT p.id_pedido, l.n, pp.id_producto,
       cant = CASE c.segmento
           WHEN N'Minorista' THEN 1 + FLOOR(r_cant.r * 12)
           WHEN N'Gastronomía' THEN 2 + FLOOR(r_cant.r * 23)
           WHEN N'Mayorista' THEN 6 * (2 + FLOOR(r_cant.r * 19))
           ELSE 6 + FLOOR(r_cant.r * 55) END,
       -- Precios de cada momento: la lista actual descontada por la inflación (≈ 41 % anual).
       precio_unit = ROUND(pr.precio_lista * POWER(0.5, DATEDIFF(DAY, p.fecha_pedido, '2026-11-23') / 730.0)
                           * (0.97 + 0.06 * r_pre.r), 2),
       dto_pct = CASE
           WHEN c.segmento = N'Mayorista' THEN CASE WHEN r_dto.r < 0.01 THEN 15 ELSE 5 + FLOOR(r_dto.r * 6) END
           WHEN c.segmento = N'Gastronomía' THEN CASE WHEN r_dto.r < 0.5 THEN 0 ELSE 2 + FLOOR(r_dto.r * 4) END
           WHEN c.segmento = N'Institucional' THEN CASE WHEN r_dto.r < 0.3 THEN 3 ELSE 0 END
           ELSE 0 END
FROM dbo.pedidos AS p
JOIN dbo.clientes AS c ON c.id_cliente = p.id_cliente
CROSS APPLY dbo.aux_azar('det_lin', p.id_pedido) AS r_lin
CROSS APPLY dbo.aux_azar('det_base', p.id_pedido) AS r_base
JOIN #lineas AS l ON l.n <= 1 + FLOOR(POWER(r_lin.r, 1.3) * 6) + CASE WHEN c.segmento = N'Mayorista' THEN 2 ELSE 0 END
CROSS APPLY (SELECT solo_activos = CASE WHEN p.fecha_pedido >= '2026-01-01' THEN 1 ELSE 0 END) AS a
CROSS APPLY (SELECT idx = (CAST(FLOOR(CASE WHEN a.solo_activos = 1 THEN @prod_activos ELSE @total_prod END
                                      * POWER(r_base.r, 1.4)) AS INT) + (l.n - 1) * 37)
                          % CASE WHEN a.solo_activos = 1 THEN @prod_activos ELSE @total_prod END) AS i
JOIN #pop_prod AS pp
  ON (a.solo_activos = 1 AND pp.rn_activos = i.idx) OR (a.solo_activos = 0 AND pp.rn_todos = i.idx)
JOIN dbo.productos AS pr ON pr.id_producto = pp.id_producto
CROSS APPLY dbo.aux_azar('det_cant', p.id_pedido * 10 + l.n) AS r_cant
CROSS APPLY dbo.aux_azar('det_pre', p.id_pedido * 10 + l.n) AS r_pre
CROSS APPLY dbo.aux_azar('det_dto', p.id_pedido) AS r_dto;
GO

/* --- Clientes: alta y actividad según sus pedidos ------------------------------- */

UPDATE c
SET fecha_alta = CASE
        WHEN x.primer_pedido IS NULL THEN DATEADD(DAY, FLOOR(r.r * 700), '2024-11-01')
        ELSE DATEADD(DAY, -FLOOR(r.r * 1500), x.primer_pedido) END,
    -- Sin pedidos en los últimos 9 meses, la mayoría quedó inactiva.
    activo = CASE WHEN x.ultimo_pedido IS NULL OR x.ultimo_pedido < '2026-02-23'
                  THEN CASE WHEN r2.r < 0.6 THEN 0 ELSE 1 END ELSE 1 END
FROM dbo.clientes AS c
OUTER APPLY (SELECT primer_pedido = CAST(MIN(p.fecha_pedido) AS DATE),
                    ultimo_pedido = CAST(MAX(p.fecha_pedido) AS DATE)
             FROM dbo.pedidos AS p WHERE p.id_cliente = c.id_cliente) AS x
CROSS APPLY dbo.aux_azar('cli_alta', c.id_cliente) AS r
CROSS APPLY dbo.aux_azar('cli_activo', c.id_cliente) AS r2;
GO

/* --- Facturas -------------------------------------------------------------------- */

INSERT INTO dbo.tbl_fac_cab
    (nro_fac, tip_cbte, pto_vta, id_ped, cod_cli, fec_emi, fec_vto, imp_net, imp_iva, imp_tot, cod_est_fac)
SELECT nro_fac = 400000 + ROW_NUMBER() OVER (ORDER BY p.fecha_pedido, p.id_pedido),
       CASE WHEN c.cond_iva = 'RI' THEN 'A' ELSE 'B' END,
       p.id_sucursal, p.id_pedido, p.id_cliente,
       emi.fec_emi,
       DATEADD(DAY, CASE WHEN c.segmento = N'Minorista' THEN 15 ELSE 30 END, emi.fec_emi),
       t.neto, ROUND(t.neto * 0.21, 2), t.neto + ROUND(t.neto * 0.21, 2),
       CASE WHEN r_anu.r < 0.008 THEN 8 ELSE 1 END
FROM dbo.pedidos AS p
JOIN dbo.clientes AS c ON c.id_cliente = p.id_cliente
CROSS APPLY dbo.aux_azar('fac_emi', p.id_pedido) AS r_emi
CROSS APPLY dbo.aux_azar('fac_anu', p.id_pedido) AS r_anu
CROSS APPLY (SELECT fec_emi = CASE
        WHEN CAST(p.fecha_pedido AS DATE) >= '2026-11-23' OR r_emi.r < 0.6 THEN CAST(p.fecha_pedido AS DATE)
        ELSE DATEADD(DAY, 1, CAST(p.fecha_pedido AS DATE)) END) AS emi
CROSS APPLY (SELECT neto = ROUND(SUM(d.cant * d.precio_unit * (1 - d.dto_pct / 100)), 2)
             FROM dbo.pedidos_detalle AS d WHERE d.id_pedido = p.id_pedido) AS t
WHERE p.cod_est IN (3, 4);
GO

/* --- Cobranzas --------------------------------------------------------------------- */

-- Un grupo chico de clientes paga tarde o no paga (morosos).
DROP TABLE IF EXISTS #pagos;
SELECT f.nro_fac, f.fec_emi, f.fec_vto, f.imp_tot, c.segmento,
       escenario = CASE
           WHEN m.r < 0.04 THEN CASE WHEN r_esc.r < 0.45 THEN 'ninguno' WHEN r_esc.r < 0.65 THEN 'parcial' ELSE 'total' END
           WHEN f.fec_vto < '2026-11-23' THEN CASE WHEN r_esc.r < 0.004 THEN 'ninguno' WHEN r_esc.r < 0.010 THEN 'parcial' ELSE 'total' END
           ELSE CASE WHEN r_esc.r < 0.45 THEN 'total' WHEN r_esc.r < 0.55 THEN 'parcial' ELSE 'ninguno' END END,
       r_dia.r AS r_dia, r_par.r AS r_par, r_div.r AS r_div, r_med.r AS r_med
INTO #pagos
FROM dbo.tbl_fac_cab AS f
JOIN dbo.clientes AS c ON c.id_cliente = f.cod_cli
CROSS APPLY dbo.aux_azar('moroso', f.cod_cli) AS m
CROSS APPLY dbo.aux_azar('cob_esc', f.nro_fac) AS r_esc
CROSS APPLY dbo.aux_azar('cob_dia', f.nro_fac) AS r_dia
CROSS APPLY dbo.aux_azar('cob_par', f.nro_fac) AS r_par
CROSS APPLY dbo.aux_azar('cob_div', f.nro_fac) AS r_div
CROSS APPLY dbo.aux_azar('cob_med', f.nro_fac) AS r_med
WHERE f.cod_est_fac <> 8;

DROP TABLE IF EXISTS #cuotas;
SELECT p.nro_fac, p.segmento, p.r_med,
       cuota = q.n,
       fec_cob = CASE WHEN f1.fecha > '2026-11-23' THEN CAST('2026-11-23' AS DATE) ELSE f1.fecha END,
       imp_cob = CASE
           WHEN p.escenario = 'parcial' THEN ROUND(p.imp_tot * (0.3 + 0.4 * p.r_par), 2)
           WHEN q.cuotas = 1 THEN p.imp_tot
           WHEN q.n = 1 THEN ROUND(p.imp_tot / 2, 2)
           ELSE p.imp_tot - ROUND(p.imp_tot / 2, 2) END
INTO #cuotas
FROM #pagos AS p
CROSS APPLY (SELECT cuotas = CASE WHEN p.escenario = 'total' AND p.r_div < 0.15 THEN 2 ELSE 1 END) AS k
CROSS APPLY (SELECT n.n, k.cuotas FROM #n AS n WHERE n.n <= k.cuotas) AS q
CROSS APPLY (SELECT fecha = DATEADD(DAY,
        FLOOR(p.r_dia * (DATEDIFF(DAY, p.fec_emi, p.fec_vto) + 15)) + (q.n - 1) * (7 + FLOOR(p.r_par * 14)),
        p.fec_emi)) AS f1
WHERE p.escenario <> 'ninguno';

INSERT INTO dbo.tbl_cob (id_cob, nro_fac, fec_cob, imp_cob, med_pag, nro_ref)
SELECT id_cob = ROW_NUMBER() OVER (ORDER BY q.fec_cob, q.nro_fac, q.cuota),
       q.nro_fac, q.fec_cob, q.imp_cob, med.med_pag,
       CASE med.med_pag
           WHEN 2 THEN CONCAT('TRF-', CAST(100000000 + FLOOR(q.r_med * 899999999) AS BIGINT))
           WHEN 3 THEN CONCAT('CHQ-', CAST(10000000 + FLOOR(q.r_med * 89999999) AS BIGINT))
           WHEN 4 THEN CONCAT('POS-', CAST(100000 + FLOOR(q.r_med * 899999) AS BIGINT))
           ELSE NULL END
FROM #cuotas AS q
CROSS APPLY (SELECT med_pag = CAST(CASE
        WHEN q.segmento = N'Institucional' THEN CASE WHEN q.r_med < 0.85 THEN 2 ELSE 3 END
        WHEN q.segmento = N'Mayorista' THEN CASE WHEN q.r_med < 0.45 THEN 3 ELSE 2 END
        WHEN q.segmento = N'Gastronomía' THEN CASE WHEN q.r_med < 0.30 THEN 1 WHEN q.r_med < 0.75 THEN 2 ELSE 4 END
        ELSE CASE WHEN q.r_med < 0.45 THEN 1 WHEN q.r_med < 0.65 THEN 2 ELSE 4 END END AS TINYINT)) AS med;

-- Estado de cada factura según lo cobrado.
UPDATE f
SET cod_est_fac = CASE WHEN x.cobrado >= f.imp_tot THEN 3 WHEN x.cobrado > 0 THEN 2 ELSE 1 END
FROM dbo.tbl_fac_cab AS f
OUTER APPLY (SELECT cobrado = ISNULL(SUM(c.imp_cob), 0) FROM dbo.tbl_cob AS c WHERE c.nro_fac = f.nro_fac) AS x
WHERE f.cod_est_fac <> 8;
GO

/* --- Devoluciones ------------------------------------------------------------------ */

INSERT INTO dbo.devoluciones (id_devolucion, nro_fac, id_producto, cantidad, motivo_cod, fecha, importe)
SELECT id_devolucion = ROW_NUMBER() OVER (ORDER BY fecha.fecha, f.nro_fac, d.nro_linea),
       f.nro_fac, d.id_producto, q.cantidad,
       CASE WHEN r_mot.r < 0.30 THEN 'RT' WHEN r_mot.r < 0.50 THEN 'VC' WHEN r_mot.r < 0.80 THEN 'ER' ELSE 'NC' END,
       fecha.fecha,
       ROUND(q.cantidad * d.precio_unit * (1 - d.dto_pct / 100), 2)
FROM dbo.tbl_fac_cab AS f
JOIN dbo.pedidos_detalle AS d ON d.id_pedido = f.id_ped
CROSS APPLY dbo.aux_azar('dev_sel', f.nro_fac * 10 + d.nro_linea) AS r_sel
CROSS APPLY dbo.aux_azar('dev_mot', f.nro_fac * 10 + d.nro_linea) AS r_mot
CROSS APPLY dbo.aux_azar('dev_can', f.nro_fac * 10 + d.nro_linea) AS r_can
CROSS APPLY (SELECT cantidad = CEILING(d.cant * (0.1 + 0.4 * r_can.r))) AS q
CROSS APPLY (SELECT fecha = CASE WHEN DATEADD(DAY, 1 + FLOOR(r_can.r * 20), f.fec_emi) > '2026-11-23'
                                 THEN CAST('2026-11-23' AS DATE)
                                 ELSE DATEADD(DAY, 1 + FLOOR(r_can.r * 20), f.fec_emi) END) AS fecha
WHERE f.cod_est_fac <> 8 AND r_sel.r < 0.016;
GO

/* --- Stock ------------------------------------------------------------------------ */

DROP TABLE IF EXISTS #mov;
CREATE TABLE #mov (cod_prod INT, cod_suc SMALLINT, fec_mov DATETIME2(0), tip_mov CHAR(2),
    cant DECIMAL(10, 2), ref_doc VARCHAR(20), usr_alt VARCHAR(20));

-- Productos × sucursales que maneja cada sucursal (Neuquén desde su apertura).
DROP TABLE IF EXISTS #pares;
SELECT p.id_producto AS cod_prod, s.id_sucursal AS cod_suc, p.activo,
       inicio = CASE WHEN s.fecha_apertura > '2024-11-01' THEN s.fecha_apertura ELSE CAST('2024-11-01' AS DATE) END
INTO #pares
FROM dbo.productos AS p CROSS JOIN dbo.sucursales AS s;

-- Inventario inicial.
INSERT INTO #mov
SELECT pa.cod_prod, pa.cod_suc, DATEADD(HOUR, 7, CAST(pa.inicio AS DATETIME2(0))), 'AJ',
       20 + FLOOR(r.r * 80), 'INV-INICIAL', 'DEPOSITO1'
FROM #pares AS pa
CROSS APPLY dbo.aux_azar('stk_ini', pa.cod_prod * 10 + pa.cod_suc) AS r;

-- Ventas facturadas (salen del stock de la sucursal que factura).
INSERT INTO #mov
SELECT d.id_producto, f.pto_vta, DATEADD(MINUTE, 600, CAST(f.fec_emi AS DATETIME2(0))), 'VT',
       -d.cant, CONCAT('F', f.nro_fac), 'SIST'
FROM dbo.tbl_fac_cab AS f
JOIN dbo.pedidos_detalle AS d ON d.id_pedido = f.id_ped
WHERE f.cod_est_fac <> 8;

-- Devoluciones que vuelven al stock (las roturas y los vencidos no reingresan).
INSERT INTO #mov
SELECT dv.id_producto, f.pto_vta, DATEADD(MINUTE, 900, CAST(dv.fecha AS DATETIME2(0))), 'DV',
       dv.cantidad, CONCAT('D', dv.id_devolucion), 'SIST'
FROM dbo.devoluciones AS dv
JOIN dbo.tbl_fac_cab AS f ON f.nro_fac = dv.nro_fac
WHERE dv.motivo_cod IN ('ER', 'NC');

-- Compras trimestrales: reponen lo vendido en el trimestre.
INSERT INTO #mov
SELECT v.cod_prod, v.cod_suc, DATEADD(HOUR, 8, CAST(v.trimestre AS DATETIME2(0))), 'IN',
       CEILING(v.vendido * (0.95 + 0.3 * r.r) / 6) * 6,
       CONCAT('OC-', YEAR(v.trimestre), RIGHT(CONCAT('0', MONTH(v.trimestre)), 2), '-', v.cod_prod), 'COMPRAS'
FROM (
    SELECT m.cod_prod, m.cod_suc, t.trimestre, vendido = -SUM(m.cant)
    FROM #mov AS m
    CROSS APPLY (SELECT trimestre = DATEADD(MONTH, (DATEDIFF(MONTH, '2024-11-01', m.fec_mov) / 3) * 3,
                                            CAST('2024-11-01' AS DATE))) AS t
    WHERE m.tip_mov = 'VT'
    GROUP BY m.cod_prod, m.cod_suc, t.trimestre
) AS v
CROSS APPLY dbo.aux_azar('stk_in', v.cod_prod * 1000 + v.cod_suc * 100 + MONTH(v.trimestre)) AS r;

-- Transferencias entre sucursales (salida y entrada).
DROP TABLE IF EXISTS #transf;
SELECT t.n, pa.cod_prod, pa.cod_suc AS origen,
       destino = CAST(1 + (pa.cod_suc + CAST(FLOOR(r_dst.r * 4) AS INT)) % 5 AS SMALLINT),
       fecha = DATEADD(DAY, FLOOR(r_fec.r * 700), CAST('2024-11-15' AS DATETIME2(0))),
       cant = 6 * (1 + FLOOR(r_can.r * 5))
INTO #transf
FROM #n AS t
CROSS APPLY dbo.aux_azar('tr_par', t.n) AS r_par
CROSS APPLY dbo.aux_azar('tr_dst', t.n) AS r_dst
CROSS APPLY dbo.aux_azar('tr_fec', t.n) AS r_fec
CROSS APPLY dbo.aux_azar('tr_can', t.n) AS r_can
CROSS APPLY (SELECT TOP (1) x.cod_prod, x.cod_suc FROM #pares AS x
             WHERE x.cod_suc <= 5 AND x.activo = 1
             ORDER BY HASHBYTES('MD5', CONCAT('tr', t.n, ':', x.cod_prod, ':', x.cod_suc))) AS pa
WHERE t.n <= 200;

INSERT INTO #mov
SELECT cod_prod, origen, DATEADD(HOUR, 9, fecha), 'TR', -cant, CONCAT('TR-', n), 'DEPOSITO2' FROM #transf
WHERE destino <> origen
UNION ALL
SELECT cod_prod, destino, DATEADD(HOUR, 15, fecha), 'TR', cant, CONCAT('TR-', n), 'DEPOSITO2' FROM #transf
WHERE destino <> origen;

-- Ajustes por roturas, robos o diferencias de inventario.
INSERT INTO #mov
SELECT pa.cod_prod, pa.cod_suc,
       DATEADD(HOUR, 18, CAST(DATEADD(DAY, FLOOR(r_fec.r * 740), pa.inicio) AS DATETIME2(0))),
       'AJ', CASE WHEN r_sig.r < 0.8 THEN -(1 + FLOOR(r_can.r * 5)) ELSE 1 + FLOOR(r_can.r * 5) END,
       NULL, CASE WHEN r_sig.r < 0.5 THEN 'JPEREZ' ELSE 'MGARCIA' END
FROM #n AS t
CROSS APPLY dbo.aux_azar('aj_par', t.n) AS r_par
CROSS APPLY dbo.aux_azar('aj_fec', t.n) AS r_fec
CROSS APPLY dbo.aux_azar('aj_sig', t.n) AS r_sig
CROSS APPLY dbo.aux_azar('aj_can', t.n) AS r_can
CROSS APPLY (SELECT TOP (1) x.cod_prod, x.cod_suc, x.inicio FROM #pares AS x
             ORDER BY HASHBYTES('MD5', CONCAT('aj', t.n, ':', x.cod_prod, ':', x.cod_suc))) AS pa
WHERE t.n <= 500 AND DATEADD(DAY, FLOOR(r_fec.r * 740), pa.inicio) <= '2026-11-22';

-- Productos dados de baja: se descarta el stock que quedaba.
INSERT INTO #mov
SELECT m.cod_prod, m.cod_suc, '2026-01-02T08:00:00', 'AJ', -SUM(m.cant), 'BAJA-PRODUCTO', 'COMPRAS'
FROM #mov AS m
JOIN dbo.productos AS p ON p.id_producto = m.cod_prod AND p.activo = 0
WHERE m.fec_mov < '2026-01-02'
GROUP BY m.cod_prod, m.cod_suc
HAVING SUM(m.cant) <> 0;

-- Control de inventario al cierre (23/11/2026): corrige los saldos negativos y deja en
-- cero los productos discontinuados.
INSERT INTO #mov
SELECT s.cod_prod, s.cod_suc, '2026-11-23T18:00:00', 'AJ',
       -s.saldo + CASE WHEN p.activo = 1 THEN FLOOR(r.r * 10) ELSE 0 END, 'INV-CONTROL', 'DEPOSITO1'
FROM (SELECT cod_prod, cod_suc, saldo = SUM(cant) FROM #mov GROUP BY cod_prod, cod_suc) AS s
CROSS APPLY dbo.aux_azar('stk_fix', s.cod_prod * 10 + s.cod_suc) AS r
JOIN dbo.productos AS p ON p.id_producto = s.cod_prod
WHERE s.saldo < 0 OR (p.activo = 0 AND s.saldo <> 0);

-- Punto de reposición: 60 % de la venta mensual promedio del último semestre (mínimo 6).
DROP TABLE IF EXISTS #minimos;
SELECT pa.cod_prod, pa.cod_suc, pa.activo,
       cant_min = CASE WHEN pa.activo = 0 THEN 0
                       WHEN CEILING(ISNULL(v.vendido_6m, 0) / 6.0 * 0.6) < 6 THEN 6
                       ELSE CEILING(ISNULL(v.vendido_6m, 0) / 6.0 * 0.6) END,
       vendido_6m = ISNULL(v.vendido_6m, 0)
INTO #minimos
FROM #pares AS pa
OUTER APPLY (SELECT vendido_6m = -SUM(m.cant) FROM #mov AS m
             WHERE m.cod_prod = pa.cod_prod AND m.cod_suc = pa.cod_suc
               AND m.tip_mov = 'VT' AND m.fec_mov >= '2026-05-23') AS v;

-- Quiebres: en algunos productos que se venden, el control encontró menos stock que el
-- punto de reposición (faltantes de inventario).
INSERT INTO #mov
SELECT mi.cod_prod, mi.cod_suc, '2026-11-23T19:00:00', 'AJ',
       FLOOR(mi.cant_min * 0.7 * r.r) - s.saldo, 'INV-CONTROL', 'DEPOSITO1'
FROM #minimos AS mi
CROSS APPLY dbo.aux_azar('quiebre', mi.cod_prod * 10 + mi.cod_suc) AS r
CROSS APPLY (SELECT saldo = SUM(m.cant) FROM #mov AS m
             WHERE m.cod_prod = mi.cod_prod AND m.cod_suc = mi.cod_suc) AS s
WHERE mi.activo = 1 AND mi.vendido_6m > 0 AND r.r < 0.06
  AND s.saldo > FLOOR(mi.cant_min * 0.7 * r.r);

INSERT INTO dbo.tbl_mov_stk (id_mov, cod_prod, cod_suc, fec_mov, tip_mov, cant, ref_doc, usr_alt)
SELECT ROW_NUMBER() OVER (ORDER BY fec_mov, tip_mov, cod_prod, cod_suc, ref_doc),
       cod_prod, cod_suc, fec_mov, tip_mov, cant, ref_doc, usr_alt
FROM #mov;

-- Stock actual por producto y sucursal.
INSERT INTO dbo.stk_act (cod_prod, cod_suc, cant_disp, cant_min, fec_ult_act)
SELECT mi.cod_prod, mi.cod_suc, ISNULL(s.saldo, 0), mi.cant_min,
       ISNULL(s.ultimo, '2024-11-01T07:00:00')
FROM #minimos AS mi
OUTER APPLY (SELECT saldo = SUM(m.cant), ultimo = MAX(m.fec_mov) FROM dbo.tbl_mov_stk AS m
             WHERE m.cod_prod = mi.cod_prod AND m.cod_suc = mi.cod_suc) AS s;
GO

/* --- Limpieza --------------------------------------------------------------------- */

DROP FUNCTION dbo.aux_azar;
GO

SELECT tabla = t.name, filas = SUM(p.rows)
FROM sys.tables AS t
JOIN sys.partitions AS p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name
ORDER BY t.name;
PRINT 'Datos de DistribuidoraDemo cargados.';
GO
