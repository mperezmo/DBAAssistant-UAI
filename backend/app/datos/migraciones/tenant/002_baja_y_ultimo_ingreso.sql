-- Baja de usuarios con período de gracia (CU-001-000-B): el usuario dado de baja
-- queda deshabilitado 30 días, durante los cuales puede rehabilitarse; luego se purga.
ALTER TABLE dbo.usuario DROP CONSTRAINT CK_usuario_estado;
GO

ALTER TABLE dbo.usuario ADD CONSTRAINT CK_usuario_estado
    CHECK (estado IN ('invitado', 'activo', 'inactivo', 'baja'));
GO

ALTER TABLE dbo.usuario ADD
    fecha_baja     DATETIME2(0) NULL,
    estado_previo  NVARCHAR(20) NULL,  -- estado al que vuelve si se rehabilita
    ultimo_ingreso DATETIME2(0) NULL;
