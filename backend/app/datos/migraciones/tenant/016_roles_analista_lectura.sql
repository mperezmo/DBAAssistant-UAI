-- Roles finales del TFI (10.4.2.3): Administrador, DBA, Analista y Lectura.
-- Consulta pasa a llamarse Lectura sin cambiar lo que puede hacer. En las listas de roles
-- (destinatarios de reglas de alerta y roles que ejecutan una remediación), donde estaba
-- Consulta quedan Analista y Lectura: el Analista tiene, como mínimo, lo de Lectura.
ALTER TABLE dbo.usuario DROP CONSTRAINT CK_usuario_rol;
GO

UPDATE dbo.usuario SET rol = 'LECTURA' WHERE rol = 'CONSULTA';
GO

ALTER TABLE dbo.usuario ADD CONSTRAINT CK_usuario_rol
    CHECK (rol IN ('ADMINISTRADOR', 'DBA', 'ANALISTA', 'LECTURA'));
GO

UPDATE dbo.regla_alerta
SET destinatarios = REPLACE(destinatarios, 'CONSULTA', 'ANALISTA,LECTURA')
WHERE ',' + destinatarios + ',' LIKE '%,CONSULTA,%';
GO

UPDATE dbo.workaround
SET roles = REPLACE(roles, 'CONSULTA', 'ANALISTA,LECTURA')
WHERE ',' + roles + ',' LIKE '%,CONSULTA,%';
