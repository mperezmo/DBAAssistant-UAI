/*
    DistribuidoraDemo · 4. Login para DBA Assistant (en la instancia DESTINO)

    Correr después de restaurar la base. Los logins son de la instancia y no viajan en el
    backup. Crea un login propio (así la app no usa "sa") con los permisos que pide la
    prueba de conexión: estado del servidor, lectura de backups y jobs en msdb, y
    creación de jobs para las remediaciones que programan backups.

    Antes de ejecutarlo, cambiar la clave (y el login, si se quiere) en los :setvar de
    abajo. Ejecutar con sqlcmd o, en SSMS, con el menú Query → SQLCMD Mode activado.
*/
:setvar LOGIN "dba_assistant_demo"
:setvar CLAVE "CambiarEstaClave_2026!"

SET NOCOUNT ON;
GO

USE master;
GO
IF SUSER_ID(N'$(LOGIN)') IS NULL
    CREATE LOGIN [$(LOGIN)] WITH PASSWORD = N'$(CLAVE)', CHECK_POLICY = ON,
        DEFAULT_DATABASE = DistribuidoraDemo;
GRANT VIEW SERVER STATE TO [$(LOGIN)];
GO

USE DistribuidoraDemo;
GO
IF USER_ID(N'$(LOGIN)') IS NULL
    CREATE USER [$(LOGIN)] FOR LOGIN [$(LOGIN)];
ALTER ROLE db_datareader ADD MEMBER [$(LOGIN)];
ALTER ROLE db_datawriter ADD MEMBER [$(LOGIN)];
-- Esquema, planes de ejecución, Query Store y estadísticas de índices.
GRANT VIEW DEFINITION, SHOWPLAN, VIEW DATABASE STATE TO [$(LOGIN)];
GO

USE msdb;
GO
IF USER_ID(N'$(LOGIN)') IS NULL
    CREATE USER [$(LOGIN)] FOR LOGIN [$(LOGIN)];
GRANT SELECT ON dbo.backupset TO [$(LOGIN)];
GRANT SELECT ON dbo.backupmediafamily TO [$(LOGIN)];
GRANT SELECT ON dbo.sysjobs TO [$(LOGIN)];
GRANT SELECT ON dbo.sysjobhistory TO [$(LOGIN)];
GRANT SELECT ON dbo.sysjobactivity TO [$(LOGIN)];
GRANT SELECT ON dbo.syssessions TO [$(LOGIN)];
-- Crear y lanzar jobs (backups programados y reintento de jobs).
ALTER ROLE SQLAgentUserRole ADD MEMBER [$(LOGIN)];
GO

/*
    Opcional, para probar las remediaciones que cambian cosas en la base:

    USE DistribuidoraDemo;
    ALTER ROLE db_ddladmin ADD MEMBER [$(LOGIN)];   -- rebuild de índices, estadísticas
    USE master;
    GRANT ALTER ANY CONNECTION TO [$(LOGIN)];       -- terminar sesiones bloqueantes (KILL)
*/

PRINT 'Login $(LOGIN) listo para DistribuidoraDemo.';
GO
